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
from app.core.gemini_runtime import (
    call_gemini_text_only,
    call_gemini_text_with_images,
)
from app.core.heartbeat import Heartbeat
from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.question_regeneration import QuestionRegeneration
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json
from app.workers.celery_app import celery_app
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

# Completeness contract for question regen — every source question MUST get
# at least one variant. A single Gemini call sometimes returns empty (esp.
# multimodal/figure questions) or transiently fails; without a retry the
# source was silently skipped (0 variants), and a section-reseed even
# wiped the existing variants first, leaving the section blank. Bounded
# retry per source closes both gaps. Mirrors the extraction-side Pass-3
# philosophy: retry empties, never silently drop. Cost is bounded — extra
# calls only fire on sources that came back empty the first time.
_REGEN_SOURCE_MAX_ATTEMPTS = 3
_REGEN_SOURCE_BACKOFF_S = (1.0, 2.0)  # waits between attempts 1→2, 2→3

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


def _load_source_image_bytes(
    session: Session,
    book_id: UUID,
    question_id: UUID,
) -> list[tuple[bytes, str]]:
    """Phase 4 — fetch image bytes for any figures attached to this
    question via figure_references. Returns [(bytes, mime), ...]. Empty
    when no images attached or none have stored bytes.

    Variant choice mirrors the embedder's rule: regen variant if approved,
    else original. Only includes references with placement_kind != hidden
    and placement_kind != unattached.
    """
    refs = (
        session.execute(
            select(FigureReference)
            .where(FigureReference.book_id == book_id)
            .where(FigureReference.question_id == question_id)
            .where(FigureReference.context == "question")
            .where(FigureReference.is_hidden.is_(False))
            .where(FigureReference.placement_kind != "unattached")
        )
        .scalars()
        .all()
    )
    if not refs:
        return []
    fig_ids = {r.figure_id for r in refs}
    figs = (
        session.execute(select(Figure).where(Figure.id.in_(fig_ids)))
        .scalars()
        .all()
    )
    out: list[tuple[bytes, str]] = []
    for f in figs:
        data = (
            f.regen_image_bytes
            if (f.regen_image_bytes and f.approved_at is not None)
            else f.image_bytes
        )
        if not data:
            continue
        mime = f.mime_type or "image/png"
        out.append((data, mime))
    return out


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
    image_bytes_list: list[tuple[bytes, str]] | None = None,
    image_addendum_prompt: str | None = None,
) -> dict[str, Any]:
    """Single Gemini call to regenerate `count` variants from `source`.

    PHASE 4 — multimodal branch:
      When ``image_bytes_list`` is non-empty AND ``settings.MULTIMODAL_REGEN_ENABLED``
      is True, the call goes through ``call_gemini_text_with_images`` on Pro
      with the image_addendum appended to the system prompt. Each returned
      regen item then carries ``image_needs_regen`` + ``image_regen_reason``
      fields used downstream to optionally chain a figure regeneration.

      Otherwise the existing text-only Flash path runs (unchanged behaviour).

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

    # Decide path
    use_multimodal = bool(
        image_bytes_list
        and settings.MULTIMODAL_REGEN_ENABLED
    )

    # Bounded retry: a source MUST yield ≥1 variant. Retry on transient
    # failure OR a valid-but-empty response (0 usable items). Only after
    # exhausting attempts do we report failure — at which point the caller
    # records a visible gap in stats (never a silent drop).
    last_err = ""
    for attempt in range(1, _REGEN_SOURCE_MAX_ATTEMPTS + 1):
        try:
            if use_multimodal:
                # Append the image-addendum rules to the standard system prompt
                full_system = system_prompt
                if image_addendum_prompt:
                    full_system = system_prompt + "\n\n" + image_addendum_prompt
                raw = await asyncio.to_thread(
                    call_gemini_text_with_images,
                    system_prompt=full_system,
                    user_prompt=user_prompt,
                    image_bytes_list=image_bytes_list,
                    # Pro for multimodal — better visual reasoning than Flash
                    model="gemini-2.5-pro",
                    timeout_s=GEMINI_TIMEOUT_S,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0.4,
                )
            else:
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
                last_err = "response was not a JSON object"
            else:
                items = list(data.get("regenerated") or [])
                notes = str(data.get("notes") or "")
                # Keep only items that have a non-empty question string.
                items = [it for it in items if isinstance(it, dict)
                         and (it.get("question") or "").strip()]
                if items:
                    # Normalise multimodal-only fields so they always exist.
                    for it in items:
                        if "image_needs_regen" not in it:
                            it["image_needs_regen"] = False
                        if "image_regen_reason" not in it:
                            it["image_regen_reason"] = ""
                    if attempt > 1:
                        logger.info(
                            "regen-v3 source q=%s recovered on attempt %d",
                            source.id, attempt,
                        )
                    return {"ok": True, "items": items, "notes": notes, "error": ""}
                # Valid JSON but zero usable items → retry.
                last_err = "empty result (0 usable variants)"
        except Exception as e:
            last_err = str(e)
            logger.warning(
                "regen-v3 single-source call failed (q=%s, multimodal=%s, "
                "attempt=%d/%d): %s",
                source.id, use_multimodal, attempt,
                _REGEN_SOURCE_MAX_ATTEMPTS, e,
            )
        # Backoff before the next attempt (skip after the final one).
        if attempt < _REGEN_SOURCE_MAX_ATTEMPTS:
            await asyncio.sleep(
                _REGEN_SOURCE_BACKOFF_S[
                    min(attempt - 1, len(_REGEN_SOURCE_BACKOFF_S) - 1)
                ]
            )

    logger.warning(
        "regen-v3 source q=%s produced NO variants after %d attempts: %s",
        source.id, _REGEN_SOURCE_MAX_ATTEMPTS, last_err,
    )
    return {"ok": False, "items": [], "notes": "",
            "error": f"no variants after {_REGEN_SOURCE_MAX_ATTEMPTS} attempts: {last_err}"}


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

    PHASE 4 — multimodal regen:
      Each regen question INHERITS figure_references from the source
      (same images attached at same offsets). When item carries
      ``image_needs_regen=true``, the verdict + reason is also stored
      under qc_local["image_regen"] so the frontend can surface a
      "⚠ Figure needs regen" hint on that variant.
    """
    # Pre-load source figure_references once — we'll copy them per regen row
    source_refs = (
        session.execute(
            select(FigureReference)
            .where(FigureReference.question_id == source.id)
            .where(FigureReference.context == "question")
        )
        .scalars()
        .all()
    )

    from app.services.question_latex_normalizer import normalize_question_latex
    from app.workers.questions_v3 import _finalize_solution_flag

    inserted = 0
    for it in items:
        text = (it.get("question") or "").strip()
        if not text:
            continue
        answer = (it.get("answer") or "").strip() or None
        # Structure-mirroring (prompt Rule 8) — see comments above
        solution = (it.get("solution") or "").strip() or None
        solution_text = solution or answer
        # Q5: regenerated text is fresh Gemini output — normalize LaTeX/
        # chemistry so the variant renders the same as extracted questions.
        # Without this, regenerated questions show raw $...$ / Unicode.
        text, _ = normalize_question_latex(text)
        if solution_text:
            solution_text, _ = normalize_question_latex(solution_text)
        # A variant only carries a solution if the SOURCE question did.
        if not source.has_solution:
            solution_text = None
        # Q1 invariant: solution_text + has_solution finalized in lockstep.
        solution_text, has_solution = _finalize_solution_flag(solution_text)
        model_says_options = bool(it.get("options"))
        has_options = model_says_options and bool(source.has_options)
        q_type = (it.get("question_type") or source.question_type or "").strip() or None
        kind = _map_question_type_to_kind(q_type)

        # Phase 4 — capture multimodal verdict in qc_local
        qc_local: dict[str, Any] = {
            "pass": True, "score": 1.0, "failures": [],
        }
        if it.get("image_needs_regen") is True:
            qc_local["image_regen"] = {
                "needed": True,
                "reason": (it.get("image_regen_reason") or "").strip(),
            }

        row = Question(
            bank_id=regen.bank_id,
            book_id=regen.book_id,
            regen_id=regen.id,
            source_question_id=source.id,
            section_ref=source.section_ref,
            section_uuid=source.section_uuid,
            section_title=source.section_title,
            page_start=source.page_start,
            page_end=source.page_end,
            raw_text=text,
            qc_local=qc_local,
            attempts=1,
            status="passed",
            question_number=None,
            exercise_ref=source.exercise_ref,
            chapter_ref=source.chapter_ref,
            kind=kind,
            question_type=q_type,
            has_options=has_options,
            solution_text=solution_text,
            has_solution=has_solution,
            identified_total=None,
            qc_status="pending",
        )
        session.add(row)
        session.flush()  # need row.id for figure_references copy

        # Inherit figure_references from source (same images, new
        # question_id). The Composer / Final view will see them attached
        # via the regen question's id. If image_needs_regen=true, the
        # frontend uses qc_local.image_regen to show a hint; the user can
        # manually trigger figure regen via the existing 🔁 Regenerate
        # touchpoint on the Figures page.
        for sref in source_refs:
            new_ref = FigureReference(
                book_id=sref.book_id,
                figure_id=sref.figure_id,
                section_ref=sref.section_ref,
                section_uuid=sref.section_uuid,
                context="question",
                question_id=row.id,
                placeholder_text=sref.placeholder_text,
                link_method="auto",
                placement_kind=sref.placement_kind,
                placement_block_idx=sref.placement_block_idx,
                placement_char_offset=sref.placement_char_offset,
            )
            session.add(new_ref)

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
        book_id_snapshot = regen.book_id
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
        # Phase 4 — optional multimodal addendum prompt. Loaded once here
        # and conditionally appended per source by _regen_one_source when
        # the source has attached images AND MULTIMODAL_REGEN_ENABLED.
        try:
            image_addendum_prompt: str | None = load_raw(
                "question_regenerator_v3_image_addendum"
            )
        except Exception:
            image_addendum_prompt = None

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
            src_book_id = source.book_id
            # Phase 4 — load image bytes if multimodal feature is on. Cheap
            # if the question has no attached figures (empty list).
            if settings.MULTIMODAL_REGEN_ENABLED:
                image_bytes_list = _load_source_image_bytes(
                    own, src_book_id, qid,
                )
            else:
                image_bytes_list = []
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
            image_bytes_list=image_bytes_list or None,
            image_addendum_prompt=image_addendum_prompt,
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

    # Post-regen embedder pass: regenerated variants get fresh
    # FigureReference rows materialized against their own raw_text +
    # solution_text. Without this, variants that reference a figure only
    # in their solution would never have it attached, so the
    # image_regen_hint badge wouldn't surface on the Final/Preview cards.
    try:
        from app.services.figure_embedder import embed_figures_for_book_sync
        with SyncSession() as own:
            embed_counters = embed_figures_for_book_sync(own, book_id_snapshot)
            logger.info("post-regen embed: %s", embed_counters)
    except Exception as e:
        logger.warning("post-regen embed failed: %s", e)

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
    section_custom_instructions: str | None = None,
) -> dict[str, Any]:
    """Re-run regeneration for ONE section.

    If `section_custom_instructions` is provided, it OVERRIDES the regen's
    persisted custom_instructions for this single retry — the regen record
    itself is NOT mutated. This is how the UI's "Reseed this section" dialog
    layers per-section instructions on top of the broader regen params.
    """
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
        # Section-level instructions OVERRIDE the regen's persisted custom
        # instructions for this single retry. The regen record stays clean.
        if section_custom_instructions and section_custom_instructions.strip():
            custom_instructions = section_custom_instructions.strip()
        else:
            custom_instructions = (regen.custom_instructions or "").strip() or None
        subject = bank.subject or None
        grade = getattr(book, "grade_level", None) or None
        board = getattr(book, "board", None) or None
        bank_id = bank.id
        book_id_snapshot = regen.book_id

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
        try:
            image_addendum_prompt: str | None = load_raw(
                "question_regenerator_v3_image_addendum"
            )
        except Exception:
            image_addendum_prompt = None
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
            src_book_id = source.book_id
            if settings.MULTIMODAL_REGEN_ENABLED:
                image_bytes_list = _load_source_image_bytes(
                    own, src_book_id, qid,
                )
            else:
                image_bytes_list = []

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
            image_bytes_list=image_bytes_list or None,
            image_addendum_prompt=image_addendum_prompt,
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

    # Post-regen embedder pass — mirror the full-regen path so section
    # retries also get figures attached to newly created variants.
    try:
        from app.services.figure_embedder import embed_figures_for_book_sync
        with SyncSession() as own:
            embed_counters = embed_figures_for_book_sync(own, book_id_snapshot)
            logger.info("post-section-regen embed: %s", embed_counters)
    except Exception as e:
        logger.warning("post-section-regen embed failed: %s", e)

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
    regen_id: str,
    section_ref: str,
    job_id: str,
    section_custom_instructions: str | None = None,
) -> dict[str, Any]:
    regen_uuid = UUID(regen_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(
            _run_regen_one_section_v3(
                regen_uuid,
                section_ref,
                job_uuid,
                section_custom_instructions=section_custom_instructions,
            )
        )
    except Exception as e:
        logger.exception("retry_regen_section_v3 failed")
        with SyncSession() as session:
            _update_job(session, job_uuid,
                        status="failed",
                        error=str(e)[:2000],
                        finished_at=datetime.utcnow())
        return {"ok": False, "error": str(e)}


# Celery-mode task wrappers. Inline path uses the underscore functions via
# register_task; Celery path uses these wrappers. Behavior identical.
@celery_app.task(name="extract_questions_regen_v3", bind=True)
def extract_questions_regen_v3_task(self, regen_id: str, job_id: str) -> dict[str, Any]:
    return _extract_questions_regen_v3(regen_id, job_id)


@celery_app.task(name="retry_regen_section_v3", bind=True)
def retry_regen_section_v3_task(
    self,
    regen_id: str,
    section_ref: str,
    job_id: str,
    section_custom_instructions: str | None = None,
) -> dict[str, Any]:
    return _retry_regen_section_v3(
        regen_id, section_ref, job_id, section_custom_instructions
    )


register_task("extract_questions_regen_v3", _extract_questions_regen_v3)
register_task("retry_regen_section_v3", _retry_regen_section_v3)
