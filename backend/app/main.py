"""FastAPI entrypoint."""

from __future__ import annotations

import asyncio  # noqa: F401  (used in type hint for _watchdog_task)
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import (
    books,
    figures as figures_v2,
    final_draft,
    final_merge,
    folders,
    jobs,
    providers,
    qa,
    question_banks,
    question_regenerations,
    regenerations,
    sections,
)
from app.core.config import settings

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

import os as _os
if settings.APP_ENV != "production" and not _os.environ.get("UVICORN_RELOAD_ACTIVE"):
    logger.warning(
        "\n"
        "╔══════════════════════════════════════════════════════════════╗\n"
        "║  ⚠️   BACKEND STARTED WITHOUT --reload                       ║\n"
        "║  Code changes will NOT be picked up automatically.           ║\n"
        "║  Always start with:  make dev   (or ./scripts/dev-local.sh)  ║\n"
        "╚══════════════════════════════════════════════════════════════╝"
    )

app = FastAPI(
    title="CMDS Extraction Service",
    version="0.1.0",
    description="Educational PDF extraction, QC, and regeneration",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    import os as _os
    return {
        "status": "ok",
        "env": settings.APP_ENV,
        "anthropic_mode": settings.anthropic_effective_mode,
        "storage_backend": settings.STORAGE_BACKEND,
        "task_executor": settings.TASK_EXECUTOR,
        "hot_reload": bool(_os.environ.get("UVICORN_RELOAD_ACTIVE")),
    }


app.include_router(folders.router)
app.include_router(books.router)
app.include_router(sections.router)
app.include_router(regenerations.router)
app.include_router(jobs.router)
app.include_router(providers.router)
app.include_router(question_banks.books_router)
app.include_router(question_banks.banks_router)
app.include_router(question_regenerations.books_router)
app.include_router(question_regenerations.banks_router)
app.include_router(question_regenerations.regens_router)
app.include_router(qa.router)
# Figures pipeline v2
app.include_router(figures_v2.books_router)
app.include_router(figures_v2.figures_router)
app.include_router(final_merge.router)
app.include_router(final_draft.router)


if settings.STORAGE_BACKEND == "local":
    from app.core.storage import local_root_path

    @app.get("/storage/{path:path}")
    async def serve_local_storage(path: str) -> FileResponse:
        root = local_root_path()
        full = (root / path).resolve()
        if not str(full).startswith(str(root)):
            raise HTTPException(400, detail="Invalid path")
        if not full.exists() or not full.is_file():
            raise HTTPException(404, detail="Not found")
        return FileResponse(full)


# Eager-load worker registrations so inline dispatch has the task table populated.
from app.workers import extract as _extract_tasks  # noqa: E402, F401
from app.workers import questions as _question_tasks  # noqa: E402, F401
from app.workers import questions_v2 as _question_v2_tasks  # noqa: E402, F401
from app.workers import questions_v3 as _question_v3_tasks  # noqa: E402, F401
from app.workers import qa as _qa_tasks  # noqa: E402, F401
from app.workers import figures_tasks as _figures_v2_tasks  # noqa: E402, F401
from app.workers import orchestrator as _orchestrator_tasks  # noqa: E402, F401


_watchdog_task: "asyncio.Task[None] | None" = None


@app.on_event("startup")
async def start_watchdog() -> None:
    """Launch the stale-job watchdog. See app.core.watchdog for behaviour."""
    import asyncio

    from app.core.watchdog import watchdog_loop

    global _watchdog_task
    _watchdog_task = asyncio.create_task(watchdog_loop(), name="watchdog")


@app.on_event("shutdown")
async def stop_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except BaseException:
            pass
        _watchdog_task = None


@app.on_event("startup")
async def run_migrations() -> None:
    """Run alembic migrations on every startup.

    This ensures the DB schema is always in sync with the code — no manual
    'alembic upgrade head' needed after pulling new code or adding tables.
    Safe to run repeatedly (alembic is idempotent — skips already-applied migrations).
    """
    import subprocess
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).parent.parent  # backend/
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error("Alembic migration failed:\n%s\n%s", result.stdout, result.stderr)
        else:
            for line in result.stdout.strip().splitlines():
                if line.strip():
                    logger.info("Migration: %s", line)
            logger.info("DB migrations up to date")
    except Exception as exc:
        logger.error("Could not run migrations: %s", exc)


@app.on_event("startup")
async def recover_orphaned_jobs() -> None:
    """On startup, find jobs stuck at queued/running and re-dispatch them.

    Handles two failure modes:
    - Inline mode: uvicorn restart kills daemon threads → jobs stuck at queued forever.
    - Celery mode: belt-and-suspenders alongside task_acks_late for edge cases.

    Each job is recovered independently — one failure never blocks the others.
    """
    # Allow skipping orphan recovery via env var. Useful when a poisoned
    # orphan job keeps OOM-killing the container in a restart loop —
    # set SKIP_ORPHAN_RECOVERY=1 to let the backend stay up so the
    # orphan can be cleared manually via /api/jobs/cancel-all.
    if _os.environ.get("SKIP_ORPHAN_RECOVERY", "").strip().lower() in ("1", "true", "yes"):
        logger.info("Orphan recovery SKIPPED via SKIP_ORPHAN_RECOVERY env var")
        return

    # Wrap the whole recovery in a try/except so a failure here NEVER
    # crashes the container on startup. A failed orphan recovery just
    # means jobs stay stuck, not that the API dies.
    try:
        await _do_recover_orphaned_jobs()
    except BaseException as exc:
        logger.error("Orphan recovery crashed (continuing startup): %s", exc, exc_info=True)


async def _do_recover_orphaned_jobs() -> None:
    """The actual orphan-recovery logic — separated so it can be wrapped
    in a try/except by the on_event handler above."""
    import os as _os  # noqa
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session, sessionmaker

    from app.models.book import Book
    from app.models.job import Job
    from app.models.regeneration import Regeneration
    from app.workers.runner import dispatch

    sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
    SyncSession = sessionmaker(bind=sync_engine, class_=Session, autoflush=False)

    recovered = 0
    skipped = 0

    with SyncSession() as session:
        from datetime import datetime, timedelta, timezone
        # Recover:
        # 1. Jobs that were queued but never started (started_at IS NULL) — thread died before launch
        # 2. Jobs stuck "running" for more than 35 minutes (past the task time limit) — hard crash
        stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
        orphans = session.execute(
            select(Job).where(
                Job.status.in_(["queued", "running"]),
                Job.finished_at.is_(None),
                Job.book_id.isnot(None),
            ).filter(
                # never started OR running too long (stale)
                (Job.started_at.is_(None)) |
                (Job.started_at < stale_cutoff)
            )
        ).scalars().all()

        if not orphans:
            return

        logger.warning("Found %d orphaned job(s) on startup — recovering...", len(orphans))

        for job in orphans:
            try:
                book = session.get(Book, job.book_id)
                if book is None:
                    job.status = "failed"
                    job.error = "Book no longer exists"
                    session.commit()
                    skipped += 1
                    continue

                # Reset to clean queued state so UI shows it restarted
                job.status = "queued"
                job.progress = 0
                job.error = None
                job.message = "Recovering after server restart..."
                session.commit()

                if job.type == "analyse":
                    book.status = "analysing"
                    session.commit()
                    dispatch("analyse_book", str(book.id), str(job.id))

                elif job.type == "extract":
                    book.status = "extracting"
                    session.commit()
                    dispatch("extract_book", str(book.id), str(job.id))

                elif job.type == "regen":
                    regen = session.execute(
                        select(Regeneration)
                        .where(Regeneration.book_id == book.id)
                        .order_by(Regeneration.created_at.desc())
                    ).scalars().first()
                    if regen is None:
                        job.status = "failed"
                        job.error = "No regeneration row found — cannot recover"
                        session.commit()
                        skipped += 1
                        continue
                    book.status = "regenerating"
                    session.commit()
                    # Extract the stashed section selection (if the original
                    # request targeted specific sections) so recovery reruns
                    # with the same scope.
                    saved_params = dict(regen.params or {})
                    recovered_section_ids = saved_params.pop("_section_ids", None)
                    dispatch(
                        "regenerate_book",
                        str(book.id),
                        str(job.id),
                        str(regen.id),
                        saved_params,
                        recovered_section_ids,
                    )

                elif job.type == "extract_figures":
                    dispatch("extract_figures", str(book.id), str(job.id))

                elif job.type == "extract_figures_v2":
                    # Figures pipeline v2 — new package at app/services/figures
                    dispatch("extract_figures_v2", str(book.id), str(job.id))

                elif job.type == "regenerate_figures_v2_section":
                    # Section-scoped figure regen — we don't have the
                    # original section_ref / params in the Job row alone,
                    # so we skip silent recovery and let the user re-trigger
                    # from the UI (same pattern as extract_questions_v3
                    # retry — the user owns the params).
                    logger.warning(
                        "Skipping recovery of regenerate_figures_v2_section "
                        "job %s — user must re-trigger from UI",
                        job.id,
                    )

                elif job.type == "regen_figures":
                    dispatch("regenerate_figures", str(book.id), str(job.id))

                elif job.type == "extract_questions":
                    dispatch("extract_questions", str(book.id), str(job.id))

                elif job.type == "extract_questions_v2":
                    dispatch("extract_questions_v2", str(book.id), str(job.id))

                elif job.type == "extract_questions_v3":
                    # v3 needs the bank_id which is not on the Job row — skip
                    # recovery and let the user retry from the UI.
                    job.status = "failed"
                    job.error = "Interrupted by server restart — click Re-extract to retry"
                    # Reconcile the QuestionBank status so it doesn't stay
                    # stuck at 'extracting'. A bank stuck at 'extracting' is
                    # invisible to build_final_merge (which filters to
                    # status='ready'), so questions + chip-merge + figure
                    # embedding all silently fail in Composer / Preview.
                    # Strategy: if questions were already written to the DB
                    # before the crash, downgrade bank to 'partial' so the
                    # merge picks it up; if no questions yet, mark 'failed'.
                    from app.models.question import Question
                    from app.models.question_bank import QuestionBank
                    stuck_banks = session.execute(
                        select(QuestionBank)
                        .where(QuestionBank.book_id == book.id)
                        .where(QuestionBank.status == "extracting")
                    ).scalars().all()
                    for bank in stuck_banks:
                        q_count = session.execute(
                            select(Question)
                            .where(Question.bank_id == bank.id)
                            .where(Question.regen_id.is_(None))
                        ).scalars().first()
                        bank.status = "partial" if q_count is not None else "failed"
                    session.commit()
                    skipped += 1
                    continue

                elif job.type == "extract_questions_regen":
                    from app.models.question_regeneration import QuestionRegeneration

                    regen = session.execute(
                        select(QuestionRegeneration).where(
                            QuestionRegeneration.job_id == job.id
                        )
                    ).scalars().first()
                    if regen is None:
                        job.status = "failed"
                        job.error = "Interrupted before regen row was linked — click Regenerate to retry"
                        session.commit()
                        skipped += 1
                        continue
                    dispatch("extract_questions_regen", str(regen.id), str(job.id))

                elif job.type == "run_qa_fidelity":
                    # Bank id is not on the Job row — cannot recover. Mark
                    # failed so the UI surfaces it and the user can retrigger.
                    job.status = "failed"
                    job.error = "Interrupted by server restart — click Run QA to retry"
                    session.commit()
                    skipped += 1
                    continue

                elif job.type == "re_extract_block":
                    # Block re-extract jobs store (bank_id, block_idx) in dispatch
                    # args, not in the Job row — cannot recover. Mark failed so the
                    # UI shows it clearly and the user can click ↺ again.
                    job.status = "failed"
                    job.error = "Interrupted by server restart — click ↺ to retry the block"
                    session.commit()
                    skipped += 1
                    continue

                elif job.type == "re_extract":
                    # Per-section re-extract jobs store the section via dispatch args,
                    # not in the Job row — cannot recover. Mark failed so the UI
                    # shows it clearly and the user can click re-extract again.
                    job.status = "failed"
                    job.error = "Interrupted by server restart — click Re-extract to retry"
                    session.commit()
                    skipped += 1
                    continue

                else:
                    job.status = "failed"
                    job.error = f"Unknown job type '{job.type}' — cannot recover"
                    session.commit()
                    skipped += 1
                    continue

                recovered += 1
                logger.info(
                    "Recovered job %s (type=%s book=%s)", job.id, job.type, book.id
                )

            except Exception as exc:
                logger.exception("Failed to recover job %s: %s", job.id, exc)
                try:
                    job.status = "failed"
                    job.error = f"Recovery error: {exc}"
                    session.commit()
                except Exception:
                    session.rollback()
                skipped += 1

    logger.warning(
        "Job recovery complete — recovered: %d, skipped/failed: %d",
        recovered,
        skipped,
    )
