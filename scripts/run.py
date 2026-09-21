#!/usr/bin/env python
"""Start the local backend, frontend, and optional background workers.

Run from the repository root with ``python scripts/run.py``. Values in the
process environment take precedence over the root ``.env`` file.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
_DOTENV_VALUES = dotenv_values(ROOT / ".env")


def _env_value(name: str, default: str) -> str:
    """Read a local setting without executing values from ``.env``."""
    return os.environ.get(name) or _DOTENV_VALUES.get(name) or default


def _display_host(host: str) -> str:
    """Return a browser-reachable representation of a listening host."""
    if host in {"0.0.0.0", "::"}:
        return "localhost"
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


@dataclass(frozen=True)
class LocalConfig:
    host: str
    port: int
    api_base_url: str
    redis_url: str
    redis_enabled: bool

    @property
    def backend_url(self) -> str:
        return f"http://{_display_host(self.host)}:{self.port}"


def load_config() -> LocalConfig:
    """Load validated startup values from the environment and root ``.env``."""
    host = _env_value("HOST", "0.0.0.0")
    raw_port = _env_value("PORT", "5001")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"PORT must be an integer, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"PORT must be between 1 and 65535, got {port}")

    backend_url = f"http://{_display_host(host)}:{port}"
    redis_enabled = _env_value("REDIS_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return LocalConfig(
        host=host,
        port=port,
        api_base_url=_env_value("REACT_APP_API_BASE_URL", f"{backend_url}/api"),
        redis_url=_env_value("REDIS_URL", "redis://localhost:6379/0"),
        redis_enabled=redis_enabled,
    )


def start_backend(config: LocalConfig) -> subprocess.Popen:
    """Start FastAPI with the configured host and port."""
    print(f"🚀 Starting backend on {config.backend_url}")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.api.main:app",
            "--host",
            config.host,
            "--port",
            str(config.port),
            "--reload",
            # Editing a test must not restart the server and re-run its whole lifespan.
            "--reload-exclude",
            str(ROOT / "backend" / "tests"),
        ],
        cwd=ROOT,
    )


def start_frontend(config: LocalConfig) -> subprocess.Popen:
    """Start React with the configured backend API URL."""
    print(f"🎨 Starting frontend on http://localhost:3000 (API: {config.api_base_url})")
    return subprocess.Popen(
        ["npm", "start"],
        cwd=ROOT / "frontend",
        env={
            **os.environ,
            "BROWSER": "none",
            "REACT_APP_API_BASE_URL": config.api_base_url,
        },
    )


def start_workers(config: LocalConfig) -> list[subprocess.Popen]:
    """Start optional RQ workers when Redis-backed jobs are enabled."""
    import shutil

    if not config.redis_enabled:
        print("ℹ️  Redis is disabled — skipping background workers.")
        return []
    if shutil.which("rq") is None:
        print("⚠️  'rq' CLI not found — skipping background workers.")
        print(
            "   AI analysis jobs and ticker backfills will queue but not run until it is installed."
        )
        return []

    worker_args = [
        "rq",
        "worker",
        "--url",
        config.redis_url,
        "--worker-class",
        "rq.worker.SimpleWorker",
    ]
    print("🚀 Starting AI analysis worker (marketlens-workers)")
    print("🚀 Starting backfill worker (marketlens-backfill x1)")
    return [
        subprocess.Popen([*worker_args, "marketlens-workers"], cwd=ROOT),
        subprocess.Popen([*worker_args, "marketlens-backfill"], cwd=ROOT),
    ]


def stop_processes(processes: list[subprocess.Popen]) -> None:
    """Terminate every child cleanly, escalating only after a short wait."""
    running = [process for process in processes if process.poll() is None]
    for process in reversed(running):
        process.terminate()
    for process in running:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    config = load_config()
    processes: list[subprocess.Popen] = []
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    print("=" * 60)
    print("  MarketLens - Market Intelligence Platform")
    print("=" * 60)

    try:
        backend = start_backend(config)
        processes.append(backend)
        time.sleep(2)
        processes.extend(start_workers(config))
        processes.append(start_frontend(config))

        print("\n✅ Services running:")
        print(f"   • Backend API:  {config.backend_url}")
        print("   • Frontend:     http://localhost:3000")
        print(f"   • API Docs:     {config.backend_url}/docs")
        print("Press Ctrl+C to stop all services.")
        backend.wait()
    except FileNotFoundError:
        print("\n⚠️  npm was not found. Backend is running without the frontend.")
        print("   Install Node.js, then run `cd frontend && npm install && npm start`.")
        if processes:
            processes[0].wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    main()
