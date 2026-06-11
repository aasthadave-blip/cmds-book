"""Sections router — list per book, get, re-extract."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.job import Job
from app.models.section import Section
from app.schemas.book import BookUploadResponse
from app.schemas.section import SectionOut

router = APIRouter(tags=["sections"])


async def _load_embedded_figures(
    session: AsyncSession,
    book_id: UUID,
) -> dict[str, list[dict[str, Any]]]:
    """Build a {section_ref: [figure_dict, ...]} map for theory-context
    figure_references on this book. Each figure_dict carries the data
    the frontend needs to render the figure inline.

    Variant is chosen per Q1 rule: regen if approved_at IS NOT NULL,
    else original.
    """
    # Join figure_references → figures, theory context only,
    # excluding hidden + unattached (those live in a separate tray).
    refs = (
        await session.execute(
            select(FigureReference)
            .where(FigureReference.book_id == book_id)
            .where(FigureReference.context == "theory")
            .where(FigureReference.is_hidden.is_(False))
            .where(FigureReference.placement_kind != "unattached")
        )
    ).scalars().all()
    if not refs:
        return {}

    fig_ids = {r.figure_id for r in refs}
    figs = (
        await session.execute(
            select(Figure).where(Figure.id.in_(fig_ids))
        )
    ).scalars().all()
    fig_by_id = {f.id: f for f in figs}

    out: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        f = fig_by_id.get(r.figure_id)
        if f is None:
            continue
        variant = "regen" if (f.regen_image_bytes and f.approved_at) else "original"
        out.setdefault(r.section_ref, []).append({
            "ref_id": str(r.id),               # needed for hide/unhide
            "figure_id": str(f.id),
            "label": f.figure_number or r.placeholder_text or "",
            "caption": f.caption or "",
            "variant": variant,
            "image_url": f"/api/figures/{f.id}/image?variant=auto",
            "placement_kind": r.placement_kind or "appended",
            "placement_block_idx": r.placement_block_idx,
        })
    # Order each section's figures by placement_block_idx (None → end)
    for k, lst in out.items():
        lst.sort(key=lambda d: (
            d.get("placement_block_idx") if d.get("placement_block_idx") is not None else 10**9
        ))
    return out


@router.get("/api/books/{book_id}/sections", response_model=list[SectionOut])
async def list_sections(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[SectionOut]:
    """Return the book's sections ordered by the schema's hierarchical
    sequence (pre-order tree walk) — NOT lexicographic section_id.

    Lexicographic sort breaks for any chapter with 2-digit subsection
    numbers (e.g. "8.10" sorts before "8.2"). The Preview/Composer/
    export pipelines already walk the schema tree directly via
    final_merge.py + books.py:_get_export_data, so they show the
    correct order. Until this fix, the sidebar (which calls this
    endpoint) was the only surface using lexicographic order — that's
    why users saw jumbled section ordering in the sidebar while
    Preview rendered correctly.

    Fallback: if the book has no schema or the schema parse fails,
    fall back to lexicographic order so old/broken books still load.
    """
    from app.models.book import Book
    from app.schemas.analyser import BookSchema
    from app.services.chunk_builder import flatten_sections as _flatten

    result = await session.execute(
        select(Section).where(Section.book_id == book_id)
    )
    all_sections = list(result.scalars().all())
    secs_by_id = {s.section_id: s for s in all_sections}
    # UUID-keyed map — canonical identity (CONTRACT.md §1).
    # Schema sections carry `uuid` per SchemaSection.uuid; Section rows
    # use `id` (UUID PK). When both align, lookup is drift-proof.
    secs_by_uuid = {str(s.id): s for s in all_sections}
    embedded_by_section = await _load_embedded_figures(session, book_id)

    # Build the canonical schema order. Same helper used by books.py
    # export ordering — keep the two paths consistent.
    # Lookup priority: UUID (canonical) → slug (legacy).
    # For new books (post-UUID migration), UUID matches → correct order.
    # For legacy books with drifted slugs AND no matching UUID, the section
    # falls through to the lexicographic fallback at the bottom (broken
    # order — a pre-existing data issue, not fixed here).
    ordered_sections: list[Section] = []
    seen_section_pks: set = set()
    book = await session.get(Book, book_id)
    if book is not None and book.schema:
        try:
            schema_obj = BookSchema(**book.schema)
            for ss in _flatten(schema_obj):
                matched: Section | None = None
                # 1. UUID (canonical, drift-proof)
                if ss.uuid and ss.uuid in secs_by_uuid:
                    matched = secs_by_uuid[ss.uuid]
                # 2. Slug (works when slugs happen to align)
                elif ss.id in secs_by_id:
                    matched = secs_by_id[ss.id]
                if matched is None or matched.id in seen_section_pks:
                    continue
                ordered_sections.append(matched)
                seen_section_pks.add(matched.id)
        except Exception:
            ordered_sections = []
            seen_section_pks = set()

    # Build the output list in schema order, then append any DB-only
    # sections (defensive — orphans that aren't in the schema but exist
    # in the sections table) at the end in lexicographic order so they
    # remain visible to the user / editor.
    out: list[SectionOut] = []
    for s in ordered_sections:
        d = SectionOut.model_validate(s)
        d.embedded_figures = embedded_by_section.get(s.section_id, [])
        out.append(d)
    for sid in sorted(secs_by_id):
        s = secs_by_id[sid]
        if s.id in seen_section_pks:
            continue
        d = SectionOut.model_validate(s)
        d.embedded_figures = embedded_by_section.get(s.section_id, [])
        out.append(d)
    return out


@router.get("/api/sections/{section_id}", response_model=SectionOut)
async def get_section(
    section_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SectionOut:
    sec = await session.get(Section, section_id)
    if sec is None:
        raise HTTPException(404, detail="Section not found")
    out = SectionOut.model_validate(sec)
    # Phase 1 figure embedder — populate inline figures for this section
    embedded_by_section = await _load_embedded_figures(session, sec.book_id)
    out.embedded_figures = embedded_by_section.get(sec.section_id, [])
    return out


@router.post("/api/sections/{section_id}/re-extract", response_model=BookUploadResponse)
async def re_extract_section(
    section_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    sec = await session.get(Section, section_id)
    if sec is None:
        raise HTTPException(404, detail="Section not found")

    job = Job(book_id=sec.book_id, type="re_extract", status="queued", progress=0)
    session.add(job)
    await session.flush()

    # Commit before dispatch so worker thread sees the new Job row.
    await session.commit()

    import app.workers.extract  # noqa: F401
    from app.workers.runner import dispatch

    dispatch("re_extract_section", str(sec.id), str(job.id))
    return BookUploadResponse(book_id=sec.book_id, job_id=job.id, status="re_extracting")
