"""P2 Schema Generator — uses Gemini 2.5 Pro for native PDF understanding.

Uses the new google-genai SDK (google.genai), not the deprecated
google-generativeai package.

build_schema() is intentionally SYNCHRONOUS. The worker (extract.py) runs
in a plain daemon thread with no event loop, so async/asyncio.run() causes
"no current event loop" errors from google-genai's internals.

We ensure an event loop exists for the thread (google-genai needs one for
its httpx internals), then call Gemini synchronously.

Retries up to MAX_ATTEMPTS times on JSON parse error.
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


def _sanitize_schema(data: dict) -> dict:
    """Normalize Gemini output to valid BookSchema fields.

    - Maps unknown section types (e.g. 'subsubsection') to valid Literal values
    - Bridges excluded_sections → exclusion_summary for UI compatibility
    """
    excluded_titles: list[str] = []

    for ex in data.get("excluded_sections") or []:
        t = ex.get("title", "") if isinstance(ex, dict) else str(ex)
        if t and t not in excluded_titles:
            excluded_titles.append(t)

    def _fix_sections(sections: list[dict]) -> None:
        for s in sections:
            raw_type = s.get("type") or ""
            if raw_type not in _VALID_TYPES:
                mapped = _TYPE_MAP.get(raw_type, "subsection")
                logger.debug("Normalizing section type %r → %r (id=%s)", raw_type, mapped, s.get("id"))
                s["type"] = mapped
            if s.get("type") == "excluded":
                t = s.get("title", "")
                if t and t not in excluded_titles:
                    excluded_titles.append(t)
            _fix_sections(s.get("subsections") or [])

    _fix_sections(data.get("sections") or [])
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
        # temperature=0.0 → greedy decoding for maximum cross-run
        # consistency. Eliminates most schema variance between Analyse
        # runs on the same PDF (e.g. local vs prod producing different
        # wrappers or different heading splits). Same understanding,
        # same speed, same cost — just locks token selection to the
        # most probable path.
        temperature=0.0,
        display_name="textbook_chapter.pdf",
    )
    return parse_json(raw)


def build_schema(pdf_bytes: bytes) -> BookSchema:
    """Generate a structural schema from PDF bytes using Gemini 2.5 Pro.

    SYNCHRONOUS — call directly, do NOT wrap in asyncio.run().
    """
    _ensure_event_loop()
    schema_prompt = load_raw("schema_gemini")

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = _run_gemini_schema(pdf_bytes, schema_prompt)
            data = _sanitize_schema(data)
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
