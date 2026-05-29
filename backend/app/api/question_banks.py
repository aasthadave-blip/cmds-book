"""Question banks router.

Endpoints:
  POST   /api/books/{book_id}/question-banks                       — create + trigger extraction
  GET    /api/books/{book_id}/question-banks                       — list banks for a book
  GET    /api/question-banks/{bank_id}                             — get bank + question count + stats
  DELETE /api/question-banks/{bank_id}                             — delete bank + questions
  GET    /api/question-banks/{bank_id}/questions                   — list questions grouped by section
  POST   /api/question-banks/{bank_id}/blocks/{block_idx}/re-extract — retry a single excluded block
  GET    /api/question-banks/{bank_id}/export/json                 — download JSON
  GET    /api/question-banks/{bank_id}/export/markdown             — download Markdown
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import extraction_limit
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.rejected_question import RejectedQuestion
from app.services.questions.linking import SchemaIndex, resolve_block_link

books_router = APIRouter(prefix="/api/books", tags=["question-banks"])
banks_router = APIRouter(prefix="/api/question-banks", tags=["question-banks"])


def _maybe_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _normalise_stats(stats: dict | None) -> dict | None:
    """Read-side fix for legacy banks where `expected_total` / `missed`
    were inflated by non-question blocks (Crossword / Activity / Try-It).

    The worker's new write path already excludes these (see
    `_is_intentional_non_question_block` in questions_v3.py), but existing
    rows still carry the old totals. We re-derive on read so the UI
    reads consistent numbers without forcing a re-extract.
    """
    if not stats:
        return stats
    try:
        from app.workers.questions_v3 import _is_intentional_non_question_block
    except Exception:
        return stats

    sections = list(stats.get("sections") or [])
    if not sections:
        return stats

    bogus_expected = 0
    bogus_extracted = 0
    bogus_missed = 0
    # Per-status correction — subtract Crossword/Activity-style blocks
    # from whichever status they were counted under (usually "partial"
    # because extracted=0 < expected=14).
    bogus_status: dict[str, int] = {}
    for s in sections:
        if _is_intentional_non_question_block(s.get("section_title")):
            bogus_expected += int(s.get("expected") or 0)
            bogus_extracted += int(s.get("extracted") or 0)
            st = s.get("status")
            if st:
                bogus_status[st] = bogus_status.get(st, 0) + 1

    blocks = list(stats.get("blocks") or [])
    fixed_blocks = []
    for b in blocks:
        if _is_intentional_non_question_block(b.get("title")):
            m = int(b.get("missed") or 0)
            bogus_missed += m
            b = {**b, "missed": 0}
        fixed_blocks.append(b)

    out = dict(stats)
    out["blocks"] = fixed_blocks
    totals = dict(out.get("totals") or {})
    if bogus_expected and "expected_total" in totals:
        totals["expected_total"] = max(0, int(totals["expected_total"]) - bogus_expected)
    if bogus_extracted and "extracted_total" in totals:
        totals["extracted_total"] = max(0, int(totals["extracted_total"]) - bogus_extracted)
    for st, n in bogus_status.items():
        if st in totals:
            totals[st] = max(0, int(totals[st] or 0) - n)
    out["totals"] = totals
    if "missed" in out:
        out["missed"] = max(0, int(out["missed"] or 0) - bogus_missed)
    return out


def _bank_dict(bank: QuestionBank, question_count: int = 0) -> dict:
    return {
        "id": str(bank.id),
        "book_id": str(bank.book_id),
        "title": bank.title,
        "subject": bank.subject,
        "status": bank.status,
        "question_count": question_count,
        "stats": _normalise_stats(bank.extraction_stats),
        "last_error": bank.last_error,
        "created_at": bank.created_at.isoformat() if bank.created_at else None,
        "updated_at": bank.updated_at.isoformat() if bank.updated_at else None,
    }


def _question_dict(q: Question) -> dict:
    # Phase 4 — surface the multimodal regen verdict if present so the
    # frontend can show a "⚠ figure needs regen" hint per regen variant.
    image_regen_hint = None
    if isinstance(q.qc_local, dict):
        ir = q.qc_local.get("image_regen")
        if isinstance(ir, dict) and ir.get("needed"):
            image_regen_hint = {
                "needed": True,
                "reason": ir.get("reason") or "",
            }
    return {
        "id": str(q.id),
        "section_ref": q.section_ref,
        "section_title": q.section_title,
        "page_start": q.page_start,
        "page_end": q.page_end,
        "raw_text": q.raw_text,
        "status": q.status,
        # Phase 1 linking context
        "excluded_block_ref": q.excluded_block_ref,
        "excluded_block_index": q.excluded_block_index,
        "link_method": q.link_method,
        "link_confidence": q.link_confidence,
        # Stage 2 OCR metadata
        "question_number": q.question_number,
        "exercise_ref": q.exercise_ref,
        "chapter_ref": q.chapter_ref,
        "sub_part": q.sub_part,
        "question_type": q.question_type,
        "has_options": q.has_options,
        "solution_text": q.solution_text,
        "has_solution": q.has_solution,
        "kind": q.kind or "exercise",
        "is_hidden": bool(q.is_hidden),
        # Phase 4 — present only when regen LLM flagged image_needs_regen=true
        "image_regen_hint": image_regen_hint,
    }


def _rejected_dict(r: RejectedQuestion) -> dict:
    return {
        "id": str(r.id),
        "section_ref": r.section_ref,
        "section_title": r.section_title,
        "page_start": r.page_start,
        "page_end": r.page_end,
        "raw_text": r.raw_text,
        "reject_reason": r.reject_reason,
        "payload": r.payload,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@books_router.get("/{book_id}/question-structure")
async def get_question_structure(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Read-only view of the book's schema with ``excluded_sections`` attached
    to their page-resolved parent theory sections.

    Pure computation — no writes, no extraction triggered. Used by the
    Questions sidebar to render the interleaved tree.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    schema = book.schema or {}

    sections = list(schema.get("sections") or [])
    excluded = list(schema.get("excluded_sections") or [])
    index = SchemaIndex.from_schema(schema)

    # Count of questions already persisted (from the current basic extractor),
    # grouped by section_ref for badges. Zero rows is fine.
    count_rows = await session.execute(
        select(Question.section_ref, func.count(Question.id))
        .where(Question.book_id == book_id)
        .group_by(Question.section_ref)
    )
    questions_by_section = {sid: int(n) for sid, n in count_rows.all() if sid}

    # Resolve each excluded_section to a parent section via the cascade.
    excluded_attachments: dict[str, list[dict]] = {}  # parent_id -> list of blocks
    unlinked: list[dict] = []

    for idx, ex in enumerate(excluded):
        if not isinstance(ex, dict):
            continue
        title = str(ex.get("title") or "")
        p_start = _maybe_int(ex.get("page_start"))
        p_end = _maybe_int(ex.get("page_end"))
        link = resolve_block_link(
            title=title,
            page_start=p_start,
            page_end=p_end,
            index=index,
        )
        entry = {
            "title": title,
            "page_start": p_start,
            "page_end": p_end,
            "reason": ex.get("reason") or "",
            "excluded_index": idx,
            "excluded_block_ref": title or f"excluded_{idx}",
            "link_method": link.method,
            "link_confidence": link.confidence,
            "section_ref": link.section_ref,
        }
        if link.section_ref and link.section_ref in index.id_set:
            excluded_attachments.setdefault(link.section_ref, []).append(entry)
        else:
            unlinked.append(entry)

    # Build the tree: walk original sections, attach resolved excluded blocks.
    def walk(arr: list) -> list[dict]:
        out: list[dict] = []
        for s in arr or []:
            if not isinstance(s, dict):
                continue
            if s.get("type") == "excluded":
                # Defensive: ignore any "excluded" nodes the analyser may have
                # embedded inside the tree. Those are surfaced via the flat
                # excluded_sections array instead.
                continue
            sid = str(s.get("id") or "").strip()
            node: dict = {
                "id": sid,
                "title": str(s.get("title") or ""),
                "level": int(s.get("level") or 0),
                "type": s.get("type") or "section",
                "page_start": _maybe_int(s.get("page_start")),
                "page_end": _maybe_int(s.get("page_end")),
                "question_count": questions_by_section.get(sid, 0),
                "excluded_blocks": excluded_attachments.get(sid, []),
                "subsections": walk(s.get("subsections") or []),
            }
            out.append(node)
        return out

    tree = walk(sections)

    # Summary
    summary = {
        "total_sections": len(index.sections),
        "total_excluded": len(excluded),
        "linked_excluded": len(excluded) - len(unlinked),
        "unlinked_excluded": len(unlinked),
    }

    return {
        "book_id": str(book.id),
        "document_title": schema.get("document_title") or book.title,
        "sections": tree,
        "unlinked_excluded": unlinked,
        "summary": summary,
    }


@books_router.post(
    "/{book_id}/question-banks",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(extraction_limit)],
)
async def create_question_bank(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")
    if not book.pdf_url:
        raise HTTPException(400, detail="Book has no PDF")

    # Mark any prior pending/extracting banks for this book as failed — they
    # are orphans from earlier retries that never finished. Prevents pileup.
    await session.execute(
        update(QuestionBank)
        .where(QuestionBank.book_id == book.id)
        .where(QuestionBank.status.in_(["pending", "extracting"]))
        .values(status="failed", last_error="Superseded by new extraction request")
    )

    bank = QuestionBank(
        book_id=book.id,
        title=book.title,
        subject=book.subject,
        status="pending",
    )
    session.add(bank)
    await session.flush()

    from app.core.config import settings as _s
    worker_version = (_s.QUESTION_WORKER_VERSION or "v3").lower()
    task_name = (
        "extract_questions_v3" if worker_version == "v3" else "extract_questions_v2"
    )

    job = Job(
        book_id=book.id, type=task_name, status="queued", progress=0
    )
    session.add(job)
    await session.flush()

    await session.commit()

    if worker_version == "v3":
        import app.workers.questions_v3  # noqa: F401 — ensure task registration
        from app.workers.runner import dispatch
        # v3 takes (book_id, bank_id, job_id) — different signature from v2.
        dispatch(task_name, str(book.id), str(bank.id), str(job.id))
    else:
        import app.workers.questions_v2  # noqa: F401
        from app.workers.runner import dispatch
        dispatch(task_name, str(book.id), str(job.id))

    return {
        "bank_id": str(bank.id),
        "job_id": str(job.id),
        "status": "extracting",
    }


@books_router.get("/{book_id}/question-banks")
async def list_question_banks(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    result = await session.execute(
        select(QuestionBank)
        .where(QuestionBank.book_id == book_id)
        .order_by(QuestionBank.created_at.desc())
    )
    banks = result.scalars().all()

    # count per bank
    count_rows = await session.execute(
        select(Question.bank_id, func.count(Question.id))
        .where(Question.book_id == book_id)
        .group_by(Question.bank_id)
    )
    counts = {bid: n for bid, n in count_rows.all()}

    return [_bank_dict(b, counts.get(b.id, 0)) for b in banks]


@banks_router.get("/{bank_id}")
async def get_question_bank(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")
    # Original-only count — exclude regen variants so the bank header's
    # "N/M extracted" number reflects extraction output, not regenerated
    # variants that live in the ✨ Regenerated folder.
    count_row = await session.execute(
        select(func.count(Question.id))
        .where(Question.bank_id == bank_id)
        .where(Question.regen_id.is_(None))
    )
    count = count_row.scalar() or 0

    out = _bank_dict(bank, count)

    # Attach the most recent running / queued extraction job so the UI can
    # poll its live message + progress for the heartbeat display.
    if bank.status in ("pending", "extracting"):
        job_row = (
            await session.execute(
                select(Job)
                .where(Job.book_id == bank.book_id)
                .where(Job.type.in_(["extract_questions_v2", "extract_questions_v3"]))
                .where(Job.status.in_(["queued", "running"]))
                .order_by(Job.started_at.desc().nullslast(), Job.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if job_row is not None:
            out["active_job_id"] = str(job_row.id)
            out["active_job"] = {
                "id": str(job_row.id),
                "status": job_row.status,
                "progress": job_row.progress,
                "message": job_row.message,
            }
    return out


@banks_router.delete("/{bank_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question_bank(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")
    await session.delete(bank)


@banks_router.post(
    "/{bank_id}/blocks/{block_idx}/re-extract",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(extraction_limit)],
)
async def re_extract_block(
    bank_id: UUID,
    block_idx: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run OCR for a single excluded block. Replaces that block's rows in-place."""
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    book = await session.get(Book, bank.book_id)
    if book is None or not book.pdf_url or not book.schema:
        raise HTTPException(400, detail="Book, PDF, or schema missing")

    # Inline block indices start at INLINE_BLOCK_BASE (10000) — these reference
    # theory sections flagged with question-like content_types, not excluded blocks.
    from app.workers.questions_v2 import INLINE_BLOCK_BASE, _collect_inline_targets

    if block_idx >= INLINE_BLOCK_BASE:
        inline_targets = _collect_inline_targets(book.schema or {})
        inline_idx = block_idx - INLINE_BLOCK_BASE
        if inline_idx < 0 or inline_idx >= len(inline_targets):
            raise HTTPException(
                400,
                detail=f"inline block_idx {inline_idx} out of range (0..{len(inline_targets) - 1})",
            )
    else:
        excluded = list((book.schema or {}).get("excluded_sections") or [])
        if block_idx < 0 or block_idx >= len(excluded):
            raise HTTPException(
                400,
                detail=f"block_idx {block_idx} out of range (0..{len(excluded) - 1})",
            )

    job = Job(book_id=book.id, type="re_extract_block", status="queued", progress=0)
    session.add(job)
    await session.flush()
    await session.commit()

    import app.workers.questions_v2  # noqa: F401 — ensure task registration
    from app.workers.runner import dispatch

    dispatch("re_extract_block", str(bank.id), int(block_idx), str(job.id))

    return {
        "bank_id": str(bank.id),
        "block_idx": block_idx,
        "job_id": str(job.id),
        "status": "queued",
    }


@banks_router.post(
    "/{bank_id}/sections/{section_ref}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(extraction_limit)],
)
async def retry_section(
    bank_id: UUID,
    section_ref: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run v3 extraction for a single section. Replaces that section's rows
    in place and updates the matching entry in extraction_stats.sections.
    """
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    job = Job(book_id=bank.book_id, type="re_extract_section_v3",
              status="queued", progress=0)
    session.add(job)
    await session.flush()
    await session.commit()

    import app.workers.questions_v3  # noqa: F401 — ensure registration
    from app.workers.runner import dispatch
    dispatch("re_extract_section_v3", str(bank.id), section_ref, str(job.id))

    return {
        "bank_id": str(bank.id),
        "section_ref": section_ref,
        "job_id": str(job.id),
        "status": "queued",
    }


@banks_router.post("/{bank_id}/link-examples")
async def link_examples(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Run the example→theory touchpoint linker for this bank's book.

    Inserts `question_ref` chip blocks into each parent theory section's
    `blocks` JSON, at the position where its child example appears in the
    prose. Idempotent — safe to re-run after re-extraction.
    """
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")
    from app.services.example_linker import link_examples_to_theory
    return await link_examples_to_theory(session, bank.book_id)


async def _load_question_embedded_figures(
    session: AsyncSession,
    book_id: UUID,
) -> dict[str, list[dict]]:
    """Build {question_id_str: [figure_dict, ...]} for question-context
    figure_references on this book. Variant choice: regen-if-approved
    else original.
    """
    from app.models.figure import Figure
    from app.models.figure_reference import FigureReference

    refs = (
        await session.execute(
            select(FigureReference)
            .where(FigureReference.book_id == book_id)
            .where(FigureReference.context == "question")
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

    out: dict[str, list[dict]] = {}
    for r in refs:
        if r.question_id is None:
            continue
        f = fig_by_id.get(r.figure_id)
        if f is None:
            continue
        # Default to the regen variant whenever it exists — no approval gate.
        # Reviewer wants regenerated figure to appear automatically inside the
        # regenerated question; the explicit Approve step is for the Figures
        # tab QA workflow, not a precondition for display here.
        variant = "regen" if f.regen_image_bytes else "original"
        out.setdefault(str(r.question_id), []).append({
            "ref_id": str(r.id),
            "figure_id": str(f.id),
            "label": f.figure_number or r.placeholder_text or "",
            "caption": f.caption or "",
            "variant": variant,
            "image_url": f"/api/figures/{f.id}/image?variant=auto",
            "placement_kind": r.placement_kind or "appended",
            "placement_char_offset": r.placement_char_offset,
        })
    for k, lst in out.items():
        lst.sort(key=lambda d: (
            d.get("placement_char_offset") if d.get("placement_char_offset") is not None else 10**9
        ))
    return out


@banks_router.get("/{bank_id}/questions")
async def list_questions(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return all questions for a bank, grouped by section in schema order."""
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    # Original-only view: exclude regen variants (rows with regen_id set).
    # Regen variants live in the ✨ Regenerated folder and have their own
    # endpoint (/api/question-regenerations/{regen_id}/questions). Leaking
    # them into the bank's question list caused them to appear under both
    # Original and Regenerated.
    # Also exclude hidden questions — when a user clicks ✕ on a question
    # in the reviewer, we set is_hidden=True and it should disappear from
    # every list endpoint (not just the regen overlay).
    result = await session.execute(
        select(Question)
        .where(Question.bank_id == bank_id)
        .where(Question.regen_id.is_(None))
        .where(Question.is_hidden.is_(False))
        .order_by(Question.section_ref, Question.page_start, Question.created_at)
    )
    questions = result.scalars().all()

    # Phase 1: pre-load embedded figures per question
    embedded_q_figures = await _load_question_embedded_figures(session, bank.book_id)

    # group by section_ref, preserving schema order if available
    book = await session.get(Book, bank.book_id)
    order: list[str] = []
    titles: dict[str, str] = {}
    if book and book.schema:
        try:
            from app.schemas.analyser import BookSchema
            from app.services.chunk_builder import flatten_sections as _flatten
            schema_obj = BookSchema(**book.schema)
            for ss in _flatten(schema_obj):
                order.append(ss.id)
                titles[ss.id] = ss.title
        except Exception:
            pass

    grouped: dict[str, list[dict]] = {sid: [] for sid in order}
    for q in questions:
        qd = _question_dict(q)
        # Phase 1 figure embedder — attach figures for this question
        qd["embedded_figures"] = embedded_q_figures.get(str(q.id), [])
        grouped.setdefault(q.section_ref, []).append(qd)

    # Pending rejected items per section (status='pending' only — restored/discarded hidden)
    rej_result = await session.execute(
        select(RejectedQuestion)
        .where(RejectedQuestion.bank_id == bank_id)
        .where(RejectedQuestion.status == "pending")
        .order_by(RejectedQuestion.section_ref, RejectedQuestion.page_start, RejectedQuestion.created_at)
    )
    rejected_rows = rej_result.scalars().all()
    rejected_grouped: dict[str, list[dict]] = {}
    for r in rejected_rows:
        rejected_grouped.setdefault(r.section_ref or "", []).append(_rejected_dict(r))

    def _group_by_kind(items: list[dict]) -> dict[str, list[dict]]:
        buckets: dict[str, list[dict]] = {}
        for it in items:
            k = (it.get("kind") or "exercise").lower()
            buckets.setdefault(k, []).append(it)
        return buckets

    sections_out: list[dict] = []
    seen: set[str] = set()
    for sid in order:
        items = grouped.get(sid, [])
        sections_out.append({
            "section_ref": sid,
            "section_title": titles.get(sid, sid),
            "questions": items,
            "by_kind": _group_by_kind(items),
            "rejected": rejected_grouped.get(sid, []),
        })
        seen.add(sid)
    for sid, items in grouped.items():
        if sid in seen:
            continue
        sections_out.append({
            "section_ref": sid,
            "section_title": sid,
            "questions": items,
            "by_kind": _group_by_kind(items),
            "rejected": rejected_grouped.get(sid, []),
        })
        seen.add(sid)
    # sections that have ONLY rejected items (no surviving questions)
    for sid, items in rejected_grouped.items():
        if sid in seen:
            continue
        sections_out.append({
            "section_ref": sid,
            "section_title": titles.get(sid, sid),
            "questions": [],
            "by_kind": {},
            "rejected": items,
        })
        seen.add(sid)

    # Attach per-section extraction counts rolled up from extraction_stats.blocks
    # (each block has a resolved section_ref — aggregate across sections).
    stats = bank.extraction_stats or {}
    blocks = list(stats.get("blocks") or [])
    per_section: dict[str, dict[str, int]] = {}
    for b in blocks:
        sref = b.get("section_ref") or "__unlinked__"
        bucket = per_section.setdefault(sref, {"identified": 0, "extracted": 0, "missed": 0})
        bucket["identified"] += int(b.get("identified") or 0)
        bucket["extracted"] += int(b.get("extracted") or 0)
        bucket["missed"] += int(b.get("missed") or 0)
    for sec in sections_out:
        c = per_section.get(sec["section_ref"], {"identified": 0, "extracted": 0, "missed": 0})
        sec["identified"] = c["identified"]
        sec["extracted"] = c["extracted"]
        sec["missed"] = c["missed"]

    return {
        "bank_id": str(bank.id),
        "book_id": str(bank.book_id),
        "title": bank.title,
        "status": bank.status,
        "total_questions": len(questions),
        "stats": _normalise_stats(stats),
        "sections": sections_out,
    }


def _safe_name(s: str) -> str:
    return re.sub(r"[^\w-]+", "_", s or "questions").strip("_") or "questions"


@banks_router.get("/{bank_id}/export/json")
async def export_bank_json(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    grouped = await list_questions(bank_id, session)
    name = _safe_name(bank.title) + "_questions"
    return Response(
        content=json.dumps(grouped, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
    )


_FIG_PLACEHOLDER_RE = re.compile(r"\{\{\s*fig\s*:\s*([^}]+?)\s*\}\}", re.IGNORECASE)


def _render_figure_placeholders(text: str) -> str:
    """Convert ``{{fig: label — caption}}`` placeholders into a Markdown-safe
    visual callout that survives the pandoc → DOCX pipeline.

    The raw JSON export keeps the original placeholder — docx/md render it as
    an inline boxed note so the reader sees exactly where the figure belongs.
    """
    def _sub(m: re.Match[str]) -> str:
        inner = (m.group(1) or "").strip() or "(unlabelled figure)"
        return f"*[Figure: {inner}]*"
    return _FIG_PLACEHOLDER_RE.sub(_sub, text or "")


def _build_bank_markdown(bank: QuestionBank, grouped: dict) -> str:
    """Render the bank as GFM with LaTeX math preserved in ``$...$`` / ``$$...$$``
    so pandoc converts it to native OMML equations. Numbered lists use native
    Markdown numbering so Word renders a real ordered list.
    """
    lines: list[str] = [f"# {bank.title} — Question Bank", ""]
    for sec in grouped["sections"]:
        qs = sec["questions"]
        if not qs:
            continue
        lines.append(f"## {sec['section_ref']} {sec['section_title']}")
        lines.append("")

        by_kind = sec.get("by_kind") or {}
        # Render in a stable, reader-friendly order of kinds.
        order = ["example", "try_it", "mcq", "exercise", "problem", "review", "other"]
        rendered_kinds: set[str] = set()
        for kind in order:
            items = by_kind.get(kind) or []
            if not items:
                continue
            heading = {
                "example": "Worked Examples",
                "try_it": "Try It",
                "mcq": "MCQs",
                "exercise": "Exercises",
                "problem": "Problems",
                "review": "Review Questions",
                "other": "Other",
            }[kind]
            lines.append(f"### {heading}")
            lines.append("")
            for idx, q in enumerate(items, start=1):
                body = _render_figure_placeholders(q.get("raw_text") or "")
                lines.append(f"{idx}. {body}")
                sol = _render_figure_placeholders(q.get("solution_text") or "")
                if sol:
                    lines.append("")
                    lines.append(f"    **Solution.** {sol}")
                lines.append("")
            rendered_kinds.add(kind)

        # Any kinds outside the known order fall through here.
        for kind, items in by_kind.items():
            if kind in rendered_kinds or not items:
                continue
            lines.append(f"### {kind.title()}")
            lines.append("")
            for idx, q in enumerate(items, start=1):
                body = _render_figure_placeholders(q.get("raw_text") or "")
                lines.append(f"{idx}. {body}")
                lines.append("")
    return "\n".join(lines)


@banks_router.get("/{bank_id}/export/markdown")
async def export_bank_markdown(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")
    grouped = await list_questions(bank_id, session)
    markdown = _build_bank_markdown(bank, grouped)

    name = _safe_name(bank.title) + "_questions"
    return Response(
        content=markdown.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


def _ensure_pandoc_on_path() -> str:
    """Locate the pandoc binary for pypandoc. Returns the resolved path.

    Mirrors the resolver in ``app/api/books.py`` — macOS launchd sometimes
    drops homebrew bins from PATH and we want the DOCX export to work out of
    the box in both dev and Railway.
    """
    found = shutil.which("pandoc")
    if found:
        return found
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
        detail="pandoc binary not found — install pandoc to enable DOCX export",
    )


@banks_router.get("/{bank_id}/export/docx")
async def export_bank_docx(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export the bank as a Word document using the native python-docx
    builder (`app.services.docx_export`). Replaces the previous pandoc
    pipeline so we control fonts, spacing, no-duplicate-heading invariant,
    and Question/Options/Answer/Solution layout precisely.
    """
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    from app.services.docx_export import build_questions_docx

    grouped = await list_questions(bank_id, session)
    # Flatten by-kind into a single questions list per section — the
    # docx builder doesn't need the kind partitioning (worked examples
    # are detected by section_title containing 'EXAMPLE').
    sections_flat: list[dict] = []
    for sec in grouped.get("sections", []):
        questions: list[dict] = []
        if "questions" in sec:
            questions = list(sec["questions"])
        else:
            by_kind = sec.get("by_kind") or {}
            for items in by_kind.values():
                questions.extend(items or [])
        sections_flat.append({
            "section_ref": sec.get("section_ref"),
            "section_title": sec.get("section_title"),
            "questions": questions,
        })

    data = build_questions_docx(bank.title or "Question Bank", sections_flat)
    name = _safe_name(bank.title) + "_questions"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Human-in-the-loop review (Issue 3)
# ─────────────────────────────────────────────────────────────────────────────

@banks_router.post("/{bank_id}/rejected/{rejected_id}/restore")
async def restore_rejected(
    bank_id: UUID,
    rejected_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Promote a rejected item into the questions table as a normal Question.

    The original RejectedQuestion row is kept (status='restored') for audit.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    rej = await session.get(RejectedQuestion, rejected_id)
    if rej is None or rej.bank_id != bank_id:
        raise HTTPException(404, detail="Rejected item not found")
    if rej.status != "pending":
        raise HTTPException(409, detail=f"Rejected item already {rej.status}")

    payload = rej.payload or {}
    q = Question(
        id=uuid4(),
        bank_id=rej.bank_id,
        book_id=rej.book_id,
        section_ref=rej.section_ref,
        section_title=rej.section_title,
        page_start=rej.page_start,
        page_end=rej.page_end,
        raw_text=rej.raw_text,
        status="passed",
        question_number=payload.get("question_number"),
        exercise_ref=payload.get("exercise_ref"),
        chapter_ref=payload.get("chapter_ref"),
        sub_part=payload.get("sub_part"),
        question_type=payload.get("question_type"),
        has_options=bool(payload.get("has_options") or False),
        solution_text=payload.get("solution_text"),
        has_solution=bool(payload.get("has_solution") or False),
        kind=(payload.get("kind") or "exercise"),
    )
    session.add(q)

    rej.status = "restored"
    rej.decided_at = datetime.now(timezone.utc)
    rej.decided_by = "user"
    await session.commit()

    return {"ok": True, "question_id": str(q.id), "rejected_id": str(rej.id)}


@banks_router.post("/{bank_id}/rejected/{rejected_id}/discard")
async def discard_rejected(
    bank_id: UUID,
    rejected_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mark a rejected item as discarded so it stops showing up in the UI."""
    from datetime import datetime, timezone

    rej = await session.get(RejectedQuestion, rejected_id)
    if rej is None or rej.bank_id != bank_id:
        raise HTTPException(404, detail="Rejected item not found")
    if rej.status != "pending":
        raise HTTPException(409, detail=f"Rejected item already {rej.status}")

    rej.status = "discarded"
    rej.decided_at = datetime.now(timezone.utc)
    rej.decided_by = "user"
    await session.commit()
    return {"ok": True, "rejected_id": str(rej.id)}


@banks_router.patch("/questions/{question_id}/hide")
async def hide_question(
    question_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    q = await session.get(Question, question_id)
    if q is None:
        raise HTTPException(404, detail="Question not found")
    q.is_hidden = True
    await session.commit()
    return {"ok": True, "question_id": str(q.id), "is_hidden": True}


@banks_router.patch("/questions/{question_id}/unhide")
async def unhide_question(
    question_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    q = await session.get(Question, question_id)
    if q is None:
        raise HTTPException(404, detail="Question not found")
    q.is_hidden = False
    await session.commit()
    return {"ok": True, "question_id": str(q.id), "is_hidden": False}
