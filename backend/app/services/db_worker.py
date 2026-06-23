"""v3 db_worker — Phase 3: the actual polling worker that replaces Celery.

This is where the "DB-polled worker" architecture comes alive. Phase 3 wires
the coordinator (Phase 1) and stage_runner (Phase 2) into a real loop that:

  1. Every POLL_INTERVAL_S seconds, scans Postgres for in-flight books.
  2. Builds a BookSnapshot from each book row + its job heartbeats.
  3. Asks coordinator.next_action(snap) what to do next.
  4. For dispatch actions, atomically claims the stage (PENDING -> RUNNING
     via DB CAS) and runs the extraction in a thread pool.
  5. For lifecycle actions (FINALIZE, RECOVER_STALE, ESCALATE_UNKNOWN),
     updates DB state directly — no broker, no message indirection.

Production impact: STILL ZERO. This module is fully written and tested but
nothing in main.py imports it yet. Phase 5 will add a USE_DB_WORKER feature
flag and wire it into the FastAPI lifespan; until then v2's Celery path
remains the only thing actually running work.

Why this replaces ~1500 lines of v2 coordination code:
  • Single source of truth — the DB. No broker queue, no in-memory orch
    state, no Celery task registry to keep in sync.
  • Single source of decision — coordinator.next_action(). Provably correct
    via the 2401-combo truth-table test (Phase 1).
  • Single source of work — stage_runner. Already battle-tested via v2's
    inline-mode codepath (Phase 2).
  • Recovery is automatic — a stale-heartbeat stage gets reset on the next
    tick; a redelivered task is impossible (no broker = no redelivery).

Concurrency model:
  • One asyncio loop (the poll loop) — single-threaded scheduler.
  • One ThreadPoolExecutor (WORKER_CONCURRENCY, default 5) — runs stages.
  • Per-book in-flight tracking (a Python set guarded by a lock) prevents
    this worker from double-dispatching the same book within a tick. The
    DB CAS in _claim_stage is the cross-worker safety net.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.book import Book
from app.models.job import Job
from app.services import stage_runner
from app.services.coordinator import (
    Action,
    BOOK_INFLIGHT,
    BookSnapshot,
    PENDING,
    RUNNING,
    derive_terminal_book_status,
    next_action,
)

logger = logging.getLogger(__name__)


# ─── Configuration knobs (env-driven) ──────────────────────────────────


POLL_INTERVAL_S: float = float(os.getenv("DB_WORKER_POLL_INTERVAL_S", "2.0"))
WORKER_CONCURRENCY: int = int(os.getenv("WORKER_CONCURRENCY", "5"))
STALE_HEARTBEAT_AFTER_S: int = int(os.getenv("STALE_HEARTBEAT_AFTER_S", "120"))

# Stage -> Job.type mapping. Keeps v2's naming so existing tools (logs, the
# /api/jobs endpoint, the Job-by-id heartbeat lookup) work unchanged.
_JOB_TYPE_FOR_STAGE: dict[str, str] = {
    "schema_status":    "analyse",
    "theory_status":    "extract",
    "questions_status": "extract_questions",
    "figures_status":   "extract_figures",
}


# ─── Module-level DB session + thread-pool state ───────────────────────


_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_pre_ping=True,
    # Small bounded pool — the worker is single-process; concurrency comes
    # from the executor, not from a giant pool.
    pool_size=5,
    max_overflow=5,
    pool_timeout=20,
    pool_recycle=900,
)
WorkerSession = sessionmaker(bind=_engine, class_=Session, autoflush=False)

_executor: ThreadPoolExecutor | None = None

# Per-process tracking: which book_ids this worker has currently scheduled in
# the thread-pool. Prevents the same tick from double-dispatching a book in
# the brief window between deciding to dispatch and the executor starting.
# The DB CAS in _claim_stage is the authoritative cross-worker safety net;
# this set is just a fast-path optimization.
_in_flight_books: set[UUID] = set()
_in_flight_lock = threading.Lock()


# ─── Public entry points ────────────────────────────────────────────────


async def db_worker_loop(stop_event: asyncio.Event) -> None:
    """Main poll loop. Runs until stop_event is set.

    Should be started from the FastAPI lifespan (Phase 5) like:

        @asynccontextmanager
        async def lifespan(app):
            if settings.USE_DB_WORKER:
                stop = asyncio.Event()
                task = asyncio.create_task(db_worker_loop(stop))
                yield
                stop.set()
                await task
            else:
                yield
    """
    _ensure_executor()
    stage_runner.assert_registry_complete()
    logger.info(
        "db_worker started: poll=%ss concurrency=%d stale_after=%ss",
        POLL_INTERVAL_S, WORKER_CONCURRENCY, STALE_HEARTBEAT_AFTER_S,
    )
    try:
        while not stop_event.is_set():
            try:
                n = await asyncio.to_thread(advance_one_tick)
                if n > 0:
                    logger.debug("db_worker: dispatched %d action(s) this tick", n)
            except Exception:
                # Single-tick failures must not kill the loop.
                logger.exception("db_worker tick raised")
            # Wait POLL_INTERVAL_S, but wake immediately if stop_event fires.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("db_worker stopping…")
        _shutdown_executor()
        logger.info("db_worker stopped")


def advance_one_tick() -> int:
    """One pass: find in-flight books, decide + dispatch. Sync, runs in
    asyncio.to_thread from the poll loop. Returns the number of actions
    actually dispatched (for logging)."""
    book_ids = _list_inflight_book_ids()
    if not book_ids:
        return 0

    dispatched = 0
    for book_id in book_ids:
        # Fast-path: skip books this worker already has running in the pool.
        with _in_flight_lock:
            if book_id in _in_flight_books:
                continue

        snap = _build_snapshot_for(book_id)
        if snap is None:
            continue  # book disappeared between list + load

        try:
            action = next_action(snap)
        except Exception:
            logger.exception(
                "coordinator failed for book=%s — escalating", book_id,
            )
            action = Action.ESCALATE_UNKNOWN

        try:
            if _handle_action(book_id, action):
                dispatched += 1
        except Exception:
            logger.exception(
                "_handle_action failed: book=%s action=%s", book_id, action,
            )

    return dispatched


# ─── Snapshot building ─────────────────────────────────────────────────


def _list_inflight_book_ids() -> list[UUID]:
    """All book IDs in a non-terminal status. Single quick query per tick."""
    with WorkerSession() as session:
        rows = session.execute(
            select(Book.id).where(Book.status.in_(BOOK_INFLIGHT))
        ).scalars().all()
    return list(rows)


def _build_snapshot_for(book_id: UUID) -> BookSnapshot | None:
    """Load a book + its heartbeat-stale flag, return a coordinator snapshot.

    Returns None if the book disappeared (deleted between list and load).
    """
    with WorkerSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            return None
        heartbeat_stale = _has_stale_running_job(session, book_id)
        return BookSnapshot(
            book_status=book.status,
            schema_status=book.schema_status,
            theory_status=book.theory_status,
            questions_status=book.questions_status,
            figures_status=book.figures_status,
            theory_finalized_at_is_set=book.theory_finalized_at is not None,
            stage_heartbeat_stale=heartbeat_stale,
        )


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC-aware. SQLite returns naive datetimes
    (loses the DateTime(timezone=True) info on round-trip) — Postgres
    returns aware. Comparison between aware + naive raises in Python.
    This helper makes comparisons portable across both backends.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _has_stale_running_job(session: Session, book_id: UUID) -> bool:
    """True iff at least one RUNNING job for this book hasn't heartbeat
    within STALE_HEARTBEAT_AFTER_S — i.e. worker process is presumed dead."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_HEARTBEAT_AFTER_S)
    rows = session.execute(
        select(Job.last_heartbeat_at).where(
            Job.book_id == book_id,
            Job.status == "running",
        )
    ).scalars().all()
    return any(
        (_as_utc(hb) is not None and _as_utc(hb) < cutoff)  # type: ignore[operator]
        for hb in rows
    )


# ─── Cancellation helpers (Phase 4) ─────────────────────────────────────


def _book_is_cancelled(book_id: UUID) -> bool:
    """Quick check: has the book been cancelled?

    v3 cancellation flow:
      1. /api/jobs/cancel-all (or /books/<id>/cancel) sets book.status='cancelled'
         + flips any RUNNING stage to 'cancelled'.
      2. This worker checks book.status at the start of every queued stage
         (after claim, before execution) — skips the stage if cancelled, so a
         queued-then-cancelled book doesn't waste a Gemini call.
      3. After the cancel, the coordinator sees book.status in BOOK_TERMINAL
         and returns NOTHING — no more dispatches.
    """
    with WorkerSession() as session:
        book = session.get(Book, book_id)
        return book is not None and book.status == "cancelled"


def _mark_stage_cancelled(
    book_id: UUID, stage_attr: str, job_id: UUID,
) -> None:
    """Move a freshly-claimed stage from RUNNING -> 'cancelled' (no actual
    extraction done). Also marks the corresponding Job as cancelled with a
    diagnostic so the audit trail shows what happened.
    """
    with WorkerSession() as session:
        book = session.get(Book, book_id)
        if book is not None and getattr(book, stage_attr) == RUNNING:
            setattr(book, stage_attr, "cancelled")
        job = session.get(Job, job_id)
        if job is not None and job.status == "running":
            job.status = "cancelled"
            job.error = "Cancelled before execution (book cancellation)"
            job.finished_at = datetime.now(timezone.utc)
        session.commit()


# ─── Action dispatcher ─────────────────────────────────────────────────


def _handle_action(book_id: UUID, action: Action) -> bool:
    """Execute one Action. Returns True if the action did something (for tick
    accounting). Pure dispatch — all real work lives in the per-action helpers
    below."""
    if action == Action.NOTHING or action == Action.WAIT:
        return False
    if action == Action.RECOVER_STALE:
        _recover_stale(book_id)
        return True
    if action == Action.FINALIZE:
        _finalize(book_id)
        return True
    if action == Action.ESCALATE_UNKNOWN:
        _escalate_unknown(book_id)
        return True
    if action == Action.DISPATCH_SCHEMA:
        return _schedule_stage(book_id, "schema_status", _run_schema)
    if action == Action.DISPATCH_THEORY:
        return _schedule_stage(book_id, "theory_status", _run_theory)
    if action == Action.DISPATCH_QUESTIONS:
        return _schedule_stage(book_id, "questions_status", _run_questions)
    if action == Action.DISPATCH_FIGURES:
        return _schedule_stage(book_id, "figures_status", _run_figures)
    logger.error("db_worker: unhandled Action %r for book=%s", action, book_id)
    return False


# ─── Stage claiming (atomic PENDING -> RUNNING) ────────────────────────


def _claim_stage(
    session: Session, book_id: UUID, stage_attr: str,
) -> Job | None:
    """Atomically transition `<stage>_status` from PENDING to RUNNING and
    create a Job row. Returns the Job if claimed, None if another worker
    already moved the stage (CAS lost).

    The CAS is the cross-worker safety net — even if multiple worker
    processes ran the poll loop simultaneously (we don't today, but the
    architecture supports it), only one would win the claim.
    """
    column = getattr(Book, stage_attr)
    result = session.execute(
        sa.update(Book)
        .where(Book.id == book_id)
        .where(column == PENDING)
        .values({stage_attr: RUNNING})
    )
    if result.rowcount == 0:
        # CAS lost (status wasn't PENDING anymore) — skip.
        session.rollback()
        return None

    now = datetime.now(timezone.utc)
    job = Job(
        book_id=book_id,
        type=_JOB_TYPE_FOR_STAGE[stage_attr],
        status="running",
        progress=0,
        started_at=now,
        last_heartbeat_at=now,
    )
    session.add(job)
    session.flush()  # populate job.id

    # Move book.status forward so the UI reflects the active stage. (Mirrors
    # v2's orchestrator behavior: 'analysing' for schema, 'extracting' for
    # theory/questions/figures.)
    book = session.get(Book, book_id)
    if book is not None:
        book.status = "analysing" if stage_attr == "schema_status" else "extracting"

    session.commit()
    return job


def _schedule_stage(
    book_id: UUID, stage_attr: str, runner: Callable[[UUID, UUID], None],
) -> bool:
    """Claim the stage in DB, then submit the runner to the thread pool."""
    with WorkerSession() as session:
        job = _claim_stage(session, book_id, stage_attr)
        if job is None:
            # Another worker (or this worker's previous tick) claimed it.
            return False
        job_id = job.id

    # Add to in-flight set BEFORE submitting so the next tick skips this book.
    with _in_flight_lock:
        _in_flight_books.add(book_id)

    def _wrapped() -> None:
        # Pre-execution cancellation check: between the claim above and this
        # function actually starting on a worker thread, the user may have
        # cancelled the book. Honor that intent before burning a (potentially
        # 30-second) Gemini call on work the user no longer wants.
        # (Cancellation during the actual stage run isn't interruptible in
        # Python — no thread.kill() — but the book remains 'cancelled' at the
        # book-status level, so the coordinator stops driving it on the next
        # tick and the partial stage result is harmless.)
        if _book_is_cancelled(book_id):
            logger.info(
                "db_worker: skipping stage — book cancelled mid-claim "
                "book=%s stage=%s job=%s", book_id, stage_attr, job_id,
            )
            _mark_stage_cancelled(book_id, stage_attr, job_id)
            with _in_flight_lock:
                _in_flight_books.discard(book_id)
            return

        try:
            runner(book_id, job_id)
        except Exception:
            logger.exception(
                "db_worker stage runner crashed: book=%s stage=%s job=%s",
                book_id, stage_attr, job_id,
            )
            # Mark the stage failed so the next tick can finalize the book
            # instead of seeing a stale RUNNING that the reaper would
            # recover into yet another retry.
            try:
                with WorkerSession() as s:
                    b = s.get(Book, book_id)
                    if b is not None and getattr(b, stage_attr) == RUNNING:
                        setattr(b, stage_attr, "failed")
                        s.commit()
            except Exception:
                logger.exception("db_worker: failed to mark stage failed")
        finally:
            with _in_flight_lock:
                _in_flight_books.discard(book_id)

    assert _executor is not None  # _ensure_executor() ran in db_worker_loop
    _executor.submit(_wrapped)
    return True


# ─── Per-stage runners (call stage_runner with the right args) ─────────


def _run_schema(book_id: UUID, job_id: UUID) -> None:
    stage_runner.run_analyse(book_id, job_id)


def _run_theory(book_id: UUID, job_id: UUID) -> None:
    stage_runner.run_theory(book_id, job_id)


def _run_questions(book_id: UUID, job_id: UUID) -> None:
    """Questions needs a QuestionBank row before the extractor runs.
    Mirrors v2's orchestrator._dispatch_questions (incl. superseding old banks).
    """
    from app.models.question_bank import QuestionBank

    with WorkerSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            return

        # Supersede any prior pending/extracting banks (orphans from earlier
        # retries that didn't finish). Keeps the bank list clean.
        session.execute(
            sa.update(QuestionBank)
            .where(QuestionBank.book_id == book_id)
            .where(QuestionBank.status.in_(["pending", "extracting"]))
            .values(
                status="failed",
                last_error="Superseded by db_worker re-dispatch",
            )
        )
        bank = QuestionBank(
            book_id=book_id,
            title=book.title,
            subject=book.subject,
            status="pending",
        )
        session.add(bank)
        session.flush()
        bank_id = bank.id
        session.commit()

    stage_runner.run_questions(book_id, bank_id, job_id)


def _run_figures(book_id: UUID, job_id: UUID) -> None:
    stage_runner.run_figures(book_id, job_id)


# ─── Lifecycle handlers ────────────────────────────────────────────────


def _finalize(book_id: UUID) -> None:
    """All stages terminal — set book.status from coordinator's derivation."""
    with WorkerSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            return
        snap = BookSnapshot(
            book_status=book.status,
            schema_status=book.schema_status,
            theory_status=book.theory_status,
            questions_status=book.questions_status,
            figures_status=book.figures_status,
            theory_finalized_at_is_set=book.theory_finalized_at is not None,
        )
        new_status = derive_terminal_book_status(snap)
        if new_status is None:
            # Coordinator told us to FINALIZE but derive said book isn't
            # terminal — should be impossible given the truth-table tests.
            logger.error(
                "db_worker: FINALIZE on non-terminal book=%s (stages=%s)",
                book_id, snap.stages,
            )
            return
        if book.status != new_status:
            logger.info(
                "db_worker: finalize book=%s %s -> %s (stages=%s)",
                book_id, book.status, new_status, snap.stages,
            )
            book.status = new_status
            session.commit()


def _recover_stale(book_id: UUID) -> None:
    """A RUNNING stage with stale heartbeat means a dead worker. Reset to
    PENDING so the next tick re-dispatches it. (v2's reaper + watchdog
    crash-recovery, simplified into one explicit handler.)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_HEARTBEAT_AFTER_S)
    with WorkerSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            return
        recovered_any = False
        for attr in (
            "schema_status", "theory_status", "questions_status", "figures_status",
        ):
            if getattr(book, attr) != RUNNING:
                continue
            # Find the latest running Job for this stage.
            latest = session.execute(
                select(Job).where(
                    Job.book_id == book_id,
                    Job.type == _JOB_TYPE_FOR_STAGE[attr],
                    Job.status == "running",
                ).order_by(Job.started_at.desc()).limit(1)
            ).scalar_one_or_none()
            hb_utc = _as_utc(latest.last_heartbeat_at) if latest else None
            if latest is not None and hb_utc is not None and hb_utc < cutoff:
                setattr(book, attr, PENDING)
                latest.status = "failed"
                latest.error = (
                    f"Worker died (heartbeat stale > {STALE_HEARTBEAT_AFTER_S}s)"
                )
                latest.finished_at = datetime.now(timezone.utc)
                recovered_any = True
                logger.warning(
                    "db_worker: recovered stale stage book=%s stage=%s",
                    book_id, attr,
                )
        if recovered_any:
            session.commit()


def _escalate_unknown(book_id: UUID) -> None:
    """Unrecognizable status combo — fail the book with a clear diagnostic.

    This is the "no silent stuck" guarantee from Phase 1's coordinator: if
    we ever land in an unexpected state, the book becomes visibly failed
    with the offending status combo in the error message, NOT a hanging
    "processing" with no explanation.
    """
    with WorkerSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            return
        stages = (
            book.schema_status, book.theory_status,
            book.questions_status, book.figures_status,
        )
        diag = (
            f"db_worker ESCALATE: unrecognized stage status combo "
            f"(schema={stages[0]} theory={stages[1]} "
            f"questions={stages[2]} figures={stages[3]})"
        )
        logger.error("db_worker: %s book=%s", diag, book_id)
        if book.status not in ("failed", "cancelled"):
            book.status = "failed"
            session.commit()


# ─── Executor lifecycle ────────────────────────────────────────────────


def _ensure_executor() -> None:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=WORKER_CONCURRENCY,
            thread_name_prefix="db_worker_stage",
        )


def _shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        # cancel_futures=False so in-flight work is allowed to finish — we
        # rely on the heartbeat mechanism to recover anything that truly
        # gets cut off mid-stage.
        _executor.shutdown(wait=True, cancel_futures=False)
        _executor = None


__all__ = [
    "POLL_INTERVAL_S",
    "WORKER_CONCURRENCY",
    "STALE_HEARTBEAT_AFTER_S",
    "WorkerSession",
    "advance_one_tick",
    "db_worker_loop",
    "next_action",  # convenience re-export
]
