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

    # Day 3 (implemented):
    NON_INTEGER_PAGE = "non_integer_page"
    INVERTED_RANGE = "inverted_range"
    PAGE_OUT_OF_BOUNDS = "page_out_of_bounds"
    # Day 4 (implemented — your Q4 case):
    INDIVIDUAL_QUESTION_AS_SECTION = "individual_question_as_section"
    # Day 6 (implemented — structural integrity):
    PAGE_OUTSIDE_PARENT = "page_outside_parent"
    SIBLING_PAGE_OVERLAP = "sibling_page_overlap"
    PAGE_COVERAGE_GAP = "page_coverage_gap"
    INVALID_TYPE = "invalid_type"
    MISSING_LEAF_PAGE = "missing_leaf_page"
    # Day 7 (implemented):
    INVALID_CONTENT_TYPES = "invalid_content_types"
    CAT_A_AT_END_NOT_EXCLUDED = "cat_a_at_end_not_excluded"
    EMPTY_PLACEHOLDER = "empty_placeholder"


# Canonical set — used by Rule 7 and others.
_VALID_SECTION_TYPES = frozenset({
    "chapter", "section", "subsection", "excluded",
})

# Rule 10 — canonical content_types vocabulary.
# Sections describe what they CONTAIN: theory prose, questions, figures
# (or some combination). Order doesn't matter; we treat as sets.
_VALID_CONTENT_TYPE_VALUES = frozenset({
    "theory", "questions", "figures",
})

# Allowed COMBINATIONS (as frozensets — order-independent).
# Mixed ("theory" + "questions") IS allowed — represents sections where
# theory prose contains inline numbered questions/exercises. Today's
# sanitizer collapses this to ["theory"] only; Rule 10 enforces preservation
# once sanitizer is deleted Day 14.
_VALID_CONTENT_TYPE_COMBOS = frozenset({
    frozenset({"theory"}),
    frozenset({"questions"}),
    frozenset({"theory", "questions"}),
    frozenset({"theory", "figures"}),
    frozenset({"questions", "figures"}),
    frozenset({"theory", "questions", "figures"}),
})

# Rule 11 — standalone help-section titles that ALWAYS belong in
# excluded_sections regardless of position. These are typically
# chapter-end aids that don't fit the inline numbered pattern.
# Matched as exact phrase (case-insensitive) — substring matching
# would false-positive on titles like "Worked Solutions to Example 8.1".
_END_OF_CHAPTER_HELP_TITLES = frozenset({
    "hints", "solutions", "answers",
    "answer key", "answer keys",
    "key to exercises", "key to problems",
})


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


def _iter_with_parent(data: dict) -> Iterator[tuple[dict, dict | None, list[dict]]]:
    """Walk every section yielding (section, parent, siblings).

    `parent` is None for top-level sections (no parent in schema).
    `siblings` is the list this section is part of (so we can pair-check
    sibling overlaps). For top-level sections, siblings == data["sections"].

    excluded_sections also yielded (with parent=None, siblings=data["excluded_sections"]).
    """
    sections = data.get("sections") or []

    def walk(siblings, parent):
        for s in siblings or []:
            if not isinstance(s, dict):
                continue
            yield s, parent, siblings
            yield from walk(s.get("subsections") or [], s)

    yield from walk(sections, None)
    yield from walk(data.get("excluded_sections") or [], None)


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


def _is_individual_question_title(title: str | None) -> bool:
    """Detect titles that look like individual question identifiers.

    Patterns matched (case-insensitive):
      "4"           — bare number
      "4."          — number with period
      "Q4" / "Q.4"  — Q-prefixed
      "Question 4"  — long form
      "(4)" / "(iv)" — parenthesized
      "MCQ 4"       — MCQ-prefixed
      "Problem 4"   — problem-prefixed

    These are wrongly-promoted individual questions. They should live
    inside the parent Cat A bank's expected_question_count, not as
    standalone schema entries.

    Distinguished from LEGITIMATE Cat A section titles like
    "EXAMPLE 9.1" (worked example with explanation), "Exercise 8.3"
    (named exercise block), or "Practice Questions" (bank heading) —
    those have descriptive words and ARE valid Cat A sections.
    """
    if not title:
        return False
    import re
    t = title.strip().lower()
    patterns = [
        r"^q?\.?\s*\d+\.?$",         # "4", "4.", "q4", "q.4", "q 4"
        r"^question\s+\d+\.?$",       # "Question 4"
        r"^\(\s*\d+\s*\)$",           # "(4)"
        r"^\(\s*[ivxlcdm]+\s*\)$",   # "(iv)", "(ix)"  Roman lower
        r"^mcq\s+\d+\.?$",            # "MCQ 4"
        r"^problem\s+\d+\.?$",        # "Problem 4" (without theory context)
        r"^prob\.?\s*\d+\.?$",        # "Prob 4", "Prob.4"
    ]
    return any(re.match(p, t) for p in patterns)


def _check_individual_question_as_section(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 9: Individual numbered questions wrongly promoted as schema sections.

    Catches the user's "Q4 on page 6 wrongly emitted as section" case.

    Triggers when:
      - section is Cat A (content_types includes "questions")
      - title matches individual-question pattern (see
        _is_individual_question_title)

    These should live inside the parent bank's
    `expected_question_count`, NOT as separate schema entries.

    Page-number-as-question-number side effect (e.g. Gemini set
    page_start=4 because it grabbed the "4" from "Question 4") is
    automatically prevented when this rule fires — corrective retry
    removes the section entirely.
    """
    title = section.get("title")
    if not _is_individual_question_title(title):
        return []

    content_types = section.get("content_types") or []
    if not isinstance(content_types, list):
        content_types = [content_types]
    is_cat_a = "questions" in content_types
    if not is_cat_a:
        # If it's tagged as theory with a number-only title, it's
        # probably TOC bullet noise — caught by a different rule. Skip.
        return []

    return [ValidationError(
        type=ErrorType.INDIVIDUAL_QUESTION_AS_SECTION,
        section_id=section.get("id"),
        section_title=title,
        severity="error",
        message=(
            f'Section "{title}" appears to be an individual numbered '
            f"question wrongly promoted to a standalone schema entry. "
            f"Individual questions belong inside their parent bank's "
            f"expected_question_count, not as separate schema sections."
        ),
        context={
            "title_pattern": "individual_question",
            "page_start": section.get("page_start"),
        },
    )]


def _check_page_outside_parent(
    child: dict, parent: dict | None
) -> list[ValidationError]:
    """Rule 4: child's page range must be ⊂ parent's range.

    Catches cross-section bleed: child claims pages outside what its
    parent says it covers. Either child is wrong or parent's range is
    too narrow — corrective asks Gemini to reconcile.

    Skipped if:
      - No parent (top-level chapter)
      - Either parent or child has None for the relevant page
    """
    if parent is None:
        return []

    out = []
    c_start = child.get("page_start")
    c_end = child.get("page_end")
    p_start = parent.get("page_start")
    p_end = parent.get("page_end")

    # Only validate when both sides have integer pages — rule 1 catches None/strings.
    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if _is_int(c_start) and _is_int(p_start) and c_start < p_start:
        out.append(ValidationError(
            type=ErrorType.PAGE_OUTSIDE_PARENT,
            section_id=child.get("id"),
            section_title=child.get("title"),
            severity="error",
            message=(
                f'Child page_start={c_start} is before parent '
                f'"{parent.get("title")}" page_start={p_start}. '
                f"Children must start at or after their parent."
            ),
            context={
                "child_start": c_start,
                "parent_start": p_start,
                "parent_title": parent.get("title"),
            },
        ))
    if _is_int(c_end) and _is_int(p_end) and c_end > p_end:
        out.append(ValidationError(
            type=ErrorType.PAGE_OUTSIDE_PARENT,
            section_id=child.get("id"),
            section_title=child.get("title"),
            severity="error",
            message=(
                f'Child page_end={c_end} exceeds parent '
                f'"{parent.get("title")}" page_end={p_end}. '
                f"Children must end at or before their parent."
            ),
            context={
                "child_end": c_end,
                "parent_end": p_end,
                "parent_title": parent.get("title"),
            },
        ))
    return out


def _check_sibling_page_overlap(
    siblings: list[dict],
) -> list[ValidationError]:
    """Rule 5: among siblings, no two should overlap by 2+ pages.

    Boundary share (1 page common — section A ends where B starts)
    is allowed. Deep overlap (2+ shared pages) signals two sections
    fighting over the same content.

    Edge cases handled:
      - Either page None → skip the pair
      - Single sibling → no overlap possible
      - Self-comparison → skipped (i != j)
    """
    out = []

    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    # Pair-wise check; we report each conflict once (i < j)
    for i, a in enumerate(siblings):
        if not isinstance(a, dict):
            continue
        a_start = a.get("page_start")
        a_end = a.get("page_end")
        if not (_is_int(a_start) and _is_int(a_end)):
            continue
        for j in range(i + 1, len(siblings)):
            b = siblings[j]
            if not isinstance(b, dict):
                continue
            b_start = b.get("page_start")
            b_end = b.get("page_end")
            if not (_is_int(b_start) and _is_int(b_end)):
                continue
            # Compute overlap range
            overlap_start = max(a_start, b_start)
            overlap_end = min(a_end, b_end)
            if overlap_end < overlap_start:
                continue  # no overlap
            overlap_pages = overlap_end - overlap_start + 1
            if overlap_pages >= 2:
                out.append(ValidationError(
                    type=ErrorType.SIBLING_PAGE_OVERLAP,
                    section_id=a.get("id"),
                    section_title=a.get("title"),
                    severity="error",
                    message=(
                        f'Section "{a.get("title")}" (pages {a_start}-{a_end}) '
                        f'and "{b.get("title")}" (pages {b_start}-{b_end}) '
                        f"both claim pages {overlap_start}-{overlap_end} "
                        f"({overlap_pages} pages of overlap). Sections "
                        f"shouldn't cover the same content."
                    ),
                    context={
                        "section_a_title": a.get("title"),
                        "section_a_pages": [a_start, a_end],
                        "section_b_title": b.get("title"),
                        "section_b_pages": [b_start, b_end],
                        "overlap": [overlap_start, overlap_end],
                    },
                ))
    return out


def _check_page_coverage_gap(
    data: dict, pdf_total_pages: int | None
) -> list[ValidationError]:
    """Rule 6: every PDF page should be in ≥1 leaf section (including
    excluded_sections). Severity = WARNING, not error.

    Gaps are informational — some PDFs legitimately have unannotated
    pages (covers, blanks, copyright). User reviews warnings via
    schema_warnings field; not blocking.

    Edge cases:
      - pdf_total_pages None → skip (no way to compute gaps)
      - Container sections contribute pages via their children's coverage
      - Excluded sections (terminal banks) DO count toward coverage
      - Gaps grouped into ranges (page 5-7 instead of 5, 6, 7)
    """
    if pdf_total_pages is None or pdf_total_pages <= 0:
        return []

    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    covered: set[int] = set()

    def _collect(sections):
        for s in sections or []:
            if not isinstance(s, dict):
                continue
            # A section is a "leaf" for coverage purposes if it has no
            # subsections. Container pages come from descendants.
            kids = s.get("subsections") or []
            ps = s.get("page_start")
            pe = s.get("page_end")
            if not kids and _is_int(ps) and _is_int(pe) and ps <= pe:
                for p in range(ps, pe + 1):
                    if 1 <= p <= pdf_total_pages:
                        covered.add(p)
            _collect(kids)

    _collect(data.get("sections") or [])
    # Excluded sections also count
    for ex in data.get("excluded_sections") or []:
        if not isinstance(ex, dict):
            continue
        ps = ex.get("page_start")
        pe = ex.get("page_end")
        if _is_int(ps) and _is_int(pe) and ps <= pe:
            for p in range(ps, pe + 1):
                if 1 <= p <= pdf_total_pages:
                    covered.add(p)

    all_pages = set(range(1, pdf_total_pages + 1))
    missing = sorted(all_pages - covered)
    if not missing:
        return []

    # Group consecutive missing pages into ranges for cleaner messages
    ranges = []
    if missing:
        run_start = missing[0]
        run_end = missing[0]
        for p in missing[1:]:
            if p == run_end + 1:
                run_end = p
            else:
                ranges.append((run_start, run_end))
                run_start = run_end = p
        ranges.append((run_start, run_end))

    range_strs = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    return [ValidationError(
        type=ErrorType.PAGE_COVERAGE_GAP,
        section_id=None,
        section_title=None,
        severity="warning",
        message=(
            f"Pages not covered by any leaf section: "
            f"{', '.join(range_strs)}. "
            f"If these pages have content (theory or questions), add a "
            f"section to cover them. Cover pages and blanks may be "
            f"legitimately uncovered."
        ),
        context={
            "missing_ranges": [list(r) for r in ranges],
            "total_missing": len(missing),
            "pdf_total_pages": pdf_total_pages,
        },
    )]


def _check_invalid_type(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 7: section.type must be in canonical enum.

    Catches Gemini emitting non-standard types like 'unit',
    'subsubsection', 'sub-section', 'topic'. Today these are silently
    remapped by _sanitize_schema._TYPE_MAP; once that's deleted (Day 14)
    this rule becomes the sole enforcement.
    """
    t = section.get("type")
    if t is None:
        return [ValidationError(
            type=ErrorType.INVALID_TYPE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has no type. '
                f"Required: one of {sorted(_VALID_SECTION_TYPES)}."
            ),
            context={"value": None, "valid_types": sorted(_VALID_SECTION_TYPES)},
        )]
    if not isinstance(t, str):
        return [ValidationError(
            type=ErrorType.INVALID_TYPE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has type={t!r} '
                f"({type(t).__name__}). Type must be a string from: "
                f"{sorted(_VALID_SECTION_TYPES)}."
            ),
            context={"value": t, "valid_types": sorted(_VALID_SECTION_TYPES)},
        )]
    if t not in _VALID_SECTION_TYPES:
        return [ValidationError(
            type=ErrorType.INVALID_TYPE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has type="{t}". '
                f"Valid types: {sorted(_VALID_SECTION_TYPES)}."
            ),
            context={"value": t, "valid_types": sorted(_VALID_SECTION_TYPES)},
        )]
    return []


def _check_invalid_content_types(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 10: content_types must be a valid combination from a strict vocabulary.

    Allowed values per element: {"theory", "questions", "figures"}.
    Allowed combinations:
        ["theory"]
        ["questions"]
        ["theory", "questions"]    ← Mixed (today silently collapsed by sanitizer)
        ["theory", "figures"]
        ["questions", "figures"]
        ["theory", "questions", "figures"]

    Normalization rules (per user spec):
      - Duplicates allowed in raw input; we dedupe before comparison
      - Order doesn't matter (set-based)
      - Case STRICT: only lowercase accepted (so "Theory" → error)
      - Empty list → error (must specify at least one)
      - None → error (must be specified)
    """
    ct = section.get("content_types")
    if ct is None:
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has no content_types. '
                f"Required: one of theory/questions/mixed plus optional figures."
            ),
            context={"value": None},
        )]
    if not isinstance(ct, list):
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has content_types '
                f"of type {type(ct).__name__}, expected list."
            ),
            context={"value": ct, "value_type": type(ct).__name__},
        )]
    if not ct:
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has empty content_types '
                f"list. Must specify at least one of theory/questions."
            ),
            context={"value": []},
        )]

    # Check all elements are strings + lowercase + in valid vocabulary
    for v in ct:
        if not isinstance(v, str):
            return [ValidationError(
                type=ErrorType.INVALID_CONTENT_TYPES,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f'Section "{section.get("title")}" content_types '
                    f"contains non-string: {v!r}. Expected: lowercase strings."
                ),
                context={"value": ct, "bad_element": v},
            )]
        if v != v.lower():
            return [ValidationError(
                type=ErrorType.INVALID_CONTENT_TYPES,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f'Section "{section.get("title")}" content_types '
                    f'contains "{v}" (mixed case). Must be lowercase '
                    f'(e.g. "theory" not "Theory").'
                ),
                context={"value": ct, "bad_element": v},
            )]
        if v not in _VALID_CONTENT_TYPE_VALUES:
            return [ValidationError(
                type=ErrorType.INVALID_CONTENT_TYPES,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f'Section "{section.get("title")}" content_types '
                    f'contains "{v}". Valid values: '
                    f"{sorted(_VALID_CONTENT_TYPE_VALUES)}."
                ),
                context={"value": ct, "bad_element": v},
            )]

    # Dedupe and compare as set (order/duplicates don't matter)
    normalized = frozenset(ct)
    if normalized not in _VALID_CONTENT_TYPE_COMBOS:
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" content_types {ct} '
                f"is not a valid combination. Allowed: each set must "
                f"contain at least one of theory/questions, optionally figures."
            ),
            context={"value": ct, "normalized": sorted(normalized)},
        )]
    return []


def _has_inline_decimal_pattern(title: str | None) -> bool:
    """True if title contains a X.Y decimal pattern (Example 1.1, Exercise 8.3).

    Used to whitelist inline Cat A items from Rule 11. Per user spec:
    Cat A items with decimal numbering are INLINE; without decimal
    they may be chapter-end banks.

    Matches digit.digit anywhere in the title (also handles X.Y.Z, e.g. 1.2.3).
    """
    if not title:
        return False
    import re
    return bool(re.search(r"\d+\.\d+", title))


def _is_standalone_help_title(title: str | None) -> bool:
    """True if title is a standalone end-of-chapter help section.

    Hints/Solutions/Answer Keys/Answers when used as a section heading
    (not part of a longer descriptive title like "Worked Solutions to
    Example 8.1") signal a chapter-end aid that belongs in
    excluded_sections.

    Match logic: title's normalized form is exactly equal to one of
    the help phrases (or differs only by trailing punctuation/colon).
    """
    if not title:
        return False
    import re
    # Strip surrounding whitespace, lowercase, drop trailing punct/colons
    t = re.sub(r"[\s:.,;!\-]+$", "", title.strip().lower())
    return t in _END_OF_CHAPTER_HELP_TITLES


def _check_cat_a_at_end_not_excluded(
    section: dict,
    parent: dict | None,
    siblings: list[dict],
) -> list[ValidationError]:
    """Rule 11: Cat A sections at end of chapter belong in excluded_sections.

    Per user spec:
      - Cat A items with X.Y decimal pattern (Example 1.1, Exercise 8.3)
        are INLINE — nested under Cat B parent regardless of position
      - Cat A bank-style sections (Practice Questions, MCQs, Hints, etc.)
        at the END of a chapter belong in excluded_sections (flat array)
      - "End of chapter" = no Cat B (theory) sibling AFTER this section
        in the parent's children list
      - Standalone "Hints", "Solutions", "Answer Keys", "Answers" titles
        ALWAYS belong in excluded_sections (titles signal chapter-end aid)

    Fires when ALL true:
      1. Section is Cat A (content_types includes "questions")
      2. Title does NOT have X.Y decimal pattern (inline whitelist)
      3. EITHER:
         a. No Cat B section appears AFTER it in parent's subsections
         b. OR title is a standalone help-section name (always excluded)

    Returns ERROR with suggested move to excluded_sections.

    Skip cases:
      - Section is in excluded_sections (no parent in main tree, won't fire)
      - Section is a top-level chapter (parent is None — not "in a chapter")
      - X.Y decimal title → whitelist, never flag
    """
    # Must be Cat A
    ct = section.get("content_types") or []
    if not isinstance(ct, list) or "questions" not in ct:
        return []

    # Title-based whitelist: inline decimal pattern → always inline, never flag
    title = section.get("title") or ""
    if _has_inline_decimal_pattern(title):
        return []

    # Skip Cat A nested inside another Cat A — only flag the TOP-MOST
    # bank. If parent is also Cat A (e.g. "Very Short Answer Type" inside
    # "CLASSROOM WING" inside "Practice Questions"), the parent will be
    # flagged and its children move with it structurally. Flagging every
    # descendant would be noisy and redundant.
    if parent is not None:
        parent_ct = parent.get("content_types") or []
        if isinstance(parent_ct, list) and "questions" in parent_ct:
            return []

    # Standalone help title → ALWAYS flag regardless of position
    # (still only fires for top-most, since nested Cat A skipped above)
    if _is_standalone_help_title(title):
        return [ValidationError(
            type=ErrorType.CAT_A_AT_END_NOT_EXCLUDED,
            section_id=section.get("id"),
            section_title=title,
            severity="error",
            message=(
                f'Section "{title}" is a standalone help section '
                f"(hints/solutions/answer keys) — these belong in "
                f"excluded_sections (flat top-level array), not nested "
                f"in the main sections tree."
            ),
            context={
                "title": title,
                "reason": "standalone_help",
            },
        )]

    # Positional check: skip top-level chapters (no parent in main tree)
    if parent is None:
        return []

    # Position check: no Cat B sibling AFTER this section?
    if section not in siblings:
        # Defensive — shouldn't happen, walker yields (section, parent, siblings)
        return []
    try:
        idx = siblings.index(section)
    except ValueError:
        return []

    # Look at siblings AFTER this section in the parent's children list
    has_cat_b_after = False
    for sib in siblings[idx + 1:]:
        sib_ct = sib.get("content_types") or []
        if isinstance(sib_ct, list) and "theory" in sib_ct:
            has_cat_b_after = True
            break

    if has_cat_b_after:
        # Cat B follows → this is NOT end-of-chapter → inline OK
        return []

    # We're a Cat A section with no Cat B after us in parent's children → end-of-chapter bank
    return [ValidationError(
        type=ErrorType.CAT_A_AT_END_NOT_EXCLUDED,
        section_id=section.get("id"),
        section_title=title,
        severity="error",
        message=(
            f'Section "{title}" is a Cat A bank at end of its chapter '
            f"(no theory section follows it). End-of-chapter banks "
            f"belong in excluded_sections (flat top-level array), "
            f"not nested as a subsection of a theory parent."
        ),
        context={
            "title": title,
            "reason": "end_of_chapter_position",
            "parent_title": parent.get("title"),
        },
    )]


def _check_empty_placeholder(
    data: dict, pdf_total_pages: int | None
) -> list[ValidationError]:
    """Rule 12: catastrophic schema (empty sections + excluded) on a non-blank PDF.

    Fires when:
      - data["sections"] is empty/missing
      - AND data["excluded_sections"] is empty/missing
      - AND pdf_total_pages > 1 (cover-only PDFs allowed)

    This catches the worst-case Gemini failure where it returns
    {"sections": [], "excluded_sections": []} on a PDF that clearly
    has content. The prompt already has anti-placeholder guards, but
    Gemini sometimes ignores them.
    """
    sections = data.get("sections") or []
    excluded = data.get("excluded_sections") or []
    if sections or excluded:
        return []
    # Empty schema — only flag if PDF has more than trivial content
    if pdf_total_pages is None or pdf_total_pages <= 1:
        return []
    return [ValidationError(
        type=ErrorType.EMPTY_PLACEHOLDER,
        section_id=None,
        section_title=None,
        severity="error",
        message=(
            f"Schema is empty (zero sections, zero excluded_sections) "
            f"but the PDF has {pdf_total_pages} pages. Cannot proceed "
            f"with an empty schema. Re-extract with anti-placeholder "
            f"guard active."
        ),
        context={
            "sections_count": 0,
            "excluded_count": 0,
            "pdf_total_pages": pdf_total_pages,
        },
    )]


def _check_missing_leaf_page(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 8: leaf sections MUST have page_start.

    Critical for extraction contract: a leaf section's content is
    extracted from EXACTLY its own page range. If page_start is None,
    extraction has nothing to slice — silent extraction failure.

    Container sections (with subsections) MAY have None page_start
    (derivable from min(children's page_start)). True placeholders
    (no children AND no pages) are flagged.

    page_end can be None on leaves (extractor derives from next
    section's start or chapter end — but page_start is mandatory).
    """
    subs = section.get("subsections") or []
    is_leaf = len(subs) == 0
    if not is_leaf:
        return []

    page_start = section.get("page_start")
    if isinstance(page_start, int) and not isinstance(page_start, bool):
        return []  # has a valid integer page_start

    return [ValidationError(
        type=ErrorType.MISSING_LEAF_PAGE,
        section_id=section.get("id"),
        section_title=section.get("title"),
        severity="error",
        message=(
            f'Leaf section "{section.get("title")}" has no valid '
            f"page_start. Leaf sections (no subsections) require a "
            f"physical page number — extraction CANNOT fall back to "
            f"parent's range. Either add page_start or make it a "
            f"container with subsections."
        ),
        context={
            "is_leaf": True,
            "page_start_value": page_start,
        },
    )]


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

    # Track which sibling-lists we've checked for overlap so we don't
    # re-check the same siblings once per section in that group.
    seen_sibling_lists: set[int] = set()

    for section, parent, siblings in _iter_with_parent(data):
        # Per-section rules (called for every section)
        for err in _check_non_integer_page(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        for err in _check_inverted_range(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        for err in _check_page_out_of_bounds(
            section, [], pdf_total_pages=pdf_total_pages
        ):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 4 — Q4-as-section
        for err in _check_individual_question_as_section(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 6 — parent containment
        for err in _check_page_outside_parent(section, parent):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 6 — type enum
        for err in _check_invalid_type(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 6 — leaf page presence
        for err in _check_missing_leaf_page(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 7 — content_types vocabulary + Mixed preservation
        for err in _check_invalid_content_types(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 7 — Cat A at end belongs in excluded_sections
        for err in _check_cat_a_at_end_not_excluded(section, parent, siblings):
            (errors if err.severity == "error" else warnings).append(err)

        # Sibling-pair rule: check ONCE per sibling list (not once per
        # section in the list).
        siblings_id = id(siblings)
        if siblings_id not in seen_sibling_lists:
            seen_sibling_lists.add(siblings_id)
            for err in _check_sibling_page_overlap(siblings):
                (errors if err.severity == "error" else warnings).append(err)

    # Whole-schema rules (run once, not per section)
    # Day 6 — page coverage gap (warning)
    for err in _check_page_coverage_gap(data, pdf_total_pages):
        (errors if err.severity == "error" else warnings).append(err)
    # Day 7 — catastrophic empty schema
    for err in _check_empty_placeholder(data, pdf_total_pages):
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
