#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Restarts the backend (port 5001), frontend (port 3000), and RQ background
# workers (marketlens-workers, marketlens-backfill).
#
# Invoked by POST /api/system/restart (backend/api/system/router.py) as a
# fully detached subprocess — it survives the backend process it's about to
# kill. Can also be run manually: ./scripts/restart_dev.sh
#
# Kills backend/frontend by PORT, not by a remembered PID. uvicorn --reload
# spawns its actual worker via `multiprocessing`, and that child can outlive
# its parent's PID once the parent is killed (reparented to PID 1, still
# bound to the port) — found repeatedly during manual restarts this session.
# Killing "the PID we started" is not reliable; killing whatever is actually
# bound to the port is.
#
# RQ workers have no fixed port to key off of, so they're matched by their
# process command line instead (pkill -f) — same "kill what's actually
# running, not a remembered PID" principle applied to a process that isn't
# port-bound. Previously this script only restarted backend+frontend, so an
# RQ worker running stale code (e.g. after a backend code change) had to be
# killed and relaunched by hand — found and manually worked around during
# this session's AI-feature work; fixed here so a normal restart covers it.
# ─────────────────────────────────────────────────────────────────────────────
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs

BACKEND_PORT=5001
FRONTEND_PORT=3000
# Prefer the project's configured Python (currently the pyenv 3.12
# interpreter). On this machine `python3` resolves to Homebrew Python 3.14,
# which does not have the application's Uvicorn dependency installed.
# PYTHON_BIN remains overridable for a virtual environment or another local
# interpreter when needed.
PYTHON_BIN="${PYTHON_BIN:-$(command -v python)}"

if ! "$PYTHON_BIN" -m uvicorn --version >/dev/null 2>&1; then
    echo "$(date): $PYTHON_BIN cannot import uvicorn; backend was not started" >> logs/restart_dev.log
    echo "Unable to start backend: $PYTHON_BIN cannot import uvicorn." >&2
    exit 1
fi

# Give the HTTP response for the request that triggered this a moment to
# actually reach the client before we kill the process serving it.
sleep "${RESTART_DELAY:-1}"

echo "$(date): restart_dev.sh starting" >> logs/restart_dev.log

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    # -sTCP:LISTEN: only the process SERVING the port. A bare `lsof -ti:PORT` also returns every
    # CLIENT with a socket on it (a browser or IDE tab talking to the dev server), and those got
    # `kill -9`'d too (an Electron app's network helper died on every restart).
    pids=$(lsof -ti:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$(date): killing PIDs on port $port: $pids" >> logs/restart_dev.log
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
    fi
done

# RQ workers aren't bound to a port — match by command line instead.
# Matches both queues (marketlens-workers, marketlens-backfill) in one
# pass since they share this substring.
worker_pids=$(pgrep -f "rq worker .*marketlens-" 2>/dev/null || true)
if [ -n "$worker_pids" ]; then
    echo "$(date): killing RQ worker PIDs: $worker_pids" >> logs/restart_dev.log
    # shellcheck disable=SC2086
    kill -9 $worker_pids 2>/dev/null || true
fi

# Let the OS actually release the ports before rebinding.
sleep 1

# Every console log below is piped through rotate_stdin.py (MD-05) instead of a raw `>>`
# append: nothing rotated these, unlike logs/marketlens.log (the app's own RotatingFileHandler,
# 50MB x 5 files) — found live 2026-09-24: logs/backend.log alone reached 442 MB in 11 days.
# rotate_stdin.py applies the same 50MB x 5 cap. Each redirected process gets its OWN log file
# and rotator process — two independent rotators appending AND rotating the same path would
# race on the rename (each has a stable fd across an external rename, so it would keep writing
# to what's now a stale backup instead of the fresh file) — this is why the two RQ workers below
# no longer share logs/rq_workers.log.
ROTATE="$SCRIPT_DIR/scripts/rotate_stdin.py"

# --stable flag: skip --reload so uvicorn never auto-restarts on file changes.
# WebSocket connections (Latest Price badge, realtime bars) stay alive for the
# whole session. Pass --stable for live trading; omit for dev work.
STABLE_MODE=false
for arg in "$@"; do
    case "$arg" in
        --stable) STABLE_MODE=true ;;
    esac
done

if [[ "$STABLE_MODE" == "true" ]]; then
    echo "$(date): starting backend in stable mode (no --reload)" >> logs/restart_dev.log
    STABLE_MODE=true nohup "$PYTHON_BIN" -m uvicorn backend.api.main:app --host 127.0.0.1 --port "$BACKEND_PORT" 2>&1 \
        | nohup "$PYTHON_BIN" "$ROTATE" logs/backend.log &
    disown
else
    # --reload-exclude: editing a test must not restart the server (each restart re-runs the whole
    # lifespan: migrations, cache flush, provider handshakes, engine seeding). Keep in sync with
    # start.sh and scripts/run.py. It must be the ABSOLUTE directory: the relative "backend/tests/*"
    # only matches files directly in backend/tests/, so every edit under backend/tests/<pkg>/ (most of
    # the suite) still reloaded the server.
    STABLE_MODE=false nohup "$PYTHON_BIN" -m uvicorn backend.api.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload \
        --reload-exclude "$SCRIPT_DIR/backend/tests" 2>&1 \
        | nohup "$PYTHON_BIN" "$ROTATE" logs/backend.log &
    disown
fi

if [ -d "frontend" ] && [ -f "frontend/package.json" ]; then
    (cd frontend && nohup npx craco start 2>&1 | nohup "$PYTHON_BIN" "$ROTATE" ../logs/frontend.log &)
    disown 2>/dev/null || true
fi

# Same soft-fail as start.sh/scripts/run.py: skip if `rq` isn't installed —
# the app still runs, background jobs (AI analysis, ticker backfill) just
# queue without being processed until a worker exists.
if command -v rq >/dev/null 2>&1; then
    # One backfill worker, not two — see start.sh's matching comment. Two
    # backfill workers, together with the live 1m ingestion loop, all
    # instantiate a Webull provider at once and trip Webull's REST 429
    # (TOO_MANY_REQUESTS) on the token endpoint, starving the live 1m bar
    # feed and leaving "latest bar" frozen. This script used to launch two
    # (a leftover from before that fix landed in start.sh/scripts/run.py)
    # and silently reintroduced the exact bug on every restart.
    echo "$(date): relaunching RQ workers (marketlens-workers x1, marketlens-backfill x1)" >> logs/restart_dev.log
    nohup rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-workers 2>&1 \
        | nohup "$PYTHON_BIN" "$ROTATE" logs/rq_workers.log &
    disown
    nohup rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-backfill 2>&1 \
        | nohup "$PYTHON_BIN" "$ROTATE" logs/rq_backfill.log &
    disown
else
    echo "$(date): 'rq' CLI not found — skipping RQ workers" >> logs/restart_dev.log
fi

# Prune the Webull SDK's own dated/rotated log files past a week. Its TimedRotatingFileHandler
# (backup_count=72, hourly) only prunes past-backup-count files when IT rotates — with --reload
# restarting the process (and the handler) more often than hourly during active dev work, that
# rollover rarely fires, so files piled up regardless of backup_count: 146 found live 2026-09-24.
find logs -maxdepth 1 -name 'webull_*.log.*' -mtime +7 -delete 2>/dev/null || true

echo "$(date): restart_dev.sh done — backend + frontend + RQ workers relaunched" >> logs/restart_dev.log
