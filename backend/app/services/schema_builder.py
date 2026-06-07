"""Schema Generator — Gemini 2.5 Pro for native PDF understanding.

Uses the google-genai SDK (NOT the deprecated google-generativeai
package). Routes to prompts/v2/schema_architecture.txt (single-column)
or prompts/v2/schema_architecture_multicolumn.txt (multi-column) based
on the upload-time is_multi_column flag AND auto-detection via
pdf_layout_detector (see Day 12 wiring below).

build_schema() is intentionally SYNCHRONOUS. The Celery worker thread
(extract.py:analyse_book_task) runs without an event loop, so async
calls cause "no current event loop" errors from google-genai's httpx
internals. We ensure a loop exists for the thread, then call Gemini
synchronously.

Pipeline (after SCHEMA Week 1 completion):

    Gemini call (one of 3 attempts, attempt 2+ uses corrective prompt)
        ↓
    assign_uuids_to_schema()  — stable section identity
        ↓
    validate_schema()  — 12 hard rules; failure → corrective retry
        ↓
    BookSchema(**data)  — pydantic construction
        ↓
    verify_schema_against_pdf_text()  — pypdf cross-check (missing labels)
        ↓
    cross_check_section_pages()  — pypdf page verification + auto-correct
        ↓
    return validated schema

NO SILENT FIXES. Every issue is either auto-corrected via Gemini retry
with structured feedback, OR auto-corrected via pypdf ground truth, OR
surfaced as a validation error to the user.
"""

from __future__ import annotations

import asyncio
import logging

from app.schemas.analyser import BookSchema, SchemaSection
from app.services.prompt_loader import load_raw
from app.services.schema_postpass import verify_schema_against_pdf_text
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
GEMINI_MODEL = "gemini-2.5-flash"


def _ensure_event_loop() -> None:
    """Ensure the current thread has an event loop.

    google-genai's httpx internals call asyncio.get_event_loop() internally.
    In non-main threads (like our task-analyse_book daemon thread), no loop
    exists by default — this creates one and sets it as current.
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


def assign_uuids_to_schema(
    data: dict,
    *,
    existing_uuid_by_key: dict[tuple[str, int | None], str] | None = None,
) -> dict:
    """Assign canonical UUIDs to every section in the parsed schema dict.

    SCHEMA Week 1 Day 2 — adds `uuid` field to each section. The UUID
    becomes the canonical identity used by Section rows downstream,
    replacing the slug-based identity that schema_alignment.py tries
    to repair on re-analyse.

    Parameters
    ----------
    data : dict
        Parsed schema dict (Gemini output after parse_json + sanitize).
        Must have a 'sections' key; mutated in place.
    existing_uuid_by_key : dict, optional
        On re-analyse: map of (title, page_start) → uuid for previously
        extracted sections. Matching schema sections preserve their old
        UUID instead of getting a new one. None on first analyse.

    Returns the same dict (mutated). Sections that already carry a
    'uuid' field are left untouched (idempotent — safe to re-run).

    Pure function — no DB I/O. Caller is responsible for providing
    existing_uuid_by_key from the DB if re-analyse safety matters.
    """
    import uuid as _uuid

    existing_uuid_by_key = existing_uuid_by_key or {}

    def _walk(sections):
        if not isinstance(sections, list):
            return
        for s in sections:
            if not isinstance(s, dict):
                continue
            # Idempotent: skip if already assigned
            if s.get("uuid"):
                _walk(s.get("subsections") or [])
                continue
            # Try to preserve existing UUID via (title, page_start) match
            key = (
                (s.get("title") or "").strip().lower(),
                s.get("page_start"),
            )
            preserved = existing_uuid_by_key.get(key)
            s["uuid"] = preserved or str(_uuid.uuid4())
            _walk(s.get("subsections") or [])

    _walk(data.get("sections") or [])
    return data


# SCHEMA Week 1 Day 14 — _sanitize_schema DELETED.
# Replaced architecturally by:
#   * services/schema_validator.py — 12 hard validation rules
#   * services/schema_correctors.py — corrective prompts that drive
#     Gemini to fix issues on retry
#   * services/schema_postpass.cross_check_section_pages —
#     pypdf-verified page correction
# Total LOC removed: ~140. No silent fixes anywhere; every issue is
# either auto-corrected via retry OR surfaced as a validation error.


def _run_gemini_schema(
    pdf_bytes: bytes, schema_prompt: str, *, timeout_s: int = 300,
) -> dict:
    """One synchronous Gemini call: upload PDF → generate schema → return dict.

    Real socket timeout via ``HttpOptions`` — see app.core.gemini_runtime.
    Schema generation runs over the full book PDF and is the slowest single
    call in the system. Timeout is per-PDF-type (Day 12 wiring): digital
    PDFs get 180s, scanned PDFs get 600s; default 300s if caller skips it.
    """
    from app.core.gemini_runtime import call_gemini_with_pdf

    raw = call_gemini_with_pdf(
        pdf_bytes=pdf_bytes,
        system_prompt=schema_prompt,
        user_prompt="",
        model=GEMINI_MODEL,
        timeout_s=timeout_s,
        max_output_tokens=32000,
        # temperature=0.1 — slight sampling variance helps Gemini find
        # rule-compliant interpretations on ambiguous page-spanning
        # cases. Pure greedy (0.0) was observed to lock into wrong
        # most-probable answers on specific PDF layouts; 0.1 gives
        # enough wiggle room to find the binary-rule-compliant
        # interpretation while still being highly deterministic.
        temperature=0.1,
        display_name="textbook_chapter.pdf",
    )
    return parse_json(raw)


def _build_image_only_template_schema(
    total_pages: int, pdf_title: str | None
) -> BookSchema:
    """Generate a minimal valid schema for image-only PDFs.

    Image-only PDFs (scanned without OCR, single-page graphics, etc.) have
    no extractable text — pypdf returns nothing. Sending them to Gemini
    yields incomplete output that the strict validator rejects, burning
    3 retries (~$5 + 10 min wall time) before failing the upload.

    This bypass generates a structurally valid minimal schema:
      • Chapter wrapper at level 1 covering all pages
      • content_types=["theory"] (chapter wrappers always carry theory)
      • Empty subsections (no structure can be extracted from image-only PDF)
      • extraction_notes explains the bypass to the user

    The user can later use the schema editor UI to rename or add
    structure manually — or re-upload an OCR'd version of the PDF.
    """
    import uuid as _uuid

    title = (pdf_title or "").strip() or "Scanned Chapter"
    chapter = SchemaSection(
        id="ch1",
        uuid=str(_uuid.uuid4()),
        level=1,
        title=title,
        type="chapter",
        page_start=1,
        page_end=max(1, total_pages),
        content_types=["theory"],
        is_numbered=False,
        expected_question_count=0,
        subsections=[],
    )
    return BookSchema(
        document_title=title,
        subject="",
        grade_level=None,
        board=None,
        total_pages=max(1, total_pages),
        sections=[chapter],
        excluded_sections=[],
        exclusion_summary=[],
        extraction_notes=(
            "Image-only PDF detected by preflight — no text was extractable. "
            "Schema generation was bypassed and a minimal placeholder created. "
            "For full structural extraction, OCR the PDF and re-upload."
        ),
    )


def build_schema(
    pdf_bytes: bytes,
    *,
    is_multi_column: bool = False,
    pdf_title: str | None = None,
) -> BookSchema:
    """Generate a structural schema from PDF bytes using Gemini 2.5 Pro.

    SYNCHRONOUS — call directly, do NOT wrap in asyncio.run().

    When ``is_multi_column`` is True (user-flagged at upload time for
    MHT-CET / JEE / NEET prep books with dense 2-column layouts), the
    multi-column-aware prompt is loaded. SCHEMA Day 12: if the user did
    NOT flag multi-column but the layout detector confidently says
    otherwise on a digital PDF, we auto-route to the multicolumn prompt.
    User's explicit flag always wins.

    Retry behaviour (SCHEMA Week 1 Day 3):
      Attempt 1: standard prompt
      Attempt 2+: corrective prompt — if attempt N's response failed
                  validation (e.g. NON_INTEGER_PAGE), attempt N+1
                  appends specific fix instructions for those errors
                  via schema_correctors.build_corrective_prompt().

      Each retry is no longer identical to the previous — Gemini
      receives targeted feedback and re-emits with fixes applied.
    """
    _ensure_event_loop()

    # SCHEMA Day 12 — preflight FIRST. Fails fast on encrypted/empty/
    # corrupt PDFs before burning a 3-5 minute Gemini call. Also gives
    # us total_pages (validator bounds rule) and a per-PDF-type Gemini
    # timeout (digital: 180s, scanned: 600s).
    from app.services.pdf_preflight import run_preflight
    preflight = run_preflight(pdf_bytes)
    if not preflight.ok:
        raise ValueError(f"PDF preflight failed: {preflight.error}")
    pdf_total_pages = preflight.total_pages
    gemini_timeout_s = preflight.recommended_timeout_s

    # SCHEMA Day 12 — autodetect column layout. User's explicit
    # upload-time flag ALWAYS wins; we only auto-route when the user
    # did NOT flag multi-column AND the detector is confident on a
    # digital PDF (scanned/image-only PDFs have no reliable text-block
    # geometry, so detection is meaningless there).
    effective_multi_column = is_multi_column
    auto_routed = False
    if not is_multi_column and preflight.pdf_type == "digital":
        from app.services.pdf_layout_detector import detect_layout
        layout = detect_layout(pdf_bytes)
        if layout.layout == "multi" and layout.confidence >= 0.7:
            effective_multi_column = True
            auto_routed = True
            logger.info(
                "Schema layout autodetect: routing to multicolumn prompt "
                "(layout=%s confidence=%.2f pages_sampled=%d) — user did "
                "not flag is_multi_column at upload",
                layout.layout, layout.confidence, layout.pages_sampled,
            )

    logger.info(
        "Schema preflight: pdf_type=%s pages=%d timeout=%ds "
        "multi_column=%s%s rotated_pages=%d",
        preflight.pdf_type, preflight.total_pages, gemini_timeout_s,
        effective_multi_column, " (auto)" if auto_routed else "",
        len(preflight.rotation_pages),
    )

    # SCHEMA Day 12.5 — image-only PDF bypass.
    # When the preflight detector classifies the PDF as image-only
    # (no extractable text via pypdf), Gemini consistently emits
    # incomplete output that the strict validator rejects — burning 3
    # retries and ~$5 of Gemini cost before failing the upload entirely.
    # Generate a minimal valid template schema instead. The user can
    # rename / restructure via the schema editor UI, or re-upload an
    # OCR'd version for full structural extraction.
    if preflight.pdf_type == "image_only":
        logger.warning(
            "Schema build: image-only PDF (%d page%s) — bypassing Gemini, "
            "emitting minimal template schema (user can edit via UI)",
            preflight.total_pages,
            "" if preflight.total_pages == 1 else "s",
        )
        return _build_image_only_template_schema(
            total_pages=preflight.total_pages, pdf_title=pdf_title,
        )

    prompt_name = (
        "schema_architecture_multicolumn" if effective_multi_column else "schema_architecture"
    )
    base_prompt = load_raw(prompt_name, version="v2")

    # Track the previous attempt's validation errors so the next
    # attempt's prompt can include corrective instructions.
    last_errors: list = []
    last_err: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # SCHEMA Day 3: corrective retry — each attempt after the
            # first appends specific fix instructions from prior errors.
            if attempt == 1 or not last_errors:
                prompt = base_prompt
            else:
                from app.services.schema_correctors import build_corrective_prompt
                prompt = build_corrective_prompt(
                    base_prompt,
                    last_errors,
                    pdf_total_pages=pdf_total_pages,
                )
                logger.info(
                    "Schema attempt %s — using corrective prompt for %d errors",
                    attempt, len(last_errors),
                )

            data = _run_gemini_schema(pdf_bytes, prompt, timeout_s=gemini_timeout_s)
            # SCHEMA Day 14: sanitizer DELETED. Validator + corrective
            # retry handle all the cases sanitizer previously masked.
            data = assign_uuids_to_schema(data)

            # SCHEMA Day 3: hard validation BEFORE accepting the schema.
            # If errors found, save them for the next attempt's corrective
            # prompt and retry.
            from app.services.schema_validator import validate_schema
            validation = validate_schema(data, pdf_total_pages=pdf_total_pages)
            if not validation.is_valid:
                last_errors = validation.errors
                logger.warning(
                    "Schema attempt %s failed validation: %d errors, %d warnings",
                    attempt, validation.error_count, validation.warning_count,
                )
                # Trigger next attempt with corrective prompt
                raise ValueError(
                    f"validation failed: {validation.error_count} errors"
                )

            schema = BookSchema(**data)

            # Postpass — two passes:
            # Pass 1 (legacy): verify_schema_against_pdf_text — finds
            #   labels in PDF that Gemini missed. Logs warnings only.
            try:
                schema, _warnings = verify_schema_against_pdf_text(pdf_bytes, schema)
            except Exception as e:
                logger.warning("schema verifier failed (continuing): %s", e)

            # SCHEMA Day 5 — Pass 2: cross_check_section_pages.
            # For each section, verify title actually appears on claimed
            # page_start. If not, search PDF — if found exactly once
            # elsewhere, AUTO-CORRECT the page. If nowhere, flag as
            # phantom. Skipped for scanned PDFs.
            # Catches the "EXAMPLE 9.18 → page_start=9" subtle case
            # where the validator can't tell page=9 is wrong because
            # 9 IS a valid integer.
            try:
                from app.services.schema_postpass import (
                    cross_check_section_pages, apply_page_corrections,
                )
                cross_result = cross_check_section_pages(pdf_bytes, schema)
                if cross_result.skipped_no_text:
                    logger.info(
                        "schema cross-check: skipped (no pypdf text — scanned PDF)"
                    )
                else:
                    logger.info(
                        "schema cross-check: confirmed=%d corrections=%d "
                        "phantoms=%d skipped=%d",
                        cross_result.confirmed,
                        len(cross_result.corrections),
                        len(cross_result.phantoms),
                        cross_result.skipped_count,
                    )
                    if cross_result.corrections:
                        schema = apply_page_corrections(
                            schema, cross_result.corrections
                        )
                    if cross_result.phantoms:
                        for p in cross_result.phantoms:
                            logger.warning(
                                "schema cross-check: PHANTOM section '%s' "
                                "(claimed page=%s, not found in PDF text)",
                                p.section_title, p.claimed_page_start,
                            )
            except Exception as e:
                logger.warning("schema cross-check failed (continuing): %s", e)

            # SCHEMA Day 8 — Pass 3: cross_check_page_ends.
            # For each section in document order, verify its claimed
            # page_end against where the NEXT heading actually appears
            # in PDF text (via pypdf). Auto-correct (narrow only) when
            # Gemini over-reported page_end. Shared-boundary aware: if
            # next heading sits mid-page, this section's page_end can
            # legitimately equal that page. Skipped for scanned PDFs.
            try:
                from app.services.schema_postpass import (
                    cross_check_page_ends, apply_page_end_corrections,
                )
                end_corrections = cross_check_page_ends(pdf_bytes, schema)
                if end_corrections:
                    logger.info(
                        "schema page_end cross-check: %d correction(s)",
                        len(end_corrections),
                    )
                    schema = apply_page_end_corrections(schema, end_corrections)
                else:
                    logger.info("schema page_end cross-check: all clean")
            except Exception as e:
                logger.warning("schema page_end cross-check failed (continuing): %s", e)

            return schema
        except Exception as e:
            last_err = e
            logger.warning("Schema attempt %s failed: %s", attempt, e)

    raise ValueError(
        f"Schema generation failed after {MAX_ATTEMPTS} attempts: {last_err}"
    )
