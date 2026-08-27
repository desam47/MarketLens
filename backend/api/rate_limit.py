"""
In-process rate limiting for write/mutation endpoints.

We rate-limit only `POST`/`PUT`/`DELETE` requests — read endpoints (`GET`)
are not throttled because they're already bounded by the underlying engine
state (e.g. regime endpoint just returns whatever the in-memory engine has).
Mutation endpoints (ingestion start/stop, watchlist edits) are the
realistic target because a runaway client (e.g. a stuck hot-reload loop,
or a hostile scraper) could otherwise spam the DB or trigger expensive
provider calls.

This is an in-process token bucket. Sufficient for single-instance dev /
self-hosting. For multi-instance production, replace with a Redis-backed
implementation behind the same `RateLimiter` interface.

Why not slowapi / fastapi-limiter: those pin the request handler signature
or require Redis. For a small, focused change this is clearer and has
zero new dependencies.
"""
import logging
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Methods that should be rate-limited. Read-only methods (GET, HEAD, OPTIONS)
# are excluded — they're cheap and bounded.
_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# Path prefixes that are exempt from rate limiting even for write methods.
# E.g. health checks, internal FastAPI docs, the /api/market-data/quote read
# endpoint that the dashboard polls every 30s.
_EXEMPT_PATH_PREFIXES = (
    "/api/health",
    "/api/system/status",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class InMemoryRateLimiter:
    """Sliding-window rate limiter keyed by client IP.

    For each IP we keep a deque of request timestamps within the last
    `window_seconds` seconds. A new request is allowed if the deque has
    fewer than `max_requests` entries. This is exact (not token-bucket
    approximate) and cheap — O(1) amortized per check.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # IP -> deque of monotonic timestamps
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Check whether `client_ip` may make a request right now.

        Returns (allowed, remaining). `remaining` is the count of requests
        the client can still make in the current window — useful for the
        `X-RateLimit-Remaining` response header.
        """
        ts = now if now is not None else time.monotonic()
        window_start = ts - self.window_seconds

        hits = self._hits[client_ip]
        # Evict timestamps that fell out of the window.
        while hits and hits[0] < window_start:
            hits.popleft()

        if len(hits) >= self.max_requests:
            # Reject. Don't evict — the client must wait.
            return False, 0

        # Allow. Record the hit.
        hits.append(ts)
        return True, max(0, self.max_requests - len(hits))

    def reset(self) -> None:
        """Clear all state. Useful for tests."""
        self._hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the limiter to write/mutation requests on all paths.

    Exempt paths (health, docs) are skipped. Read methods (GET) are skipped.
    On rejection: 429 with a `Retry-After` header.
    """

    def __init__(self, app, limiter: InMemoryRateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        if request.method not in _WRITE_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        # X-Forwarded-For first hop is the real client when behind a proxy.
        # Otherwise fall back to the direct connection's client.
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )

        allowed, remaining = self.limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(
                "rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "path": path,
                    "method": request.method,
                },
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down and try again later."
                },
                headers={
                    "Retry-After": str(self.limiter.window_seconds),
                    "X-RateLimit-Limit": str(self.limiter.max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        # Inform well-behaved clients of their remaining budget.
        response.headers["X-RateLimit-Limit"] = str(self.limiter.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
