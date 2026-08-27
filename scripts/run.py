#!/usr/bin/env python3
"""
MarketLens - Start both backend and frontend
Run: python3 run.py
"""
import subprocess
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).parent

def start_backend():
    """Start FastAPI backend"""
    print("🚀 Starting backend on http://localhost:5001")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.main:app",
         "--host", "0.0.0.0", "--port", "5001", "--reload"],
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

def main():
    print("=" * 60)
    print("  MarketLens - Market Intelligence Platform")
    print("=" * 60)
    print()

    backend = start_backend()
    time.sleep(2)

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
    print("=" * 60)
    print()
    print("Press Ctrl+C to stop all services")

    try:
        backend.wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        backend.terminate()
        try:
            frontend.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    main()
