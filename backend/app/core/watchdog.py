"""Background task that fails jobs whose worker has stopped responding.

The previous reliability story was: if a worker hangs, the recovery handler
on the NEXT startup will re-dispatch jobs >35 min old. That's too long —
the UI shows a frozen progress bar for half an hour and the user has no
signal that anything is wrong.

The watchdog runs inside the API process, polls every ``CHECK_INTERVAL_S``,
and marks any ``running`` job whose ``last_heartbeat_at`` is older than
``STALE_AFTER_S`` as ``failed``. The error message tells the user clearly
that the job was killed by the watchdog so they can retry.

This is the first line of defence. The startup recovery handler still runs
for the case where the API itself crashed (no in-flight watchdog).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.job import Job

logger = logging.getLogger(__name__)

# How often to check for stale jobs.
CHECK_INTERVAL_S = 60
# A running job is stale if its heartbeat (or started_at, when heartbeat is
# NULL — e.g. a job started before this column existed) is older than this.
# The Gemini timeout is 150s; a heartbeat is written every 10s. 5 minutes is
# 30+ missed heartbeats — a confident "this is stuck", not a slow call.
STALE_AFTER_S = 300


_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
_WatchdogSession = sessionmaker(bind=_engine, class_=Session, autoflush=False)


def _scan_once() -> int:
    """Mark stale running jobs as failed. Returns count killed."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S)
    killed = 0
    with _WatchdogSession() as session:
        rows = session.execute(
            select(Job).where(
                Job.status == "running",
                Job.finished_at.is_(None),
            )
        ).scalars().all()
        for job in rows:
            # Use heartbeat if present; otherwise fall back to started_at.
            # If both are NULL the job is freshly queued — leave it alone.
            ref = job.last_heartbeat_at or job.started_at
            if ref is None:
                continue
            # SQLite stores naive datetimes; force UTC-aware for comparison.
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            if ref >= cutoff:
                continue
            age_s = int((datetime.now(timezone.utc) - ref).total_seconds())
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.error = (
                f"Watchdog: no progress for {age_s}s "
                f"(threshold {STALE_AFTER_S}s). The worker hung or crashed. "
                "Click retry to re-run."
            )
            killed += 1
            logger.warning(
                "watchdog killed stale job %s type=%s age=%ss",
                job.id,
                job.type,
                age_s,
            )
        if killed:
            session.commit()

        # Phase 7 (CONTRACT.md §5): also catch orphan per-stage status.
        # If a Book has stage_status="running" but no live Job exists for
        # that book, the worker died mid-stage (OOM, container restart,
        # process crash). Mark the stage failed so the user can retry,
        # and derive book.status accordingly.
        from app.models.book import Book
        from app.services.book_status import derive_book_status

        book_rows = session.execute(
            select(Book).where(
                (Book.schema_status == "running")
                | (Book.theory_status == "running")
                | (Book.questions_status == "running")
                | (Book.figures_status == "running")
            )
        ).scalars().all()
        for book in book_rows:
            # Does the book still have a live Job?
            has_live_job = session.execute(
                select(Job).where(
                    Job.book_id == book.id,
                    Job.status.in_(["queued", "running"]),
                ).limit(1)
            ).scalars().first()
            if has_live_job is not None:
                continue  # legitimate in-flight
            # No live Job, but stage(s) say "running" → orphan. Fail them.
            for stage in (
                "schema_status", "theory_status",
                "questions_status", "figures_status",
            ):
                if getattr(book, stage) == "running":
                    setattr(book, stage, "failed")
                    logger.warning(
                        "watchdog: orphan running stage book=%s %s "
                        "(no live Job)",
                        book.id, stage,
                    )
                    killed += 1
            book.status = derive_book_status(book)
            session.commit()
    return killed


async def watchdog_loop() -> None:
    """Run forever, polling every CHECK_INTERVAL_S. Cancelled on shutdown."""
    logger.info(
        "watchdog started — interval=%ss stale_after=%ss",
        CHECK_INTERVAL_S,
        STALE_AFTER_S,
    )
    while True:
        try:
            await asyncio.to_thread(_scan_once)
        except Exception:
            logger.exception("watchdog scan failed")
        try:
            await asyncio.sleep(CHECK_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("watchdog cancelled")
            raise
