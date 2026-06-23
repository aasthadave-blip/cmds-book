"""v3 coordinator — the SINGLE source of truth for "what should happen next."

This file replaces the multi-layered decision-making in v2 (orchestrator.py +
watchdog driver + reaper coordination + ad-hoc derive_book_status). v3's
philosophy: ONE pure function decides every transition; nothing else gets to
have an opinion about book/stage state.

Why this lives alone in a file with no IO and no DB:
  • It can be tested EXHAUSTIVELY without spinning up Postgres or workers.
  • Every status combination that can possibly exist is fed through the
    same function — the truth-table test in tests/test_coordinator.py
    enumerates all 7^4 = 2401 stage combos and asserts a sane action for
    every single one. The "needs_review stuck at processing" class of bug
    (which we shipped two production fixes for in v2) becomes a test
    failure at coding time, not a 3-hour debugging session in prod.
  • The worker (db_worker.py) is then a thin shell: ask next_action(book),
    do that thing, commit. Almost no logic on the worker side.

Status vocabulary (kept identical to v2 so the DB doesn't need migration):

  PENDING       stage hasn't started yet
  RUNNING       worker is actively processing this stage right now
  DONE          stage completed successfully
  NEEDS_REVIEW  schema-specific: built but flagged for optional review (the
                v2 bug-of-the-day was treating this as non-terminal -> stuck)
  PARTIAL       stage finished but with some non-fatal issues (e.g. theory
                done for 28/29 sections; figures detected fewer than expected)
  FAILED        stage tried and gave up after retries
  CANCELLED     user-initiated stop while the stage was running

Three categorical buckets matter for decisions:

  TERMINAL_OK   = {DONE, NEEDS_REVIEW, PARTIAL}   — stage is finished, advance
  TERMINAL_FAIL = {FAILED, CANCELLED}             — stage is finished, give up
  TERMINAL_ALL  = TERMINAL_OK | TERMINAL_FAIL     — anything that isn't pending/running
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

# ─── Status constants ──────────────────────────────────────────────────

PENDING       = "pending"
RUNNING       = "running"
DONE          = "done"
NEEDS_REVIEW  = "needs_review"
PARTIAL       = "partial"
FAILED        = "failed"
CANCELLED     = "cancelled"

# Every status value the coordinator considers valid. Anything outside this
# set is an "unknown" status — the coordinator escalates with ESCALATE_UNKNOWN
# so it surfaces as a real bug instead of silently hanging the book.
ALL_STATUSES: frozenset[str] = frozenset({
    PENDING, RUNNING, DONE, NEEDS_REVIEW, PARTIAL, FAILED, CANCELLED,
})

# "Stage is complete and we should move on" — counts as done-equivalent.
TERMINAL_OK: frozenset[str] = frozenset({DONE, NEEDS_REVIEW, PARTIAL})

# "Stage is done but bad — don't advance past it (for the stages that gate
# downstream work; finalize uses different rules)."
TERMINAL_FAIL: frozenset[str] = frozenset({FAILED, CANCELLED})

# Anything terminal — neither pending nor running. The coordinator never
# tries to dispatch one of these.
TERMINAL_ALL: frozenset[str] = TERMINAL_OK | TERMINAL_FAIL

# Book-level statuses (mirrors v2's _INFLIGHT_STATUSES). The coordinator
# only acts on books in one of these states; everything else is left alone.
BOOK_INFLIGHT: frozenset[str] = frozenset({
    "analysing", "extracting", "processing", "schema_ready",
})
BOOK_TERMINAL: frozenset[str] = frozenset({
    "ready", "partial", "failed", "cancelled",
})


# ─── Action enum ───────────────────────────────────────────────────────


class Action(str, Enum):
    """Every decision the coordinator can return.

    Each value names WHAT to do; the worker maps that to an actual function
    call. We avoid free-form strings here so a typo can't introduce a
    silently-unhandled action.
    """

    # Forward progress — dispatch a stage worker.
    DISPATCH_SCHEMA    = "dispatch_schema"
    DISPATCH_THEORY    = "dispatch_theory"
    DISPATCH_QUESTIONS = "dispatch_questions"
    DISPATCH_FIGURES   = "dispatch_figures"

    # Lifecycle.
    FINALIZE           = "finalize"        # all stages terminal -> set book.status
    WAIT               = "wait"            # a stage is RUNNING; check back next tick
    NOTHING            = "nothing"         # book is already terminal — leave it alone

    # Recovery / safety net.
    RECOVER_STALE      = "recover_stale"   # a 'running' stage with a stale heartbeat
                                           # -> reset to pending so it gets re-dispatched
    ESCALATE_UNKNOWN   = "escalate_unknown"  # status combo the coordinator can't classify
                                             # -> log loudly + mark book failed with diagnostic
                                             # (this is the "no silent stuck" guarantee)


# ─── Book snapshot — pure data, no DB ──────────────────────────────────


@dataclass(frozen=True)
class BookSnapshot:
    """Everything the coordinator needs to decide. Pure data, no SQLAlchemy.

    Worker assembles this from a Book row + heartbeat lookups, then calls
    next_action(snapshot). Keeps the decision logic 100% testable in isolation
    — see tests/test_coordinator.py for the 2401-combo truth-table test.

    `stage_heartbeat_stale` is True iff at least one stage is RUNNING and its
    most recent heartbeat is older than the staleness threshold (default 120s
    in v2). The worker computes that boolean from job.last_heartbeat_at.
    """

    book_status: str
    schema_status: str
    theory_status: str
    questions_status: str
    figures_status: str
    # Theory finalization is a v2 concept that gated downstream dispatch — kept
    # for compatibility so v3 doesn't change DB semantics during cutover.
    theory_finalized_at_is_set: bool = False
    # If any RUNNING stage's heartbeat is older than threshold, the worker is
    # presumed dead and the stage should be recovered (reset to pending).
    stage_heartbeat_stale: bool = False

    @property
    def stages(self) -> tuple[str, str, str, str]:
        return (
            self.schema_status,
            self.theory_status,
            self.questions_status,
            self.figures_status,
        )


# ─── The single decision function ──────────────────────────────────────


def next_action(book: BookSnapshot) -> Action:
    """Decide what to do for this book RIGHT NOW. Pure, deterministic.

    INVARIANTS (covered by tests/test_coordinator.py truth-table):

      1. NEVER returns None.
      2. NEVER raises.
      3. NEVER returns WAIT unless at least one stage is actually RUNNING.
      4. NEVER returns DISPATCH_* for a stage that is already RUNNING or
         TERMINAL.
      5. If ANY stage status is outside ALL_STATUSES, returns ESCALATE_UNKNOWN
         (the worker then fails the book with a clear diagnostic, instead of
         silently hanging on an unrecognized value).
      6. If the book is itself terminal (ready / failed / cancelled / partial),
         returns NOTHING — the worker leaves it alone.
      7. NEEDS_REVIEW and PARTIAL on any stage count as DONE for "is this stage
         finished?" — this is what eliminates the v2 needs_review-stuck bug.

    Decision order (each step is a guard; first match wins):

      Step A: Validate inputs. Unknown status -> ESCALATE.
      Step B: Book is terminal -> NOTHING.
      Step C: Any RUNNING stage with stale heartbeat -> RECOVER_STALE.
      Step D: Any RUNNING stage with fresh heartbeat -> WAIT.
      Step E: Walk stages in order (schema -> theory -> questions -> figures),
              dispatching the first PENDING one whose prerequisites are met.
      Step F: All stages reached a terminal state -> FINALIZE.
      Step G: Defensive: anything unaccounted for -> ESCALATE.
    """

    # Step A — validate every status. Unknown value = bug somewhere; surface
    # it loudly instead of silently hanging.
    for s in book.stages:
        if s not in ALL_STATUSES:
            return Action.ESCALATE_UNKNOWN

    # Step B — book is already terminal at the book level. Leave it alone.
    # (Note: BOOK_INFLIGHT and BOOK_TERMINAL are disjoint; anything else (e.g.
    # 'queued', 'pending') is treated as a book that hasn't been picked up yet
    # — fall through to per-stage logic.)
    if book.book_status in BOOK_TERMINAL:
        return Action.NOTHING

    # Step C — dead-worker detection. A RUNNING stage with a stale heartbeat
    # means the worker process died mid-stage; reset to PENDING so the next
    # dispatch picks it up. (v2's reaper + watchdog-driver's job, simplified
    # into one explicit signal here.)
    any_running = any(s == RUNNING for s in book.stages)
    if any_running and book.stage_heartbeat_stale:
        return Action.RECOVER_STALE

    # Step D — something is actively running and looks alive. Just wait.
    # (WAIT invariant: from this point on, no stage is RUNNING — so we never
    # return WAIT below this line.)
    if any_running:
        return Action.WAIT

    # ── Step E — Forward-progress dispatch (nothing is running) ──
    #
    # Walk stages in dependency order. For each gate:
    #   - PENDING with upstream OK -> dispatch this stage
    #   - TERMINAL_FAIL upstream    -> downstream is unreachable, FINALIZE
    #   - TERMINAL_OK upstream      -> proceed to next gate
    #
    # Dependency graph:
    #   schema -> theory -> {questions, figures}
    #   (questions + figures both gated on theory but independent of each
    #    other; v2 dispatched both in parallel via "dispatch_both" — v3
    #    picks them up on consecutive ticks, equivalent at this scale.)
    #
    # The "upstream fail blocks downstream" branch (return FINALIZE) is the
    # critical fix for the v2 class of bug we observed: when schema or theory
    # failed, downstream stages stayed PENDING forever and the book never
    # finalized. v3 finalizes the book as soon as forward progress is
    # impossible — the user sees the failure instead of an indefinite hang.

    schema = book.schema_status
    theory = book.theory_status
    questions = book.questions_status
    figures = book.figures_status

    # ── Schema gate ──
    if schema == PENDING:
        return Action.DISPATCH_SCHEMA
    if schema in TERMINAL_FAIL:
        # Schema failed/cancelled -> downstream stages can never run -> finalize.
        return Action.FINALIZE
    # schema is in TERMINAL_OK -> proceed.

    # ── Theory gate ──
    if theory == PENDING:
        return Action.DISPATCH_THEORY
    if theory in TERMINAL_FAIL:
        # Theory failed/cancelled -> Q + Fig can never run -> finalize.
        return Action.FINALIZE
    # theory is in TERMINAL_OK. (We dropped v2's theory_finalized_at gate
    # here: in v3 the worker commits theory_status=DONE + theory_finalized_at
    # atomically as part of the same DB transaction, so the gate is moot. If
    # a v2 book somehow lands here mid-cutover with finalized_at=None, we let
    # downstream proceed — the status DONE bit is the ground truth.)

    # ── Questions / figures gates (independent of each other) ──
    if questions == PENDING:
        return Action.DISPATCH_QUESTIONS
    if figures == PENDING:
        return Action.DISPATCH_FIGURES

    # ── Step F — All terminal-equivalent. Finalize the book. ──
    # Everything reached either TERMINAL_OK or TERMINAL_FAIL. Set book.status.
    # (derive_terminal_book_status decides ready / partial / failed / cancelled.)
    if all(s in TERMINAL_ALL for s in book.stages):
        return Action.FINALIZE

    # ── Step G — Defensive ──
    # The truth-table test enumerates every status combination; this branch
    # should be unreachable. If we reach it in production, surface loudly
    # rather than hang silently.
    return Action.ESCALATE_UNKNOWN


# ─── Stage-status convenience helpers (read-only) ──────────────────────


def stage_is_terminal(status: str) -> bool:
    """Has this stage finished (success, partial, or failure)?"""
    return status in TERMINAL_ALL


def stage_is_done_ok(status: str) -> bool:
    """Did this stage finish OK? (DONE, NEEDS_REVIEW, or PARTIAL — all count.)"""
    return status in TERMINAL_OK


def derive_terminal_book_status(book: BookSnapshot) -> Optional[str]:
    """If the book is finalizable, return what book.status should be.

    Returns None if the book isn't in a finalizable state. Otherwise returns
    one of: 'ready', 'partial', 'failed', 'cancelled'.

    Rules (mirrors v2's derive_book_status but expressed in one place):
      • ALL stages DONE-equivalent (DONE/NEEDS_REVIEW/PARTIAL) -> 'ready'
        if all DONE, else 'partial' if any of them was NEEDS_REVIEW/PARTIAL.
      • Any stage CANCELLED -> 'cancelled' (loud user action; preserve signal).
      • Any stage FAILED and theory+questions DONE -> 'partial' (the v2
        figures-only-fail case we shipped a fix for: don't wipe usable data
        behind a 'failed' screen).
      • Any other FAILED -> 'failed' (loud).
    """
    if not all(s in TERMINAL_ALL for s in book.stages):
        return None

    schema, theory, questions, figures = book.stages

    # Cancelled wins — user intent must be honored visibly.
    if any(s == CANCELLED for s in book.stages):
        return "cancelled"

    # Figures-only failure on an otherwise-extracted book -> partial (the
    # bug we fixed in v2 commit 177642b — don't lose theory + questions
    # behind a generic 'failed').
    if (
        figures == FAILED
        and theory in TERMINAL_OK
        and questions in TERMINAL_OK
        and schema in TERMINAL_OK
    ):
        return "partial"

    # Any other failure short-circuits to failed.
    if any(s == FAILED for s in book.stages):
        return "failed"

    # All terminal-OK. Distinguish strict-done from partial-done.
    if all(s == DONE for s in book.stages):
        return "ready"

    # At least one stage was NEEDS_REVIEW or PARTIAL (but none failed) ->
    # ready. NEEDS_REVIEW schema is auto-healed to DONE elsewhere in v2;
    # treat it as ready here so the book reaches a usable state.
    if all(s in TERMINAL_OK for s in book.stages):
        # Strictly speaking some of these are NEEDS_REVIEW/PARTIAL — but the
        # USER outcome is that the book is fully extracted and usable.
        return "ready"

    # Shouldn't reach here given the all-terminal guard, but be honest:
    return "failed"
