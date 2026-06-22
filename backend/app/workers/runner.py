"""Task dispatcher — picks inline (threaded) or Celery based on settings.

All callers use ``dispatch(name, *args)`` instead of ``task.delay(...)``. The
dispatcher looks up the task function and either runs it in a daemon thread
(inline) or enqueues it on Celery.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable[..., Any]] = {}


def register(name: str, fn: Callable[..., Any]) -> None:
    _REGISTRY[name] = fn


def dispatch(name: str, *args: Any, **kwargs: Any) -> None:
    """Fire-and-forget task dispatch.

    - In ``inline`` mode: runs ``fn(*args, **kwargs)`` in a daemon thread so the
      HTTP request returns immediately. Progress is still visible because tasks
      update Job rows as they run.
    - In ``celery`` mode: enqueues via ``celery_app.send_task(name, args, kwargs)``.
    """
    fn = _REGISTRY.get(name)
    if settings.TASK_EXECUTOR == "inline":
        if fn is None:
            raise RuntimeError(f"Inline task not registered: {name}")

        def target() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Inline task %s raised", name)

        thread = threading.Thread(target=target, daemon=True, name=f"task-{name}")
        thread.start()
        return

    # Celery path
    from app.workers.celery_app import celery_app

    celery_app.send_task(name, args=list(args), kwargs=kwargs)


def dispatch_after(name: str, delay_s: float, *args: Any, **kwargs: Any) -> None:
    """Schedule a task to run after ``delay_s`` seconds.

    Used for self-verifying dispatches: after firing a stage worker, we schedule
    a verify task ``delay_s`` later to confirm the stage actually picked up. If
    not, the verify re-dispatches. See ``verify_dispatch`` in orchestrator.py.

    - Inline mode: ``threading.Timer`` fires the registered function on a daemon
      thread after the delay.
    - Celery mode: ``send_task(countdown=delay_s)`` — Celery natively supports
      delayed delivery via its broker.
    """
    if settings.TASK_EXECUTOR == "inline":
        fn = _REGISTRY.get(name)
        if fn is None:
            raise RuntimeError(f"Inline task not registered: {name}")

        def target() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Inline scheduled task %s raised", name)

        timer = threading.Timer(delay_s, target)
        timer.daemon = True
        timer.start()
        return

    from app.workers.celery_app import celery_app

    celery_app.send_task(
        name, args=list(args), kwargs=kwargs, countdown=delay_s,
    )
