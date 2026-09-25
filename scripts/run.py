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


def _debug_value() -> bool:
    """Read a valid debug value, preferring the local file over host noise."""
    raw_value = _DOTENV_VALUES.get("DEBUG") or os.environ.get("DEBUG") or "false"
    normalized = raw_value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"DEBUG must be a boolean, got {raw_value!r}")


def _startup_mode_value() -> str:
    """Return the validated local startup mode."""
    mode = _env_value("STARTUP_MODE", "full").lower()
    if mode not in {"full", "api"}:
        raise SystemExit(f"STARTUP_MODE must be 'full' or 'api', got {mode!r}")
    return mode


def _display_host(host: str) -> str:
    """Return a browser-reachable representation of a listening host."""
    if host in {"0.0.0.0", "::"}:
        return "localhost"
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


@dataclass(frozen=True)
class LocalConfig:
    host: str
    port: int
    frontend_port: int
    debug: bool
    startup_mode: str
    api_base_url: str
    redis_url: str
    redis_enabled: bool

    @property
    def backend_url(self) -> str:
        return f"http://{_display_host(self.host)}:{self.port}"

    @property
    def frontend_url(self) -> str:
        return f"http://localhost:{self.frontend_port}"


def _port_value(name: str, default: str) -> int:
    raw_port = _env_value(name, default)
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"{name} must be between 1 and 65535, got {port}")
    return port


def load_config() -> LocalConfig:
    """Load validated startup values from the environment and root ``.env``."""
    host = _env_value("HOST", "0.0.0.0")
    port = _port_value("PORT", "5001")
    frontend_port = _port_value("FRONTEND_PORT", "3000")
    if port == frontend_port:
        raise SystemExit("PORT and FRONTEND_PORT must use different values")

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
        frontend_port=frontend_port,
        debug=_debug_value(),
        startup_mode=_startup_mode_value(),
        api_base_url=_env_value("REACT_APP_API_BASE_URL", f"{backend_url}/api"),
        redis_url=_env_value("REDIS_URL", "redis://localhost:6379/0"),
        redis_enabled=redis_enabled,
    )


def _backend_env(config: LocalConfig) -> dict[str, str]:
    """Pin child settings so unrelated parent-shell variables cannot override .env."""
    return {
        **os.environ,
        "DEBUG": str(config.debug).lower(),
        "HOST": config.host,
        "PORT": str(config.port),
        "REDIS_URL": config.redis_url,
        "REDIS_ENABLED": str(config.redis_enabled).lower(),
        "STARTUP_MODE": config.startup_mode,
    }


def start_backend(config: LocalConfig, *, stable: bool = False) -> subprocess.Popen:
    """Start FastAPI with the configured host and port.

    ``stable=True`` omits ``--reload`` so uvicorn never auto-restarts on file
    changes — WebSocket connections stay alive for the whole session.  Use this
    for live trading.  The default (``stable=False``) adds ``--reload`` for the
    dev workflow where you want code changes to take effect immediately.
    """
    mode = "stable — no auto-reload" if stable else "dev — auto-reload enabled"
    print(f"🚀 Starting backend on {config.backend_url} ({mode})")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.api.main:app",
        "--host",
        config.host,
        "--port",
        str(config.port),
    ]
    if not stable:
        # Editing a test must not restart the server and re-run its whole lifespan.
        cmd += ["--reload", "--reload-exclude", str(ROOT / "backend" / "tests")]
    return subprocess.Popen(cmd, cwd=ROOT, env=_backend_env(config))


def start_frontend(config: LocalConfig) -> subprocess.Popen:
    """Start React with the configured backend API URL."""
    print(f"🎨 Starting frontend on {config.frontend_url} (API: {config.api_base_url})")
    return subprocess.Popen(
        ["npm", "start"],
        cwd=ROOT / "frontend",
        env={
            **os.environ,
            "BROWSER": "none",
            "PORT": str(config.frontend_port),
            "REACT_APP_API_BASE_URL": config.api_base_url,
        },
    )


def start_workers(config: LocalConfig) -> list[subprocess.Popen]:
    """Start optional RQ workers when Redis-backed jobs are enabled."""
    import shutil

    if config.startup_mode == "api":
        print("ℹ️  STARTUP_MODE=api — skipping background workers.")
        return []
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
        subprocess.Popen([*worker_args, "marketlens-workers"], cwd=ROOT, env=_backend_env(config)),
        subprocess.Popen([*worker_args, "marketlens-backfill"], cwd=ROOT, env=_backend_env(config)),
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
    stable = "--stable" in sys.argv
    config = load_config()
    processes: list[subprocess.Popen] = []
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    print("=" * 60)
    print("  MarketLens - Market Intelligence Platform")
    print("=" * 60)

    try:
        backend = start_backend(config, stable=stable)
        processes.append(backend)
        time.sleep(2)
        processes.extend(start_workers(config))
        processes.append(start_frontend(config))

        print("\n✅ Services running:")
        print(f"   • Backend API:  {config.backend_url}")
        print(f"   • Frontend:     {config.frontend_url}")
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
