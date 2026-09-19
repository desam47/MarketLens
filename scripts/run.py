#!/usr/bin/env python3
"""
MarketLens - Start both backend and frontend
Run: python3 run.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent

def start_backend():
    """Start FastAPI backend"""
    print("🚀 Starting backend on http://localhost:5001")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.main:app",
         "--host", "0.0.0.0", "--port", "5001", "--reload",
         # editing a test must not restart the server and re-run its whole lifespan. Absolute:
         # the relative "backend/tests/*" misses everything below backend/tests/<pkg>/.
         "--reload-exclude", str(ROOT / "backend" / "tests")],
        cwd=ROOT
    )

def start_frontend():
    """Start React frontend"""
    print("🎨 Starting frontend on http://localhost:3000")
    return subprocess.Popen(
        ["npm", "start"],
        cwd=ROOT / "frontend",
        env={**os.environ, "BROWSER": "none"}
    )

def start_workers():
    """Start the RQ background workers — AI analysis jobs and symbol-history
    backfill (one backfill worker). Two workers run backfill jobs concurrently
    and, with the live 1m ingestion loop, all instantiate a Webull provider at
    once — enough to trip Webull's REST 429 (TOO_MANY_REQUESTS) on the
    /openapi/config token endpoint, which starves the live 1m bar feed and
    leaves the "latest bar" frozen. One worker serializes the heavy
    historical fetches so the live loop keeps quota. Without these workers,
    POST /api/ai/jobs and adding a ticker to a watchlist both silently queue
    a job that nothing ever consumes (found via a 2026-09-08 completeness
    audit — this script started only the API + frontend, same gap as
    start.sh). Returns an empty list (soft-fail) if the `rq` CLI isn't
    installed; the app still runs, it just won't process background jobs.
    """
    import shutil
    if shutil.which("rq") is None:
        print("⚠️  'rq' CLI not found — skipping background workers.")
        print("   AI analysis jobs and ticker backfills will queue but not")
        print("   run until you install it (pip install rq) and restart.")
        return []
    print("🚀 Starting AI analysis worker (marketlens-workers)")
    print("🚀 Starting backfill worker (marketlens-backfill x1)")
    redis_url = "redis://localhost:6379/0"
    return [
        # --worker-class SimpleWorker: RQ's default Worker forks a child
        # process per job, and this project's webull provider SDK
        # reproducibly segfaults the forked child — see
        # backend/workers/backfill_worker.py's module docstring.
        subprocess.Popen(["rq", "worker", "--url", redis_url, "--worker-class", "rq.worker.SimpleWorker", "marketlens-workers"], cwd=ROOT),
        subprocess.Popen(["rq", "worker", "--url", redis_url, "--worker-class", "rq.worker.SimpleWorker", "marketlens-backfill"], cwd=ROOT),
    ]

def main():
    print("=" * 60)
    print("  MarketLens - Market Intelligence Platform")
    print("=" * 60)
    print()

    backend = start_backend()
    time.sleep(2)
    workers = start_workers()

    try:
        frontend = start_frontend()
    except FileNotFoundError:
        print("\n⚠️  npm not found. Backend is running.")
        print("   Install Node.js and run 'cd frontend && npm install && npm start'")
        backend.wait()
        return

    print()
    print("=" * 60)
    print("  ✅ Services running:")
    print("     • Backend API:  http://localhost:5001")
    print("     • Frontend:     http://localhost:3000")
    print("     • API Docs:     http://localhost:5001/docs")
    if workers:
        print(f"     • Workers:      {len(workers)} running (AI jobs + backfill)")
    print("=" * 60)
    print()
    print("Press Ctrl+C to stop all services")

    try:
        backend.wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        backend.terminate()
        for w in workers:
            try:
                w.terminate()
            except Exception:
                pass
        try:
            frontend.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    main()
