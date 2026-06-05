"""Corrective prompt library — turns validation errors into Gemini
re-instructions.

When `schema_validator.validate_schema()` returns errors, the schema
builder doesn't just retry with the same prompt (today's broken
behaviour). It builds a CORRECTIVE prompt that names each specific
error and asks Gemini to fix exactly that.

Example: validator catches NON_INTEGER_PAGE on section "EXAMPLE 8.1"
with page_start="9.18". Corrective prompt appended to base schema
prompt becomes:

    ## YOUR PREVIOUS ATTEMPT HAD ERRORS — FIX THESE

    Section "EXAMPLE 8.1" had page_start="9.18" which is not an integer.
    Pages must be the physical printed page number where the section's
    content appears (an integer between 1 and 50). Label numbers in
    section titles (like "9.18" in "EXAMPLE 9.18") are NEVER page numbers.

    Re-emit the schema. For this section, set page_start to the actual
    physical page where "EXAMPLE 8.1" is printed.

This makes Gemini fix the specific issue instead of guessing.

Pure-string functions — no Gemini call, no DB I/O. The caller
(schema_builder) appends the output to the base prompt and re-runs
Gemini.
"""

from __future__ import annotations

from app.services.schema_validator import ErrorType, ValidationError


# ─── PER-ERROR CORRECTIVE FRAGMENTS ────────────────────────────────


def _fragment_non_integer_page(err: ValidationError, total_pages: int | None) -> str:
    """NON_INTEGER_PAGE — Gemini emitted a non-integer page value."""
    field = err.context.get("field", "page")
    value = err.context.get("value", "?")
    title = err.section_title or "<unknown section>"
    bounds = f"between 1 and {total_pages}" if total_pages else "≥ 1"
    return (
        f'- Section "{title}" had {field}={value!r}, which is NOT a valid '
        f"integer page number. Pages must be physical printed page "
        f"numbers (integers {bounds}). "
        f"NEVER use the section's label number (e.g. \"9.18\" in "
        f"\"EXAMPLE 9.18\") as a page — that's the example identifier, "
        f"not a page. Re-emit this section with the correct physical "
        f"printed page number."
    )


def _fragment_inverted_range(err: ValidationError, _total_pages: int | None) -> str:
    """INVERTED_RANGE — page_start > page_end."""
    ps = err.context.get("page_start")
    pe = err.context.get("page_end")
    title = err.section_title or "<unknown section>"
    return (
        f'- Section "{title}" had page_start={ps} > page_end={pe}. '
        f"A section's pages are contiguous: page_start must be ≤ page_end. "
        f"Re-emit this section with the correct order (start is the FIRST "
        f"page, end is the LAST page where the section's content appears)."
    )


def _fragment_page_out_of_bounds(err: ValidationError, total_pages: int | None) -> str:
    """PAGE_OUT_OF_BOUNDS — page number exceeds PDF length."""
    field = err.context.get("field", "page")
    value = err.context.get("value", "?")
    total = err.context.get("pdf_total_pages", total_pages)
    title = err.section_title or "<unknown section>"
    return (
        f'- Section "{title}" had {field}={value}, but the PDF only has '
        f"{total} pages. Page numbers cannot exceed the total. "
        f"Re-emit this section with the correct physical page number "
        f"(between 1 and {total})."
    )


def _fragment_individual_question_as_section(
    err: ValidationError, _total_pages: int | None
) -> str:
    """INDIVIDUAL_QUESTION_AS_SECTION — Gemini wrongly promoted a single
    numbered MCQ/question into its own schema entry."""
    title = err.section_title or "<unknown section>"
    page = err.context.get("page_start")
    page_hint = (
        f" (with page_start={page}, which is likely the question's "
        f"NUMBER not its page)" if page is not None else ""
    )
    return (
        f'- Section "{title}" looks like an individual numbered '
        f"question wrongly emitted as a standalone schema section"
        f"{page_hint}. "
        f"Individual questions inside a question bank are NOT "
        f"separate schema entries — they're counted in the parent "
        f"bank's `expected_question_count` field. "
        f"REMOVE this section entirely. Increment the parent question "
        f"bank's expected_question_count instead. "
        f"NEVER emit ids like 'practice-q-4' or titles like 'Question 4' "
        f"or '4' for individual questions."
    )


# ─── DISPATCH TABLE ────────────────────────────────────────────────

# Adding a new ErrorType requires adding a matching fragment here.
# If missing, the corrector raises — better to fail loudly than to
# silently skip an error class.
_FRAGMENT_BUILDERS = {
    ErrorType.NON_INTEGER_PAGE: _fragment_non_integer_page,
    ErrorType.INVERTED_RANGE: _fragment_inverted_range,
    ErrorType.PAGE_OUT_OF_BOUNDS: _fragment_page_out_of_bounds,
    ErrorType.INDIVIDUAL_QUESTION_AS_SECTION: _fragment_individual_question_as_section,
    # Day 5+ entries pending: PAGE_OUTSIDE_PARENT, SIBLING_PAGE_OVERLAP,
    # PAGE_COVERAGE_GAP, INVALID_TYPE, INVALID_CONTENT_TYPES,
    # CAT_A_NESTED_IN_CAT_B, EMPTY_PLACEHOLDER
}


# ─── PUBLIC API ────────────────────────────────────────────────────


def build_corrective_prompt(
    base_prompt: str,
    errors: list[ValidationError],
    *,
    pdf_total_pages: int | None = None,
    max_errors_in_prompt: int = 20,
) -> str:
    """Append a corrective-instructions block to the base schema prompt.

    Parameters
    ----------
    base_prompt : str
        The unmodified base schema prompt (schema_gemini.txt or
        schema_gemini_multicolumn.txt content).
    errors : list[ValidationError]
        Errors from the previous attempt's `validate_schema()` call.
        Only ERROR-severity errors should be passed; warnings are
        surfaced separately (schema_warnings field).
    pdf_total_pages : int, optional
        Total page count, used inside fragments for clarity.
    max_errors_in_prompt : int
        Cap on how many errors to include. If the previous attempt
        produced 200 errors, we don't dump all 200 — pick the first
        20 (most representative; Gemini will fix patterns after seeing
        a handful).

    Returns the base prompt with a corrective-instructions section
    appended. The returned prompt is what the caller sends to Gemini
    for the next attempt.
    """
    if not errors:
        return base_prompt

    fragments: list[str] = []
    for err in errors[:max_errors_in_prompt]:
        builder = _FRAGMENT_BUILDERS.get(err.type)
        if builder is None:
            # Unknown error type — include a generic fragment rather than
            # silently dropping the error. This is a code error (missing
            # corrector); surfaces during dev.
            fragments.append(
                f"- Section \"{err.section_title or '?'}\" had error "
                f"{err.type.value}: {err.message}"
            )
        else:
            fragments.append(builder(err, pdf_total_pages))

    remaining = max(0, len(errors) - max_errors_in_prompt)
    truncation_note = (
        f"\n\n(Plus {remaining} more similar errors not shown — "
        f"apply the same fixes throughout.)"
        if remaining else ""
    )

    correction_block = (
        "\n\n"
        "## YOUR PREVIOUS ATTEMPT HAD ERRORS — FIX THESE\n\n"
        f"The downstream validator caught {len(errors)} error(s) in your "
        f"previous response. Re-emit the FULL schema with these specific "
        f"fixes applied:\n\n"
        + "\n\n".join(fragments)
        + truncation_note
        + "\n\n"
        "Keep all OTHER sections unchanged unless they have the same "
        "issue pattern. Output the corrected JSON only — no commentary."
    )

    return base_prompt + correction_block


__all__ = ["build_corrective_prompt"]
