"""v3 stage runner — the bridge between coordinator decisions and extraction work.

Phase 2 of the v3 rewrite. ZERO impact on production behavior — this module is
not called by anything yet (db_worker.py wires it up in Phase 3).

Design:
  • The coordinator (services/coordinator.py) returns an Action like
    DISPATCH_THEORY. The db_worker turns that into a direct Python call to
    run_theory(book_id, job_id).
  • Each run_* function is a thin pass-through into the existing extraction
    implementation, looked up by name from app.workers.runner._REGISTRY.
  • Why use the existing registry instead of importing the impl functions
    directly? Two reasons:
      1. v2's inline-mode codepath (TASK_EXECUTOR=inline) already uses this
         registry — meaning these EXACT same calls have been battle-tested in
         local development for months. Phase 2 piggybacks on proven plumbing.
      2. The impl modules import each other transitively; importing them all
         from a single shim is the cleanest way without circular imports.
  • These functions are SYNCHRONOUS. They block the calling thread until the
    stage completes. The db_worker (Phase 3) runs them in a thread-pool so
    multiple stages can execute concurrently.

What this module deliberately does NOT do:
  • No DB session management — each impl opens its own (existing v2 behavior).
  • No retry logic — that's the coordinator's job (re-dispatch on failure).
  • No Celery interaction — the whole point is bypassing Celery.
  • No status updates beyond what the impl already does — the coordinator
    reads status from DB after each stage returns.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from uuid import UUID

from app.workers import runner

logger = logging.getLogger(__name__)


# ─── Task names — single source of truth for what the registry must contain ─


# Extraction pipeline stages (the 4 the coordinator dispatches).
TASK_ANALYSE   = "analyse_book"
TASK_THEORY    = "extract_book"
TASK_QUESTIONS = "extract_questions_v3"
TASK_FIGURES   = "extract_figures_v2"

# Regen / retry / repair tasks (user-triggered; db_worker routes via job.type).
TASK_REGENERATE_BOOK              = "regenerate_book"
TASK_REGENERATE_FIGURES_SECTION   = "regenerate_figures_v2_section"
TASK_REGENERATE_FIGURES_FULL      = "regenerate_figures"
TASK_EXTRACT_QUESTIONS_REGEN_V3   = "extract_questions_regen_v3"
TASK_RETRY_REGEN_SECTION_V3       = "retry_regen_section_v3"
TASK_RE_EXTRACT_SECTION_V3        = "re_extract_section_v3"
TASK_RE_EXTRACT_SECTION           = "re_extract_section"
TASK_RE_EXTRACT_BLOCK             = "re_extract_block"
TASK_RUN_QA_FIDELITY              = "run_qa_fidelity"

# Every task name v3 expects to find in the registry. The boot-time
# verify_registry() check fails loudly at startup if any name is missing —
# better than a NoneType crash mid-extraction.
EXPECTED_TASK_NAMES: frozenset[str] = frozenset({
    TASK_ANALYSE, TASK_THEORY, TASK_QUESTIONS, TASK_FIGURES,
    TASK_REGENERATE_BOOK,
    TASK_REGENERATE_FIGURES_SECTION,
    TASK_REGENERATE_FIGURES_FULL,
    TASK_EXTRACT_QUESTIONS_REGEN_V3,
    TASK_RETRY_REGEN_SECTION_V3,
    TASK_RE_EXTRACT_SECTION_V3,
    TASK_RE_EXTRACT_SECTION,
    TASK_RE_EXTRACT_BLOCK,
    TASK_RUN_QA_FIDELITY,
})


# ─── Generic stage invocation ──────────────────────────────────────────────


def _lookup(task_name: str) -> Callable[..., Any]:
    """Look up a registered task implementation by name.

    Raises a clean RuntimeError on miss — never returns None.  The error
    message names the missing task so a misconfigured worker boot is
    diagnosable in one log line (not a NoneType somewhere downstream).
    """
    # Look up through the module reference (not a captured `_REGISTRY` name)
    # so monkeypatching in tests works AND so a live registry replacement at
    # runtime would be picked up immediately.
    registry = runner._REGISTRY
    fn = registry.get(task_name)
    if fn is None:
        raise RuntimeError(
            f"stage_runner: task '{task_name}' not registered. "
            f"Available: {sorted(registry.keys())}"
        )
    return fn


def run(task_name: str, *args: Any, **kwargs: Any) -> Any:
    """Run any registered task by name, synchronously. Generic backstop for
    the db_worker's job.type dispatch (regen tasks routed dynamically)."""
    fn = _lookup(task_name)
    logger.debug("stage_runner: running %s args=%s", task_name, args)
    return fn(*args, **kwargs)


# ─── Named entry points for the 4 extraction stages ────────────────────────


def run_analyse(book_id: UUID | str, job_id: UUID | str) -> Any:
    """Schema build (Gemini analyses PDF -> book.schema_)."""
    return _lookup(TASK_ANALYSE)(str(book_id), str(job_id))


def run_theory(book_id: UUID | str, job_id: UUID | str) -> Any:
    """Theory extraction (per-section OCR via Gemini, populates Section rows)."""
    return _lookup(TASK_THEORY)(str(book_id), str(job_id))


def run_questions(
    book_id: UUID | str, bank_id: UUID | str, job_id: UUID | str,
) -> Any:
    """Question extraction (Cat-A sections + standalone q-banks)."""
    return _lookup(TASK_QUESTIONS)(str(book_id), str(bank_id), str(job_id))


def run_figures(book_id: UUID | str, job_id: UUID | str) -> Any:
    """Figure detection + crop + embedder (vision Gemini + section linking)."""
    return _lookup(TASK_FIGURES)(str(book_id), str(job_id))


# ─── Registry-completeness check (boot-time fast fail) ─────────────────────


def verify_registry() -> dict[str, bool]:
    """Return a {task_name: registered?} map for every task v3 expects.

    The db_worker calls this at boot. If anything's missing, it logs a CRITICAL
    line naming the specific task — instead of failing 5 minutes into the first
    book with a cryptic NoneType. Returns the map so the boot log can show what
    the worker considers ready.
    """
    return {name: name in runner._REGISTRY for name in EXPECTED_TASK_NAMES}


def assert_registry_complete() -> None:
    """Raise if any expected task isn't registered. Use at worker startup."""
    status = verify_registry()
    missing = [name for name, ok in status.items() if not ok]
    if missing:
        raise RuntimeError(
            f"stage_runner: missing task registrations: {sorted(missing)}. "
            "Ensure app.workers.* modules were imported before starting "
            "the db_worker (Celery's `include` list serves the same purpose "
            "in v2; v3 must import them explicitly)."
        )


__all__ = [
    # Task name constants.
    "TASK_ANALYSE", "TASK_THEORY", "TASK_QUESTIONS", "TASK_FIGURES",
    "TASK_REGENERATE_BOOK", "TASK_REGENERATE_FIGURES_SECTION",
    "TASK_REGENERATE_FIGURES_FULL", "TASK_EXTRACT_QUESTIONS_REGEN_V3",
    "TASK_RETRY_REGEN_SECTION_V3", "TASK_RE_EXTRACT_SECTION_V3",
    "TASK_RE_EXTRACT_SECTION", "TASK_RE_EXTRACT_BLOCK", "TASK_RUN_QA_FIDELITY",
    "EXPECTED_TASK_NAMES",
    # Generic + named entry points.
    "run", "run_analyse", "run_theory", "run_questions", "run_figures",
    # Health checks.
    "verify_registry", "assert_registry_complete",
]
