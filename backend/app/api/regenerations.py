"""Regenerations router — kick off regen and inspect results."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.book import Book
from app.models.job import Job
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.book import BookUploadResponse
from app.schemas.regen import RegenerationOut, RegenParams

router = APIRouter(tags=["regenerations"])


@router.get("/api/recap-rules")
async def list_recap_rules() -> list[dict[str, Any]]:
    """Return the catalog of theory-regen recap rules.

    Frontend uses this to render opt-in checkboxes in the theory regen
    config. Activating any rule requires THEORY_REGEN_PROMPT_VERSION=v3
    on the backend; with v1 the recap_rule_ids field is accepted but
    ignored (the v1 prompt has no recap placeholders).
    """
    from app.services.recap_config import RECAP_RULES

    return [
        {
            "id": r["id"],
            "label": r["label"],
            "mode": r["mode"],
            "embed_as": r["embed_as"],
            "description": r["description"],
        }
        for r in RECAP_RULES
    ]


@router.post("/api/books/{book_id}/regenerate", response_model=BookUploadResponse)
async def regenerate_book(
    book_id: UUID,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Kick off a regeneration.

    Body shape:
        {...RegenParams fields..., "section_ids": ["1.1", "1.2"] | null}

    If ``section_ids`` is omitted or null, every leaf section of the book
    is regenerated. If provided, only those sections are regenerated — the
    rest are left absent from ``blocks_by_section``.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    # Split body: RegenParams fields + optional section_ids
    section_ids_raw = body.pop("section_ids", None)
    try:
        params = RegenParams(**body)
    except Exception as e:
        raise HTTPException(422, detail=f"Invalid regen params: {e}") from e

    section_ids: list[str] | None = None
    if section_ids_raw is not None:
        if not isinstance(section_ids_raw, list) or not all(
            isinstance(s, str) for s in section_ids_raw
        ):
            raise HTTPException(422, detail="section_ids must be a list of strings")
        # Empty list = nothing selected → reject (avoids creating empty regen)
        if not section_ids_raw:
            raise HTTPException(400, detail="section_ids is empty — select at least one section")
        section_ids = list(section_ids_raw)

    # Stash the section selection on the regen row so startup recovery
    # after a backend crash can restart with the same scope.
    params_payload = params.model_dump()
    if section_ids is not None:
        params_payload["_section_ids"] = list(section_ids)

    # CARRY-FORWARD — seed the new regen row with the prior regen's
    # blocks_by_section / qc_drift, so sections the user previously
    # regenerated stay visible in the Final / Composer view even when
    # this run's scope only covers a subset of sections. The worker
    # then MERGES this run's regenerated sections into the seed.
    prior = (
        await session.execute(
            select(Regeneration)
            .where(Regeneration.book_id == book.id)
            .order_by(desc(Regeneration.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    seed_blocks = dict(prior.blocks_by_section or {}) if prior else {}
    seed_qc = dict(prior.qc_drift or {}) if prior and prior.qc_drift else {}

    regen = Regeneration(
        book_id=book.id,
        params=params_payload,
        blocks_by_section=seed_blocks,
        qc_drift=seed_qc or None,
    )
    session.add(regen)
    await session.flush()

    job = Job(book_id=book.id, type="regen", status="queued", progress=0)
    session.add(job)
    await session.flush()

    # Commit BEFORE dispatch. In inline mode the worker thread starts
    # immediately and runs with a separate sync session — it can't see
    # uncommitted data. Without this commit, regen_row lookup in the
    # worker returns None and blocks_by_section stays empty ({}).
    await session.commit()

    import app.workers.extract  # noqa: F401
    from app.workers.runner import dispatch

    dispatch(
        "regenerate_book",
        str(book.id),
        str(job.id),
        str(regen.id),
        params.model_dump(),  # pure RegenParams — no _section_ids leak into the worker's RegenParams(**)
        section_ids,
    )

    return BookUploadResponse(book_id=book.id, job_id=job.id, regen_id=regen.id, status="regenerating")


@router.get("/api/books/{book_id}/regenerations", response_model=list[RegenerationOut])
async def list_regenerations(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[RegenerationOut]:
    """List all regenerations for a book, newest first."""
    result = await session.execute(
        select(Regeneration)
        .where(Regeneration.book_id == book_id)
        .order_by(desc(Regeneration.created_at))
    )
    return [RegenerationOut.model_validate(r) for r in result.scalars().all()]


@router.get("/api/regenerations/{regen_id}", response_model=RegenerationOut)
async def get_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> RegenerationOut:
    regen = await session.get(Regeneration, regen_id)
    if regen is None:
        raise HTTPException(404, detail="Regeneration not found")
    return RegenerationOut.model_validate(regen)


@router.post("/api/regenerations/{regen_id}/sections/{section_id}/rerun")
async def rerun_section(
    regen_id: UUID,
    section_id: str,
    body: dict[str, Any] = Body(default={}),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run regeneration for a single section with optional custom instructions."""
    regen = await session.get(Regeneration, regen_id)
    if regen is None:
        raise HTTPException(404, detail="Regeneration not found")

    # Find the section by section_id string (e.g. "1.1")
    result = await session.execute(
        select(Section).where(
            Section.book_id == regen.book_id,
            Section.section_id == section_id,
        )
    )
    sec = result.scalar_one_or_none()
    if sec is None:
        raise HTTPException(404, detail=f"Section {section_id!r} not found")

    # Build params: inherit from original regen, override custom_instructions
    base_params = dict(regen.params or {})
    custom = body.get("custom_instructions", "")
    if custom:
        base_params["custom_instructions"] = custom
    params = RegenParams(**base_params)

    from app.services.regenerator import regenerate_section
    new_blocks = await regenerate_section(
        section_id=sec.section_id,
        section_title=sec.title,
        blocks=list(sec.blocks or []),
        params=params,
    )

    # Patch blocks_by_section in-place
    updated = dict(regen.blocks_by_section or {})
    updated[sec.section_id] = new_blocks
    regen.blocks_by_section = updated
    await session.flush()

    return {"section_id": section_id, "blocks": new_blocks}


@router.post("/api/regenerations/{regen_id}/save", response_model=dict)
async def save_regeneration(
    regen_id: UUID,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Save only confirmed sections to the regeneration record.

    Removes skipped sections from blocks_by_section so the Regenerated
    folder in the sidebar only shows confirmed content. Original sections
    are never modified.
    """
    regen = await session.get(Regeneration, regen_id)
    if regen is None:
        raise HTTPException(404, detail="Regeneration not found")

    confirmed_ids: list[str] = body.get("confirmed_section_ids", [])
    if not confirmed_ids:
        raise HTTPException(400, detail="No confirmed section IDs provided")

    current = dict(regen.blocks_by_section or {})
    # Keep only confirmed sections
    saved = {sid: blocks for sid, blocks in current.items() if sid in confirmed_ids}
    regen.blocks_by_section = saved
    await session.flush()

    return {"saved": True, "sections_saved": len(saved)}
