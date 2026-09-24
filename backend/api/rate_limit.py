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

import redis
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


# Socket timeout (seconds) for the limiter's Redis client, and how long the
# limiter skips Redis entirely after a failure before trying it again.
_REDIS_SOCKET_TIMEOUT = 0.25
_REDIS_RETRY_AFTER_SECONDS = 5.0


class RedisRateLimiter:
    """Redis-backed fixed window rate limiter keyed by client IP.

    Uses Redis INCR with EXPIRE to implement a fixed window counter.
    Each IP gets a key like "rate_limit:{ip}" that tracks request count
    in the current window. The key automatically expires after window_seconds.

    Falls back to in-memory rate limiter if Redis is unavailable.
    """

    def __init__(self, max_requests: int, window_seconds: int, name: str = "global"):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # ``name`` namespaces the Redis key so the global write-method
        # middleware and per-endpoint limiters do not share a counter.
        # Without it, every request hits two limiters but they INCR
        # the same key, so the tighter cap is reached at half its real
        # budget. The in-memory fallback's per-IP dict already keys
        # separately by limiter instance, so it does not need namespacing.
        self.name = name
        self._fallback_limiter = InMemoryRateLimiter(max_requests, window_seconds)
        self._redis_client: redis.Redis | None = None
        # Monotonic deadline before which Redis is skipped (circuit breaker
        # opened by a failed call), so a dead Redis costs one short timeout
        # per retry window rather than one per request.
        self._redis_down_until = 0.0
        # True while Redis is considered down; lets us warn once per outage
        # (not on every retry) and log the recovery.
        self._redis_failing = False
        self._initialize_redis()

    def _initialize_redis(self):
        """Initialize Redis connection if enabled in settings."""
        if not _settings.redis.enabled:
            logger.info("Redis is disabled, using in-memory rate limiter fallback")
            return

        try:
            # Short socket timeouts: this client is called synchronously
            # from request handling (an async middleware), so an
            # unresponsive Redis must fail fast instead of stalling the
            # event loop for redis-py's default (no timeout at all).
            kwargs: dict = {
                "decode_responses": False,  # Keep as bytes for INCR operations
                "socket_connect_timeout": _REDIS_SOCKET_TIMEOUT,
                "socket_timeout": _REDIS_SOCKET_TIMEOUT,
            }
            if _settings.redis.password:
                kwargs["password"] = _settings.redis.password
            # ``from_url`` is lazy — it builds a connection pool and does no I/O.
            self._redis_client = redis.Redis.from_url(_settings.redis.url, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to create Redis client for rate limiting: {e}")
            self._redis_client = None
            return

        # Startup probe. A failure here must NOT discard the client: with the
        # short socket timeout above, a Redis that is merely slow or still
        # booting (docker-compose start order) would otherwise pin this
        # limiter to per-process counters for the life of the process, because
        # nothing ever re-creates the client. Keep it and let the circuit
        # breaker retry — the first requests use the in-memory fallback.
        try:
            self._redis_client.ping()
            logger.info(f"Connected to Redis at {_settings.redis.url} for rate limiting")
        except Exception as e:
            self._mark_redis_down(e)

    def _mark_redis_down(self, exc: Exception) -> None:
        """Open the circuit breaker; warn only on the first failure of an outage."""
        first_failure = not self._redis_failing
        self._redis_failing = True
        self._redis_down_until = time.monotonic() + _REDIS_RETRY_AFTER_SECONDS
        if first_failure:
            logger.warning(
                f"Redis rate limiting unavailable, using in-memory fallback "
                f"(retrying every {_REDIS_RETRY_AFTER_SECONDS:.0f}s): {exc}"
            )

    def _is_redis_available(self) -> bool:
        """Check if Redis is available and connected."""
        if not self._redis_client:
            return False
        if time.monotonic() < self._redis_down_until:
            return False  # breaker open: don't spend a socket timeout probing
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
        # Use Redis if configured and not in its post-failure cool-down,
        # otherwise fall back to in-memory. No availability ping here: the
        # pipeline call below already fails (and falls back) on its own,
        # and pinging first cost two extra Redis round-trips on every
        # write request.
        if self._redis_client is not None and time.monotonic() >= self._redis_down_until:
            return self._is_allowed_redis(client_ip, now)
        return self._fallback_limiter.is_allowed(client_ip, now)

    def _is_allowed_redis(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Redis-backed rate limit check using fixed window algorithm."""
        # Get a local reference to avoid issues if self._redis_client changes
        redis_client = self._redis_client
        if redis_client is None:
            return self._fallback_limiter.is_allowed(client_ip, now)

        ts = now if now is not None else time.time()
        # Use integer timestamp for Redis key to bucket by fixed window
        window_key = int(ts // self.window_seconds)
        # Namespace by ``self.name`` so per-endpoint limiters do not
        # share their Redis key with the global write-method middleware.
        # See RedisRateLimiter.__init__ docstring for the rationale.
        redis_key = f"rate_limit:{self.name}:{client_ip}:{window_key}"

        try:
            # INCR the key and get the new value
            pipe = redis_client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, self.window_seconds)
            results = pipe.execute()
            current_count = results[0]  # INCR result
            if self._redis_failing:
                self._redis_failing = False
                logger.info("Redis rate limiting recovered")

            if current_count > self.max_requests:
                # Reject. Don't reset the counter — client must wait for window to expire.
                return False, 0

            # Allow. Calculate remaining requests in current window.
            remaining = max(0, self.max_requests - current_count)
            return True, remaining

        except Exception as e:
            self._mark_redis_down(e)
            # Fall back to in-memory limiter on Redis failure
            return self._fallback_limiter.is_allowed(client_ip, now)

    def reset(self) -> None:
        """Clear all state. Useful for tests."""
        # Reset fallback limiter
        self._fallback_limiter.reset()
        self._redis_down_until = 0.0
        self._redis_failing = False
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

    def stats(self) -> dict:
        """Alias for ``get_stats()`` for callers that prefer the shorter name."""
        return self.get_stats()


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
        self._last_prune: float | None = None

    def _prune_idle_ips(self, ts: float) -> None:
        """Drop IPs whose hits have all aged out of the window.

        ``_hits`` otherwise keeps one entry per IP ever seen, forever — and
        since the key can be an attacker-chosen X-Forwarded-For value, that
        is an unbounded memory leak. Swept at most once per window so the
        cost stays amortized-O(1) per request.
        """
        if self._last_prune is not None and ts - self._last_prune < self.window_seconds:
            return
        self._last_prune = ts
        window_start = ts - self.window_seconds
        for ip in [ip for ip, hits in self._hits.items() if not hits or hits[-1] < window_start]:
            del self._hits[ip]

    def is_allowed(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Check whether `client_ip` may make a request right now.

        Returns (allowed, remaining). `remaining` is the count of requests
        the client can still make in the current window — useful for the
        `X-RateLimit-Remaining` response header.
        """
        ts = now if now is not None else time.monotonic()
        window_start = ts - self.window_seconds
        self._prune_idle_ips(ts)

        # Membership must be checked before indexing: ``_hits`` is a
        # defaultdict, so indexing first creates the key and the "new IP"
        # counter below would never fire.
        if client_ip not in self._hits:
            self._total_ips_seen += 1
        hits = self._hits[client_ip]
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
        self._last_prune = None

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
            "reject_rate": (round(self._total_rejected / total, 4) if total > 0 else 0.0),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
        }


class RateLimitMiddleware:
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

    def __init__(self, app: ASGIApp, limiter: RedisRateLimiter) -> None:
        # A plain ASGI middleware (not BaseHTTPMiddleware, ~180 us per request): the
        # overwhelming majority of traffic is GET, which just passes straight through.
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _WRITE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        allowed, remaining = self.limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(
                "rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "path": path,
                    "method": scope["method"],
                },
            )
            # Inline security headers so 429 responses carry the full
            # security baseline (this short-circuit never reaches the inner
            # SecurityHeadersMiddleware).
            headers = {
                "Retry-After": str(self.limiter.window_seconds),
                "X-RateLimit-Limit": str(self.limiter.max_requests),
                "X-RateLimit-Remaining": "0",
            }
            headers.update(SecurityHeadersMiddleware._static_headers())
            response = JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down and try again later."},
                headers=headers,
            )
            await response(scope, receive, send)
            return

        async def send_with_limit_headers(message: Message) -> None:
            # Inform well-behaved clients of their remaining budget. A per-endpoint
            # limiter (``check_rate_limit``) that rejected the request has already set
            # its own, tighter numbers; keep those rather than reporting the global cap.
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                if "x-ratelimit-limit" not in headers:
                    headers["X-RateLimit-Limit"] = str(self.limiter.max_requests)
                    headers["X-RateLimit-Remaining"] = str(remaining)
            await send(message)

        await self.app(scope, receive, send_with_limit_headers)


# ----------------------------------------------------------------------
# Per-endpoint rate limiters (v2.1 Item 1.10)
# ----------------------------------------------------------------------
#
# These are tighter second-tier limits applied to the most expensive
# write endpoints on top of the global write-method middleware
# (30 req/min). They exist so a runaway client cannot hammer the LLM
# (AI), pollute the alert registration set (alerts), or queue up
# long-running backtests (backtest).
#
# Stacking is intentional: the global middleware caps total write
# traffic per IP at 30/min, and these add a per-endpoint sub-cap.

# AI — LLM calls are slow and expensive. 10/min is generous for a UI
# but stops a stuck script from spinning up a cost run.
_ai_limiter = RedisRateLimiter(max_requests=10, window_seconds=60, name="ai")

# Alerts — moderate cost (DB writes + engine registration). 30/min
# matches the global write cap, but tracks the IP independently so a
# heavy scanner run doesn't crowd out the user.
_alerts_limiter = RedisRateLimiter(max_requests=30, window_seconds=60, name="alerts")

# Backtest — full backtests and walk-forward analyses can take many
# minutes. 5/min per IP is plenty for a UI and stops accidental
# double-clicks from queueing expensive runs.
_backtest_limiter = RedisRateLimiter(max_requests=5, window_seconds=60, name="backtest")


def _client_ip(request: Request) -> str:
    """Identify the client for rate limiting: the connection's peer address.

    Deliberately does NOT read ``X-Forwarded-For`` itself. That header is
    client-controlled, so trusting it here let any caller dodge the limit
    by sending a different value on every request (and mint unlimited
    limiter keys). Behind a reverse proxy, run uvicorn with
    ``--proxy-headers --forwarded-allow-ips=<proxy ip>``: it then rewrites
    ``request.client`` from the header, but only for requests that really
    arrived from a trusted proxy.

    Shared by ``RateLimitMiddleware`` and ``check_rate_limit`` so the
    global and per-endpoint limiters identify clients identically.
    """
    return request.client.host if request.client else "unknown"


def check_rate_limit(limiter: RedisRateLimiter):
    """Build a FastAPI dependency that enforces ``limiter`` for one endpoint.

    Usage::

        @router.post("/expensive")
        async def expensive_endpoint(
            _rl: None = Depends(check_rate_limit(_ai_limiter)),
        ):
            ...

    The dependency is independent of the global ``RateLimitMiddleware``:
    both run, and the stricter one wins (i.e. the per-endpoint limit
    fires before the global cap is reached).
    """

    async def _dep(request: Request) -> None:
        client_ip = _client_ip(request)
        allowed, remaining = limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(
                "per-endpoint rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "path": request.url.path,
                    "method": request.method,
                    "limiter_max": limiter.max_requests,
                    "limiter_window": limiter.window_seconds,
                },
            )
            # Reuse the same header style as the middleware so clients
            # see a consistent shape whether they hit the global cap
            # or a per-endpoint cap.
            headers = {
                "Retry-After": str(limiter.window_seconds),
                "X-RateLimit-Limit": str(limiter.max_requests),
                "X-RateLimit-Remaining": "0",
            }
            headers.update(SecurityHeadersMiddleware._static_headers())
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again later.",
                headers=headers,
            )

    return _dep
