"""Question regeneration worker — v3.

R2 (2026-05-08): new v3-quality regeneration worker that processes the
PREVIOUSLY EXTRACTED questions (not PDF re-OCR). For each source question,
calls Gemini with the `question_regenerator_v3` system prompt and persists
the generated variants as new rows in the questions table with
``regen_id = regen.id`` set.

Architecture:
  - One Gemini call per source question (cheap text-only call)
  - Concurrency: 4-wide via existing gemini_runtime semaphore
  - Heartbeat keeps the watchdog quiet during long regen runs
  - section_ref is ALWAYS stamped from the source question (Q4 rule mirrored)
  - regen_id flags the row as generated; originals have regen_id=NULL

What this worker does NOT do (deferred to later R-steps):
  - R3  Custom-instruction priority modes (override / layer / specific)
  - R4  API params (similarity_level, count, question_type) — hardcoded defaults for R2
  - R5  Migration for new QuestionRegeneration columns
  - R6  Section-level retry endpoint
  - R8+ Frontend changes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.gemini_runtime import call_gemini_text_only
from app.core.heartbeat import Heartbeat
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.question_regeneration import QuestionRegeneration
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

# Single sync engine + session factory (mirrors questions_v3.py).
_sync_engine = create_engine(
    settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", ""),
    pool_pre_ping=True,
)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

# Defaults for R2 — R4/R5 make these configurable per regen run via API/UI.
DEFAULT_SIMILARITY = "numbers_and_rephrase"
DEFAULT_COUNT = 3
DEFAULT_QUESTION_TYPE = "same_as_source"
DEFAULT_PRIORITY_MODE = "override"

# Valid priority modes (R3).
_VALID_PRIORITY_MODES = {"override", "layer_on_top", "specific_aspects"}


def _priority_mode_block(mode: str, custom_instructions: str) -> str:
    """Build the framing block that tells Gemini HOW to apply custom
    instructions, per the user-selected priority mode.

    Returns "" if custom_instructions is empty/None — mode is irrelevant
    without instructions to apply.
    """
    txt = (custom_instructions or "").strip()
    if not txt:
        return ""
    mode = (mode or "").strip().lower()
    if mode not in _VALID_PRIORITY_MODES:
        mode = DEFAULT_PRIORITY_MODE

    if mode == "override":
        header = (
            "PRIORITY MODE: OVERRIDE\n"
            "The custom_instructions below COMPLETELY REPLACE the default "
            "similarity-level behavior. Follow them above all other rules "
            "EXCEPT factual correctness (which always wins). The similarity "
            "level still selects which aspects are conceptually LOCKED, but "
            "every other generation choice (tone, structure, language, "
            "style, pattern) is dictated by these instructions."
        )
    elif mode == "layer_on_top":
        header = (
            "PRIORITY MODE: LAYER_ON_TOP\n"
            "Apply the default similarity-level behavior FIRST (following "
            "the LOCKED / CHANGES rules for the selected similarity level). "
            "THEN, on top of the resulting question, apply the "
            "custom_instructions below as ADDITIONAL constraints. Both must "
            "be honoured. If the custom_instructions conflict with the "
            "similarity-level locks, the similarity locks win (e.g. "
            "similarity 'numbers_only' still requires sentence structure "
            "to remain unchanged)."
        )
    else:  # specific_aspects
        header = (
            "PRIORITY MODE: SPECIFIC_ASPECTS\n"
            "The custom_instructions below modify ONLY the aspects the user "
            "has listed in the instructions text. Preserve all other aspects "
            "of the source question. If the user did NOT enumerate which "
            "aspects to modify, default to changing ONLY wording and "
            "scenario; preserve numbers, concept, sentence-level structure, "
            "and question_type. Do NOT introduce changes beyond the listed "
            "aspects."
        )
    return header + "\n\nCustom instructions:\n" + txt

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT_S = 150
MAX_OUTPUT_TOKENS = 32768

# Question kind enum allowed in the DB (matches Question.kind column).
_LEGACY_KINDS = {"exercise", "example", "problem", "mcq", "review", "other"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _update_job(session: Session, job_id: UUID, **fields: Any) -> None:
    """Update a job row by id."""
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        if hasattr(job, k):
            setattr(job, k, v)
    session.commit()


def _update_regen(session: Session, regen_id: UUID, **fields: Any) -> None:
    """Update a QuestionRegeneration row by id."""
    r = session.get(QuestionRegeneration, regen_id)
    if r is None:
        return
    for k, v in fields.items():
        if hasattr(r, k):
            setattr(r, k, v)
    session.commit()


def _map_question_type_to_kind(qtype: str | None) -> str:
    """Map Gemini's question_type (14 types from the prompt) to the legacy
    Question.kind enum used by the DB.

    Conservative — anything unrecognised falls to "other". The full
    question_type string is preserved separately in Question.question_type.
    """
    if not qtype:
        return "exercise"
    q = qtype.lower().strip()
    if "scq" in q or "mcq" in q or "binary" in q or "assertion" in q:
        return "mcq"
    if "integer" in q or "numerical" in q or "fill" in q:
        return "exercise"
    if "subjective" in q or "comprehension" in q:
        return "exercise"
    if "matching" in q:
        return "exercise"
    if q in _LEGACY_KINDS:
        return q
    return "other"


def _flatten_source_section_refs(
    regen: QuestionRegeneration,
    bank_id: UUID,
    session: Session,
) -> list[str]:
    """Return the list of section_refs to regenerate from, based on regen
    scope.

    scope="bank"      → every section_ref that has at least one extracted
                        Question row (regen_id IS NULL) in this bank.
    scope="sections"  → use regen.section_refs verbatim.
    """
    if regen.scope == "sections":
        return list(regen.section_refs or [])

    rows = session.execute(
        select(Question.section_ref).where(
            Question.bank_id == bank_id,
            Question.regen_id.is_(None),
        ).distinct()
    ).all()
    return [r[0] for r in rows if r[0]]


def _build_user_prompt(
    source: Question,
    *,
    similarity_level: str,
    count: int,
    question_type: str,
    custom_instructions: str | None,
    priority_mode: str,
    subject: str | None,
    chapter: str | None,
    grade: str | None,
    board: str | None,
) -> str:
    """Build the user prompt for one source-question regeneration call.

    Format mirrors the "INPUTS YOU WILL RECEIVE" section in
    `question_regenerator_v3.txt`. When custom_instructions is present, a
    PRIORITY MODE block (R3) is prepended that tells Gemini HOW to apply
    them (override / layer_on_top / specific_aspects).
    """
    def _maybe(v: str | None, fallback: str = "infer from source") -> str:
        return v if (v and str(v).strip()) else fallback

    parts: list[str] = []

    # Priority-mode block (only if custom instructions present).
    pm_block = _priority_mode_block(priority_mode, custom_instructions or "")
    if pm_block:
        parts.append(pm_block)
        parts.append("")

    parts.extend([
        f"similarity_level    : {similarity_level}",
        f"count               : {count}",
        f"question_type       : {question_type}",
        f"subject             : {_maybe(subject)}",
        f"chapter             : {_maybe(chapter)}",
        f"grade               : {_maybe(grade)}",
        f"board               : {_maybe(board)}",
        f"custom_instructions : {custom_instructions or '(none)'}",
        "",
        "source_question     :",
        (source.raw_text or "").strip(),
    ])
    if source.solution_text:
        parts.append("")
        parts.append("source_answer       :")
        parts.append(source.solution_text.strip())
    return "\n".join(parts)


async def _regen_one_source(
    source: Question,
    *,
    system_prompt: str,
    similarity_level: str,
    count: int,
    question_type: str,
    custom_instructions: str | None,
    priority_mode: str,
    subject: str | None,
    chapter: str | None,
    grade: str | None,
    board: str | None,
) -> dict[str, Any]:
    """Single Gemini call to regenerate `count` variants from `source`.

    Returns:
        {"ok": bool, "items": [<regen dict>...], "notes": str, "error": str}
    """
    user_prompt = _build_user_prompt(
        source,
        similarity_level=similarity_level,
        count=count,
        question_type=question_type,
        custom_instructions=custom_instructions,
        priority_mode=priority_mode,
        subject=subject,
        chapter=chapter,
        grade=grade,
        board=board,
    )

    try:
        raw = await asyncio.to_thread(
            call_gemini_text_only,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=GEMINI_MODEL,
            timeout_s=GEMINI_TIMEOUT_S,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.4,
        )
        data = parse_json(raw)
        if not isinstance(data, dict):
            return {"ok": False, "items": [], "notes": "",
                    "error": "response was not a JSON object"}
        items = list(data.get("regenerated") or [])
        notes = str(data.get("notes") or "")
        # Keep only items that have a non-empty question string.
        items = [it for it in items if isinstance(it, dict)
                 and (it.get("question") or "").strip()]
        return {"ok": True, "items": items, "notes": notes, "error": ""}
    except Exception as e:
        logger.warning(
            "regen-v3 single-source call failed (q=%s): %s",
            source.id, e,
        )
        return {"ok": False, "items": [], "notes": "", "error": str(e)}


def _persist_regen_items(
    session: Session,
    *,
    regen: QuestionRegeneration,
    source: Question,
    items: list[dict[str, Any]],
) -> int:
    """Persist regenerated Question rows. Returns the count inserted.

    ARCHITECTURE RULE (mirrors Q4):
      section_ref / section_title / bank_id / book_id are ALL stamped from
      the worker's known state (source row + regen row). NEVER from the
      Gemini item dict. Item-level fields (question, answer, etc.) are
      model-supplied. The section anchor is owned by the worker.
    """
    inserted = 0
    for it in items:
        text = (it.get("question") or "").strip()
        if not text:
            continue
        answer = (it.get("answer") or "").strip() or None
        # Structure-mirroring (prompt Rule 8): the model returns `solution`
        # only when the source had a printed solution, `answer` only when
        # the source had an inline answer key. Prefer `solution` for
        # solution_text; fall back to `answer` so older prompt outputs
        # without the new field still produce sensible behaviour. If the
        # SOURCE had neither, the model returns both empty → regen also
        # has empty solution_text / has_solution=False.
        solution = (it.get("solution") or "").strip() or None
        solution_text = solution or answer
        # Decide has_solution by mirroring the source — the prompt is
        # instructed to leave fields empty when the source lacks them,
        # so an empty solution_text here means "source didn't have one".
        has_solution = bool(solution_text) and bool(source.has_solution)
        # Same idea for options: if source isn't an MCQ, regen variants
        # shouldn't claim has_options either.
        model_says_options = bool(it.get("options"))
        has_options = model_says_options and bool(source.has_options)
        q_type = (it.get("question_type") or source.question_type or "").strip() or None
        kind = _map_question_type_to_kind(q_type)

        row = Question(
            bank_id=regen.bank_id,           # worker-known
            book_id=regen.book_id,           # worker-known
            regen_id=regen.id,               # GENERATED flag
            source_question_id=source.id,    # 0014 — point back to source
            section_ref=source.section_ref,  # Q4 rule — from source, never model
            section_title=source.section_title,
            page_start=source.page_start,
            page_end=source.page_end,
            raw_text=text,
            qc_local={"pass": True, "score": 1.0, "failures": []},
            attempts=1,
            status="passed",
            question_number=None,            # regen items have no printed q_no
            exercise_ref=source.exercise_ref,
            chapter_ref=source.chapter_ref,
            kind=kind,
            question_type=q_type,
            has_options=has_options,
            solution_text=solution_text if has_solution else None,
            has_solution=has_solution,
            identified_total=None,
            qc_status="pending",
        )
        session.add(row)
        inserted += 1
    session.commit()
    return inserted


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------
async def _run_regen_v3(regen_id: UUID, job_id: UUID) -> dict[str, Any]:
    with SyncSession() as session:
        regen = session.get(QuestionRegeneration, regen_id)
        if regen is None:
            raise ValueError(f"Regen {regen_id} not found")
        bank = session.get(QuestionBank, regen.bank_id)
        book = session.get(Book, regen.book_id)
        if bank is None or book is None:
            raise ValueError("Bank or Book missing for regen")

        # R4 will read these from regen.* once the columns land in R5.
        # Forward-compat: getattr falls back to defaults if column not yet
        # present on the model.
        similarity_level = (
            (getattr(regen, "similarity_level", None) or "").strip()
            or DEFAULT_SIMILARITY
        )
        count_raw = getattr(regen, "count", None)
        try:
            count = int(count_raw) if count_raw else DEFAULT_COUNT
        except (TypeError, ValueError):
            count = DEFAULT_COUNT
        question_type = (
            (getattr(regen, "question_type", None) or "").strip()
            or DEFAULT_QUESTION_TYPE
        )
        priority_mode = (
            (getattr(regen, "priority_mode", None) or "").strip().lower()
            or DEFAULT_PRIORITY_MODE
        )
        if priority_mode not in _VALID_PRIORITY_MODES:
            priority_mode = DEFAULT_PRIORITY_MODE
        custom_instructions = (regen.custom_instructions or "").strip() or None

        # Snapshot for downstream use.
        subject = bank.subject or None
        grade = getattr(book, "grade_level", None) or None
        board = getattr(book, "board", None) or None
        bank_id = bank.id
        regen.status = "extracting"
        regen.last_error = None
        session.commit()

        # Load source questions for the requested sections.
        section_refs = _flatten_source_section_refs(regen, bank_id, session)
        if not section_refs:
            _update_regen(
                session, regen.id,
                status="ready",
                finished_at=datetime.utcnow(),
                extraction_stats={
                    "sections": [], "totals": {
                        "expected_total": 0, "extracted_total": 0,
                        "complete": 0, "partial": 0, "empty": 0, "failed": 0,
                    },
                },
            )
            _update_job(session, job_id,
                        status="succeeded", progress=100,
                        message="No sections in regen scope",
                        finished_at=datetime.utcnow())
            return {"ok": True, "regen_id": str(regen.id), "total": 0}

        source_qs = session.execute(
            select(Question).where(
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
                Question.section_ref.in_(section_refs),
            )
        ).scalars().all()

        if not source_qs:
            _update_regen(
                session, regen.id,
                status="ready",
                finished_at=datetime.utcnow(),
                extraction_stats={
                    "sections": [], "totals": {
                        "expected_total": 0, "extracted_total": 0,
                        "complete": 0, "partial": 0, "empty": 0, "failed": 0,
                    },
                },
            )
            _update_job(session, job_id,
                        status="succeeded", progress=100,
                        message="No source questions to regenerate",
                        finished_at=datetime.utcnow())
            return {"ok": True, "regen_id": str(regen.id), "total": 0}

        # Capture source IDs BEFORE the wipe/commit. After commit() the
        # source_qs objects are expired and accessing their attributes
        # outside the session would trigger a refresh — fails because the
        # session is closed.
        source_ids = [q.id for q in source_qs]
        total_sources = len(source_ids)

        # Wipe any previous regen rows for THIS regen so a retry is clean.
        # (R6 offers section-level retry that wipes only one section.)
        session.execute(
            delete(Question).where(Question.regen_id == regen.id)
        )
        session.commit()

        system_prompt = load_raw("question_regenerator_v3")

        _update_job(
            session, job_id,
            status="running", progress=5,
            message=f"v3 regen — {total_sources} source question(s) × {count} variants",
        )

    # Stats accumulators.
    section_counts: dict[str, dict[str, int]] = {}
    total_generated = 0
    total_failed = 0

    async def _process_one(qid: UUID) -> tuple[UUID, dict[str, Any]]:
        # Re-fetch source row in its own session for thread-safe SQLite use.
        with SyncSession() as own:
            source = own.get(Question, qid)
            if source is None:
                return qid, {"ok": False, "items": [], "notes": "",
                             "error": "source vanished"}
            # Detach values we need so the session can close.
            cached = {
                "section_ref": source.section_ref,
                "raw_text": source.raw_text,
                "solution_text": source.solution_text,
                "question_type": source.question_type,
                "section_title": source.section_title,
                "page_start": source.page_start,
                "page_end": source.page_end,
                "exercise_ref": source.exercise_ref,
                "chapter_ref": source.chapter_ref,
            }
        # Build a transient Question-like object for prompt building. Easiest:
        # use a tiny attribute holder rather than instantiating ORM detached.
        class _SrcView:
            pass
        sv = _SrcView()
        for k, v in cached.items():
            setattr(sv, k, v)
        sv.id = qid

        result = await _regen_one_source(
            sv,  # type: ignore[arg-type]
            system_prompt=system_prompt,
            similarity_level=similarity_level,
            count=count,
            question_type=question_type,
            custom_instructions=custom_instructions,
            priority_mode=priority_mode,
            subject=subject,
            chapter=cached.get("section_title") or None,
            grade=grade,
            board=board,
        )
        # Persist within a fresh session.
        if result.get("ok") and result.get("items"):
            with SyncSession() as own:
                src = own.get(Question, qid)
                regen_obj = own.get(QuestionRegeneration, regen_id)
                if src is not None and regen_obj is not None:
                    inserted = _persist_regen_items(
                        own, regen=regen_obj, source=src,
                        items=result["items"],
                    )
                    result["persisted"] = inserted
        return qid, result

    # Heartbeat-wrap the parallel run.
    with Heartbeat(
        job_id,
        base_msg=f"Regen ({total_sources} source questions)",
        progress=5,
    ):
        tasks = [asyncio.create_task(_process_one(qid)) for qid in source_ids]
        done = 0
        for coro in asyncio.as_completed(tasks):
            qid, result = await coro
            done += 1
            progress = 5 + int(90 * done / max(total_sources, 1))

            # Accumulate per-section stats. We need section_ref for the qid —
            # cheapest: read it once before the task starts. We could cache
            # in source_qs, but doing a tiny lookup here keeps the loop simple.
            with SyncSession() as own:
                src = own.get(Question, qid)
                section_ref = src.section_ref if src else "?"
            persisted = int(result.get("persisted") or 0)
            ok = bool(result.get("ok"))
            bucket = section_counts.setdefault(section_ref, {
                "source_count": 0, "generated": 0, "failed": 0,
            })
            bucket["source_count"] += 1
            bucket["generated"] += persisted
            if ok and persisted > 0:
                total_generated += persisted
            else:
                bucket["failed"] += 1
                total_failed += 1

            with SyncSession() as own:
                _update_job(own, job_id, progress=progress,
                            message=f"Regen {done}/{total_sources} sources — "
                                    f"{total_generated} generated, "
                                    f"{total_failed} failed")

    # Final stats + status.
    sections_report: list[dict[str, Any]] = []
    complete = partial = empty = failed = 0
    for ref, c in section_counts.items():
        if c["failed"] == c["source_count"] and c["generated"] == 0:
            sec_status = "failed"
            failed += 1
        elif c["failed"] > 0:
            sec_status = "partial"
            partial += 1
        elif c["generated"] == 0:
            sec_status = "empty"
            empty += 1
        else:
            sec_status = "complete"
            complete += 1
        sections_report.append({
            "section_ref": ref,
            "source_count": c["source_count"],
            "generated": c["generated"],
            "failed": c["failed"],
            "status": sec_status,
        })

    stats = {
        "sections": sections_report,
        "totals": {
            "expected_total": total_sources * count,
            "extracted_total": total_generated,
            "complete": complete,
            "partial": partial,
            "empty": empty,
            "failed": failed,
        },
    }

    bank_status = "ready" if total_failed == 0 else "partial"
    with SyncSession() as session:
        _update_regen(session, regen_id,
                      status=bank_status,
                      finished_at=datetime.utcnow(),
                      extraction_stats=stats)
        _update_job(session, job_id,
                    status="succeeded", progress=100,
                    message=f"Regen complete — generated {total_generated} "
                            f"across {len(section_counts)} section(s)",
                    finished_at=datetime.utcnow())

    return {
        "ok": True,
        "regen_id": str(regen_id),
        "total_sources": total_sources,
        "total_generated": total_generated,
        "total_failed": total_failed,
    }


# ---------------------------------------------------------------------------
# R6 — Section-level retry
# Run regen for a SINGLE section_ref within an existing regen. Wipes only
# the rows for that (regen_id, section_ref) — other sections stay intact.
# Per-section status is recomputed and merged into regen.extraction_stats
# without touching other sections' reports.
# ---------------------------------------------------------------------------
async def _run_regen_one_section_v3(
    regen_id: UUID,
    section_ref: str,
    job_id: UUID,
) -> dict[str, Any]:
    with SyncSession() as session:
        regen = session.get(QuestionRegeneration, regen_id)
        if regen is None:
            raise ValueError(f"Regen {regen_id} not found")
        bank = session.get(QuestionBank, regen.bank_id)
        book = session.get(Book, regen.book_id)
        if bank is None or book is None:
            raise ValueError("Bank or Book missing for regen")

        # Same param resolution as the main worker.
        similarity_level = (
            (getattr(regen, "similarity_level", None) or "").strip()
            or DEFAULT_SIMILARITY
        )
        count_raw = getattr(regen, "count", None)
        try:
            count = int(count_raw) if count_raw else DEFAULT_COUNT
        except (TypeError, ValueError):
            count = DEFAULT_COUNT
        question_type = (
            (getattr(regen, "question_type", None) or "").strip()
            or DEFAULT_QUESTION_TYPE
        )
        priority_mode = (
            (getattr(regen, "priority_mode", None) or "").strip().lower()
            or DEFAULT_PRIORITY_MODE
        )
        if priority_mode not in _VALID_PRIORITY_MODES:
            priority_mode = DEFAULT_PRIORITY_MODE
        custom_instructions = (regen.custom_instructions or "").strip() or None
        subject = bank.subject or None
        grade = getattr(book, "grade_level", None) or None
        board = getattr(book, "board", None) or None
        bank_id = bank.id

        # Wipe ONLY this section's regen rows. Other sections preserved.
        session.execute(
            delete(Question).where(
                Question.regen_id == regen.id,
                Question.section_ref == section_ref,
            )
        )
        session.commit()

        # Load source Questions for this section.
        source_qs = session.execute(
            select(Question).where(
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
                Question.section_ref == section_ref,
            )
        ).scalars().all()

        if not source_qs:
            _update_job(session, job_id,
                        status="succeeded", progress=100,
                        message=f"No source questions in section {section_ref}",
                        finished_at=datetime.utcnow())
            return {"ok": True, "regen_id": str(regen.id),
                    "section_ref": section_ref, "total": 0}

        total_sources = len(source_qs)
        system_prompt = load_raw("question_regenerator_v3")
        source_ids = [q.id for q in source_qs]

        _update_job(
            session, job_id,
            status="running", progress=5,
            message=f"Section retry — {section_ref} ({total_sources} sources × {count})",
        )

    # Per-source processing (same pattern as main worker).
    total_generated = 0
    total_failed = 0

    async def _process_one(qid: UUID) -> tuple[UUID, dict[str, Any]]:
        with SyncSession() as own:
            source = own.get(Question, qid)
            if source is None:
                return qid, {"ok": False, "items": [],
                             "error": "source vanished"}
            cached = {
                "section_ref": source.section_ref,
                "raw_text": source.raw_text,
                "solution_text": source.solution_text,
                "question_type": source.question_type,
                "section_title": source.section_title,
                "page_start": source.page_start,
                "page_end": source.page_end,
                "exercise_ref": source.exercise_ref,
                "chapter_ref": source.chapter_ref,
            }

        class _SrcView:
            pass
        sv = _SrcView()
        for k, v in cached.items():
            setattr(sv, k, v)
        sv.id = qid

        result = await _regen_one_source(
            sv,  # type: ignore[arg-type]
            system_prompt=system_prompt,
            similarity_level=similarity_level,
            count=count,
            question_type=question_type,
            custom_instructions=custom_instructions,
            priority_mode=priority_mode,
            subject=subject,
            chapter=cached.get("section_title") or None,
            grade=grade,
            board=board,
        )
        if result.get("ok") and result.get("items"):
            with SyncSession() as own:
                src = own.get(Question, qid)
                regen_obj = own.get(QuestionRegeneration, regen_id)
                if src is not None and regen_obj is not None:
                    inserted = _persist_regen_items(
                        own, regen=regen_obj, source=src,
                        items=result["items"],
                    )
                    result["persisted"] = inserted
        return qid, result

    with Heartbeat(
        job_id,
        base_msg=f"Section retry — {section_ref}",
        progress=5,
    ):
        tasks = [asyncio.create_task(_process_one(qid)) for qid in source_ids]
        done = 0
        for coro in asyncio.as_completed(tasks):
            qid, result = await coro
            done += 1
            progress = 5 + int(90 * done / max(total_sources, 1))
            persisted = int(result.get("persisted") or 0)
            ok = bool(result.get("ok"))
            if ok and persisted > 0:
                total_generated += persisted
            else:
                total_failed += 1
            with SyncSession() as own:
                _update_job(own, job_id, progress=progress,
                            message=f"Section retry {done}/{total_sources} — "
                                    f"{total_generated} generated")

    # Merge this section's report into the regen's extraction_stats without
    # touching other sections. If extraction_stats is missing, build minimal.
    if total_failed == total_sources and total_generated == 0:
        sec_status = "failed"
    elif total_failed > 0:
        sec_status = "partial"
    elif total_generated == 0:
        sec_status = "empty"
    else:
        sec_status = "complete"

    this_section_report = {
        "section_ref": section_ref,
        "source_count": total_sources,
        "generated": total_generated,
        "failed": total_failed,
        "status": sec_status,
    }

    with SyncSession() as session:
        regen_obj = session.get(QuestionRegeneration, regen_id)
        stats = dict(regen_obj.extraction_stats or {}) if regen_obj else {}
        sections = list(stats.get("sections") or [])
        # Replace existing report for this section_ref or append.
        sections = [s for s in sections if s.get("section_ref") != section_ref]
        sections.append(this_section_report)

        # Recompute totals.
        total_expected = sum(int(s.get("source_count", 0)) for s in sections)
        total_generated_all = sum(int(s.get("generated", 0)) for s in sections)
        complete = sum(1 for s in sections if s.get("status") == "complete")
        partial = sum(1 for s in sections if s.get("status") == "partial")
        empty = sum(1 for s in sections if s.get("status") == "empty")
        failed_count = sum(1 for s in sections if s.get("status") == "failed")

        stats["sections"] = sections
        stats["totals"] = {
            "expected_total": total_expected * count,
            "extracted_total": total_generated_all,
            "complete": complete,
            "partial": partial,
            "empty": empty,
            "failed": failed_count,
        }

        new_regen_status = "ready" if failed_count == 0 else "partial"
        _update_regen(session, regen_id,
                      status=new_regen_status,
                      extraction_stats=stats)
        _update_job(session, job_id,
                    status="succeeded", progress=100,
                    message=f"Section retry complete — generated "
                            f"{total_generated} in {section_ref}",
                    finished_at=datetime.utcnow())

    return {
        "ok": True,
        "regen_id": str(regen_id),
        "section_ref": section_ref,
        "total_sources": total_sources,
        "total_generated": total_generated,
        "total_failed": total_failed,
        "section_status": sec_status,
    }


# ---------------------------------------------------------------------------
# Sync entry-points + task registration
# ---------------------------------------------------------------------------
def _extract_questions_regen_v3(regen_id: str, job_id: str) -> dict[str, Any]:
    regen_uuid = UUID(regen_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(_run_regen_v3(regen_uuid, job_uuid))
    except Exception as e:
        logger.exception("extract_questions_regen_v3 failed")
        with SyncSession() as session:
            _update_regen(session, regen_uuid,
                          status="failed",
                          finished_at=datetime.utcnow(),
                          last_error=str(e)[:2000])
            _update_job(session, job_uuid,
                        status="failed",
                        error=str(e)[:2000],
                        finished_at=datetime.utcnow())
        return {"ok": False, "error": str(e)}


def _retry_regen_section_v3(
    regen_id: str, section_ref: str, job_id: str,
) -> dict[str, Any]:
    regen_uuid = UUID(regen_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(
            _run_regen_one_section_v3(regen_uuid, section_ref, job_uuid)
        )
    except Exception as e:
        logger.exception("retry_regen_section_v3 failed")
        with SyncSession() as session:
            _update_job(session, job_uuid,
                        status="failed",
                        error=str(e)[:2000],
                        finished_at=datetime.utcnow())
        return {"ok": False, "error": str(e)}


register_task("extract_questions_regen_v3", _extract_questions_regen_v3)
register_task("retry_regen_section_v3", _retry_regen_section_v3)
