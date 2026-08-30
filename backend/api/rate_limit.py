"""
Redis-backed rate limiting for write/mutation endpoints.

We rate-limit only `POST`/`PUT`/`DELETE` requests — read endpoints (`GET`)
are not throttled because they're already bounded by the underlying engine
state (e.g. regime endpoint just returns whatever the in-memory engine has).
Mutation endpoints (ingestion start/stop, watchlist edits) are the
realistic target because a runaway client (e.g. a stuck hot-reload loop,
or a hostile scraper) could otherwise spam the DB or trigger expensive
provider calls.

This is a Redis-backed rate limiter using the fixed window algorithm with
INCR and EXPIRE commands. It works across multiple instances for distributed
rate limiting.

Falls back to in-memory token bucket if Redis is unavailable.
"""
import logging
import time
from collections import defaultdict, deque
from typing import Optional

import redis
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.security_headers import SecurityHeadersMiddleware
from ..config.settings import settings as _settings

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


class RedisRateLimiter:
    """Redis-backed fixed window rate limiter keyed by client IP.

    Uses Redis INCR with EXPIRE to implement a fixed window counter.
    Each IP gets a key like "rate_limit:{ip}" that tracks request count
    in the current window. The key automatically expires after window_seconds.

    Falls back to in-memory rate limiter if Redis is unavailable.
    """

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._fallback_limiter = InMemoryRateLimiter(max_requests, window_seconds)
        self._redis_client: Optional[redis.Redis] = None
        self._initialize_redis()

    def _initialize_redis(self):
        """Initialize Redis connection if enabled in settings."""
        if not _settings.redis.enabled:
            logger.info("Redis is disabled, using in-memory rate limiter fallback")
            return

        try:
            # Parse Redis URL to handle password if needed
            if _settings.redis.password:
                self._redis_client = redis.Redis.from_url(
                    _settings.redis.url,
                    password=_settings.redis.password,
                    decode_responses=False,  # Keep as bytes for INCR operations
                )
            else:
                self._redis_client = redis.Redis.from_url(
                    _settings.redis.url,
                    decode_responses=False,
                )

            # Test connection
            self._redis_client.ping()
            logger.info(f"Connected to Redis at {_settings.redis.url} for rate limiting")

        except Exception as e:
            logger.warning(f"Failed to initialize Redis connection for rate limiting: {e}")
            self._redis_client = None

    def _is_redis_available(self) -> bool:
        """Check if Redis is available and connected."""
        if not self._redis_client:
            return False
        try:
            return self._redis_client.ping()
        except Exception:
            return False

    def is_allowed(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Check whether `client_ip` may make a request right now.

        Returns (allowed, remaining). `remaining` is the count of requests
        the client can still make in the current window — useful for the
        `X-RateLimit-Remaining` response header.
        """
        # Use Redis if available, otherwise fall back to in-memory
        if self._is_redis_available():
            return self._is_allowed_redis(client_ip, now)
        else:
            return self._fallback_limiter.is_allowed(client_ip, now)

    def _is_allowed_redis(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Redis-backed rate limit check using fixed window algorithm."""
        # Check if Redis is available before proceeding
        if not self._is_redis_available():
            logger.warning("Redis not available for rate limiting, falling back to in-memory")
            return self._fallback_limiter.is_allowed(client_ip, now)

        # Get a local reference to avoid issues if self._redis_client changes
        redis_client = self._redis_client
        if redis_client is None:
            logger.warning("Redis client is None, falling back to in-memory")
            return self._fallback_limiter.is_allowed(client_ip, now)

        ts = now if now is not None else time.time()
        # Use integer timestamp for Redis key to bucket by fixed window
        window_key = int(ts // self.window_seconds)
        redis_key = f"rate_limit:{client_ip}:{window_key}"

        try:
            # Double-check redis_client is not None before using it
            if redis_client is None:
                logger.warning("Redis client became None before pipeline operation, falling back to in-memory")
                return self._fallback_limiter.is_allowed(client_ip, now)

            # INCR the key and get the new value
            pipe = redis_client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, self.window_seconds)
            results = pipe.execute()
            current_count = results[0]  # INCR result

            if current_count > self.max_requests:
                # Reject. Don't reset the counter — client must wait for window to expire.
                return False, 0

            # Allow. Calculate remaining requests in current window.
            remaining = max(0, self.max_requests - current_count)
            return True, remaining

        except Exception as e:
            logger.warning(f"Redis rate limiting failed, falling back to in-memory: {e}")
            # Fall back to in-memory limiter on Redis failure
            return self._fallback_limiter.is_allowed(client_ip, now)

    def reset(self) -> None:
        """Clear all state. Useful for tests."""
        # Reset fallback limiter
        self._fallback_limiter.reset()
        # Note: We don't flush Redis keys as that could affect other clients
        # In a test environment, you might want to flush specific keys

    def get_stats(self) -> dict:
        """Return observability counters for the metrics endpoint.

        Includes which backend is active (redis vs in-memory fallback)
        and the fallback's lifetime counters. The Redis backend doesn't
        expose request counts here — those are aggregated in Redis itself
        if needed (e.g. via INFO commandstats).
        """
        return {
            "backend": "redis" if self._is_redis_available() else "in_memory",
            "redis_enabled": _settings.redis.enabled,
            "redis_available": self._is_redis_available(),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "fallback": self._fallback_limiter.get_stats(),
        }


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
        # Lifetime counters — useful for observability/metrics.
        self._total_allowed: int = 0
        self._total_rejected: int = 0
        self._total_ips_seen: int = 0

    def is_allowed(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Check whether `client_ip` may make a request right now.

        Returns (allowed, remaining). `remaining` is the count of requests
        the client can still make in the current window — useful for the
        `X-RateLimit-Remaining` response header.
        """
        ts = now if now is not None else time.monotonic()
        window_start = ts - self.window_seconds

        hits = self._hits[client_ip]
        if client_ip not in self._hits:
            self._total_ips_seen += 1
        # Evict timestamps that fell out of the window.
        while hits and hits[0] < window_start:
            hits.popleft()

        if len(hits) >= self.max_requests:
            # Reject. Don't evict — the client must wait.
            self._total_rejected += 1
            return False, 0

        # Allow. Record the hit.
        hits.append(ts)
        self._total_allowed += 1
        return True, max(0, self.max_requests - len(hits))

    def reset(self) -> None:
        """Clear all state. Useful for tests."""
        self._hits.clear()
        self._total_allowed = 0
        self._total_rejected = 0
        self._total_ips_seen = 0

    def get_stats(self) -> dict:
        """Return observability counters for the metrics endpoint.

        Fields are stable — adding new keys is OK, removing or renaming
        existing ones would break dashboards.
        """
        total = self._total_allowed + self._total_rejected
        return {
            "tracked_ips": len(self._hits),
            "total_allowed": self._total_allowed,
            "total_rejected": self._total_rejected,
            "total_requests": total,
            "reject_rate": (
                round(self._total_rejected / total, 4) if total > 0 else 0.0
            ),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
        }


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the limiter to write/mutation requests on all paths.

    Exempt paths (health, docs) are skipped. Read methods (GET) are skipped.
    On rejection: 429 with a `Retry-After` header.

    **FastAPI exception handling:** FastAPI's ``ExceptionMiddleware`` intercepts
    HTTPExceptions raised from within endpoint handlers. When this middleware
    short-circuits with a 429 (without calling ``call_next``), the
    ``ExceptionMiddleware`` layer sits *outside* the ``BaseHTTPMiddleware``
    chain and does not call ``call_next`` — so any ``BaseHTTPMiddleware``
    outside RateLimitMiddleware (e.g. ``SecurityHeadersMiddleware``) is never
    reached. To ensure every 429 carries the full security-header baseline,
    we inline them here rather than relying on the middleware chain.
    """

    def __init__(self, app, limiter: RedisRateLimiter) -> None:
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
            # Inline security headers so 429 responses carry the full
            # security baseline (bypasses FastAPI's ExceptionMiddleware
            # which sits outside the BaseHTTPMiddleware chain).
            headers = {
                "Retry-After": str(self.limiter.window_seconds),
                "X-RateLimit-Limit": str(self.limiter.max_requests),
                "X-RateLimit-Remaining": "0",
            }
            headers.update(SecurityHeadersMiddleware._static_headers())
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down and try again later."
                },
                headers=headers,
            )

        response = await call_next(request)
        # Inform well-behaved clients of their remaining budget.
        response.headers["X-RateLimit-Limit"] = str(self.limiter.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response