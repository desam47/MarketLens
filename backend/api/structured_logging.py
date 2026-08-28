"""
Structured JSON logging for MarketLens.

Why structured logs: production log aggregators (Datadog, ELK, CloudWatch
Logs Insights) can index and filter on JSON fields, but plain text logs
require fragile regex parsing. This module replaces the default
`logging.basicConfig` output with one JSON object per line, including
timestamp, level, logger, message, and any `extra={...}` fields passed
to the log call.

Usage:
    from backend.api.structured_logging import get_logger
    log = get_logger(__name__)
    log.info("engine updated", extra={"symbol": "AAPL", "price": 313.45})

Produces:
    {"ts":"2026-08-26T18:40:00.000Z","level":"INFO","logger":"backend.regime",
     "message":"engine updated","symbol":"AAPL","price":313.45}
"""
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Standard LogRecord attributes that we don't want to spill into the JSON
# payload. Anything else passed via `extra={...}` is treated as structured
# context and emitted as a top-level field.
_RESERVED_LOGRECORD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        # Build the base payload from the record.
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
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

        # Exception info, if any — formatted as a string for readability.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(debug: bool = False) -> None:
    """Configure root logger with the JSON formatter.

    Idempotent: safe to call multiple times (e.g. from tests). Removes any
    previously installed handlers to avoid duplicate log lines.
    """
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    # Clear existing handlers — `basicConfig` from a previous import would
    # otherwise produce duplicate lines on every config call.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    # Tame uvicorn's own loggers. They emit in plain text by default; route
    # them through the JSON formatter so log output is uniform.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — equivalent to logging.getLogger but documents
    that callers should use this module's configured logger."""
    return logging.getLogger(name)
