#!/usr/bin/env bash
# Run backend + frontend locally. Requires ./scripts/bootstrap-local.sh to have
# been run first.

set -euo pipefail

cd "$(dirname "$0")/.."

UV="${HOME}/.local/bin/uv"

if [[ ! -f .env ]]; then
  echo "Missing .env — run ./scripts/bootstrap-local.sh first" >&2
  exit 1
fi

# Kill any existing backend/frontend processes on our ports so we never
# end up with duplicate stale instances stealing the port.
echo "==> Cleaning up old instances..."
lsof -ti :8001 | xargs kill -9 2>/dev/null || true
lsof -ti :5174 | xargs kill -9 2>/dev/null || true
sleep 1

# Export vars
set -a
# shellcheck disable=SC1091
source .env
set +a

# Start backend
pushd backend >/dev/null
echo "==> Starting backend on :8001"
UVICORN_RELOAD_ACTIVE=1 "$UV" run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload &
BACK_PID=$!
popd >/dev/null

# Start frontend
pushd frontend >/dev/null
echo "==> Starting frontend on :5174"
PATH=/usr/local/bin:$PATH VITE_API_BASE=http://localhost:8001 /usr/local/bin/npm run dev -- --host 0.0.0.0 --port 5174 &
FRONT_PID=$!
popd >/dev/null

trap "echo 'stopping...'; kill $BACK_PID $FRONT_PID 2>/dev/null || true" EXIT INT TERM

wait
