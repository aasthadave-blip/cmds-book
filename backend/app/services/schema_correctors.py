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


def _fragment_page_outside_parent(
    err: ValidationError, _total_pages: int | None
) -> str:
    """PAGE_OUTSIDE_PARENT — child's pages extend outside parent's range."""
    title = err.section_title or "<unknown section>"
    parent = err.context.get("parent_title", "<unknown parent>")
    c_start = err.context.get("child_start")
    p_start = err.context.get("parent_start")
    c_end = err.context.get("child_end")
    p_end = err.context.get("parent_end")
    if c_start is not None and p_start is not None:
        # before-parent error
        return (
            f'- Section "{title}" starts on page {c_start} but its '
            f'parent "{parent}" only starts on page {p_start}. '
            f"A child section must start at OR after its parent. "
            f"Either fix the child's page_start to be ≥ {p_start}, "
            f"or extend parent's page_start to ≤ {c_start} so it "
            f"truly covers the child."
        )
    # after-parent error
    return (
        f'- Section "{title}" ends on page {c_end} but its parent '
        f'"{parent}" only ends on page {p_end}. '
        f"A child section must end at OR before its parent. "
        f"Either fix the child's page_end to be ≤ {p_end}, "
        f"or extend parent's page_end to ≥ {c_end}."
    )


def _fragment_sibling_page_overlap(
    err: ValidationError, _total_pages: int | None
) -> str:
    """SIBLING_PAGE_OVERLAP — two sibling sections claim overlapping pages."""
    a_title = err.context.get("section_a_title", "<A>")
    b_title = err.context.get("section_b_title", "<B>")
    a_pages = err.context.get("section_a_pages", [])
    b_pages = err.context.get("section_b_pages", [])
    overlap = err.context.get("overlap", [])
    return (
        f'- Sibling sections "{a_title}" (pages {a_pages[0]}-{a_pages[1]}) '
        f'and "{b_title}" (pages {b_pages[0]}-{b_pages[1]}) both claim '
        f'pages {overlap[0]}-{overlap[1]}. Sections cover different '
        f"parts of the PDF — they shouldn't overlap. Fix one of their "
        f"page ranges so they're adjacent (e.g. A ends at page 5, B "
        f"starts at page 6) or just touch on one boundary page."
    )


def _fragment_page_coverage_gap(
    err: ValidationError, _total_pages: int | None
) -> str:
    """PAGE_COVERAGE_GAP — pages not covered by any leaf section (warning).

    This is a WARNING-severity error, but we include a corrective
    fragment in case the caller chooses to feed it back to Gemini for
    completeness.
    """
    ranges = err.context.get("missing_ranges", [])
    range_strs = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    pretty = ", ".join(range_strs)
    return (
        f"- The schema doesn't cover these pages with any leaf "
        f"section: {pretty}. If those pages contain theory or "
        f"questions, add sections for them. If they're cover/blank/"
        f"copyright/index, leave as-is — those are legitimately "
        f"uncovered."
    )


def _fragment_invalid_type(
    err: ValidationError, _total_pages: int | None
) -> str:
    """INVALID_TYPE — section type not in canonical enum."""
    title = err.section_title or "<unknown section>"
    bad = err.context.get("value")
    valid = err.context.get("valid_types", [])
    return (
        f'- Section "{title}" has type={bad!r}. Valid types are exactly: '
        f"{valid}. Re-emit with the correct canonical type. Common "
        f"mappings: \"subsubsection\" → \"subsection\", \"unit\" → "
        f"\"chapter\" (if it's a top-level division) or \"section\" "
        f"(if it's a chapter subdivision)."
    )


def _fragment_missing_leaf_page(
    err: ValidationError, _total_pages: int | None
) -> str:
    """MISSING_LEAF_PAGE — leaf section has no page_start, extraction can't run."""
    title = err.section_title or "<unknown section>"
    return (
        f'- Section "{title}" has no subsections AND no page_start. '
        f"Leaf sections require a physical page number — content "
        f"extraction needs to know which page to look at. "
        f"Either add page_start (the page where this section begins) "
        f"OR add subsections (making it a container that covers "
        f"its children's pages). NEVER leave a leaf with page_start "
        f"= null — extraction will silently fail."
    )


def _fragment_invalid_content_types(
    err: ValidationError, _total_pages: int | None
) -> str:
    """INVALID_CONTENT_TYPES — content_types is malformed or uses unknown values."""
    title = err.section_title or "<unknown section>"
    value = err.context.get("value")
    return (
        f'- Section "{title}" has content_types={value!r}. Valid '
        f"content_types arrays (lowercase, order-independent):\n"
        f"    [\"theory\"]\n"
        f"    [\"questions\"]\n"
        f"    [\"theory\", \"questions\"]   (mixed — preserve when "
        f"both theory prose AND inline questions appear in same section)\n"
        f"    [\"theory\", \"figures\"]\n"
        f"    [\"questions\", \"figures\"]\n"
        f"    [\"theory\", \"questions\", \"figures\"]\n"
        f"Use only \"theory\", \"questions\", and optionally "
        f"\"figures\". All lowercase. NEVER collapse Mixed "
        f"[\"theory\", \"questions\"] to [\"theory\"] alone — "
        f"the question pipeline needs to know about inline questions."
    )


def _fragment_cat_a_at_end_not_excluded(
    err: ValidationError, _total_pages: int | None
) -> str:
    """CAT_A_AT_END_NOT_EXCLUDED — chapter-end Cat A bank wrongly nested
    in main sections tree instead of excluded_sections."""
    title = err.section_title or "<unknown section>"
    reason = err.context.get("reason", "")
    parent = err.context.get("parent_title", "")

    if reason == "standalone_help":
        return (
            f'- Section "{title}" is a standalone help-section title '
            f"(hints, solutions, answer keys, answers). These ALWAYS "
            f"belong in the schema's excluded_sections array (flat "
            f"top-level list), never nested as a subsection of any "
            f"theory parent. Move \"{title}\" out of the sections "
            f"tree and into excluded_sections with content_types="
            f"[\"questions\"]."
        )
    # Positional case
    parent_hint = f' (currently nested under "{parent}")' if parent else ""
    return (
        f'- Section "{title}" is a Cat A bank at the END of its chapter'
        f"{parent_hint} — no theory section follows it at the same "
        f"level. End-of-chapter banks belong in excluded_sections "
        f"(flat top-level array), not nested in the main sections "
        f"tree. Move \"{title}\" to excluded_sections with "
        f"content_types=[\"questions\"]. Inline numbered items "
        f"(Example 1.1, Exercise 8.3, Problem 5.1 — anything with "
        f"X.Y decimal) stay inline in their parent's subsections."
    )


def _fragment_empty_placeholder(
    err: ValidationError, _total_pages: int | None
) -> str:
    """EMPTY_PLACEHOLDER — schema is empty but PDF has content."""
    total = err.context.get("pdf_total_pages", "?")
    return (
        f"- The schema you returned is COMPLETELY EMPTY (zero "
        f"sections AND zero excluded_sections), but the PDF has "
        f"{total} pages of content. This is forbidden. Re-emit "
        f"the schema with at least one section. If the PDF truly "
        f"has no instructional content (covers, blanks only), "
        f"populate extraction_notes explaining why. Otherwise, "
        f"identify the chapter / sections / banks present and "
        f"emit them."
    )


# ─── DISPATCH TABLE ────────────────────────────────────────────────

# Adding a new ErrorType requires adding a matching fragment here.
# If missing, the corrector raises — better to fail loudly than to
# silently skip an error class.
_FRAGMENT_BUILDERS = {
    # Day 3
    ErrorType.NON_INTEGER_PAGE: _fragment_non_integer_page,
    ErrorType.INVERTED_RANGE: _fragment_inverted_range,
    ErrorType.PAGE_OUT_OF_BOUNDS: _fragment_page_out_of_bounds,
    # Day 4
    ErrorType.INDIVIDUAL_QUESTION_AS_SECTION: _fragment_individual_question_as_section,
    # Day 6
    ErrorType.PAGE_OUTSIDE_PARENT: _fragment_page_outside_parent,
    ErrorType.SIBLING_PAGE_OVERLAP: _fragment_sibling_page_overlap,
    ErrorType.PAGE_COVERAGE_GAP: _fragment_page_coverage_gap,
    ErrorType.INVALID_TYPE: _fragment_invalid_type,
    ErrorType.MISSING_LEAF_PAGE: _fragment_missing_leaf_page,
    # Day 7 — validator now feature-complete (12 rules total)
    ErrorType.INVALID_CONTENT_TYPES: _fragment_invalid_content_types,
    ErrorType.CAT_A_AT_END_NOT_EXCLUDED: _fragment_cat_a_at_end_not_excluded,
    ErrorType.EMPTY_PLACEHOLDER: _fragment_empty_placeholder,
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
