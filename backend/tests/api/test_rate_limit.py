"""
Tests for backend/api/rate_limit.py

Tests the Redis-backed and in-memory rate limiters with focus on:
- Fallback behavior when Redis is unavailable
- Proper rate limiting enforcement
- IP-based rate limiting
- Header correctness on allowed/rejected requests

RateLimitMiddleware is a plain ASGI middleware. Tests drive it as an ASGI app
(``await middleware(scope, receive, send)``) and inspect the messages it sends.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from backend.api import rate_limit as rl
from backend.api.rate_limit import (
    InMemoryRateLimiter,
    RateLimitMiddleware,
    RedisRateLimiter,
)


class TestInMemoryRateLimiter(unittest.TestCase):
    """Tests for the fallback in-memory rate limiter."""

    def setUp(self):
        self.limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)

    def test_allows_under_limit(self):
        """Request under the limit should be allowed."""
        allowed, remaining = self.limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 4)

    def test_allows_up_to_limit(self):
        """Request exactly at the limit should be allowed."""
        for _ in range(5):
            allowed, _ = self.limiter.is_allowed("127.0.0.1")
            self.assertTrue(allowed)
        # 6th request should be rejected
        allowed, _ = self.limiter.is_allowed("127.0.0.1")
        self.assertFalse(allowed)

    def test_rejects_over_limit(self):
        """Request over the limit should be rejected."""
        for _ in range(5):
            self.limiter.is_allowed("127.0.0.1")
        allowed, remaining = self.limiter.is_allowed("127.0.0.1")
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)

    def test_different_ips_independent(self):
        """Different IPs should have independent rate limits."""
        for _ in range(5):
            self.limiter.is_allowed("127.0.0.1")
        # IP1 exhausted
        allowed1, _ = self.limiter.is_allowed("127.0.0.1")
        self.assertFalse(allowed1)
        # IP2 should still have budget
        allowed2, remaining2 = self.limiter.is_allowed("192.168.1.1")
        self.assertTrue(allowed2)
        self.assertEqual(remaining2, 4)

    def test_window_expiry(self):
        """Requests after window expiry should be allowed again."""
        # Exhaust the limit
        for _ in range(5):
            self.limiter.is_allowed("127.0.0.1")
        # Should be rejected (within window)
        allowed, _ = self.limiter.is_allowed("127.0.0.1")
        self.assertFalse(allowed)
        # After window expiry, simulated by reset
        self.limiter.reset()
        allowed, _ = self.limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)

    def test_reset_clears_all_state(self):
        """Reset should clear all IP state."""
        for _ in range(5):
            self.limiter.is_allowed("127.0.0.1")
        self.limiter.reset()
        allowed, remaining = self.limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 4)

    def test_idle_ips_are_pruned(self):
        """IPs whose hits all aged out must not accumulate forever."""
        limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)
        for i in range(50):
            limiter.is_allowed(f"10.0.0.{i}", now=1000.0)
        self.assertEqual(len(limiter._hits), 50)
        # A later request, one full window on, sweeps the expired IPs.
        limiter.is_allowed("192.168.0.1", now=1061.0)
        self.assertEqual(list(limiter._hits), ["192.168.0.1"])

    def test_total_ips_seen_counts_each_new_ip_once(self):
        limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)
        limiter.is_allowed("a", now=1.0)
        limiter.is_allowed("a", now=2.0)
        limiter.is_allowed("b", now=3.0)
        self.assertEqual(limiter._total_ips_seen, 2)


class TestRedisRateLimiterFallback(unittest.TestCase):
    """Tests for RedisRateLimiter fallback behavior when Redis is unavailable."""

    def _make_limiter(self):
        """Create a RedisRateLimiter with Redis disabled so it uses the fallback.

        We patch ``_settings.redis.enabled`` to False before construction so
        the limiter never attempts to connect to Redis. This is what these
        tests have always intended to exercise — the in-memory fallback path
        — independent of whether the test environment happens to have a
        running Redis instance.
        """
        with patch("backend.api.rate_limit._settings") as mock_settings:
            mock_settings.redis.enabled = False
            mock_settings.redis.url = "redis://localhost:6379"
            mock_settings.redis.password = None
            return RedisRateLimiter(max_requests=10, window_seconds=60)

    def test_uses_in_memory_when_redis_disabled(self):
        """Should use in-memory limiter when Redis is disabled."""
        limiter = self._make_limiter()
        allowed, remaining = limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 9)

    def test_fallback_maintains_separate_state(self):
        """Fallback limiter should have its own state."""
        limiter = self._make_limiter()
        # Make some requests
        for _ in range(3):
            limiter.is_allowed("127.0.0.1")
        # Should still be allowed (limit is 10)
        allowed, _ = limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        # Reset and verify
        limiter.reset()
        allowed, remaining = limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 9)

    def test_fallback_ip_independence(self):
        """Fallback should enforce per-IP limits."""
        limiter = self._make_limiter()
        ip1 = "10.0.0.1"
        ip2 = "10.0.0.2"
        # Exhaust ip1
        for _ in range(10):
            limiter.is_allowed(ip1)
        # ip1 exhausted
        allowed1, _ = limiter.is_allowed(ip1)
        self.assertFalse(allowed1)
        # ip2 should still work
        allowed2, _ = limiter.is_allowed(ip2)
        self.assertTrue(allowed2)


class TestRedisRateLimiterTimeoutFallback(unittest.TestCase):
    """Redis operations that time out must fall back to in-memory gracefully."""

    @patch("backend.api.rate_limit._settings")
    def test_pipeline_timeout_falls_back_to_in_memory(self, mock_settings):
        """A TimeoutError from the Redis pipeline must not propagate — the
        in-memory limiter must handle the request instead."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_pipeline = MagicMock()
        # Simulate a timeout on the execute() call.
        mock_pipeline.execute.side_effect = TimeoutError("Redis command timed out")
        mock_redis.pipeline.return_value = mock_pipeline

        with patch("backend.api.rate_limit.redis.Redis.from_url", return_value=mock_redis):
            limiter = RedisRateLimiter(max_requests=5, window_seconds=60)
            # Must not raise.
            allowed, remaining = limiter.is_allowed("127.0.0.1")
            # Fallback in-memory limiter must grant the request.
            self.assertTrue(allowed)
            self.assertEqual(remaining, 4)


class TestRedisRateLimiterWithMockedRedis(unittest.TestCase):
    """Tests for RedisRateLimiter with mocked Redis client."""

    @patch("backend.api.rate_limit._settings")
    def test_uses_redis_when_available(self, mock_settings):
        """Should use Redis when connection succeeds."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.pipeline.return_value.__enter__ = MagicMock(return_value=mock_redis)
        mock_redis.pipeline.return_value.__exit__ = MagicMock(return_value=False)
        mock_redis.pipeline.return_value.execute.return_value = [1, True]

        with patch("backend.api.rate_limit.redis.Redis.from_url", return_value=mock_redis):
            RedisRateLimiter(max_requests=10, window_seconds=60)
            # Verify Redis connection was attempted
            mock_redis.ping.assert_called()

    @patch("backend.api.rate_limit._settings")
    def test_falls_back_on_redis_failure(self, mock_settings):
        """Should fall back to in-memory on Redis connection failure."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        with patch("backend.api.rate_limit.redis.Redis") as mock_redis_class:
            mock_redis_class.side_effect = ConnectionError("Redis unavailable")

            limiter = RedisRateLimiter(max_requests=5, window_seconds=60)
            # Should fall back to in-memory
            allowed, remaining = limiter.is_allowed("127.0.0.1")
            self.assertTrue(allowed)
            self.assertEqual(remaining, 4)

    @patch("backend.api.rate_limit._settings")
    def test_hot_path_does_not_ping_redis(self, mock_settings):
        """is_allowed must cost one pipeline round-trip, not ping+ping+pipeline."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value.execute.return_value = [1, True]
        with patch("backend.api.rate_limit.redis.Redis.from_url", return_value=mock_redis):
            limiter = RedisRateLimiter(max_requests=10, window_seconds=60)
        mock_redis.ping.reset_mock()

        allowed, remaining = limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 9)
        mock_redis.ping.assert_not_called()

    @patch("backend.api.rate_limit._settings")
    def test_redis_failure_opens_circuit_breaker(self, mock_settings):
        """After a Redis failure, later calls skip Redis until the retry window passes."""
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value.execute.side_effect = ConnectionError("down")
        with patch("backend.api.rate_limit.redis.Redis.from_url", return_value=mock_redis):
            limiter = RedisRateLimiter(max_requests=5, window_seconds=60)

        limiter.is_allowed("127.0.0.1")  # fails, falls back, opens the breaker
        self.assertEqual(mock_redis.pipeline.call_count, 1)
        allowed, _ = limiter.is_allowed("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(mock_redis.pipeline.call_count, 1)  # Redis skipped


class _FakeTime:
    """Stand-in for the ``time`` module inside rate_limit: a manually advanced clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


class TestRedisRateLimiterLifecycle(unittest.TestCase):
    """Redis going down / coming back — startup failure and breaker recovery."""

    def _limiter(self, mock_settings, fake_redis, clock, **kw):
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "redis://localhost:6379"
        mock_settings.redis.password = None
        with (
            patch("backend.api.rate_limit.redis.Redis.from_url", return_value=fake_redis),
            patch("backend.api.rate_limit.time", clock),
        ):
            return RedisRateLimiter(max_requests=10, window_seconds=60, **kw)

    @patch("backend.api.rate_limit._settings")
    def test_failed_startup_ping_keeps_client_and_later_uses_redis(self, mock_settings):
        """A slow/booting Redis at startup must not pin the limiter to the
        in-memory fallback for the life of the process."""
        clock = _FakeTime()
        fake = MagicMock()
        fake.ping.side_effect = TimeoutError("slower than the socket timeout")
        fake.pipeline.return_value.execute.return_value = [1, True]
        limiter = self._limiter(mock_settings, fake, clock)

        self.assertIs(limiter._redis_client, fake, "client must survive a failed startup ping")

        with patch("backend.api.rate_limit.time", clock):
            # Breaker is open right after the failed probe: served in-memory.
            allowed, _ = limiter.is_allowed("1.2.3.4")
            self.assertTrue(allowed)
            fake.pipeline.assert_not_called()

            # Retry window elapses; Redis is healthy now -> it is used again.
            clock.now += rl._REDIS_RETRY_AFTER_SECONDS + 1
            allowed, remaining = limiter.is_allowed("1.2.3.4")
            self.assertTrue(allowed)
            self.assertEqual(remaining, 9)
            fake.pipeline.assert_called_once()

    @patch("backend.api.rate_limit._settings")
    def test_client_construction_failure_still_falls_back(self, mock_settings):
        mock_settings.redis.enabled = True
        mock_settings.redis.url = "not-a-valid-url"
        mock_settings.redis.password = None
        with patch(
            "backend.api.rate_limit.redis.Redis.from_url", side_effect=ValueError("bad url")
        ):
            limiter = RedisRateLimiter(max_requests=5, window_seconds=60)
        self.assertIsNone(limiter._redis_client)
        allowed, remaining = limiter.is_allowed("1.2.3.4")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 4)

    @patch("backend.api.rate_limit._settings")
    def test_breaker_closes_after_retry_window(self, mock_settings):
        clock = _FakeTime()
        fake = MagicMock()
        fake.ping.return_value = True
        fake.pipeline.return_value.execute.side_effect = [ConnectionError("down"), [1, True]]
        limiter = self._limiter(mock_settings, fake, clock)

        with patch("backend.api.rate_limit.time", clock):
            limiter.is_allowed("a")  # fails -> breaker opens
            limiter.is_allowed("a")  # skipped
            self.assertEqual(fake.pipeline.call_count, 1)

            clock.now += rl._REDIS_RETRY_AFTER_SECONDS - 0.1
            limiter.is_allowed("a")  # still inside the window
            self.assertEqual(fake.pipeline.call_count, 1)

            clock.now += 0.2  # window elapsed
            allowed, remaining = limiter.is_allowed("a")
            self.assertEqual(fake.pipeline.call_count, 2)
            self.assertTrue(allowed)
            self.assertEqual(remaining, 9)
            self.assertFalse(limiter._redis_failing)

    @patch("backend.api.rate_limit._settings")
    def test_outage_warns_once_and_recovery_is_logged(self, mock_settings):
        clock = _FakeTime()
        fake = MagicMock()
        fake.ping.return_value = True
        fake.pipeline.return_value.execute.side_effect = [
            ConnectionError("down"),
            ConnectionError("still down"),
            [1, True],
        ]
        limiter = self._limiter(mock_settings, fake, clock)

        with (
            patch("backend.api.rate_limit.time", clock),
            self.assertLogs("backend.api.rate_limit", level="INFO") as logs,
        ):
            limiter.is_allowed("a")  # 1st failure -> warn
            clock.now += rl._REDIS_RETRY_AFTER_SECONDS + 1
            limiter.is_allowed("a")  # 2nd failure -> no new warning
            clock.now += rl._REDIS_RETRY_AFTER_SECONDS + 1
            limiter.is_allowed("a")  # success -> recovery info

        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        recovered = [r for r in logs.records if "recovered" in r.getMessage()]
        self.assertEqual(len(warnings), 1, [r.getMessage() for r in warnings])
        self.assertEqual(len(recovered), 1)

    @patch("backend.api.rate_limit._settings")
    def test_stats_do_not_ping_while_breaker_is_open(self, mock_settings):
        """get_stats() must not spend a socket timeout probing a known-down Redis."""
        clock = _FakeTime()
        fake = MagicMock()
        fake.ping.return_value = True
        fake.pipeline.return_value.execute.side_effect = ConnectionError("down")
        limiter = self._limiter(mock_settings, fake, clock)

        with patch("backend.api.rate_limit.time", clock):
            limiter.is_allowed("a")  # opens the breaker
            fake.ping.reset_mock()
            stats = limiter.get_stats()
        fake.ping.assert_not_called()
        self.assertEqual(stats["backend"], "in_memory")
        self.assertFalse(stats["redis_available"])

    @patch("backend.api.rate_limit._settings")
    def test_reset_clears_breaker_and_outage_flag(self, mock_settings):
        clock = _FakeTime()
        fake = MagicMock()
        fake.ping.return_value = True
        fake.pipeline.return_value.execute.side_effect = ConnectionError("down")
        limiter = self._limiter(mock_settings, fake, clock)
        with patch("backend.api.rate_limit.time", clock):
            limiter.is_allowed("a")
        self.assertTrue(limiter._redis_failing)
        limiter.reset()
        self.assertFalse(limiter._redis_failing)
        self.assertEqual(limiter._redis_down_until, 0.0)


class TestRateLimitMiddleware(unittest.TestCase):
    """Tests for RateLimitMiddleware driven as a raw ASGI app."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
        self.middleware = RateLimitMiddleware(self._downstream_app(), limiter=self.limiter)

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _downstream_app(self):
        """Minimal ASGI app that always returns 200."""

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"ok": true}',
                }
            )

        return app

    def _dispatch(self, method="POST", path="/api/data", ip="127.0.0.1"):
        """Run the middleware as an ASGI app; return an object with .status_code / .headers."""
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": (ip, 0),
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        self._run(self.middleware(scope, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")

        class _Response:
            status_code = start["status"]
            headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}

        return _Response

    def test_read_methods_not_rate_limited(self):
        """GET/HEAD/OPTIONS should pass through without rate limiting."""
        response = self._dispatch(method="GET")
        self.assertEqual(response.status_code, 200)

    def test_exempt_paths_skip_rate_limiting(self):
        """Health and docs paths should be exempt."""
        response = self._dispatch(method="POST", path="/api/health")
        self.assertEqual(response.status_code, 200)

    def test_rate_limit_rejection_returns_429(self):
        """Requests over the limit should get 429."""
        # Exhaust the limit
        for _ in range(3):
            self.limiter.is_allowed("127.0.0.1")

        response = self._dispatch(method="POST", path="/api/watchlist")
        self.assertEqual(response.status_code, 429)
        # Retry-After header must be present
        self.assertEqual(response.headers["retry-after"], "60")

    def test_rate_limit_429_includes_ratelimit_headers(self):
        """429 response must include X-RateLimit-Limit and X-RateLimit-Remaining."""
        # Exhaust the limit
        for _ in range(3):
            self.limiter.is_allowed("127.0.0.1")

        response = self._dispatch(method="POST", path="/api/watchlist")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["x-ratelimit-limit"], "3")
        self.assertEqual(response.headers["x-ratelimit-remaining"], "0")

    def test_allowed_response_includes_remaining_header(self):
        """Allowed response should include X-RateLimit-Remaining."""
        response = self._dispatch(method="POST", path="/api/watchlist")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-ratelimit-limit"], "3")
        self.assertEqual(response.headers["x-ratelimit-remaining"], "2")


    def test_per_endpoint_limit_headers_are_not_overwritten(self):
        """A route's own limiter rejected the request: report its limit, not the global one."""

        async def rejected_by_route_limiter(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [(b"x-ratelimit-limit", b"10"), (b"x-ratelimit-remaining", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b"{}"})

        self.middleware = RateLimitMiddleware(rejected_by_route_limiter, limiter=self.limiter)
        response = self._dispatch(method="POST", path="/api/ai/jobs")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["x-ratelimit-limit"], "10")
        self.assertEqual(response.headers["x-ratelimit-remaining"], "0")


class TestRateLimitMiddlewareIntegration(unittest.TestCase):
    """Integration test: verify 429 responses have security headers from
    the real SecurityHeadersMiddleware in the full app stack.

    BaseHTTPMiddleware's call_next bubbles responses correctly — the
    short-circuit JSONResponse reaches outer middlewares without any
    inline security header hack.
    """

    def setUp(self):
        from starlette.testclient import TestClient

        from backend.api.main import app

        self.client = TestClient(app)
        # Reset the limiter between tests.
        from backend.api.main import _write_limiter

        _write_limiter.reset()

    def _headers_lower(self, response) -> dict:
        return {k.lower(): v for k, v in response.headers.items()}

    def test_429_responses_get_security_headers(self):
        """A 429 from the rate-limit middleware must receive security headers
        from the outer SecurityHeadersMiddleware via BaseHTTPMiddleware's
        call_next chain."""
        from backend.api.main import _write_limiter

        _write_limiter.reset()

        # Exhaust the write-rate-limit window. /ingestion/start is only a convenient write endpoint
        # here; started for real, it left the shared ingestion service's daemon thread (and its live
        # loops) running for the rest of the session, so stub start().
        from unittest.mock import patch

        from backend.market_data.services.ingestion_service import ingestion_service

        with patch.object(ingestion_service, "start"):
            for _ in range(_write_limiter.max_requests + 2):
                r = self.client.post("/api/market-data/ingestion/start", json={})

        h = self._headers_lower(r)
        self.assertEqual(r.status_code, 429)
        # Security headers must be present.
        self.assertIn("content-security-policy", h)
        self.assertIn("x-frame-options", h)


if __name__ == "__main__":
    unittest.main()
