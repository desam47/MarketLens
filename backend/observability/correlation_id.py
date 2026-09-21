"""
Correlation ID middleware for request tracking.

This module provides middleware that generates or extracts correlation IDs
for each request and makes them available throughout the request lifecycle
for logging and tracing purposes. It integrates with the structured logging
system so every log line automatically includes the request's correlation ID.
"""
import uuid
from collections.abc import Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Defer the context-var import so the module can be imported safely even
# when logging_enhanced hasn't been imported yet (e.g. during startup).
_logging_ctx: Callable[[], None] | None = None


def _get_set_correlation_id():
    """Lazily import set_correlation_id to avoid circular imports."""
    global _logging_ctx
    if _logging_ctx is None:
        try:
            from .logging_enhanced import set_correlation_id as _set

            _logging_ctx = _set
        except ImportError:
            def _logging_ctx(*_):
                return None  # no-op fallback
    return _logging_ctx


class CorrelationIdMiddleware:
    """Middleware that adds correlation ID to requests for tracking.

    A plain ASGI middleware (see SecurityHeadersMiddleware for why): the downstream app
    now runs in the same task, so the logging context var set here is visible to it
    directly instead of via BaseHTTPMiddleware's copied context.

    Each request gets a correlation ID (from the ``X-Correlation-ID`` header
    if present, otherwise a new UUID). The ID is:

    1. Stored on ``request.state`` for access in endpoints.
    2. Set in the logging context so all log lines include it.
    3. Echoed in the response ``X-Correlation-ID`` header.
    """

    def __init__(
        self,
        app: ASGIApp,
        header_name: str = "X-Correlation-ID",
        update_request_header: bool = True,
        generator: Callable[[], str] | None = None,
    ):
        self.app = app
        self.header_name = header_name
        self.update_request_header = update_request_header
        self.generator = generator or (lambda: str(uuid.uuid4()))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract or generate correlation ID
        correlation_id = Headers(scope=scope).get(self.header_name)
        if not correlation_id:
            correlation_id = self.generator()

        # Make it available on the request state (``request.state`` reads scope["state"]).
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        # Set it in the logging context for this async task
        set_corr = _get_set_correlation_id()
        set_corr(correlation_id)

        async def send_with_id(message: Message) -> None:
            # Add correlation ID to response headers for client tracking
            if self.update_request_header and message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[self.header_name] = correlation_id
            await send(message)

        try:
            # Process the request — all log lines inside here include the ID
            await self.app(scope, receive, send_with_id)
        finally:
            # Reset the context variable when the request ends so it doesn't
            # leak into concurrent or subsequent tasks
            set_corr(None)
