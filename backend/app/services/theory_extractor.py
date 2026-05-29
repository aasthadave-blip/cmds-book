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
import logging
from dataclasses import dataclass

from app.schemas.qc import QCResult
from app.services.invariant_splitter import paragraphs_to_blocks
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Theory extractor model.
#
# LOCAL TEST (current): gemini-2.5-flash on the v2 extractor prompt.
# v2 prompt was designed for Flash-compatibility (explicit pattern-match
# rules, negative examples, self-check, section-boundary rule at top).
# REVERT to "gemini-2.5-pro" if quality regresses.
GEMINI_MODEL = "gemini-2.5-pro"

# Sub-retry policy for transient infra errors only (network blips, Gemini 5xx,
# read timeouts). These DO NOT count against MAX_ATTEMPTS and do not change
# anything about the extraction itself — same prompt, same slice, same model,
# same output. They only stop a transient network error from being mistaken
# for a content failure and burning a real QC attempt.
TRANSIENT_SUBSTRINGS = (
    "Server disconnected",
    "RemoteProtocolError",
    "ReadTimeout",
    "ReadError",
    "ConnectionError",
    "ConnectError",
    "ConnectTimeout",
    "503",
    "502",
    "504",
    "Connection reset",
    "Temporary failure",
)
TRANSIENT_SUB_ATTEMPTS = 4  # initial + 3 retries
TRANSIENT_BACKOFF_S = (5.0, 15.0, 45.0)


def _is_transient(err: Exception) -> bool:
    msg = f"{type(err).__name__}: {err}"
    return any(s in msg for s in TRANSIENT_SUBSTRINGS)


async def _call_gemini_with_transient_retries(
    pdf_slice: bytes, system_prompt: str, user_prompt: str, section_id: str
) -> str:
    """Retry the SAME Gemini call on transient infra errors only.

    Non-transient errors (auth, 4xx, schema) bubble immediately so we don't
    waste time on something that can't recover. Output is identical to a
    direct call — this only changes resilience, not behaviour.
    """
    last_err: Exception | None = None
    for sub in range(TRANSIENT_SUB_ATTEMPTS):
        try:
            return await asyncio.to_thread(
                _call_gemini_ocr_sync, pdf_slice, system_prompt, user_prompt
            )
        except Exception as e:
            if not _is_transient(e):
                raise
            last_err = e
            if sub == TRANSIENT_SUB_ATTEMPTS - 1:
                break
            wait = TRANSIENT_BACKOFF_S[min(sub, len(TRANSIENT_BACKOFF_S) - 1)]
            logger.warning(
                "Gemini transient error (section=%s sub-attempt=%s/%s wait=%ss): %s",
                section_id, sub + 1, TRANSIENT_SUB_ATTEMPTS, wait, e,
            )
            await asyncio.sleep(wait)
    assert last_err is not None
    raise last_err


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
    if next_title:
        stop_instruction = (
            f"\nSTOP extracting the MOMENT you reach the heading \"{next_title}\". Anything below that heading — even a single line, even a single equation — belongs to a different section and must NOT appear in your output.\n"
            f"DO NOT stop earlier than \"{next_title}\". Continue transcribing every paragraph, every line, every callout box, every figure caption that appears BETWEEN \"{title}\" and \"{next_title}\". Even if the content feels 'complete' or 'wraps up', KEEP GOING until you literally see \"{next_title}\" on the page.\n"
            f"If the section's content continues onto the next page, KEEP TRANSCRIBING on the next page until you reach \"{next_title}\". Do NOT assume a page break means the section ended.\n"
            f"If you are uncertain whether a paragraph belongs to \"{title}\" or to the next section, EXCLUDE it. Over-including a paragraph means the next section's extraction will be incomplete and the user will see the same content under two sidebar entries. Under-include rather than over-include — the system can recover from a missed paragraph (retry this one section), but it cannot recover from a section eating its neighbour's content."
        )
    else:
        stop_instruction = (
            "\nThis is the last section of its scope. Transcribe everything from the heading to the end of the provided pages."
        )
    return (
        f"Extract ALL theory content from the section titled: \"{title}\" (ID: {section_id}).\n\n"
        f"START extracting from the heading \"{title}\" — include everything from that heading."
        f"{stop_instruction}\n\n"
        "These PDF pages may contain content from adjacent sections. "
        "Extract ONLY the content that belongs to this section (between START heading and STOP heading).\n"
        "Transcribe EVERY word of theory content verbatim — pure OCR, no summarisation, no skipping.\n"
        "Do NOT use training knowledge. Only transcribe what you see on the pages.\n"
        "Do NOT decide the section is 'complete' on your own — completeness is determined ONLY by reaching the STOP heading.\n\n"
        f"Return JSON with section_id=\"{section_id}\" and section_title=\"{title}\"."
    )


def _call_gemini_ocr_sync(
    pdf_slice: bytes,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Upload PDF slice to Gemini and get OCR JSON back.

    Real socket timeout via ``HttpOptions`` — see app.core.gemini_runtime.
    """
    from app.core.gemini_runtime import call_gemini_with_pdf

    return call_gemini_with_pdf(
        pdf_bytes=pdf_slice,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=GEMINI_MODEL,
        max_output_tokens=32000,
        temperature=0.0,
        display_name="section.pdf",
    )


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
            raw = await _call_gemini_with_transient_retries(
                pdf_slice, system_prompt, user_prompt, section_id
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
