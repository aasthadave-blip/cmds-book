"""Schema validator — hard rules, fail loud, no silent fixes.

Replaces `_sanitize_schema`'s silent-drop behavior with structured
validation errors that:
  1. Surface every problem to the caller
  2. Carry enough context to drive a corrective retry (see
     `schema_correctors.py`)

The validator is PURE — no DB I/O, no Gemini call. Caller decides
what to do based on the returned `ValidationResult`.

Rules are added incrementally — each Day in SCHEMA Week 1-2 adds
more. Today (Day 3):
  * Rule 1 (NON_INTEGER_PAGE):   page values must be positive integers
  * Rule 2 (INVERTED_RANGE):     page_start ≤ page_end
  * Rule 3 (PAGE_OUT_OF_BOUNDS): 1 ≤ pages ≤ pdf_total_pages

Day 4-5 add rules 4-10. Together they're the SOLE gate before a
schema is accepted as `done`.

Usage:
    result = validate_schema(parsed_data, pdf_total_pages=42)
    if not result.is_valid:
        # Drive corrective retry — see schema_correctors.build_corrective_prompt
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class ErrorType(str, Enum):
    """Error types correspond 1:1 with corrective prompt fragments in
    schema_correctors.py. Adding a new error type requires adding a
    matching corrective."""

    NON_INTEGER_PAGE = "non_integer_page"
    INVERTED_RANGE = "inverted_range"
    PAGE_OUT_OF_BOUNDS = "page_out_of_bounds"
    # Day 4 (planned):
    PAGE_OUTSIDE_PARENT = "page_outside_parent"
    SIBLING_PAGE_OVERLAP = "sibling_page_overlap"
    PAGE_COVERAGE_GAP = "page_coverage_gap"
    INVALID_TYPE = "invalid_type"
    # Day 5 (planned):
    INVALID_CONTENT_TYPES = "invalid_content_types"
    CAT_A_IN_CAT_B = "cat_a_in_cat_b"
    EMPTY_PLACEHOLDER = "empty_placeholder"


@dataclass(frozen=True)
class ValidationError:
    """A single validation failure. Structured for corrective retry."""

    type: ErrorType
    """Error category — drives the corrective prompt selection."""

    section_id: str | None
    """The offending section's id, if applicable. None for whole-schema errors."""

    section_title: str | None
    """Human-readable section title for the corrective prompt."""

    severity: str
    """'error' (must fix) | 'warning' (accept if quality_score above threshold)."""

    message: str
    """Plain-English explanation for logs + UI surfacing."""

    context: dict[str, Any] = field(default_factory=dict)
    """Extra data the corrective prompt builder needs (e.g. the bad value,
    expected range, parent reference)."""


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running all validators against a schema."""

    is_valid: bool
    """True if zero ERROR-severity issues. Warnings allowed."""

    errors: list[ValidationError]
    """ERROR-severity issues. Block schema acceptance, drive corrective retry."""

    warnings: list[ValidationError]
    """WARNING-severity issues. Surface to user via book.schema_warnings."""

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


# ─── RULE IMPLEMENTATIONS ──────────────────────────────────────────


def _iter_all_sections(data: dict) -> Iterator[tuple[dict, list[str]]]:
    """Walk every section (including excluded_sections) yielding the
    section dict and its parent-chain (list of titles for context).
    """
    def walk(sections, parent_chain):
        for s in sections or []:
            if not isinstance(s, dict):
                continue
            yield s, parent_chain
            yield from walk(
                s.get("subsections") or [],
                parent_chain + [s.get("title", "")],
            )

    yield from walk(data.get("sections") or [], [])
    # excluded_sections too — they have page_start/page_end and matter
    yield from walk(data.get("excluded_sections") or [], ["<excluded>"])


def _check_non_integer_page(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 1: page_start and page_end must be positive integers OR None.

    Catches the bug we keep hitting: Gemini emits "9.18" (a question
    label) or 8.31 (a float) or "9-18" (a range string) as page values.

    `None` is allowed here — caller may treat None as "unknown, fall back
    to extraction-time correction" via E1.
    """
    out = []
    for field_name in ("page_start", "page_end"):
        v = section.get(field_name)
        if v is None:
            continue
        # Booleans are a subclass of int — guard explicitly.
        if isinstance(v, bool) or not isinstance(v, int):
            out.append(ValidationError(
                type=ErrorType.NON_INTEGER_PAGE,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f"{field_name}={v!r} is not an integer. "
                    f"Pages must be positive integers (the physical page "
                    f"where the section's content is printed)."
                ),
                context={
                    "field": field_name,
                    "value": v,
                    "value_type": type(v).__name__,
                },
            ))
        elif v < 1:
            out.append(ValidationError(
                type=ErrorType.NON_INTEGER_PAGE,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f"{field_name}={v} is not positive. Pages start at 1."
                ),
                context={"field": field_name, "value": v},
            ))
    return out


def _check_inverted_range(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 2: page_start ≤ page_end.

    Catches Gemini emitting reversed ranges like page_start=10, page_end=5.
    Only fires when both values are integers (rule 1 catches the rest).
    """
    ps = section.get("page_start")
    pe = section.get("page_end")
    if not (isinstance(ps, int) and not isinstance(ps, bool)):
        return []
    if not (isinstance(pe, int) and not isinstance(pe, bool)):
        return []
    if ps > pe:
        return [ValidationError(
            type=ErrorType.INVERTED_RANGE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f"page_start={ps} > page_end={pe}. "
                f"A section's page_start must be ≤ page_end (sections are "
                f"contiguous, not reverse-ordered)."
            ),
            context={"page_start": ps, "page_end": pe},
        )]
    return []


def _check_page_out_of_bounds(
    section: dict, _parents: list[str], *, pdf_total_pages: int | None
) -> list[ValidationError]:
    """Rule 3: 1 ≤ page_start, page_end ≤ pdf_total_pages.

    Catches Gemini hallucinating page numbers beyond the PDF
    (e.g. page=999 on a 50-page book). Only checks if total_pages
    is provided; otherwise skipped (best-effort).
    """
    if pdf_total_pages is None or pdf_total_pages <= 0:
        return []
    out = []
    for field_name in ("page_start", "page_end"):
        v = section.get(field_name)
        if not (isinstance(v, int) and not isinstance(v, bool)):
            continue
        if v > pdf_total_pages:
            out.append(ValidationError(
                type=ErrorType.PAGE_OUT_OF_BOUNDS,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f"{field_name}={v} exceeds PDF length "
                    f"({pdf_total_pages} pages)."
                ),
                context={
                    "field": field_name,
                    "value": v,
                    "pdf_total_pages": pdf_total_pages,
                },
            ))
    return out


# ─── ENTRY POINT ───────────────────────────────────────────────────


def validate_schema(
    data: dict,
    *,
    pdf_total_pages: int | None = None,
) -> ValidationResult:
    """Run all enabled validation rules against the parsed schema dict.

    Parameters
    ----------
    data : dict
        Parsed schema (Gemini output after parse_json + sanitize, with
        UUIDs assigned). Pydantic validation can be lossy on partials;
        we validate the raw dict before constructing BookSchema.
    pdf_total_pages : int, optional
        Total page count from preflight (or pymupdf). Required for
        PAGE_OUT_OF_BOUNDS rule; rule is skipped if None.

    Returns
    -------
    ValidationResult with `errors` (block schema acceptance, drive
    corrective retry) and `warnings` (surface to user, don't block).
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    for section, parents in _iter_all_sections(data):
        # Each rule returns a list of ValidationError; consolidate.
        for err in _check_non_integer_page(section, parents):
            (errors if err.severity == "error" else warnings).append(err)
        for err in _check_inverted_range(section, parents):
            (errors if err.severity == "error" else warnings).append(err)
        for err in _check_page_out_of_bounds(
            section, parents, pdf_total_pages=pdf_total_pages
        ):
            (errors if err.severity == "error" else warnings).append(err)

    return ValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
    )


__all__ = [
    "ErrorType",
    "ValidationError",
    "ValidationResult",
    "validate_schema",
]
