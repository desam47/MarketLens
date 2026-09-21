"""
Structured JSON logging for MarketLens.

Why structured logs: production log aggregators (Datadog, ELK, CloudWatch
Logs Insights) can index and filter on JSON fields, but plain text logs
require fragile regex parsing. This module replaces the default
`logging.basicConfig` output with one JSON object per line, including
timestamp, level, logger, message, and any `extra={...}` fields passed
to the log call.

Phase 3.5.7: LOG_LEVEL env var controls the root logger level.
Phase 3.5.8: RotatingFileHandler writes to logs/marketlens.log (50 MB / 5 files).

Usage:
    from .structured_logging import get_logger
    log = get_logger(__name__)
    log.info("engine updated", extra={"symbol": "AAPL", "price": 313.45})

Produces:
    {"ts":"2026-08-26T18:40:00.000Z","level":"INFO","logger":"backend.regime",
     "message":"engine updated","symbol":"AAPL","price":313.45}
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Standard LogRecord attributes that we don't want to spill into the JSON
# payload. Anything else passed via `extra={...}` is treated as structured
# context and emitted as a top-level field.
_RESERVED_LOGRECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        # Build the base payload from the record.
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Surface any `extra={...}` fields as top-level JSON keys.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_KEYS or key.startswith("_"):
                continue
            try:
                json.dumps(value)  # type: ignore
                payload[key] = value
            except (TypeError, ValueError):
                # Non-serializable values: stringify so we don't crash the
                # log handler and lose the entire line.
                payload[key] = repr(value)

        # Attach correlation_id from the contextvar (if any) so all log
        # lines emitted during a request can be grouped by the ID.
        # This is a soft dependency: the contextvar module is optional
        # and we degrade gracefully if it's not available.
        if "correlation_id" not in payload:
            try:
                from backend.observability.logging_enhanced import (
                    get_correlation_id,
                )

                cid = get_correlation_id()
                if cid:
                    payload["correlation_id"] = cid
            except ImportError:
                # Observability module not in path — fine, skip.
                pass

        # Exception info, if any — formatted as a string for readability.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def _resolve_log_level(level_hint: str | None) -> int:
    """Parse a level string (DEBUG/INFO/WARNING/ERROR/CRITICAL) to an int."""
    if level_hint is None:
        return logging.INFO
    upper = level_hint.strip().upper()
    return getattr(logging, upper, logging.INFO)


def _get_log_dir() -> Path:
    """Return the logs directory, creating it if needed.

    ``MARKETLENS_LOG_DIR`` overrides the default ``<project>/logs``. The test suite sets it
    (see ``backend/tests/conftest.py``): importing the app configures file logging, so every
    test run used to append its deliberate failures and MagicMock errors to the live server's
    ``logs/marketlens.log``.
    """
    override = os.environ.get("MARKETLENS_LOG_DIR")
    if override:
        log_dir = Path(override)
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    # Work from the project root (one level up from the backend package).
    project_root = Path(__file__).parent.parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir


def configure_logging(
    debug: bool = False,
    log_level: str | None = None,
    enable_file: bool = True,
) -> None:
    """Configure root logger with the JSON formatter.

    Phase 3.5.7: log_level is resolved from the LOG_LEVEL env var, falling
    back to DEBUG if debug=True, else INFO.

    Phase 3.5.8: when enable_file=True (default), a RotatingFileHandler
    writes to logs/marketlens.log with 50 MB / 5-file rotation.

    Idempotent: safe to call multiple times (e.g. from tests). Removes any
    previously installed handlers to avoid duplicate log lines.
    """
    # Resolve level: explicit arg > LOG_LEVEL env > debug flag > INFO
    if log_level is not None:
        level = _resolve_log_level(log_level)
    else:
        env_level = os.environ.get("LOG_LEVEL")
        level = (
            _resolve_log_level(env_level)
            if env_level
            else (logging.DEBUG if debug else logging.INFO)
        )

    json_formatter = JsonFormatter()
    root = logging.getLogger()

    # ---- Console handler (always) ----
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(json_formatter)

    # ---- File handler (optional, 50 MB / 5 files) ----
    file_handler: logging.Handler | None = None
    if enable_file:
        log_path = _get_log_dir() / "marketlens.log"
        file_handler = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)

    # Clear existing handlers to avoid duplicates on re-configure.
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(console_handler)
    if file_handler:
        root.addHandler(file_handler)
    root.setLevel(level)

    # Phase 3.5.7: Tame noisy third-party loggers so they don't spam the output.
    # Each is set to WARNING unless already set lower; they propagate to root so
    # the JSON formatter still applies.
    for name, min_level in (
        ("uvicorn", "WARNING"),
        ("uvicorn.error", "WARNING"),
        ("uvicorn.access", "WARNING"),
        ("websockets", "WARNING"),
        ("asyncio", "WARNING"),
        ("sqlalchemy.engine", "WARNING"),
        ("sqlalchemy.pool", "WARNING"),
        ("httpx", "WARNING"),
        ("httpcore", "WARNING"),
        ("finnhub", "INFO"),
        ("yfinance", "WARNING"),
        ("alpaca", "INFO"),
        ("webull", "INFO"),
    ):
        lg = logging.getLogger(name)
        if lg.level == 0:  # never explicitly set
            lg.setLevel(getattr(logging, min_level, logging.WARNING))
        lg.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — equivalent to logging.getLogger but documents
    that callers should use this module's configured logger."""
    return logging.getLogger(name)
