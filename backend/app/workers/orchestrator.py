"""Post-schema extraction orchestrator.

State-driven Celery task that drives a book's post-schema lifecycle:

    schema done → theory → finalized → (questions + figures in parallel)
    → all terminal → ready/partial/failed

Idempotent — safe to dispatch multiple times. Each invocation:
  1. Acquires an atomic lock on the book row (10 min timeout)
  2. Reads current state (per-stage status fields)
  3. Decides the NEXT transition (one step)
  4. Dispatches the relevant worker(s) for that transition
  5. Releases the lock

Re-entrant — when a worker completes its work, it dispatches
coordinate_extraction again to step the state machine forward.

Pure logic — no LLM calls. Decides which worker to fire based on
state in DB. Workers do the actual Gemini work.

Replaces the fragile frontend-driven orchestration in
extractionPipeline.ts where every 2-second poll could miss state
transitions, /approve had no in-flight guard, and embedders ran twice
because theory_status="done" was committed BEFORE example_linker +
figure_embedder finished their tail work.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.book import Book
from app.models.job import Job
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# Sync SQLAlchemy session for Celery tasks (mirror of the pattern in
# workers/extract.py). The shared async session in app.core.db isn't
# usable inside Celery's synchronous task functions.
_sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)


# Coordinator lock timeout. Watchdog should detect stale workers via
# heartbeat (5 min) and clean up Job rows; this lock auto-releases
# 5 min later as a safety net.
LOCK_TIMEOUT_MIN = 10


# Status sets — one source of truth.
_PENDING = "pending"
_RUNNING = "running"
_DONE = "done"
_FAILED = "failed"
_PARTIAL = "partial"
_TERMINAL = frozenset({_DONE, _FAILED, _PARTIAL})


# ─── State machine ────────────────────────────────────────────────────


def _decide_next_action(book: Book) -> str:
    """Decide the next single transition based on current book state.

    Returns one of:
      "dispatch_theory"     — schema done, theory pending
      "dispatch_questions"  — theory finalized, questions pending (alone)
      "dispatch_figures"    — theory finalized, figures pending (alone)
      "dispatch_both"       — theory finalized, both pending
      "finalize"            — all stages terminal, set book.status final
      "no_action"           — waiting for in-flight work to complete
    """
    # Don't act until schema is fully done
    if book.schema_status != _DONE:
        return "no_action"

    # Phase A — theory pending → dispatch
    if book.theory_status == _PENDING:
        return "dispatch_theory"

    # Phase B — theory still running → wait
    if book.theory_status == _RUNNING:
        return "no_action"

    # Phase C — theory failed → no Q/Fig point, finalize
    if book.theory_status == _FAILED:
        return "finalize"

    # Phase D — theory done but tail (linker+embedder) not yet finished
    if book.theory_status == _DONE and book.theory_finalized_at is None:
        return "no_action"

    # Phase E — theory finalized → decide on Q + Fig
    if book.theory_finalized_at is not None:
        q_pending = book.questions_status == _PENDING
        f_pending = book.figures_status == _PENDING

        if q_pending and f_pending:
            return "dispatch_both"
        if q_pending:
            return "dispatch_questions"
        if f_pending:
            return "dispatch_figures"

        # Neither pending — both running or terminal
        if (book.questions_status in _TERMINAL
                and book.figures_status in _TERMINAL):
            return "finalize"

    return "no_action"


# ─── Lock primitives (atomic via UPDATE WHERE) ───────────────────────


def _try_acquire_lock(session, book_uuid: UUID) -> bool:
    """Atomically acquire the orchestrator lock for this book.

    Returns True if we got the lock (and the row's extraction_lock_at
    is now updated). False if another coordinator already holds it
    AND the lock is still fresh.

    Uses an atomic UPDATE with WHERE so concurrent racing coordinators
    will see exactly one succeed. The other sees rowcount=0 and exits.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=LOCK_TIMEOUT_MIN)
    result = session.execute(
        sa.update(Book)
        .where(Book.id == book_uuid)
        .where(sa.or_(
            Book.extraction_lock_at.is_(None),
            Book.extraction_lock_at < cutoff,
        ))
        .values(extraction_lock_at=datetime.utcnow())
    )
    session.commit()
    return result.rowcount > 0


def _release_lock(session, book_uuid: UUID) -> None:
    """Release the orchestrator lock. Idempotent."""
    session.execute(
        sa.update(Book)
        .where(Book.id == book_uuid)
        .values(extraction_lock_at=None)
    )
    session.commit()


# ─── Dispatchers (create Job rows + dispatch worker tasks) ───────────


def _new_job(session, book_uuid: UUID, job_type: str) -> UUID:
    """Create a Job row, commit, return its UUID."""
    job = Job(book_id=book_uuid, type=job_type, status="queued", progress=0)
    session.add(job)
    session.flush()
    job_id = job.id
    session.commit()
    return job_id


def _dispatch_theory(session, book: Book) -> None:
    job_id = _new_job(session, book.id, "extract")
    book.status = "extracting"
    session.commit()
    from app.workers.runner import dispatch
    dispatch("extract_book", str(book.id), str(job_id))
    logger.info(
        "orchestrator: dispatched extract_book for book=%s job=%s",
        book.id, job_id,
    )


def _dispatch_questions(session, book: Book) -> None:
    job_id = _new_job(session, book.id, "extract_questions")
    book.status = "extracting"
    session.commit()
    from app.workers.runner import dispatch
    dispatch("extract_questions_v3", str(book.id), str(job_id))
    logger.info(
        "orchestrator: dispatched extract_questions_v3 for book=%s job=%s",
        book.id, job_id,
    )


def _dispatch_figures(session, book: Book) -> None:
    job_id = _new_job(session, book.id, "extract_figures")
    book.status = "extracting"
    session.commit()
    from app.workers.runner import dispatch
    dispatch("extract_figures_v2", str(book.id), str(job_id))
    logger.info(
        "orchestrator: dispatched extract_figures_v2 for book=%s job=%s",
        book.id, job_id,
    )


def _finalize(session, book: Book) -> None:
    """Derive the terminal book.status from per-stage fields and commit."""
    from app.services.book_status import derive_book_status
    book.status = derive_book_status(book)
    session.commit()
    logger.info(
        "orchestrator: finalized book=%s status=%s "
        "(theory=%s questions=%s figures=%s)",
        book.id, book.status, book.theory_status,
        book.questions_status, book.figures_status,
    )


# ─── Public task ──────────────────────────────────────────────────────


@celery_app.task(name="coordinate_extraction", bind=True)
def coordinate_extraction_task(self, book_id: str) -> dict:
    """Step the post-schema extraction state machine forward by ONE transition.

    Idempotent. Safe to dispatch repeatedly. Worker tails of
    extract_book, extract_questions_v3, extract_figures_v2 re-dispatch
    this task at their completion to step the state forward.

    No LLM calls. Pure DB state inspection + Celery dispatch.
    """
    try:
        book_uuid = UUID(book_id)
    except (ValueError, TypeError):
        logger.warning("orchestrator: invalid book_id %r", book_id)
        return {"ok": False, "reason": "invalid_book_id"}

    with SyncSession() as session:
        # Atomic lock acquisition
        if not _try_acquire_lock(session, book_uuid):
            logger.info(
                "orchestrator: book %s lock held by another coordinator, exiting",
                book_id,
            )
            return {"ok": True, "reason": "lock_held"}

        try:
            book = session.get(Book, book_uuid)
            if book is None:
                logger.warning("orchestrator: book %s not found", book_id)
                return {"ok": False, "reason": "book_not_found"}

            action = _decide_next_action(book)
            logger.info(
                "orchestrator: book=%s schema=%s theory=%s(finalized=%s) "
                "questions=%s figures=%s → action=%s",
                book.id, book.schema_status, book.theory_status,
                book.theory_finalized_at is not None,
                book.questions_status, book.figures_status, action,
            )

            if action == "dispatch_theory":
                _dispatch_theory(session, book)
            elif action == "dispatch_questions":
                _dispatch_questions(session, book)
            elif action == "dispatch_figures":
                _dispatch_figures(session, book)
            elif action == "dispatch_both":
                _dispatch_questions(session, book)
                _dispatch_figures(session, book)
            elif action == "finalize":
                _finalize(session, book)
            # action == "no_action" → no-op

            return {"ok": True, "action": action}
        except Exception as e:
            logger.exception("orchestrator: book=%s crashed: %s", book_id, e)
            return {"ok": False, "reason": "exception", "error": str(e)[:200]}
        finally:
            # Always release the lock so retries can proceed
            _release_lock(session, book_uuid)
