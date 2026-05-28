"""Celery app — only used when TASK_EXECUTOR=celery.

In local mode (default) tasks run inline via app.workers.runner. We still
create a stand-in ``celery_app`` object so the ``@celery_app.task`` decorator
in ``extract.py`` is a no-op pass-through that returns the wrapped function.
"""

from __future__ import annotations

from app.core.config import settings


class _NoopCeleryApp:
    """Minimal shim so module-level decorators work when Celery isn't installed."""

    conf = type("Conf", (), {"update": staticmethod(lambda **_: None)})()

    def task(self, *_args, **_kwargs):
        def decorator(fn):
            fn.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
            return fn

        return decorator

    def send_task(self, *_a, **_kw):  # pragma: no cover — inline path never hits this
        raise RuntimeError("Celery is not available; set TASK_EXECUTOR=inline")


if settings.TASK_EXECUTOR == "celery":
    from celery import Celery  # type: ignore[import-not-found]

    # NOTE: no result backend configured. Our app tracks task state via Job
    # rows in Postgres (status/progress/error fields) — we never call
    # `.delay().get()` or use AsyncResult. Configuring a Redis result backend
    # caused "Retry limit exceeded while trying to reconnect to the Celery
    # result store backend" errors in the API process because send_task tried
    # to set up a result consumer over a flaky Redis pub/sub connection.
    celery_app: "Celery | _NoopCeleryApp" = Celery(
        "cmds",
        broker=settings.CELERY_BROKER_URL,
        # Every worker module that defines @celery_app.task functions must be
        # listed here so Celery imports them at startup and registers the
        # tasks in its registry. Missing a module = silent "task not found"
        # on dispatch.
        include=[
            "app.workers.extract",
            "app.workers.questions",
            "app.workers.questions_v2",
            "app.workers.questions_v3",
            "app.workers.question_regen_v3",
            "app.workers.figures_tasks",
            "app.workers.qa",
        ],
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        # No result backend → no task_track_started (would require result store).
        task_ignore_result=True,
        task_time_limit=60 * 30,
        task_soft_time_limit=60 * 25,
        worker_max_tasks_per_child=50,
        # Crash safety: don't ack until task fully completes.
        # If the worker is killed mid-task, the broker re-queues it automatically.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # One task at a time per worker slot — prevents slow tasks starving the queue.
        worker_prefetch_multiplier=1,
    )
else:
    celery_app = _NoopCeleryApp()
