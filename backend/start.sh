#!/bin/sh
# Single-container startup: runs Celery worker + uvicorn API together so
# they share the same filesystem (and therefore the same Railway volume
# mounted at /app/storage). Eliminates the "PDF not found" class of bugs
# that arose when api and worker ran in separate containers with
# separate filesystems.
#
# Process layout:
#   • Celery worker  → background (manages itself; restarts via supervisor-style trap)
#   • uvicorn API    → foreground (PID 1 so Railway sees it for healthchecks)
#
# Signal handling: when Railway sends SIGTERM, we forward to the worker
# and wait for it to drain before exiting.
set -e

CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-2}"
LOG_LEVEL="${LOG_LEVEL:-info}"

echo "[start.sh] launching celery worker (concurrency=$CELERY_CONCURRENCY, log=$LOG_LEVEL)"
celery -A app.workers.celery_app worker \
  --loglevel="$LOG_LEVEL" \
  --concurrency="$CELERY_CONCURRENCY" \
  --without-gossip \
  --without-mingle \
  --without-heartbeat &
CELERY_PID=$!

# Forward shutdown signals to celery so it drains in-flight tasks.
trap 'echo "[start.sh] SIGTERM → stopping celery (pid=$CELERY_PID)"; kill -TERM "$CELERY_PID" 2>/dev/null; wait "$CELERY_PID"' TERM INT

echo "[start.sh] launching uvicorn on port ${PORT:-8000} (workers=${UVICORN_WORKERS:-2})"
# --workers 2 (override via UVICORN_WORKERS env var): 2x API concurrency so
# 5-10 active users don't serialize through a single worker. Conservative
# pick — the watchdog + orphan-recovery startup hooks aren't multi-worker-
# safe (no DB advisory lock), so 4+ workers risks duplicate orphan
# re-dispatch. Bump higher only after that's refactored.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${UVICORN_WORKERS:-2}"
