"""
Enhanced logging with correlation ID support for MarketLens.

This module extends the structured JSON logging with automatic correlation ID
propagation. When a request has a correlation ID (set by the
CorrelationIdMiddleware), it is automatically included in all log messages
emitted during that request's lifecycle.

Usage:
    from backend.observability.logging_enhanced import get_logger
    log = get_logger(__name__)
    log.info("processing data")  # correlation_id included automatically if context is set
"""
import logging
import uuid
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
]

# Context variable to hold the correlation ID for the current request.
# Uses contextvars (PEP 567) for async-safe propagation — works correctly
# with FastAPI's async context and concurrent requests.
_correlation_id_ctx: ContextVar[str | None] = ContextVar(
    "correlation_id", default=None
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
    """Logging filter that injects correlation_id into log records.

    Add this filter to any logger or handler to automatically include
    the correlation ID in structured log output when one is active.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        correlation_id = get_correlation_id()
        if correlation_id:
            record.correlation_id = correlation_id
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
