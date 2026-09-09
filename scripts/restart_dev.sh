#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Restarts the backend (port 5001) and frontend (port 3000) dev servers.
#
# Invoked by POST /api/system/restart (backend/api/system/router.py) as a
# fully detached subprocess — it survives the backend process it's about to
# kill. Can also be run manually: ./scripts/restart_dev.sh
#
# Kills by PORT, not by a remembered PID. uvicorn --reload spawns its actual
# worker via `multiprocessing`, and that child can outlive its parent's PID
# once the parent is killed (reparented to PID 1, still bound to the port) —
# found repeatedly during manual restarts this session. Killing "the PID we
# started" is not reliable; killing whatever is actually bound to the port is.
# ─────────────────────────────────────────────────────────────────────────────
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs

BACKEND_PORT=5001
FRONTEND_PORT=3000

# Give the HTTP response for the request that triggered this a moment to
# actually reach the client before we kill the process serving it.
sleep "${RESTART_DELAY:-1}"

echo "$(date): restart_dev.sh starting" >> logs/restart_dev.log

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    pids=$(lsof -ti:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$(date): killing PIDs on port $port: $pids" >> logs/restart_dev.log
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
    fi
done

# Let the OS actually release the ports before rebinding.
sleep 1

nohup python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload \
    >> logs/backend.log 2>&1 &
disown

if [ -d "frontend" ] && [ -f "frontend/package.json" ]; then
    (cd frontend && nohup npx craco start >> ../logs/frontend.log 2>&1 &)
    disown 2>/dev/null || true
fi

echo "$(date): restart_dev.sh done — backend + frontend relaunched" >> logs/restart_dev.log
