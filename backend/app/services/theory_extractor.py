"""P4 Theory Extractor — per-section Gemini OCR from PDF page slices.

Each section gets its own Gemini call:
  - Slice the PDF to page_start..page_end (from schema)
  - Upload the slice to Gemini File API
  - Gemini reads the pixels and transcribes verbatim (pure OCR)
  - Returns structured JSON blocks

No LLM reasoning about content — only OCR transcription.
Retries up to MAX_ATTEMPTS times on empty/failed extraction.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from dataclasses import dataclass

from app.schemas.qc import QCResult
from app.services.invariant_splitter import paragraphs_to_blocks
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
GEMINI_MODEL = "gemini-2.5-pro"


@dataclass
class ExtractionResult:
    section_id: str
    title: str
    blocks: list[dict]
    paragraphs: list[dict]
    qc: QCResult
    attempts: int
    local_qc_fail: bool = False
    raw_response: str = ""
    notes: str = ""


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


def _slice_pdf(pdf_bytes: bytes, page_start: int | None, page_end: int | None) -> bytes:
    """Return a PDF containing only pages page_start..page_end (1-indexed).

    Falls back to the full PDF if page numbers are missing or out of range.
    """
    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total = len(doc)

        # Convert 1-indexed schema page numbers to 0-indexed pymupdf
        p0 = max(0, (page_start or 1) - 1)
        p1 = min(total - 1, (page_end or total) - 1)

        if p0 > p1 or p0 >= total:
            doc.close()
            return pdf_bytes  # fallback: full PDF

        out = pymupdf.open()
        out.insert_pdf(doc, from_page=p0, to_page=p1)
        result = out.tobytes()
        out.close()
        doc.close()
        return result
    except Exception as e:
        logger.warning("PDF slice failed (pages %s-%s): %s — using full PDF", page_start, page_end, e)
        return pdf_bytes


def _build_user_prompt(section_id: str, title: str, next_title: str | None = None) -> str:
    stop_instruction = (
        f"\nSTOP extracting when you reach the heading \"{next_title}\" — do NOT include any content from that heading onwards."
        if next_title
        else ""
    )
    return (
        f"Extract ALL theory content from the section titled: \"{title}\" (ID: {section_id}).\n\n"
        f"START extracting from the heading \"{title}\" — include everything from that heading."
        f"{stop_instruction}\n\n"
        "These PDF pages may contain content from adjacent sections. "
        "Extract ONLY the content that belongs to this section.\n"
        "Transcribe EVERY word of theory content verbatim — pure OCR, no summarisation.\n"
        "Do NOT use training knowledge. Only transcribe what you see on the pages.\n\n"
        f"Return JSON with section_id=\"{section_id}\" and section_title=\"{title}\"."
    )


def _call_gemini_ocr_sync(
    pdf_slice: bytes,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Upload PDF slice to Gemini and get OCR JSON back. Synchronous."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_get_api_key())
    tmp_path = None
    uploaded_file = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_slice)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            uploaded_file = client.files.upload(
                file=f,
                config=types.UploadFileConfig(
                    mime_type="application/pdf",
                    display_name="section.pdf",
                ),
            )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type="application/pdf",
                ),
                system_prompt + "\n\n" + user_prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
                max_output_tokens=32000,
            ),
        )
        return response.text or ""

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


def _simple_qc(paragraphs: list[dict], section_id: str) -> QCResult:
    """Basic QC for OCR extraction: check we got non-empty blocks."""
    failures = []
    if not paragraphs:
        failures.append("No blocks extracted — empty OCR result")
        return QCResult(pass_=False, score=0.0, failures=failures)

    total_words = sum(
        len((p.get("content") or p.get("prob") or "").split())
        for p in paragraphs
    )
    if total_words < 10:
        failures.append(f"Extracted content too short ({total_words} words)")

    score = 1.0 if not failures else 0.0
    return QCResult(pass_=len(failures) == 0, score=score, failures=failures)


async def extract_section_with_qc(
    section_id: str,
    title: str,
    level: int,
    pdf_bytes: bytes,
    page_start: int | None,
    page_end: int | None,
    next_title: str | None = None,
) -> ExtractionResult:
    """Extract a single section via Gemini OCR; up to MAX_ATTEMPTS retries."""
    system_prompt = load_raw("extractor")
    user_prompt = _build_user_prompt(section_id, title, next_title)
    pdf_slice = _slice_pdf(pdf_bytes, page_start, page_end)

    last_paragraphs: list[dict] = []
    last_raw = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = await asyncio.to_thread(
                _call_gemini_ocr_sync,
                pdf_slice,
                system_prompt,
                user_prompt,
            )
            data = parse_json(raw)
            paragraphs = list(data.get("paragraphs") or [])
            notes = data.get("notes", "") or ""
            qc = _simple_qc(paragraphs, section_id)

            last_paragraphs = paragraphs
            last_raw = raw

            if qc.pass_:
                return ExtractionResult(
                    section_id=section_id,
                    title=title,
                    blocks=paragraphs_to_blocks(paragraphs),
                    paragraphs=paragraphs,
                    qc=qc,
                    attempts=attempt,
                    local_qc_fail=False,
                    raw_response=raw,
                    notes=notes,
                )

            logger.info("OCR QC failed (section=%s attempt=%s): %s", section_id, attempt, qc.failures)

        except Exception as e:
            logger.warning("OCR extraction failed (section=%s attempt=%s): %s", section_id, attempt, e)
            if attempt == MAX_ATTEMPTS:
                failed_qc = QCResult(pass_=False, score=0.0, failures=[f"OCR error: {e}"])
                return ExtractionResult(
                    section_id=section_id,
                    title=title,
                    blocks=[],
                    paragraphs=[],
                    qc=failed_qc,
                    attempts=attempt,
                    local_qc_fail=True,
                )

    # All attempts exhausted
    qc = _simple_qc(last_paragraphs, section_id)
    return ExtractionResult(
        section_id=section_id,
        title=title,
        blocks=paragraphs_to_blocks(last_paragraphs),
        paragraphs=last_paragraphs,
        qc=qc,
        attempts=MAX_ATTEMPTS,
        local_qc_fail=True,
        raw_response=last_raw,
    )


async def re_extract_with_fix(
    section_id: str,
    title: str,
    level: int,
    pdf_bytes: bytes,
    page_start: int | None,
    page_end: int | None,
    next_title: str | None = None,
) -> ExtractionResult:
    """User-triggered re-extraction — same as extract_section_with_qc, fresh attempt."""
    return await extract_section_with_qc(
        section_id=section_id,
        title=title,
        level=level,
        pdf_bytes=pdf_bytes,
        page_start=page_start,
        page_end=page_end,
        next_title=next_title,
    )
