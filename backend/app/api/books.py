"""Books router — upload, list, get, delete, analyse, schema patch, approve, export."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import extraction_limit
from app.core.storage import upload_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.analyser import BookSchema
from app.schemas.book import BookOut, BookUploadResponse

router = APIRouter(prefix="/api/books", tags=["books"])


# ─────────────────────────────────────────────────────────────────────
# Shared markdown builder (used by /export/markdown and /export/docx)
# ─────────────────────────────────────────────────────────────────────

_LEADING_NUM_RE = re.compile(r"^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+")
_HEADING_NORM_RE = re.compile(r"\s+")


def _strip_leading_number(s: str) -> str:
    """Strip baked-in ``1. ``/``2) ``/``(3) `` prefixes from list items."""
    return _LEADING_NUM_RE.sub("", s).strip()


def _norm_heading(s: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for heading compare."""
    s = _HEADING_NORM_RE.sub(" ", (s or "").strip().lower())
    return s.strip(" .:;—-")


def _build_markdown(
    book: Book,
    sections: list[Section],
    regen_blocks: dict[str, list],
    *,
    numbered_lists: bool = False,
) -> str:
    """Render extraction/regen blocks as Markdown.

    numbered_lists=True emits ``1. ``/``2. `` items so pandoc → docx produces
    a native numbered list. numbered_lists=False keeps the plain ``-`` bullets
    used by the existing .md download.
    """
    lines: list[str] = [f"# {book.title}", ""]
    for sec in sections:
        level = sec.level or 2
        hashes = "#" * min(level + 1, 6)
        lines.append(f"{hashes} {sec.section_id} {sec.title}")
        lines.append("")
        section_title_norm = _norm_heading(sec.title or "")
        blocks_to_render = regen_blocks.get(sec.section_id) if regen_blocks else None
        for block in (blocks_to_render if blocks_to_render is not None else sec.blocks or []):
            t = block.get("t")
            if t == "p":
                lines.append(block.get("c", ""))
                lines.append("")
            elif t == "h3":
                h_text = block.get("c", "")
                # Skip h3 blocks that simply repeat the section title — the
                # parent section heading already renders that text.
                if section_title_norm and _norm_heading(h_text) == section_title_norm:
                    continue
                lines.append(f"### {h_text}")
                lines.append("")
            elif t == "eq":
                lines.append("$$")
                lines.append(block.get("c", ""))
                lines.append("$$")
                lines.append("")
            elif t == "def":
                lines.append(f"**{block.get('term', 'Definition')}:** {block.get('c', '')}")
                lines.append("")
            elif t == "kp":
                lines.append(f"> **Key Point:** {block.get('c', '')}")
                lines.append("")
            elif t == "fig":
                lines.append(f"*[Figure: {block.get('c', '')}]*")
                lines.append("")
            elif t == "list":
                items = block.get("items", []) or []
                if numbered_lists:
                    for idx, item in enumerate(items, 1):
                        lines.append(f"{idx}. {_strip_leading_number(str(item))}")
                else:
                    for item in items:
                        lines.append(f"- {item}")
                lines.append("")
            elif t == "table":
                caption = block.get("caption", "")
                if caption:
                    lines.append(f"*{caption}*")
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                if headers:
                    lines.append("| " + " | ".join(headers) + " |")
                    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for row in rows:
                    lines.append("| " + " | ".join(str(c) for c in row) + " |")
                lines.append("")
            elif t == "example":
                lines.append(f"**Example — {block.get('label', '')}:** {block.get('prob', '')}")
                for eq in block.get("eqs", []):
                    lines.append(f"$$\n{eq}\n$$")
                lines.append("")
    return "\n".join(lines)


def _ensure_pandoc_on_path() -> str:
    """Locate the pandoc binary for pypandoc. Returns the resolved path."""
    found = shutil.which("pandoc")
    if found:
        return found
    # Common install locations that may not be on the launchd PATH
    candidates = [
        Path.home() / ".local/bin/pandoc",
        Path("/opt/homebrew/bin/pandoc"),
        Path("/usr/local/bin/pandoc"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            os.environ["PATH"] = f"{c.parent}:{os.environ.get('PATH', '')}"
            os.environ.setdefault("PYPANDOC_PANDOC", str(c))
            return str(c)
    raise HTTPException(
        500,
        detail="Pandoc binary not found. Install pandoc or set PYPANDOC_PANDOC.",
    )


@router.post("", response_model=BookUploadResponse, status_code=status.HTTP_201_CREATED)
async def create_book(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    # Accept by content-type OR by .pdf extension (browsers sometimes send
    # application/octet-stream when dragging from certain folders).
    filename = (file.filename or "").lower()
    is_pdf = (
        file.content_type in ("application/pdf", "application/x-pdf")
        or filename.endswith(".pdf")
    )
    if not is_pdf:
        raise HTTPException(
            400,
            detail=f"Expected a PDF file (got content-type {file.content_type!r}, filename {file.filename!r}).",
        )

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, detail="Empty file")

    pdf_key = upload_pdf(pdf_bytes, file.filename or "document.pdf")

    book = Book(
        title=title or (file.filename or "Untitled").rsplit(".", 1)[0],
        pdf_url=pdf_key,
        status="uploaded",
    )
    session.add(book)
    await session.flush()

    return BookUploadResponse(book_id=book.id, job_id=None, status="uploaded")


@router.get("", response_model=list[BookOut])
async def list_books(session: AsyncSession = Depends(get_session)) -> list[BookOut]:
    result = await session.execute(select(Book).order_by(Book.created_at.desc()))
    return [BookOut.from_orm_book(b) for b in result.scalars().all()]


@router.get("/{book_id}", response_model=BookOut)
async def get_book(book_id: UUID, session: AsyncSession = Depends(get_session)) -> BookOut:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    return BookOut.from_orm_book(book)


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_id: UUID, session: AsyncSession = Depends(get_session)) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    await session.delete(book)


@router.post(
    "/{book_id}/analyse",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def analyse_book(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.pdf_url:
        raise HTTPException(400, detail="Book has no associated PDF")

    job = Job(book_id=book.id, type="analyse", status="queued", progress=0)
    session.add(job)
    await session.flush()

    book.status = "analysing"

    # Commit before dispatch so worker thread sees the new Job row.
    await session.commit()

    # Dispatch via the runner (inline or Celery based on settings)
    import app.workers.extract  # noqa: F401 — ensure registrations run
    from app.workers.runner import dispatch

    dispatch("analyse_book", str(book.id), str(job.id))

    return BookUploadResponse(book_id=book.id, job_id=job.id, status="analysing")


@router.patch("/{book_id}/schema", response_model=BookOut)
async def patch_schema(
    book_id: UUID,
    schema: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    """User-edited schema — validates against BookSchema then persists."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    try:
        validated = BookSchema(**schema)
    except Exception as e:
        raise HTTPException(400, detail=f"Invalid schema: {e}") from e
    book.schema = validated.model_dump()
    book.title = validated.document_title or book.title
    book.subject = validated.subject or book.subject
    await session.flush()
    return BookOut.from_orm_book(book)


@router.post(
    "/{book_id}/approve",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def approve_schema(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Approve the schema and kick off the full extraction pipeline."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")

    job = Job(book_id=book.id, type="extract", status="queued", progress=0)
    session.add(job)
    await session.flush()

    book.status = "extracting"

    # Commit before dispatch so worker thread sees the new Job row.
    await session.commit()

    import app.workers.extract  # noqa: F401
    from app.workers.runner import dispatch

    dispatch("extract_book", str(book.id), str(job.id))
    return BookUploadResponse(book_id=book.id, job_id=job.id, status="extracting")


@router.post(
    "/{book_id}/re-extract",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def re_extract_book(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Re-run full extraction on a book regardless of current status.

    Resets all section statuses to pending so the extraction loop
    processes every section fresh with the current extraction logic.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")

    # Reset all existing sections to pending so they get re-extracted
    from sqlalchemy import update
    from app.models.section import Section
    await session.execute(
        update(Section)
        .where(Section.book_id == book_id)
        .values(status="pending", blocks=[], attempts=0, qc_local=None)
    )

    job = Job(book_id=book.id, type="extract", status="queued", progress=0)
    session.add(job)
    await session.flush()

    book.status = "extracting"

    # Commit before dispatch so worker thread sees the new Job row.
    await session.commit()

    import app.workers.extract  # noqa: F401
    from app.workers.runner import dispatch

    dispatch("extract_book", str(book.id), str(job.id))
    return BookUploadResponse(book_id=book.id, job_id=job.id, status="extracting")


async def _load_export_context(
    book_id: UUID,
    regen_id: UUID | None,
    session: AsyncSession,
) -> tuple[Book, list[Section], dict[str, list]]:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    regen_blocks: dict[str, list] = {}
    if regen_id is not None:
        regen = await session.get(Regeneration, regen_id)
        if regen and regen.book_id == book_id:
            regen_blocks = dict(regen.blocks_by_section or {})

    result = await session.execute(
        select(Section).where(Section.book_id == book_id)
    )
    sections_by_id = {s.section_id: s for s in result.scalars().all()}

    # Order sections by the schema's hierarchical sequence (pre-order walk),
    # not lexicographic section_id — otherwise "8.10" would sort before "8.2".
    # Also: skip container sections that have non-excluded subsections — their
    # content is fully represented by the child sections, including them would
    # duplicate every paragraph in the export.
    ordered: list[Section] = []
    if book.schema:
        try:
            from app.services.chunk_builder import flatten_sections as _flatten
            schema_obj = BookSchema(**book.schema)
            seen: set[str] = set()
            skipped_containers: set[str] = set()
            for ss in _flatten(schema_obj):
                has_live_children = any(
                    c.type != "excluded" for c in (ss.subsections or [])
                )
                if has_live_children:
                    skipped_containers.add(ss.id)
                    continue  # children will carry this section's content
                sec = sections_by_id.get(ss.id)
                if sec is not None and ss.id not in seen:
                    ordered.append(sec)
                    seen.add(ss.id)
            # Append any DB-only sections (defensive) at the end — but NOT the
            # containers we deliberately skipped above.
            for sid, sec in sections_by_id.items():
                if sid not in seen and sid not in skipped_containers:
                    ordered.append(sec)
        except Exception:
            ordered = list(sections_by_id.values())
    else:
        ordered = list(sections_by_id.values())

    # When exporting regenerated: only include sections that have regen blocks
    if regen_blocks:
        ordered = [s for s in ordered if s.section_id in regen_blocks]
    return book, ordered, regen_blocks


@router.get("/{book_id}/export/markdown")
async def export_book_markdown(
    book_id: UUID,
    regen_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export sections as Markdown. Pass regen_id to export regenerated content."""
    book, sections, regen_blocks = await _load_export_context(book_id, regen_id, session)
    content = _build_markdown(book, sections, regen_blocks, numbered_lists=False)
    safe_name = re.sub(r"[^\w-]+", "_", book.title).strip("_") or "extraction"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
    )


@router.get("/{book_id}/export/docx")
async def export_book_docx(
    book_id: UUID,
    regen_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export theory sections as a Word (.docx) document using the native
    python-docx builder (`app.services.docx_export`). Replaces the
    pandoc pipeline so we control fonts, spacing, no-duplicate-heading
    invariant, and inline math/figure rendering precisely."""
    from app.services.docx_export import build_theory_docx

    book, sections, regen_blocks = await _load_export_context(book_id, regen_id, session)

    # Shape adapter: ORM Section rows + regen overrides → builder shape.
    payload: list[dict] = []
    for sec in sections:
        blocks = regen_blocks.get(sec.section_id) if regen_blocks else None
        if blocks is None:
            blocks = sec.blocks or []
        payload.append({
            "section_id": sec.section_id,
            "title": sec.title,
            "blocks": blocks,
        })

    data = build_theory_docx(book.title or "Theory Export", payload)
    safe_name = re.sub(r"[^\w-]+", "_", book.title or "extraction").strip("_") or "extraction"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.docx"'},
    )


@router.get("/{book_id}/export/json")
async def export_book_json(
    book_id: UUID,
    regen_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export sections as JSON. Pass regen_id to export regenerated content."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    regen_blocks: dict[str, list] = {}
    if regen_id is not None:
        regen = await session.get(Regeneration, regen_id)
        if regen and regen.book_id == book_id:
            regen_blocks = dict(regen.blocks_by_section or {})

    result = await session.execute(
        select(Section)
        .where(Section.book_id == book_id)
        .order_by(Section.section_id)
    )
    sections = result.scalars().all()

    # When exporting regenerated: only include sections that have regen blocks
    if regen_blocks:
        sections = [s for s in sections if s.section_id in regen_blocks]

    payload = {
        "title": book.title,
        "subject": book.subject,
        "schema": book.schema,
        "content_type": "regenerated" if regen_blocks else "original",
        "sections": [
            {
                "section_id": sec.section_id,
                "title": sec.title,
                "level": sec.level,
                "status": sec.status,
                "blocks": regen_blocks.get(sec.section_id, sec.blocks or []),
            }
            for sec in sections
        ],
    }

    safe_name = re.sub(r"[^\w-]+", "_", book.title).strip("_") or "extraction"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )
