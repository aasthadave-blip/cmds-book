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
    folder_id: UUID | None = Form(None),
    subject: str | None = Form(None),
    # User-set flag at upload time for multi-column PDFs (MHT-CET, JEE
    # prep, dense question banks). When True, the analyse worker routes
    # to the multi-column-aware schema prompt that enforces per-column
    # reading order + per-heading classification so dense MCQ pages
    # don't get mis-tagged as "all explanations" and silently dropped.
    # Stored in book.analyser JSON (no migration needed). Default False
    # → single-column behaviour unchanged.
    is_multi_column: bool = Form(False),
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
        folder_id=folder_id,
        subject=subject,
        # Stash the multi-column flag in analyser JSON so analyse_book_task
        # can read it before generating the schema. analyser is otherwise
        # populated by the worker with AnalyserResult fields; we pre-seed
        # this one field, and the worker preserves it on overwrite.
        analyser={"is_multi_column": True} if is_multi_column else None,
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
    # Self-heal stuck books — if the worker died mid-flight and left
    # book.status="schema_ready" but every theory-bearing section actually
    # finished (passed/failed), promote the book to "ready" so the UI
    # unblocks. Idempotent: only flips schema_ready → ready, never the
    # other direction.
    if book.status == "schema_ready":
        from sqlalchemy import func, select as _select
        from app.models import Section
        counts = (
            await session.execute(
                _select(Section.status, func.count(Section.id))
                .where(Section.book_id == book_id)
                .group_by(Section.status)
            )
        ).all()
        by_status = {s: int(n) for s, n in counts}
        total = sum(by_status.values())
        terminal = by_status.get("passed", 0) + by_status.get("failed", 0)
        # All sections in a terminal state and at least one passed → ready.
        if total > 0 and terminal == total and by_status.get("passed", 0) > 0:
            book.status = "ready"
            await session.commit()
            await session.refresh(book)
    return BookOut.from_orm_book(book)


@router.get("/{book_id}/quality")
async def get_book_quality(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Phase 5c (CONTRACT.md §3 — Verification Contract).

    Read-only quality report — what does the book actually contain
    vs what the schema promised? Use to detect "ready with empty
    content" lies, missing sections, unattached figures.

    Does NOT mutate. Safe to call repeatedly. Caches nothing.

    Response shape: see app/services/verify_book.py docstring.
    """
    from app.services.verify_book import verify_book
    return await verify_book(session, book_id)


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_id: UUID, session: AsyncSession = Depends(get_session)) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    # Phase 7 (CONTRACT.md §5 — Atomicity & Concurrency): refuse to delete
    # a book that has an in-flight Celery task. The zombie-task bug we hit
    # today (15-min ObjectDeletedError grind) was caused by deleting a
    # book while extract_book was running — the task kept hitting deleted
    # row commits.
    #
    # 409 Conflict tells the user explicitly: "this book has running work
    # — wait for it to finish or cancel the job first". This is preferable
    # to silently letting the worker spin uselessly until it errors out.
    in_flight = (await session.execute(
        select(Job).where(
            Job.book_id == book_id,
            Job.status.in_(["queued", "running"]),
        ).limit(1)
    )).scalars().first()
    if in_flight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Book has an in-flight task (job_id={in_flight.id}, "
                f"status={in_flight.status!r}). Wait for it to finish or "
                "cancel it before deleting."
            ),
        )
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

    # Phase 7 (CONTRACT.md §5): refuse to start a second analyse while
    # one is already in flight. Today's prod chaos came from triggering
    # /analyse 3 times during the OOM window — three workers fought over
    # the same book.schema field, last writer won, state went incoherent.
    # 409 Conflict is the user-visible signal "we already heard you".
    in_flight = (await session.execute(
        select(Job).where(
            Job.book_id == book_id,
            Job.status.in_(["queued", "running"]),
        ).limit(1)
    )).scalars().first()
    if in_flight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Already analysing (job_id={in_flight.id}). Wait for it "
                "to finish or cancel it before re-triggering."
            ),
        )

    job = Job(book_id=book.id, type="analyse", status="queued", progress=0)
    session.add(job)
    await session.flush()

    book.status = "analysing"
    # Phase 5d/7: reset stage statuses on a fresh analyse cycle so the
    # watchdog and /quality see this as a new run, not a stale one.
    book.schema_status = "pending"
    book.theory_status = "pending"
    book.questions_status = "pending"
    book.figures_status = "pending"

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

    # Lock previously-extracted section_ids — schema edits must NEVER
    # mint a new ID for a section that already has DB content. The
    # alignment helper matches each node by (title + page range) against
    # existing DB Section rows and rewrites the node.id back to the
    # extraction-time id. Drift is impossible by construction after
    # this step; figure_references + questions joins stay valid.
    from app.services.schema_alignment import (
        align_schema_ids_to_existing_sections,
    )
    existing = (
        await session.execute(
            select(Section).where(Section.book_id == book.id)
        )
    ).scalars().all()
    validated, _remap = align_schema_ids_to_existing_sections(
        validated, existing
    )

    book.schema = validated.model_dump()
    book.title = validated.document_title or book.title
    book.subject = validated.subject or book.subject
    await session.flush()
    # Auto-relink theory chips so that manual schema edits (drag-drop in
    # the editor that moves an Example/Exercise to a different parent)
    # take effect on the theory page WITHOUT requiring a re-extract.
    # Non-fatal: if linker fails, the schema PATCH still succeeds.
    try:
        from app.services.example_linker import link_examples_to_theory
        await link_examples_to_theory(session, book.id)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "post-patch chip relink failed (book=%s): %s", book.id, e,
        )
    # Re-run figure embedder too — schema edits can move a figure's
    # parent section, so placement metadata needs to be recomputed.
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, book.id)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "post-patch figure embedder failed (book=%s): %s", book.id, e,
        )
    # Ensure all attributes are loaded inside the async context — otherwise
    # Pydantic's from_attributes=True serialization in `from_orm_book` /
    # response_model can trigger lazy IO during the response phase →
    # sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called.
    await session.refresh(book)
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
    """Approve the schema and kick off extraction via the orchestrator.

    ORCH Day 8 — was: directly dispatched extract_book and created a
    Job for it; now: dispatches the post-schema coordinator, which
    decides what to run next based on current book state. The
    coordinator is idempotent — if analyse_book already fired it
    (Day 3), this second call either no-ops (theory already running)
    or advances the state machine. Race-free either way.

    Backward-compat: frontend still receives {book_id, job_id, status}
    so polling continues to work. The Job row is logged as
    "succeeded" because the approval action itself is just a routing
    decision — the actual extraction Jobs are created by the
    coordinator per worker it dispatches.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")

    # Approval marker Job — completes immediately. The actual extraction
    # Jobs are created by the orchestrator's dispatcher functions.
    job = Job(
        book_id=book.id,
        type="extract",
        status="succeeded",
        progress=100,
        message="Approval routed to orchestrator",
    )
    session.add(job)
    await session.flush()

    book.status = "extracting"
    await session.commit()

    import app.workers.orchestrator  # noqa: F401 — ensure inline registration
    from app.workers.runner import dispatch

    # Idempotent — coordinator's lock + state machine handle the case
    # where analyse_book_task already dispatched the coordinator on
    # schema completion.
    dispatch("coordinate_extraction", str(book.id))
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

    # Phase 7 (CONTRACT.md §5): refuse if another extract is in flight.
    in_flight = (await session.execute(
        select(Job).where(
            Job.book_id == book_id,
            Job.status.in_(["queued", "running"]),
        ).limit(1)
    )).scalars().first()
    if in_flight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Book has an in-flight task (job_id={in_flight.id}, "
                f"status={in_flight.status!r}). Wait for it to finish or "
                "cancel before re-extracting."
            ),
        )

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
    # Phase 5d/7: reset per-stage status for the new extract cycle
    book.theory_status = "pending"
    book.questions_status = "pending"
    book.figures_status = "pending"

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
