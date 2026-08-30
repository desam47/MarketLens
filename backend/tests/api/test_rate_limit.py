"""
Tests for backend/api/rate_limit.py

Tests the Redis-backed and in-memory rate limiters with focus on:
- Fallback behavior when Redis is unavailable
- Proper rate limiting enforcement
- IP-based rate limiting
- Header correctness on allowed/rejected requests

RateLimitMiddleware is a ``BaseHTTPMiddleware`` subclass. Tests drive it
via the standard ``middleware.dispatch(request, call_next)`` pattern.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from starlette.requests import Request
from starlette.responses import JSONResponse

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
        mock_redis.pipeline.return_value.__enter__ = MagicMock(
            return_value=mock_redis
        )
        mock_redis.pipeline.return_value.__exit__ = MagicMock(return_value=False)
        mock_redis.pipeline.return_value.execute.return_value = [1, True]

        with patch("backend.api.rate_limit.redis.Redis.from_url", return_value=mock_redis):
            limiter = RedisRateLimiter(max_requests=10, window_seconds=60)
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


class TestRateLimitMiddleware(unittest.TestCase):
    """Tests for RateLimitMiddleware as a BaseHTTPMiddleware subclass."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
        self.middleware = RateLimitMiddleware(
            self._downstream_app(), limiter=self.limiter
        )

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _downstream_app(self):
        """Minimal ASGI app that always returns 200."""
        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"ok": true}',
            })
        return app

    def _dispatch(self, method="POST", path="/api/data", ip="127.0.0.1"):
        """Build a Request and call middleware.dispatch()."""
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": (ip, 0),
        }
        request = Request(scope, None)

        async def call_next(req):
            return JSONResponse({"ok": True})

        return self._run(self.middleware.dispatch(request, call_next))

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

        # Exhaust the write-rate-limit window.
        for _ in range(_write_limiter.max_requests + 2):
            r = self.client.post("/api/market-data/ingestion/start", json={})

        h = self._headers_lower(r)
        self.assertEqual(r.status_code, 429)
        # Security headers must be present.
        self.assertIn("content-security-policy", h)
        self.assertIn("x-frame-options", h)


if __name__ == "__main__":
    unittest.main()
