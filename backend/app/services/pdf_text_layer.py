"""Add an OCR text layer to scanned PDFs so the deterministic page-anchor
(``schema_page_anchor``) can locate section headings.

WHY THIS EXISTS
---------------
Gemini's self-reported page numbers are unreliable on scanned PDFs — it can
map every section of a 22-page chapter onto a single summary page (observed
on the Complex Numbers book: all four sections got page_start=page_end=22).
``schema_page_anchor`` fixes this by LOCATING each heading in real per-page
text — but it needs a text layer, which scanned PDFs lack, so it was silently
skipped for every scanned book (and every book in this system is scanned).

This module gives scanned PDFs a text layer via ``ocrmypdf`` (Tesseract)
WITHOUT altering the page images (``--skip-text``, ``--optimize 0``), so
Gemini-vision content extraction is completely unaffected — only an invisible
text layer is added, used solely to anchor page ranges.

CONTRACT
--------
``ensure_text_layer`` is FULLY FAIL-SAFE: if the PDF is already digital, or
ocrmypdf is unavailable, or OCR errors for any reason, it returns the ORIGINAL
bytes unchanged with ``ocr_applied=False``. It never raises and never blocks
ingest. Worst case = today's behaviour (Gemini pages, unanchored).
"""

from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# ocrmypdf shells out to tesseract + ghostscript. On macOS (Homebrew) these
# live in /opt/homebrew/bin, which the launchd-managed backend's PATH omits;
# in the prod container they're in /usr/bin (apt). Prepend both so the
# subprocess can always find them, regardless of how the worker was launched.
_OCR_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")


def _ensure_ocr_path() -> None:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    missing = [d for d in _OCR_BIN_DIRS if d not in parts]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + parts)


def ensure_text_layer(pdf_bytes: bytes) -> tuple[bytes, bool]:
    """Return ``(pdf_bytes, ocr_applied)``.

    Digital PDF (already has a usable text layer) -> input unchanged,
    ``ocr_applied=False``. Scanned PDF -> ocrmypdf adds an invisible text
    layer (images preserved) and returns the OCR'd bytes, ``ocr_applied=True``.
    Any failure -> original bytes, ``ocr_applied=False`` (never raises).
    """
    # 1) Detect scanned vs digital (reuse the existing pymupdf probe).
    try:
        from app.core.claude_agent import _pdf_has_substantive_text

        has_text, chars = _pdf_has_substantive_text(pdf_bytes)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("text-layer: scanned-detection failed (%s) — skipping OCR", e)
        return pdf_bytes, False

    if has_text:
        logger.info("text-layer: digital PDF (%d chars) — no OCR needed", chars)
        return pdf_bytes, False

    # 2) Scanned -> add a text layer via ocrmypdf (images untouched).
    try:
        import ocrmypdf
    except Exception as e:
        logger.warning("text-layer: ocrmypdf unavailable (%s) — keeping original", e)
        return pdf_bytes, False

    _ensure_ocr_path()
    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fin:
            fin.write(pdf_bytes)
            in_path = fin.name
        out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
        os.close(out_fd)

        ocrmypdf.ocr(
            in_path,
            out_path,
            skip_text=True,   # only OCR pages lacking text; idempotent
            optimize=0,       # never recompress/downsample the page images
            force_ocr=False,
            progress_bar=False,
            language="eng",
        )
        with open(out_path, "rb") as f:
            ocr_bytes = f.read()
        logger.info(
            "text-layer: OCR text layer added (scanned PDF, %d -> %d bytes)",
            len(pdf_bytes),
            len(ocr_bytes),
        )
        return ocr_bytes, True
    except Exception as e:
        logger.warning("text-layer: ocrmypdf failed (%s) — keeping original PDF", e)
        return pdf_bytes, False
    finally:
        for p in (in_path, out_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
