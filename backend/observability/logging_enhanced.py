"""
Enhanced logging with correlation ID support for MarketLens.

This module extends the structured JSON logging with automatic correlation ID
propagation. When a request has a correlation ID (set by the
CorrelationIdMiddleware), it is automatically included in all log messages
emitted during that request's lifecycle.

Phase 3.5.5: Adds LogContext.with_context() for attaching arbitrary
fields (symbol, timeframe, etc.) to log output within a with-block.

Usage:
    from backend.observability.logging_enhanced import get_logger, with_context
    log = get_logger(__name__)
    log.info("processing data")  # correlation_id included automatically if context is set

    with with_context(symbol="AAPL", timeframe="1d"):
        log.info("scanning")  # emits symbol=AAPL, timeframe=1d in JSON payload
"""
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .correlation_id import CorrelationIdMiddleware
from ..api.structured_logging import JsonFormatter, get_logger

__all__ = [
    "CorrelationIdMiddleware",
    "get_logger",
    "set_correlation_id",
    "get_correlation_id",
    "CorrelationIdFilter",
    "with_context",
]

# Context variable to hold the correlation ID for the current request.
# Uses contextvars (PEP 567) for async-safe propagation — works correctly
# with FastAPI's async context and concurrent requests.
_correlation_id_ctx: ContextVar[str | None] = ContextVar(
    "correlation_id", default=None
)

# Context variable to hold additional structured fields (symbol, timeframe, etc.)
# that should be injected into every log record during the with-block.
_extra_fields_ctx: ContextVar[dict[str, Any] | None] = ContextVar(
    "_extra_fields", default=None
)


def set_correlation_id(correlation_id: str | None) -> None:
    """Set the correlation ID for the current context.

    This is called by the CorrelationIdMiddleware to establish the
    correlation ID at the start of each request. Subsequent log calls
    will automatically include it.
    """
    if correlation_id:
        _correlation_id_ctx.set(correlation_id)
    else:
        _correlation_id_ctx.reset(_correlation_id_ctx.set(None))  # clear


def get_correlation_id() -> str | None:
    """Get the correlation ID for the current context.

    Returns None if no correlation ID has been set (e.g. outside a request).
    """
    return _correlation_id_ctx.get()


class CorrelationIdFilter(logging.Filter):
    """Logging filter that injects correlation_id and extra fields into log records.

    Add this filter to any logger or handler to automatically include
    the correlation ID and any extra context fields (symbol, timeframe, etc.)
    in structured log output when active.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        correlation_id = get_correlation_id()
        if correlation_id:
            record.correlation_id = correlation_id
        extra = get_extra_fields()
        if extra:
            for k, v in extra.items():
                setattr(record, k, v)
        return True


def get_logger_with_correlation(name: str) -> logging.Logger:
    """Get a logger configured with correlation ID support.

    This is a convenience wrapper that returns a logger with the
    CorrelationIdFilter already attached.
    """
    log = logging.getLogger(name)
    # Avoid duplicate filters
    if not any(
        isinstance(f, CorrelationIdFilter) for f in log.filters
    ):
        log.addFilter(CorrelationIdFilter())
    return log


# ---------------------------------------------------------------------------
# Phase 3.5.5: LogContext — arbitrary field injection via context manager
# ---------------------------------------------------------------------------

def get_extra_fields() -> dict[str, Any]:
    """Return extra fields set by with_context(), or empty dict if none."""
    val = _extra_fields_ctx.get()
    return val if val is not None else {}


@contextmanager
def with_context(**fields: Any):
    """Attach arbitrary fields to every log record emitted within the block.

    The fields are injected into the JSON payload by CorrelationIdFilter
    so they appear in every structured log line inside the with-block.

    Example::

        with with_context(symbol="AAPL", timeframe="1d"):
            log.info("scanning")   # JSON: {"message":"scanning","symbol":"AAPL","timeframe":"1d",...}
            log.warning("no data") # JSON: {"message":"no data","symbol":"AAPL","timeframe":"1d",...}

    Nesting is supported — inner fields take precedence over outer ones.
    """
    current = get_extra_fields()
    merged = {**current, **fields}
    token = _extra_fields_ctx.set(merged)
    try:
        yield
    finally:
        _extra_fields_ctx.reset(token)

