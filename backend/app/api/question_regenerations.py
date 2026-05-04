"""Question regenerations router.

POST   /api/question-banks/{bank_id}/regenerate                 — start a regen run
GET    /api/books/{book_id}/question-regenerations              — list runs for a book
GET    /api/question-regenerations/{regen_id}                   — fetch one run
GET    /api/question-regenerations/{regen_id}/questions         — list regen questions grouped by section
POST   /api/question-regenerations/{regen_id}/save              — mark run as saved
DELETE /api/question-regenerations/{regen_id}                   — delete the run + its questions
DELETE /api/question-regenerations/{regen_id}/questions         — bulk-delete questions inside a run
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import extraction_limit
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.question_regeneration import QuestionRegeneration

books_router = APIRouter(prefix="/api/books", tags=["question-regenerations"])
banks_router = APIRouter(prefix="/api/question-banks", tags=["question-regenerations"])
regens_router = APIRouter(prefix="/api/question-regenerations", tags=["question-regenerations"])


class RegenerateRequest(BaseModel):
    scope: str = Field(default="bank", pattern="^(bank|sections)$")
    section_refs: list[str] | None = None
    custom_instructions: str | None = None
    source_regen_id: UUID | None = None
    label: str | None = None


def _regen_dict(r: QuestionRegeneration, question_count: int = 0) -> dict:
    return {
        "id": str(r.id),
        "bank_id": str(r.bank_id),
        "book_id": str(r.book_id),
        "source_regen_id": str(r.source_regen_id) if r.source_regen_id else None,
        "label": r.label,
        "scope": r.scope,
        "section_refs": list(r.section_refs or []),
        "custom_instructions": r.custom_instructions,
        "status": r.status,
        "job_id": str(r.job_id) if r.job_id else None,
        "question_count": question_count,
        "stats": r.extraction_stats,
        "last_error": r.last_error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def _question_dict(q: Question) -> dict:
    return {
        "id": str(q.id),
        "regen_id": str(q.regen_id) if q.regen_id else None,
        "section_ref": q.section_ref,
        "section_title": q.section_title,
        "page_start": q.page_start,
        "page_end": q.page_end,
        "raw_text": q.raw_text,
        "status": q.status,
        "excluded_block_ref": q.excluded_block_ref,
        "excluded_block_index": q.excluded_block_index,
        "link_method": q.link_method,
        "link_confidence": q.link_confidence,
        "question_number": q.question_number,
        "exercise_ref": q.exercise_ref,
        "chapter_ref": q.chapter_ref,
        "sub_part": q.sub_part,
        "question_type": q.question_type,
        "has_options": q.has_options,
        "solution_text": q.solution_text,
        "has_solution": q.has_solution,
        "kind": q.kind or "exercise",
    }


@banks_router.post(
    "/{bank_id}/regenerate",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(extraction_limit)],
)
async def start_regeneration(
    bank_id: UUID,
    payload: RegenerateRequest = Body(default_factory=RegenerateRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="Bank not found")
    book = await session.get(Book, bank.book_id)
    if book is None or not book.pdf_url or not book.schema:
        raise HTTPException(400, detail="Book is not analysed/uploaded")

    if payload.scope == "sections" and not payload.section_refs:
        raise HTTPException(400, detail="section_refs required when scope='sections'")

    job = Job(book_id=book.id, type="extract_questions_regen", status="queued", progress=0)
    session.add(job)
    await session.flush()

    regen = QuestionRegeneration(
        bank_id=bank.id,
        book_id=book.id,
        source_regen_id=payload.source_regen_id,
        label=payload.label,
        scope=payload.scope,
        section_refs=payload.section_refs,
        custom_instructions=(payload.custom_instructions or None),
        status="pending",
        job_id=job.id,
    )
    session.add(regen)
    await session.commit()

    import app.workers.questions_v2  # noqa: F401
    from app.workers.runner import dispatch

    dispatch("extract_questions_regen", str(regen.id), str(job.id))

    return {
        "regen_id": str(regen.id),
        "job_id": str(job.id),
        "status": "extracting",
    }


@books_router.get("/{book_id}/question-regenerations")
async def list_regenerations(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            select(QuestionRegeneration)
            .where(QuestionRegeneration.book_id == book_id)
            .order_by(QuestionRegeneration.created_at.desc())
        )
    ).scalars().all()

    counts_rows = await session.execute(
        select(Question.regen_id, func.count(Question.id))
        .where(Question.book_id == book_id)
        .where(Question.regen_id.is_not(None))
        .group_by(Question.regen_id)
    )
    counts = {rid: n for rid, n in counts_rows.all()}

    return [_regen_dict(r, counts.get(r.id, 0)) for r in rows]


@regens_router.get("/{regen_id}")
async def get_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    count_row = await session.execute(
        select(func.count(Question.id)).where(Question.regen_id == regen_id)
    )
    return _regen_dict(r, int(count_row.scalar() or 0))


@regens_router.get("/{regen_id}/questions")
async def list_regen_questions(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")

    rows = (
        await session.execute(
            select(Question)
            .where(Question.regen_id == regen_id)
            .order_by(
                Question.section_ref.nulls_last(),
                Question.page_start.nulls_last(),
                Question.id,
            )
        )
    ).scalars().all()

    grouped: dict[str, dict[str, Any]] = {}
    for q in rows:
        key = q.section_ref or "_unsectioned"
        bucket = grouped.setdefault(
            key,
            {
                "section_ref": q.section_ref,
                "section_title": q.section_title,
                "questions": [],
            },
        )
        bucket["questions"].append(_question_dict(q))

    return {
        "regen": _regen_dict(r, len(rows)),
        "sections": list(grouped.values()),
    }


@regens_router.post("/{regen_id}/save")
async def save_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    if r.status != "ready":
        raise HTTPException(400, detail=f"Cannot save regen with status={r.status}")
    r.status = "saved"
    await session.commit()
    return _regen_dict(r)


@regens_router.delete("/{regen_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        return None
    await session.delete(r)
    await session.commit()
    return None


class BulkDeleteRequest(BaseModel):
    question_ids: list[UUID]


@regens_router.delete("/{regen_id}/questions", status_code=status.HTTP_200_OK)
async def bulk_delete_regen_questions(
    regen_id: UUID,
    payload: BulkDeleteRequest = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    if not payload.question_ids:
        return {"deleted": 0}
    rows = (
        await session.execute(
            select(Question)
            .where(Question.regen_id == regen_id)
            .where(Question.id.in_(payload.question_ids))
        )
    ).scalars().all()
    for q in rows:
        await session.delete(q)
    await session.commit()
    return {"deleted": len(rows)}


