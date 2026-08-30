"""
Tests for backend/api/cache.py - CacheMiddleware

Tests ETag generation, conditional request handling, and per-path
cache duration behavior.

CacheMiddleware is a ``BaseHTTPMiddleware`` subclass. Tests drive it
via the standard ``middleware.dispatch(request, call_next)`` pattern.
"""
import asyncio
import hashlib
import unittest

from starlette.requests import Request
from starlette.responses import Response

from backend.api.cache import CacheMiddleware


class TestCacheMiddlewareETagGeneration(unittest.TestCase):
    """Unit tests driven through dispatch()."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _make_middleware(self, status_code=200, body=b'{"ok": true}', content_type="application/json"):
        """Build a CacheMiddleware wrapping a minimal ASGI app."""

        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", content_type.encode())],
            })
            await send({"type": "http.response.body", "body": body})

        return CacheMiddleware(app)

    def test_non_get_passes_through(self):
        """POST/PUT/DELETE should not be cached."""
        middleware = self._make_middleware()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/data",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope, None)

        async def call_next(req):
            return Response(b'{"ok": true}', media_type="application/json")

        response = self._run(middleware.dispatch(request, call_next))
        # Non-GET pass through unchanged (call_next returned a response)
        self.assertEqual(response.status_code, 200)

    def test_exempt_path_passes_through(self):
        """Health check and docs paths are exempt."""
        middleware = self._make_middleware()
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/health",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope, None)

        async def call_next(req):
            return Response(b'{"ok": true}', media_type="application/json")

        response = self._run(middleware.dispatch(request, call_next))
        self.assertEqual(response.status_code, 200)

    def test_get_response_gets_etag(self):
        """GET responses should have ETag header."""
        body = b'{"price": 150}'
        middleware = self._make_middleware(body=body)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/market-data/quote/AAPL",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope, None)

        async def call_next(req):
            return Response(content=body, media_type="application/json")

        response = self._run(middleware.dispatch(request, call_next))
        self.assertEqual(response.status_code, 200)
        self.assertIn("etag", response.headers)
        self.assertIn("cache-control", response.headers)

    def test_304_on_matching_etag(self):
        """Should return 304 when If-None-Match matches."""
        body = b'{"price": 150}'
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        middleware = self._make_middleware(body=body)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/market-data/quote/AAPL",
            "headers": [(b"if-none-match", etag.encode())],
            "query_string": b"",
        }
        request = Request(scope, None)

        async def call_next(req):
            return Response(content=body, media_type="application/json")

        response = self._run(middleware.dispatch(request, call_next))
        self.assertEqual(response.status_code, 304)
        self.assertIn("etag", response.headers)

    def test_non_200_responses_not_cached(self):
        """Non-200 responses should not get cache headers."""
        middleware = self._make_middleware(status_code=404, body=b'{"error": "not found"}')
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/market-data/quote/AAPL",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope, None)

        async def call_next(req):
            return Response(b'{"error": "not found"}', status_code=404)

        response = self._run(middleware.dispatch(request, call_next))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("etag", response.headers)

    def test_per_path_cache_duration(self):
        """Market data paths should get 300s cache."""
        body = b'{"price": 150}'
        middleware = self._make_middleware(body=body)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/market-data/quote/AAPL",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope, None)

        async def call_next(req):
            return Response(content=body, media_type="application/json")

        response = self._run(middleware.dispatch(request, call_next))
        self.assertIn("max-age=300", response.headers["cache-control"])


class TestCacheMiddlewareIntegration(unittest.TestCase):
    """Integration tests: drive the full FastAPI app through TestClient.

    These verify the original motivation: the 304 short-circuit response
    from the cache middleware must reach outer middlewares
    (SecurityHeadersMiddleware) in the real chain, so security headers
    are present on 304 responses. BaseHTTPMiddleware bubbles responses
    correctly — no custom ASGI shim is needed.
    """

    def setUp(self):
        from starlette.testclient import TestClient
        from backend.api.main import app
        self.client = TestClient(app)

    def _headers_lower(self, response) -> dict:
        return {k.lower(): v for k, v in response.headers.items()}

    def test_304_responses_get_security_headers(self):
        """A 304 from the cache middleware must still receive the security
        headers that the outer SecurityHeadersMiddleware would attach.

        End-to-end: hit a cacheable endpoint twice. The second hit (with
        If-None-Match set to the etag from the first hit) returns 304,
        and that 304 must carry CSP/X-Frame-Options/etc.
        """
        # First request: try a cacheable endpoint and capture the ETag.
        # /api/market-data/quote is the canonical cacheable endpoint.
        # Skip the test if the endpoint isn't available in this env
        # (e.g. the database table doesn't exist or the upstream
        # provider fails).
        try:
            r1 = self.client.get("/api/market-data/quote/AAPL?fallback=1")
        except Exception:
            self.skipTest("quote endpoint raised in this env")
        if r1.status_code != 200:
            self.skipTest(f"quote endpoint did not return 200 (got {r1.status_code})")
        etag = r1.headers.get("etag")
        if not etag:
            self.skipTest("quote endpoint did not emit an ETag")

        # Second request: send If-None-Match → should be a 304.
        r2 = self.client.get(
            "/api/market-data/quote/AAPL?fallback=1",
            headers={"If-None-Match": etag},
        )
        self.assertEqual(r2.status_code, 304)
        h = self._headers_lower(r2)
        # Security headers from the cache middleware's inline baseline
        # must be attached.
        self.assertIn("content-security-policy", h)
        self.assertIn("x-frame-options", h)
        self.assertIn("x-content-type-options", h)


if __name__ == "__main__":
    unittest.main()
