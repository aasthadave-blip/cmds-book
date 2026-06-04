"""Final Draft API — Phase 3.2.

Endpoints (one active draft per book):

  GET    /api/books/{book_id}/final-draft
       Returns the current draft. Auto-seeds from Final Merge on first
       access. Idempotent — repeat calls return the same draft until you
       reseed.

  POST   /api/books/{book_id}/final-draft/reseed
       Discards user edits and seeds a fresh draft from the current
       Final Merge state (regen-preferred by default).

  PATCH  /api/books/{book_id}/final-draft
       Applies a batch of typed operations to the draft items. Body:
       {"operations": [{"op": "reorder", "id": "...", "after_id": "..."}, ...]}

  DELETE /api/books/{book_id}/final-draft
       Removes the draft entirely (next GET will seed again).

The composer auto-saves edits via PATCH. Multiple operations can be batched
in one request for instant-feedback drag/edit sessions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.book import Book
from app.models.final_draft import FinalDraft
from app.services.final_draft import (
    OperationError,
    apply_operation,
    seed_draft_items_from_merge,
)
from app.services.final_draft_export import (
    ExportError,
    build_draft_docx,
    build_draft_json,
    build_draft_markdown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/books", tags=["final-draft"])


def _draft_to_dict(draft: FinalDraft) -> dict[str, Any]:
    return {
        "id": str(draft.id),
        "book_id": str(draft.book_id),
        "status": draft.status,
        "prefer_regen": bool(draft.prefer_regen),
        "items": draft.items or [],
        "item_count": len(draft.items or []),
        "last_seeded_at": (
            draft.last_seeded_at.isoformat() if draft.last_seeded_at else None
        ),
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


async def _load_or_seed(
    session: AsyncSession,
    book_id: UUID,
    *,
    prefer_regen: bool = True,
) -> FinalDraft:
    """Fetch the draft; seed one if it doesn't exist yet."""
    # Verify book exists (clear 404 instead of FK error later)
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")

    existing = (
        await session.execute(
            select(FinalDraft).where(FinalDraft.book_id == book_id)
        )
    ).scalars().first()

    # Detect "stale" cached draft: figure_references have been
    # rewritten since the draft was last seeded (e.g. by the auto-heal
    # pass inside build_final_merge, or by a re-extract / restore-all
    # action). When stale, re-seed the items but PRESERVE the draft
    # row (so user edits like drag-drop reorder still work — those
    # update the draft separately).
    #
    # Detection: if any FigureReference for this book has created_at
    # newer than draft.last_seeded_at, the cached items are stale.
    if existing is not None and existing.last_seeded_at is not None:
        from app.models.figure_reference import FigureReference as _FR
        newest_ref = (
            await session.execute(
                select(_FR.created_at)
                .where(_FR.book_id == book_id)
                .order_by(_FR.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if newest_ref is not None and newest_ref > existing.last_seeded_at:
            # Re-seed items while keeping the row identity (so the
            # frontend's draft id stays stable). Only the items + the
            # last_seeded_at timestamp change. If the user had a
            # status="exported" already, we still re-seed — figure
            # changes should always reflect; user can re-export.
            #
            # Wrapped in try/except so a re-seed failure NEVER breaks
            # the GET — we serve the previously-cached items instead.
            # The next GET will try the re-seed again if data is still
            # stale. Mirrors the auto-heal failure handling in
            # build_final_merge.
            try:
                fresh_items = await seed_draft_items_from_merge(
                    session, book_id, prefer_regen=prefer_regen
                )
                existing.items = fresh_items
                existing.last_seeded_at = datetime.utcnow()
                existing.prefer_regen = prefer_regen
                await session.commit()
                await session.refresh(existing)
            except Exception as e:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "auto-reseed failed (book=%s, non-fatal): %s",
                    book_id, e,
                )
                # Roll back any partial commit and serve the cached
                # items unchanged.
                await session.rollback()
        return existing
    if existing is not None:
        return existing

    items = await seed_draft_items_from_merge(
        session, book_id, prefer_regen=prefer_regen
    )
    draft = FinalDraft(
        book_id=book_id,
        items=items,
        status="draft",
        prefer_regen=prefer_regen,
        last_seeded_at=datetime.utcnow(),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return draft


@router.get("/{book_id}/final-draft")
async def get_final_draft(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    draft = await _load_or_seed(session, book_id, prefer_regen=prefer_regen)
    return _draft_to_dict(draft)


@router.post("/{book_id}/final-draft/reseed", status_code=status.HTTP_200_OK)
async def reseed_final_draft(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Discard the user's edits and rebuild items from current Final
    Merge. Status flips back to 'draft'."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")

    items = await seed_draft_items_from_merge(
        session, book_id, prefer_regen=prefer_regen
    )
    existing = (
        await session.execute(
            select(FinalDraft).where(FinalDraft.book_id == book_id)
        )
    ).scalars().first()
    if existing is None:
        draft = FinalDraft(
            book_id=book_id,
            items=items,
            status="draft",
            prefer_regen=prefer_regen,
            last_seeded_at=datetime.utcnow(),
        )
        session.add(draft)
    else:
        existing.items = items
        existing.status = "draft"
        existing.prefer_regen = prefer_regen
        existing.last_seeded_at = datetime.utcnow()
        draft = existing
    await session.commit()
    await session.refresh(draft)
    return _draft_to_dict(draft)


@router.patch("/{book_id}/final-draft")
async def patch_final_draft(
    book_id: UUID,
    payload: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Apply a batch of operations to the draft. Operations are applied
    in order; the first one that errors aborts the whole batch."""
    operations = payload.get("operations") or []
    if not isinstance(operations, list):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="`operations` must be a list"
        )

    draft = await _load_or_seed(session, book_id)
    items = list(draft.items or [])
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"operation[{i}] must be an object",
            )
        try:
            items = apply_operation(items, op)
        except OperationError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"operation[{i}] {op.get('op')!r}: {e}",
            )

    draft.items = items
    # JSON columns: SQLAlchemy doesn't always auto-detect changes when
    # the assigned value structurally equals the old (or shares refs).
    # flag_modified guarantees the update is persisted.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(draft, "items")
    # Reset status if user edits after a previous export
    if draft.status == "exported":
        draft.status = "draft"
    await session.commit()
    await session.refresh(draft)
    return _draft_to_dict(draft)


def _safe_filename(s: str, suffix: str) -> str:
    base = re.sub(r"[^\w-]+", "_", s or "draft").strip("_") or "draft"
    return f"{base}_final-draft.{suffix}"


@router.get("/{book_id}/final-draft/export/json")
async def export_draft_json(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    draft = await _load_or_seed(session, book_id)
    book = await session.get(Book, book_id)
    title = (book.title if book else "") or ""
    data = await build_draft_json(draft, title)
    return Response(
        content=data,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(title, "json")}"'
            ),
        },
    )


@router.get("/{book_id}/final-draft/export/markdown")
async def export_draft_markdown(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    draft = await _load_or_seed(session, book_id)
    book = await session.get(Book, book_id)
    title = (book.title if book else "") or ""
    data = await build_draft_markdown(session, draft, title)
    return Response(
        content=data,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(title, "md")}"'
            ),
        },
    )


@router.get("/{book_id}/final-draft/export/docx")
async def export_draft_docx(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    draft = await _load_or_seed(session, book_id)
    book = await session.get(Book, book_id)
    title = (book.title if book else "") or ""
    try:
        data = await build_draft_docx(session, draft, title)
    except ExportError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        logger.exception("draft DOCX render failed for book %s", book_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DOCX render failed: {type(e).__name__}: {e}",
        )
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(title, "docx")}"'
            ),
        },
    )


@router.delete("/{book_id}/final-draft", status_code=status.HTTP_200_OK)
async def delete_final_draft(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    existing = (
        await session.execute(
            select(FinalDraft).where(FinalDraft.book_id == book_id)
        )
    ).scalars().first()
    if existing is None:
        return {"deleted": False, "reason": "no draft existed"}
    await session.delete(existing)
    await session.commit()
    return {"deleted": True}
