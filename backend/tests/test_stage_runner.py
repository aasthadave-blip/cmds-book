"""Smoke tests for stage_runner — the v3 bridge to extraction code.

These tests prove three things without touching extraction logic:

  1. `stage_runner` imports cleanly (no circular-import surprises).
  2. The expected task names match the task names actually registered by the
     worker modules. If somebody renames a task in v2 without updating
     EXPECTED_TASK_NAMES, this test fails — preventing the "registry-mismatch
     in prod" class of bug.
  3. The lookup-by-name path raises a clean error on missing tasks
     (never returns None / NoneType crash later).
"""

from __future__ import annotations

import pytest

# Importing the worker modules populates the runner._REGISTRY as a side effect.
# This mirrors what celery_app.py's `include=[...]` does in production.
import app.workers.extract            # noqa: F401  registers analyse/extract_book/regenerate_book/re_extract_section/regenerate_figures
import app.workers.figures_tasks      # noqa: F401  registers extract_figures_v2 + regenerate_figures_v2_section
import app.workers.question_regen_v3  # noqa: F401  registers extract_questions_regen_v3 + retry_regen_section_v3
import app.workers.questions_v2       # noqa: F401  registers re_extract_block
import app.workers.questions_v3       # noqa: F401  registers extract_questions_v3 + re_extract_section_v3
import app.workers.qa                 # noqa: F401  registers run_qa_fidelity
from app.services import stage_runner
from app.workers import runner as _runner_mod


def test_imports_cleanly():
    """stage_runner has no circular imports / no top-level side effects."""
    assert stage_runner is not None
    assert callable(stage_runner.run)
    assert callable(stage_runner.run_analyse)
    assert callable(stage_runner.run_theory)
    assert callable(stage_runner.run_questions)
    assert callable(stage_runner.run_figures)


def test_expected_tasks_are_registered():
    """Every task v3 expects to dispatch must be in the runner registry.

    This is the load-bearing test for Phase 2 — it asserts that the contract
    between stage_runner and the v2 worker modules is intact. If v2 renames
    or removes any of these task names, this test fails LOUDLY rather than
    silently producing a NoneType crash in db_worker at runtime.
    """
    missing = [
        name for name in stage_runner.EXPECTED_TASK_NAMES
        if name not in _runner_mod._REGISTRY
    ]
    assert missing == [], (
        f"Tasks expected by v3 but not registered: {sorted(missing)}. "
        f"Available in registry: {sorted(_runner_mod._REGISTRY.keys())}"
    )


def test_verify_registry_reports_all_ok():
    """verify_registry() returns a map showing every expected task as True."""
    status = stage_runner.verify_registry()
    assert all(status.values()), (
        f"Missing tasks: {[k for k, v in status.items() if not v]}"
    )
    assert set(status.keys()) == set(stage_runner.EXPECTED_TASK_NAMES)


def test_assert_registry_complete_does_not_raise():
    """assert_registry_complete() passes when everything is registered.

    This is what db_worker will call at boot — must succeed in a healthy
    container.
    """
    stage_runner.assert_registry_complete()  # raises on missing


def test_lookup_unknown_task_raises_clean_error():
    """Calling stage_runner.run() with an unknown name raises a
    diagnosable RuntimeError, NOT a NoneType error from downstream code."""
    with pytest.raises(RuntimeError, match="not registered"):
        stage_runner.run("definitely_not_a_real_task", "some_arg")


def test_assert_registry_complete_raises_on_missing(monkeypatch):
    """If the registry is missing a v3-expected task, the boot-time check
    raises with a clear message naming the specific missing task(s)."""
    # Simulate a missing registration by replacing the registry with an
    # empty dict for the duration of this test.
    monkeypatch.setattr("app.workers.runner._REGISTRY", {})
    with pytest.raises(RuntimeError, match="missing task registrations"):
        stage_runner.assert_registry_complete()


def test_run_dispatches_via_registry(monkeypatch):
    """run() actually invokes the registered function with the given args.

    Uses a stand-in registry entry to avoid running real extraction.
    """
    calls = []

    def fake_impl(*args, **kwargs):
        calls.append((args, kwargs))
        return "fake_result"

    # Inject a temporary task; monkeypatch reverts on teardown.
    fake_registry = {"__fake_task__": fake_impl}
    monkeypatch.setattr("app.workers.runner._REGISTRY", fake_registry)

    result = stage_runner.run("__fake_task__", "arg1", "arg2", kw=42)
    assert result == "fake_result"
    assert calls == [(("arg1", "arg2"), {"kw": 42})]


def test_named_entry_points_pass_args_correctly(monkeypatch):
    """run_analyse / run_theory / run_figures pass UUID args as strings —
    the existing worker functions expect strings, not UUID objects."""
    from uuid import uuid4

    calls = {}

    def make_capture(name):
        def fake(*args, **kwargs):
            calls[name] = args
        return fake

    fake_registry = {
        stage_runner.TASK_ANALYSE:   make_capture("analyse"),
        stage_runner.TASK_THEORY:    make_capture("theory"),
        stage_runner.TASK_QUESTIONS: make_capture("questions"),
        stage_runner.TASK_FIGURES:   make_capture("figures"),
    }
    monkeypatch.setattr("app.workers.runner._REGISTRY", fake_registry)

    book_id, job_id, bank_id = uuid4(), uuid4(), uuid4()

    stage_runner.run_analyse(book_id, job_id)
    stage_runner.run_theory(book_id, job_id)
    stage_runner.run_questions(book_id, bank_id, job_id)
    stage_runner.run_figures(book_id, job_id)

    # All args must be strings (UUIDs serialized).
    for name, args in calls.items():
        for arg in args:
            assert isinstance(arg, str), (
                f"{name} received non-string arg: {arg!r} (type {type(arg)})"
            )
