"""v3 db_worker — SKELETON (Phase 1 of the v3 rewrite).

This file is the home for the polling worker that will replace
Celery + watchdog + orchestrator + reaper. Phase 1 lays out the structure
and interfaces ONLY; nothing here runs in production yet.

Phase plan (each phase = one focused commit on architecture-v3):
  1. Scaffold (THIS COMMIT)               — file structure + interfaces
  2. Port stage dispatchers               — make each extraction directly callable
  3. Poll loop + claim/lease semantics    — actual worker behavior
  4. Cancellation via book.status check   — replace revoke()
  5. Cutover via USE_DB_WORKER flag       — switch start.sh
  6. Delete v2 coordination code          — remove orchestrator, celery_app, etc.

The intent is to ship Phase 1 with ZERO change to production behavior. The
v2 Celery path remains the only path that actually runs work; this file just
defines the SHAPE of v3 so the truth-table tests can validate the decision
logic against the real worker interfaces.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.services.coordinator import Action, BookSnapshot, next_action

logger = logging.getLogger(__name__)


# ─── Configuration knobs (env-driven; defaults match v2 behavior) ──────


# How often to scan the DB for work. Polling overhead is negligible at this
# interval (~30 QPS peak), and latency vs Gemini's 5-30s per-call dominates
# user-perceived speed anyway. Can be tightened later if regen latency feels
# slow — or replaced entirely with LISTEN/NOTIFY for event-driven wake-up.
POLL_INTERVAL_S: float = 2.0

# Concurrent stages running inside this worker process. v2 uses
# CELERY_CONCURRENCY=3; v3 default is 5 to give regen workloads (which are
# user-triggered and latency-sensitive) more parallel headroom. Tunable via
# WORKER_CONCURRENCY env var.
DEFAULT_CONCURRENCY: int = 5

# After this many seconds without a heartbeat, a RUNNING stage is presumed
# orphaned by a dead worker. The coordinator returns RECOVER_STALE so we
# reset the stage to PENDING for re-dispatch. Mirrors v2 watchdog setting.
STALE_HEARTBEAT_AFTER_S: int = 120


# ─── Public entry points (filled in Phase 3) ───────────────────────────


async def db_worker_loop(stop_event: asyncio.Event) -> None:
    """Main poll loop. Runs until stop_event is set.

    Phase 3 implementation:
      while not stop_event.is_set():
          try:
              await advance_one_tick()
          except Exception:
              logger.exception("db_worker tick raised")
          await asyncio.sleep(POLL_INTERVAL_S)
    """
    raise NotImplementedError(
        "db_worker_loop is a Phase 1 scaffold — implemented in Phase 3"
    )


async def advance_one_tick() -> int:
    """Find all in-flight books, decide next_action for each, execute it.

    Returns the number of book transitions performed this tick (for logging
    + observability). Phase 3 will implement:

      1. SELECT books WHERE status IN BOOK_INFLIGHT
      2. For each: build a BookSnapshot, ask coordinator.next_action(snap)
      3. Dispatch to _handle_action(book, action) in the executor pool
      4. Return count of dispatched actions
    """
    raise NotImplementedError(
        "advance_one_tick is a Phase 1 scaffold — implemented in Phase 3"
    )


# ─── Action handlers (filled in Phase 2 + 3) ───────────────────────────


async def _handle_action(book_id: UUID, action: Action) -> None:
    """Dispatch a single book's next action. Phase 3 will wire this up to
    call the directly-callable stage functions added in Phase 2.

    Layout (filled in later phases):
      DISPATCH_SCHEMA    -> call analyse_book_sync(book_id, job_id)
      DISPATCH_THEORY    -> call extract_book_sync(book_id, job_id)
      DISPATCH_QUESTIONS -> call extract_questions_sync(book_id, ...)
      DISPATCH_FIGURES   -> call extract_figures_sync(book_id, ...)
      FINALIZE           -> set book.status from derive_terminal_book_status
      WAIT               -> no-op (something is running)
      NOTHING            -> no-op (book is terminal)
      RECOVER_STALE      -> reset running stage to pending
      ESCALATE_UNKNOWN   -> log loudly + mark book failed with diagnostic
    """
    raise NotImplementedError(
        f"_handle_action({action.value}) is a Phase 1 scaffold"
    )


# ─── Helpers (Phase 3) ─────────────────────────────────────────────────


def _build_snapshot(book) -> BookSnapshot:
    """Convert a SQLAlchemy Book row -> pure BookSnapshot for the
    coordinator. Pulls the heartbeat-stale flag from the latest Job row.

    Filled in Phase 3 once we wire to DB.
    """
    raise NotImplementedError("_build_snapshot is a Phase 1 scaffold")


# Re-export for convenience: callers shouldn't need to know whether the
# decision lives in this module or in coordinator.py.
__all__ = [
    "DEFAULT_CONCURRENCY",
    "POLL_INTERVAL_S",
    "STALE_HEARTBEAT_AFTER_S",
    "advance_one_tick",
    "db_worker_loop",
    "next_action",  # re-export so users can `from db_worker import next_action`
]
