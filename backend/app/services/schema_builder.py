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
import os
import tempfile

from app.schemas.analyser import BookSchema
from app.services.prompt_loader import load_raw
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


def _get_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        try:
            from app.core.config import settings
            api_key = settings.GEMINI_API_KEY
        except Exception:
            pass
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in .env")
    return api_key


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
    """One synchronous Gemini call: upload PDF → generate schema → return dict."""
    from google import genai
    from google.genai import types

    api_key = _get_api_key()
    client = genai.Client(api_key=api_key)

    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            uploaded_file = client.files.upload(
                file=f,
                config=types.UploadFileConfig(
                    mime_type="application/pdf",
                    display_name="textbook_chapter.pdf",
                ),
            )
        logger.info("Gemini file uploaded: %s", uploaded_file.name)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type="application/pdf",
                ),
                schema_prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=16000,
            ),
        )

        return parse_json(response.text)

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


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
            return BookSchema(**data)
        except Exception as e:
            last_err = e
            logger.warning("Schema attempt %s failed: %s", attempt, e)

    raise ValueError(
        f"Schema generation failed after {MAX_ATTEMPTS} attempts: {last_err}"
    )
