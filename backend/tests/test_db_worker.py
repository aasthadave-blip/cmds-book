"""Integration tests for the v3 db_worker.

Uses SQLite in-memory + stubbed stage_runner so the test exercises the FULL
worker loop (claim -> dispatch -> finalize) without touching Gemini or
spinning up Postgres.

What these tests prove:
  • A fresh book gets schema -> theory -> questions -> figures dispatched in
    the right order.
  • Stages already RUNNING with fresh heartbeats are left alone (no double-
    dispatch).
  • Stages RUNNING with stale heartbeats get reset to PENDING for recovery.
  • All-terminal books get finalized to the correct book.status.
  • Unknown status combinations escalate (loud-fail) instead of hanging.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import Base
# Trigger model registrations so Base.metadata.create_all sees every table.
from app.models import book as _book_mod              # noqa: F401
from app.models import job as _job_mod                # noqa: F401
from app.models import question_bank as _qbank_mod    # noqa: F401
from app.models import section as _section_mod        # noqa: F401
from app.models.book import Book
from app.models.job import Job


# ─── Test fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def db_session(monkeypatch):
    """Fresh in-memory SQLite + WorkerSession redirected at it.

    Each test gets a brand-new DB so state never leaks across tests.
    Also resets the module-global executor + in-flight set so successive
    tests don't share thread-pool state.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, class_=Session, autoflush=False)

    # Redirect db_worker's WorkerSession to our in-memory DB.
    monkeypatch.setattr("app.services.db_worker.WorkerSession", SessionFactory)
    # Fresh executor + in-flight set per test (prevents cross-test leakage).
    monkeypatch.setattr("app.services.db_worker._executor", None)
    monkeypatch.setattr("app.services.db_worker._in_flight_books", set())
    yield SessionFactory
    # Teardown: ensure executor is shut down so threads don't outlive the test.
    from app.services import db_worker as _dbw
    if _dbw._executor is not None:
        _dbw._executor.shutdown(wait=True, cancel_futures=False)
        _dbw._executor = None


def _wait_for_executor():
    """Block until the db_worker executor has finished all queued tasks.

    Used by tests that submit dispatch work and need to observe its effect
    (status writes happen INSIDE the thread; without this the assertion can
    race the executor).
    """
    from app.services import db_worker as _dbw
    if _dbw._executor is not None:
        _dbw._executor.shutdown(wait=True, cancel_futures=False)
        _dbw._executor = None


@pytest.fixture
def stub_stage_runner(monkeypatch):
    """Replace the 4 stage_runner functions with stubs that just record
    invocations. Lets us assert the worker dispatched the right thing
    without running real extraction.

    Returns the calls list — tests can assert calls == [...] to verify
    dispatch order.
    """
    calls: list[tuple[str, UUID]] = []

    def make_stub(name):
        def stub(book_id, *_args, **_kwargs):
            calls.append((name, UUID(str(book_id))))
        return stub

    monkeypatch.setattr("app.services.stage_runner.run_analyse",  make_stub("analyse"))
    monkeypatch.setattr("app.services.stage_runner.run_theory",   make_stub("theory"))
    monkeypatch.setattr("app.services.stage_runner.run_questions", make_stub("questions"))
    monkeypatch.setattr("app.services.stage_runner.run_figures",  make_stub("figures"))
    monkeypatch.setattr(
        "app.services.stage_runner.assert_registry_complete", lambda: None,
    )
    return calls


def _make_book(session, **overrides) -> Book:
    """Insert a fresh book with explicit defaults so tests are self-documenting."""
    defaults = dict(
        title="Test Book",
        subject="math",
        folder_id=UUID("00000000-0000-0000-0000-000000000001"),
        pdf_url="local://test.pdf",
        status="analysing",
        schema_status="pending",
        theory_status="pending",
        questions_status="pending",
        figures_status="pending",
    )
    defaults.update(overrides)
    book = Book(**defaults)
    session.add(book)
    session.commit()
    return book


def _make_running_job(session, book_id, type_, heartbeat_age_s: float):
    """Insert a RUNNING Job with a heartbeat N seconds in the past."""
    job = Job(
        book_id=book_id,
        type=type_,
        status="running",
        progress=10,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_s + 30),
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_s),
    )
    session.add(job)
    session.commit()
    return job


# ─── Tests ─────────────────────────────────────────────────────────────


def test_fresh_book_dispatches_schema(db_session, stub_stage_runner):
    """A book in (pending,pending,pending,pending) -> dispatch schema."""
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(s)
        book_id = book.id

    db_worker._ensure_executor()
    n = db_worker.advance_one_tick()
    _wait_for_executor()  # flush the dispatched stub before assertions

    assert n == 1, "expected 1 dispatch"
    assert stub_stage_runner == [("analyse", book_id)]

    # Schema status should now be RUNNING (claimed).
    with db_session() as s:
        book = s.get(Book, book_id)
        assert book.schema_status == "running"
        assert book.status == "analysing"


def test_schema_done_dispatches_theory(db_session, stub_stage_runner):
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(s, schema_status="done", status="schema_ready")
        book_id = book.id

    db_worker._ensure_executor()
    db_worker.advance_one_tick()
    _wait_for_executor()

    assert stub_stage_runner == [("theory", book_id)]
    with db_session() as s:
        assert s.get(Book, book_id).theory_status == "running"


def test_running_with_fresh_heartbeat_is_left_alone(
    db_session, stub_stage_runner,
):
    """If a stage is RUNNING with fresh heartbeat -> WAIT, no dispatch."""
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(s, schema_status="running")
        book_id = book.id
        _make_running_job(s, book_id, "analyse", heartbeat_age_s=10)  # fresh

    n = db_worker.advance_one_tick()
    assert n == 0
    assert stub_stage_runner == []


def test_stale_heartbeat_recovers_to_pending(db_session, stub_stage_runner):
    """RUNNING + stale heartbeat -> RECOVER_STALE -> stage reset to PENDING."""
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(s, schema_status="running")
        book_id = book.id
        _make_running_job(s, book_id, "analyse", heartbeat_age_s=300)  # very stale

    n = db_worker.advance_one_tick()
    assert n == 1
    # No actual stage dispatched (recovery, not dispatch).
    assert stub_stage_runner == []
    # Schema should now be back to PENDING for next tick to re-dispatch.
    with db_session() as s:
        assert s.get(Book, book_id).schema_status == "pending"
        # Job should be marked failed with a clear diagnostic.
        job = s.execute(
            sa.select(Job).where(Job.book_id == book_id)
        ).scalar_one()
        assert job.status == "failed"
        assert "heartbeat" in (job.error or "").lower()


def test_all_done_finalizes_to_ready(db_session, stub_stage_runner):
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(
            s,
            schema_status="done", theory_status="done",
            questions_status="done", figures_status="done",
            status="processing",
        )
        # theory_finalized_at must be set or v3 still dispatches questions
        # — set it here so this test isolates the finalize behavior.
        from datetime import datetime, timezone
        book.theory_finalized_at = datetime.now(timezone.utc)
        s.commit()
        book_id = book.id

    db_worker.advance_one_tick()
    with db_session() as s:
        assert s.get(Book, book_id).status == "ready"


def test_needs_review_schema_still_finalizes(db_session, stub_stage_runner):
    """The v2 bug: schema=needs_review + everything else done -> must reach ready.
    Provable in v3 because coordinator treats needs_review as TERMINAL_OK."""
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(
            s,
            schema_status="needs_review", theory_status="done",
            questions_status="done", figures_status="done",
            status="processing",
        )
        from datetime import datetime, timezone
        book.theory_finalized_at = datetime.now(timezone.utc)
        s.commit()
        book_id = book.id

    db_worker.advance_one_tick()
    with db_session() as s:
        # The book must NOT be stuck at processing.
        new_status = s.get(Book, book_id).status
        assert new_status in {"ready", "partial"}, (
            f"needs_review schema must not hang the book — got {new_status!r}"
        )


def test_figures_only_failure_is_partial_not_failed(
    db_session, stub_stage_runner,
):
    """The v2 bug we shipped a fix for: figures-only failure -> partial."""
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(
            s,
            schema_status="done", theory_status="done",
            questions_status="done", figures_status="failed",
            status="processing",
        )
        from datetime import datetime, timezone
        book.theory_finalized_at = datetime.now(timezone.utc)
        s.commit()
        book_id = book.id

    db_worker.advance_one_tick()
    with db_session() as s:
        assert s.get(Book, book_id).status == "partial"


def test_schema_failure_finalizes_book_as_failed(db_session, stub_stage_runner):
    """Schema failed + downstream pending -> book finalizes as failed
    (does NOT hang waiting for downstream that can never run).
    """
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(
            s, schema_status="failed", status="processing",
        )
        book_id = book.id

    db_worker.advance_one_tick()
    with db_session() as s:
        assert s.get(Book, book_id).status == "failed"


def test_terminal_book_is_left_alone(db_session, stub_stage_runner):
    """A book already at terminal book.status -> no work done."""
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(
            s,
            schema_status="done", theory_status="done",
            questions_status="done", figures_status="done",
            status="ready",  # already terminal
        )
        book_id = book.id

    n = db_worker.advance_one_tick()
    # Terminal books are filtered out by the inflight query — n=0.
    assert n == 0
    with db_session() as s:
        assert s.get(Book, book_id).status == "ready"


def test_no_inflight_books_is_a_clean_noop(db_session, stub_stage_runner):
    """Empty in-flight set -> tick returns 0, no errors."""
    from app.services import db_worker

    n = db_worker.advance_one_tick()
    assert n == 0
    assert stub_stage_runner == []


def test_cas_prevents_double_claim(db_session, stub_stage_runner):
    """If a stage is already RUNNING, _claim_stage's CAS must return None.

    This is the cross-worker safety net.
    """
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(s, schema_status="running")
        book_id = book.id

    with db_session() as s:
        # Try to claim schema even though it's already running.
        result = db_worker._claim_stage(s, book_id, "schema_status")
        assert result is None, "CAS should have rejected the claim"

    # Status should still be running, not double-claimed.
    with db_session() as s:
        assert s.get(Book, book_id).schema_status == "running"


def test_unknown_status_escalates_to_failed(db_session, stub_stage_runner):
    """A nonsense status value -> ESCALATE_UNKNOWN -> book.status='failed'.

    The "no silent stuck" guarantee in action.
    """
    from app.services import db_worker

    with db_session() as s:
        book = _make_book(s, schema_status="weird_invalid_value")
        book_id = book.id

    db_worker.advance_one_tick()
    with db_session() as s:
        assert s.get(Book, book_id).status == "failed"
