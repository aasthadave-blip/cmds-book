"""Schema Generator — Gemini 2.5 Pro for native PDF understanding.

Uses the google-genai SDK (NOT the deprecated google-generativeai
package). Routes to schema_gemini.txt (single-column) or
schema_gemini_multicolumn.txt based on the upload-time is_multi_column
flag (autodetection lands in SCHEMA Week 1).

build_schema() is intentionally SYNCHRONOUS. The Celery worker thread
(extract.py:analyse_book_task) runs without an event loop, so async
calls cause "no current event loop" errors from google-genai's httpx
internals. We ensure a loop exists for the thread, then call Gemini
synchronously.

Retry behaviour (today, pre-Week-2):
- Up to MAX_ATTEMPTS (3) attempts
- Retries on ANY exception (Gemini error, JSON parse failure, pydantic
  validation failure, sanitizer crash) — same prompt each time
- _sanitize_schema patches Gemini output (silently drops malformed
  sections, coerces types, nulls invalid pages)

SCHEMA Week 1 status (in progress):
- ✅ Dead prompt files deleted
- ✅ schema_warnings + schema_quality_score columns added (this commit)
- ⏳ UUID at creation, /quality wiring, preflight, layout detector

SCHEMA Week 2+ will replace _sanitize_schema with a hard validator +
corrective retries. See SCHEMA_FINAL_PLAN.md.
"""

from __future__ import annotations

import asyncio
import logging

from app.schemas.analyser import BookSchema
from app.services.prompt_loader import load_raw
from app.services.schema_postpass import verify_schema_against_pdf_text
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
GEMINI_MODEL = "gemini-2.5-pro"


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


_VALID_TYPES = {"chapter", "section", "subsection", "excluded"}
_TYPE_MAP = {
    "subsubsection": "subsection",
    "topic": "subsection",
    "sub-section": "subsection",
    "sub_section": "subsection",
}


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


def _sanitize_schema(data) -> dict:
    """Normalize Gemini output to valid BookSchema fields.

    - Maps unknown section types (e.g. 'subsubsection') to valid Literal values
    - Bridges excluded_sections → exclusion_summary for UI compatibility

    Top-level shape recovery: Gemini occasionally returns a JSON array
    (e.g. ``[{...sections...}]``) at the root instead of the expected
    object. If we receive a list, try the first dict inside it as the
    real payload; otherwise return an empty schema shell so pydantic
    can surface a precise validation error rather than a Python
    AttributeError.
    """
    if not isinstance(data, dict):
        logger.warning(
            "schema sanitize: top-level payload is %s, not dict — attempting recovery",
            type(data).__name__,
        )
        if isinstance(data, list):
            inner = next((x for x in data if isinstance(x, dict)), None)
            if inner is not None:
                data = inner
            else:
                logger.warning(
                    "schema sanitize: no dict found inside top-level list — returning empty"
                )
                return {"sections": [], "excluded_sections": [], "exclusion_summary": []}
        else:
            return {"sections": [], "excluded_sections": [], "exclusion_summary": []}

    excluded_titles: list[str] = []

    excluded_raw = data.get("excluded_sections") or []
    if not isinstance(excluded_raw, list):
        logger.warning(
            "schema sanitize: excluded_sections is %s, not list — ignoring",
            type(excluded_raw).__name__,
        )
        excluded_raw = []
    for ex in excluded_raw:
        t = ex.get("title", "") if isinstance(ex, dict) else str(ex)
        if t and t not in excluded_titles:
            excluded_titles.append(t)

    def _fix_sections(sections) -> list:
        """Walk a sections list, normalizing in place AND filtering out
        any non-dict elements.

        Gemini occasionally emits malformed entries — a nested list, a
        bare string, or null — instead of a proper section object. This
        used to crash with ``'list' object has no attribute 'get'`` and
        kill all 3 schema-generation retries identically. Now: log and
        drop bad elements, keep the good ones, let pydantic validate
        the rest. Worst case is a partial schema (better than no
        schema at all)."""
        if not isinstance(sections, list):
            logger.warning(
                "schema sanitize: expected list of sections, got %s — treating as empty",
                type(sections).__name__,
            )
            return []
        cleaned: list[dict] = []
        for s in sections:
            if not isinstance(s, dict):
                logger.warning(
                    "schema sanitize: dropping non-dict section element of type %s: %r",
                    type(s).__name__, s,
                )
                continue
            raw_type = s.get("type") or ""
            if raw_type not in _VALID_TYPES:
                mapped = _TYPE_MAP.get(raw_type, "subsection")
                logger.debug("Normalizing section type %r → %r (id=%s)", raw_type, mapped, s.get("id"))
                s["type"] = mapped
            if s.get("type") == "excluded":
                t = s.get("title", "")
                if t and t not in excluded_titles:
                    excluded_titles.append(t)
            # Coerce page_start/page_end from string to int. Observed
            # in prod: Gemini occasionally emits question identifiers
            # like "9.18" / "9.22" / "9.26" as page values (confusion
            # between question_no and page_no fields). Pydantic
            # validation fails on these → entire schema attempt is
            # discarded → all 3 retries fail identically → user sees
            # "Schema Queued 0%" forever. Coerce safely:
            #   - int already → keep
            #   - string "N" or "N.M" → take int part if valid 1-9999
            #   - anything else → set to None (let pydantic accept
            #     null and we move on — better than crashing the
            #     entire schema)
            for _pf in ("page_start", "page_end"):
                _pv = s.get(_pf)
                if _pv is None:
                    continue
                if isinstance(_pv, int):
                    continue
                if isinstance(_pv, str):
                    _ps = _pv.strip()
                    # Clean integer string "5" → 5
                    try:
                        s[_pf] = int(_ps)
                        continue
                    except (TypeError, ValueError):
                        pass
                    # Values containing "." (like "9.18") are almost
                    # certainly question identifiers Gemini put in the
                    # wrong field. Do NOT coerce "9.18" → 9 — that would
                    # be a wrong page number masquerading as a real one
                    # and break downstream page-range overlap checks.
                    # Null it instead so the embedder falls back to
                    # anchor_text matching.
                    if "." in _ps:
                        logger.warning(
                            "schema sanitize: nulling question-like page "
                            "value %s=%r (id=%s)",
                            _pf, _pv, s.get("id"),
                        )
                        s[_pf] = None
                        continue
                # Anything else → null it so pydantic accepts
                logger.warning(
                    "schema sanitize: nulling unparseable page %s=%r (id=%s)",
                    _pf, _pv, s.get("id"),
                )
                s[_pf] = None

            # Remove "Mixed" content_types — a section is EITHER theory OR
            # questions, never both. If both are present, the section is
            # theory-bearing (its Cat A items are nested subsections with
            # their own ["questions"] content_types).
            ct = s.get("content_types") or []
            if isinstance(ct, list) and "theory" in ct and "questions" in ct:
                new_ct = [c for c in ct if c != "questions"]
                if "theory" not in new_ct:
                    new_ct.insert(0, "theory")
                logger.debug(
                    "Normalizing Mixed content_types %r → %r (id=%s)",
                    ct, new_ct, s.get("id"),
                )
                s["content_types"] = new_ct
            s["subsections"] = _fix_sections(s.get("subsections") or [])
            cleaned.append(s)
        return cleaned

    data["sections"] = _fix_sections(data.get("sections") or [])
    return {**data, "exclusion_summary": excluded_titles}


def _run_gemini_schema(pdf_bytes: bytes, schema_prompt: str) -> dict:
    """One synchronous Gemini call: upload PDF → generate schema → return dict.

    Real socket timeout via ``HttpOptions`` — see app.core.gemini_runtime.
    Schema generation runs over the full book PDF and is the slowest single
    call in the system; we give it 5 min instead of the default 150s.
    """
    from app.core.gemini_runtime import call_gemini_with_pdf

    raw = call_gemini_with_pdf(
        pdf_bytes=pdf_bytes,
        system_prompt=schema_prompt,
        user_prompt="",
        model=GEMINI_MODEL,
        timeout_s=300,
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


def build_schema(pdf_bytes: bytes, *, is_multi_column: bool = False) -> BookSchema:
    """Generate a structural schema from PDF bytes using Gemini 2.5 Pro.

    SYNCHRONOUS — call directly, do NOT wrap in asyncio.run().

    When ``is_multi_column`` is True (user-flagged at upload time for
    MHT-CET / JEE / NEET prep books with dense 2-column layouts), the
    multi-column-aware prompt is loaded. That prompt enforces per-column
    reading order and per-heading classification so dense MCQ + brief-
    explanation pages don't get mis-tagged as "all explanations" and
    silently dropped into excluded_sections. Single-column books use the
    default prompt and behave identically to before.
    """
    _ensure_event_loop()
    prompt_name = (
        "schema_gemini_multicolumn" if is_multi_column else "schema_gemini"
    )
    schema_prompt = load_raw(prompt_name)

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = _run_gemini_schema(pdf_bytes, schema_prompt)
            data = _sanitize_schema(data)
            # SCHEMA Week 1 Day 2 — assign canonical UUIDs BEFORE pydantic
            # validation so the BookSchema model carries them through.
            # First-analyse path: every section gets a fresh uuid.
            # Re-analyse path: caller will pass existing UUID map in a
            # future signature change (SCHEMA Week 3 Celery wiring).
            data = assign_uuids_to_schema(data)
            schema = BookSchema(**data)
            # Deterministic post-pass — VERIFIER ONLY (no injection).
            # Cross-checks pypdf-extracted labels against the Gemini schema
            # and logs any candidate misses as warnings. The schema is NEVER
            # mutated here. Gemini's strict OCR-ONLY Pass 3.5 is the single
            # source of truth.
            try:
                schema, _warnings = verify_schema_against_pdf_text(pdf_bytes, schema)
            except Exception as e:
                logger.warning("schema verifier failed (continuing): %s", e)
            return schema
        except Exception as e:
            last_err = e
            logger.warning("Schema attempt %s failed: %s", attempt, e)

    raise ValueError(
        f"Schema generation failed after {MAX_ATTEMPTS} attempts: {last_err}"
    )
