"""Sections router — list per book, get, re-extract."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.job import Job
from app.models.section import Section
from app.schemas.book import BookUploadResponse
from app.schemas.section import SectionOut

router = APIRouter(tags=["sections"])


@router.get("/api/books/{book_id}/sections", response_model=list[SectionOut])
async def list_sections(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[SectionOut]:
    result = await session.execute(
        select(Section).where(Section.book_id == book_id).order_by(Section.section_id)
    )
    return [SectionOut.model_validate(s) for s in result.scalars().all()]


@router.get("/api/sections/{section_id}", response_model=SectionOut)
async def get_section(
    section_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SectionOut:
    sec = await session.get(Section, section_id)
    if sec is None:
        raise HTTPException(404, detail="Section not found")
    return SectionOut.model_validate(sec)


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
