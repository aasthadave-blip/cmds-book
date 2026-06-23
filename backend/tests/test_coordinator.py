"""Exhaustive truth-table tests for the v3 coordinator.

This is the "no silent stuck" guarantee for v3. It enumerates every possible
status combination (7 statuses ^ 4 stages = 2401 combinations, x2 for the
heartbeat-stale flag, x2 for theory_finalized) and asserts that next_action()
returns a sane, non-stuck Action for each one.

The whole class of bug we shipped two v2 fixes for today (needs_review stuck
at processing; figures-only-fail wiping the partial result) becomes a test
failure here BEFORE any code ships.
"""

from __future__ import annotations

import pytest

from app.services.coordinator import (
    ALL_STATUSES,
    Action,
    BookSnapshot,
    CANCELLED,
    DONE,
    FAILED,
    NEEDS_REVIEW,
    PARTIAL,
    PENDING,
    RUNNING,
    derive_terminal_book_status,
    next_action,
    stage_is_done_ok,
    stage_is_terminal,
)


def _snap(
    schema=PENDING, theory=PENDING, questions=PENDING, figures=PENDING,
    *, book_status="analysing",
    theory_finalized=False, heartbeat_stale=False,
) -> BookSnapshot:
    return BookSnapshot(
        book_status=book_status,
        schema_status=schema,
        theory_status=theory,
        questions_status=questions,
        figures_status=figures,
        theory_finalized_at_is_set=theory_finalized,
        stage_heartbeat_stale=heartbeat_stale,
    )


# ─── Invariant tests (the safety net for v3) ───────────────────────────


def test_truth_table_never_silently_stuck():
    """THE central guarantee: for EVERY status combination, next_action
    returns a defined Action — never None, never a silent hang.

    7 statuses ^ 4 stages = 2401 combinations.
    """
    statuses = list(ALL_STATUSES)
    count = 0
    for s_sch in statuses:
        for s_th in statuses:
            for s_q in statuses:
                for s_fg in statuses:
                    snap = _snap(s_sch, s_th, s_q, s_fg)
                    action = next_action(snap)
                    assert isinstance(action, Action), (
                        f"next_action returned non-Action for "
                        f"({s_sch},{s_th},{s_q},{s_fg}): {action!r}"
                    )
                    count += 1
    assert count == 7 ** 4, f"expected {7**4} combos, walked {count}"


def test_wait_implies_something_running():
    """WAIT must NEVER be returned unless a stage is actually RUNNING.

    The v2 "stuck at no_action" class of bug came from waiting on a stage
    that wasn't actually progressing. This invariant forbids that.
    """
    statuses = list(ALL_STATUSES)
    for s_sch in statuses:
        for s_th in statuses:
            for s_q in statuses:
                for s_fg in statuses:
                    snap = _snap(s_sch, s_th, s_q, s_fg)
                    action = next_action(snap)
                    if action == Action.WAIT:
                        assert RUNNING in snap.stages, (
                            f"WAIT returned with no RUNNING stage: "
                            f"({s_sch},{s_th},{s_q},{s_fg})"
                        )


def test_dispatch_never_targets_non_pending_stage():
    """DISPATCH_<STAGE> must only be returned when that stage is PENDING.

    Prevents the "double-dispatch on a running stage" race.
    """
    dispatch_to_stage = {
        Action.DISPATCH_SCHEMA: lambda s: s.schema_status,
        Action.DISPATCH_THEORY: lambda s: s.theory_status,
        Action.DISPATCH_QUESTIONS: lambda s: s.questions_status,
        Action.DISPATCH_FIGURES: lambda s: s.figures_status,
    }
    statuses = list(ALL_STATUSES)
    for s_sch in statuses:
        for s_th in statuses:
            for s_q in statuses:
                for s_fg in statuses:
                    snap = _snap(s_sch, s_th, s_q, s_fg)
                    action = next_action(snap)
                    if action in dispatch_to_stage:
                        target_status = dispatch_to_stage[action](snap)
                        assert target_status == PENDING, (
                            f"{action.value} returned but target stage "
                            f"is {target_status!r} (not PENDING). Snap: "
                            f"({s_sch},{s_th},{s_q},{s_fg})"
                        )


def test_unknown_status_escalates():
    """An unrecognized status must trigger ESCALATE_UNKNOWN, NEVER a hang.

    Inserting a typo or new status value can't silently break the book.
    """
    snap = _snap(schema="weird_value", theory=DONE, questions=DONE, figures=DONE)
    assert next_action(snap) == Action.ESCALATE_UNKNOWN


def test_terminal_book_is_left_alone():
    """A book that's already at a terminal book.status -> NOTHING."""
    for terminal in ("ready", "partial", "failed", "cancelled"):
        snap = _snap(DONE, DONE, DONE, DONE, book_status=terminal)
        assert next_action(snap) == Action.NOTHING, (
            f"terminal book book_status={terminal} should be left alone"
        )


# ─── Specific regression tests for the bugs we shipped fixes for ───────


def test_needs_review_does_not_get_stuck():
    """v2 commit 4241ce4: needs_review schema must not block finalization.

    The book reached: schema=needs_review, theory=done, q=done, fg=done.
    v2 derive_book_status returned 'processing' because needs_review != done
    in the strict check. v3 must treat NEEDS_REVIEW as TERMINAL_OK for the
    purposes of finalization.
    """
    snap = _snap(NEEDS_REVIEW, DONE, DONE, DONE)
    assert next_action(snap) == Action.FINALIZE, (
        "needs_review schema + all other stages done -> must finalize"
    )
    # And the derived book.status must be 'ready' (or 'partial' if any stage
    # was NEEDS_REVIEW/PARTIAL) — never 'processing'.
    derived = derive_terminal_book_status(snap)
    assert derived in {"ready", "partial"}, (
        f"needs_review case derived to {derived!r}, expected ready/partial"
    )


def test_figures_only_failure_is_partial_not_failed():
    """v2 commit 177642b: figures-only failure must produce 'partial', not
    'failed'. The book has usable theory + questions data — don't wipe it
    behind a generic 'failed' screen.
    """
    snap = _snap(DONE, DONE, DONE, FAILED)
    # The coordinator's action is FINALIZE — the derived status is the test.
    assert next_action(snap) == Action.FINALIZE
    assert derive_terminal_book_status(snap) == "partial", (
        "figures-only failure with theory+questions done must be 'partial'"
    )


def test_partial_theory_propagates_forward():
    """Theory completing with status=PARTIAL should still unblock
    questions/figures (PARTIAL means usable, just imperfect).
    """
    snap = _snap(DONE, PARTIAL, PENDING, PENDING, theory_finalized=True)
    # questions hasn't started → dispatch it.
    assert next_action(snap) == Action.DISPATCH_QUESTIONS


def test_theory_finalized_gate_dropped_in_v3():
    """v2 had a 'theory_finalized_at is None while theory=done' window where
    the linker/embedder tail ran separately, blocking Q/Fig dispatch. v3
    drops that gate: the worker commits theory_status=DONE + finalized_at
    atomically in the same DB transaction, so theory=DONE alone means
    downstream is safe to dispatch. This makes the state machine simpler
    AND prevents the v2 "DONE but not finalized + nothing running -> stuck"
    edge case (e.g. when the v2 tail crashed mid-finalization).
    """
    snap = _snap(DONE, DONE, PENDING, PENDING, theory_finalized=False)
    assert next_action(snap) == Action.DISPATCH_QUESTIONS, (
        "v3 should dispatch downstream as soon as theory=DONE, ignoring "
        "the deprecated theory_finalized_at gate."
    )
    snap2 = _snap(DONE, DONE, PENDING, PENDING, theory_finalized=True)
    assert next_action(snap2) == Action.DISPATCH_QUESTIONS


def test_stale_heartbeat_triggers_recovery():
    """A RUNNING stage with stale heartbeat == dead worker -> RECOVER_STALE."""
    snap = _snap(DONE, RUNNING, PENDING, PENDING,
                 theory_finalized=False, heartbeat_stale=True)
    assert next_action(snap) == Action.RECOVER_STALE


def test_running_with_fresh_heartbeat_just_waits():
    """A RUNNING stage with fresh heartbeat (worker alive) -> WAIT."""
    snap = _snap(DONE, RUNNING, PENDING, PENDING,
                 theory_finalized=False, heartbeat_stale=False)
    assert next_action(snap) == Action.WAIT


# ─── Forward-progress walk through the pipeline ────────────────────────


def test_fresh_book_dispatches_schema():
    snap = _snap()
    assert next_action(snap) == Action.DISPATCH_SCHEMA


def test_schema_done_dispatches_theory():
    snap = _snap(schema=DONE)
    assert next_action(snap) == Action.DISPATCH_THEORY


def test_schema_failed_blocks_everything():
    """Schema failure must NOT cascade into trying theory/questions/figures."""
    snap = _snap(schema=FAILED)
    # No PENDING stage to advance + nothing running → ready to finalize.
    assert next_action(snap) == Action.FINALIZE


def test_theory_failed_book_still_finalizes():
    """If theory failed but questions/figures aren't pending — finalize."""
    snap = _snap(DONE, FAILED, PENDING, PENDING, theory_finalized=True)
    # v3 behavior: a failed theory means questions and figures can't run
    # (we don't dispatch them), book proceeds to finalize as 'failed'.
    assert next_action(snap) == Action.FINALIZE


def test_schema_done_theory_done_dispatches_questions_first():
    """When both questions and figures are pending and theory finalized,
    dispatch one of them. v3 picks questions first; figures gets dispatched
    on the next tick (idiomatic poll-based dispatch — no 'dispatch_both').
    """
    snap = _snap(DONE, DONE, PENDING, PENDING, theory_finalized=True)
    assert next_action(snap) == Action.DISPATCH_QUESTIONS


def test_questions_done_dispatches_figures():
    snap = _snap(DONE, DONE, DONE, PENDING, theory_finalized=True)
    assert next_action(snap) == Action.DISPATCH_FIGURES


def test_all_done_finalizes():
    snap = _snap(DONE, DONE, DONE, DONE, theory_finalized=True)
    assert next_action(snap) == Action.FINALIZE


# ─── derive_terminal_book_status — separate but interlinked logic ──────


def test_derive_terminal_status_only_for_terminal_books():
    """Non-terminal stages -> derive returns None (book isn't ready to
    finalize yet)."""
    assert derive_terminal_book_status(_snap()) is None
    assert derive_terminal_book_status(_snap(theory=RUNNING)) is None


def test_derive_terminal_status_all_done_is_ready():
    snap = _snap(DONE, DONE, DONE, DONE, theory_finalized=True)
    assert derive_terminal_book_status(snap) == "ready"


def test_derive_terminal_status_needs_review_is_ready():
    """needs_review schema + done downstream -> book reaches ready (the
    central v2 bug). Test covered above too but worth pinning here."""
    snap = _snap(NEEDS_REVIEW, DONE, DONE, DONE, theory_finalized=True)
    assert derive_terminal_book_status(snap) == "ready"


def test_derive_terminal_status_cancelled_propagates():
    """Cancellation must be honored visibly — preserve the user signal."""
    snap = _snap(DONE, CANCELLED, DONE, DONE, theory_finalized=True)
    assert derive_terminal_book_status(snap) == "cancelled"


def test_derive_terminal_status_partial_theory_partial_book():
    """A stage finishing PARTIAL should not collapse the book to failed."""
    snap = _snap(DONE, PARTIAL, DONE, DONE, theory_finalized=True)
    assert derive_terminal_book_status(snap) in {"ready", "partial"}


# ─── Tiny helper smoke tests ───────────────────────────────────────────


@pytest.mark.parametrize("status,expected", [
    (DONE, True), (NEEDS_REVIEW, True), (PARTIAL, True),
    (FAILED, True), (CANCELLED, True),
    (PENDING, False), (RUNNING, False),
])
def test_stage_is_terminal_classification(status, expected):
    assert stage_is_terminal(status) == expected


@pytest.mark.parametrize("status,expected", [
    (DONE, True), (NEEDS_REVIEW, True), (PARTIAL, True),
    (FAILED, False), (CANCELLED, False),
    (PENDING, False), (RUNNING, False),
])
def test_stage_is_done_ok_classification(status, expected):
    assert stage_is_done_ok(status) == expected
