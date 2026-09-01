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
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001 --reload &

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

echo ""
echo "✅ MarketLens started!"
echo "   Backend:  http://127.0.0.1:5001"
echo "   Frontend: http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop all services."

# Wait for any process to exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
