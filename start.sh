#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MarketLens startup script
# Always run from the project root (this script's parent directory).
# This ensures DATABASE_URL, .env, and all relative paths resolve correctly.
# ─────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📁 Working directory: $(pwd)"

# Start the backend on port 5001
echo "🚀 Starting backend on http://127.0.0.1:5001 ..."
# --reload-exclude: editing a test must not restart the server (each restart re-runs the whole
# lifespan: migrations, cache flush, provider handshakes, engine seeding).
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001 --reload --reload-exclude "backend/tests/*" &

BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# Start the frontend (requires separate terminal in most setups)
# If running via CRA:
if [ -d "frontend" ] && [ -f "frontend/package.json" ]; then
    echo "🚀 Starting frontend on http://localhost:3000 ..."
    cd frontend && npm start &
    FRONTEND_PID=$!
    echo "Frontend PID: $FRONTEND_PID"
    cd "$SCRIPT_DIR"
fi

# RQ workers — AI analysis jobs (marketlens-workers) and symbol-history
# backfill (marketlens-backfill, run twice for the old concurrency-cap-of-2
# equivalent). Without these, POST /api/ai/jobs and adding a ticker to a
# watchlist both silently queue a job that nothing ever consumes — found via
# a 2026-09-08 completeness audit: this script (and every other documented
# run path in the repo) started only the API + frontend, never a worker, so
# background jobs never processed by default under a normal `./start.sh`.
# Soft-fail if `rq`/Redis isn't set up — the app still runs without workers,
# it just won't process background jobs (foreground quote/bar/chart data is
# unaffected either way).
WORKER_PIDS=()
if command -v rq >/dev/null 2>&1; then
    echo "🚀 Starting AI analysis worker (marketlens-workers) ..."
    rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-workers &
    WORKER_PIDS+=($!)
    echo "🚀 Starting backfill worker (marketlens-backfill x1) ..."
    # One backfill worker, not two. Two workers run backfill jobs concurrently
    # and, together with the live 1m ingestion loop, all instantiate a Webull
    # provider at once — enough to trip Webull's REST 429 (TOO_MANY_REQUESTS)
    # on the /openapi/config token endpoint, which starves the live 1m bar
    # feed and leaves the "latest bar" frozen. One worker serializes the
    # heavy historical fetches so the live loop keeps quota to write fresh
    # 1m bars. (Reverting to 2 just requires duplicating the line below.)
    rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-backfill &
    WORKER_PIDS+=($!)
else
    echo "⚠️  'rq' CLI not found — skipping background workers."
    echo "    AI analysis jobs and ticker backfills will queue but not run"
    echo "    until you install it (pip install rq) and restart."
fi

echo ""
echo "✅ MarketLens started!"
echo "   Backend:  http://127.0.0.1:5001"
echo "   Frontend: http://localhost:3000"
if [ ${#WORKER_PIDS[@]} -gt 0 ]; then
    echo "   Workers:  ${#WORKER_PIDS[@]} running (AI jobs + backfill)"
fi
echo ""
echo "Press Ctrl+C to stop all services."

# Wait for any process to exit
# "${WORKER_PIDS[@]}" inside an already-double-quoted string still splits
# into separate words per element (bash's @-array quirk survives nesting),
# which broke trap's argument parsing — it saw extra "signal name"
# arguments instead of one command string (confirmed live: "trap: 19282:
# invalid signal specification" on the very first ./start.sh run after
# this file added worker processes). "${WORKER_PIDS[*]}" joins into one
# plain string first, which trap's single command-string argument expects.
WORKER_PIDS_STR="${WORKER_PIDS[*]}"
trap "kill $BACKEND_PID $FRONTEND_PID $WORKER_PIDS_STR 2>/dev/null; exit" INT TERM
wait
