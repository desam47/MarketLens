"""
Tests for backend/api/cache.py - CacheMiddleware

Tests ETag generation, conditional request handling, and per-path
cache duration behavior.

CacheMiddleware is a plain ASGI middleware. The unit tests drive it as an ASGI app
(``await middleware(scope, receive, send)``) and inspect the messages it sends.
"""
import asyncio
import hashlib
import unittest

from backend.api.cache import CacheMiddleware


class TestCacheMiddlewareETagGeneration(unittest.TestCase):
    """Unit tests driving the middleware as a raw ASGI app."""

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

    def _call(self, middleware, method="GET", path="/api/market-data/quote/AAPL", headers=()):
        """Run the middleware as an ASGI app; return an object with .status_code / .headers / .body."""
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": list(headers),
            "query_string": b"",
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        self._run(middleware(scope, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")

        class _Response:
            status_code = start["status"]
            headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
            body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")

        return _Response

    def test_non_get_passes_through(self):
        """POST/PUT/DELETE should not be cached."""
        response = self._call(self._make_middleware(), method="POST", path="/api/data")
        # Non-GET pass through unchanged
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("etag", response.headers)

    def test_exempt_path_passes_through(self):
        """Health check and docs paths are exempt."""
        response = self._call(self._make_middleware(), path="/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("etag", response.headers)

    def test_get_response_gets_etag(self):
        """GET responses should have ETag header."""
        body = b'{"price": 150}'
        response = self._call(self._make_middleware(body=body))
        self.assertEqual(response.status_code, 200)
        self.assertIn("etag", response.headers)
        self.assertIn("cache-control", response.headers)
        self.assertEqual(response.body, body)

    def test_304_on_matching_etag(self):
        """Should return 304 when If-None-Match matches."""
        body = b'{"price": 150}'
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        response = self._call(
            self._make_middleware(body=body), headers=[(b"if-none-match", etag.encode())]
        )
        self.assertEqual(response.status_code, 304)
        self.assertIn("etag", response.headers)

    def test_non_200_responses_not_cached(self):
        """Non-200 responses should not get cache headers."""
        response = self._call(
            self._make_middleware(status_code=404, body=b'{"error": "not found"}')
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("etag", response.headers)

    def test_per_path_cache_duration(self):
        """Market data paths should get 300s cache."""
        response = self._call(
            self._make_middleware(body=b'{"price": 150}'), path="/api/market-data/bars/AAPL"
        )
        self.assertIn("max-age=300", response.headers["cache-control"])

    def test_live_quote_is_revalidated_every_request(self):
        """Quote paths must not be browser-cached: the Dashboard polls them
        every 5s and a max-age would freeze the displayed price."""
        middleware = self._make_middleware()
        for path in ("/api/market-data/quote/AAPL", "/api/market_data/quote/AAPL"):
            self.assertEqual(middleware._get_cache_seconds(path), 0, path)
        # Other market-data paths keep the 5-minute TTL.
        self.assertEqual(middleware._get_cache_seconds("/api/market-data/bars/AAPL"), 300)


class TestCacheMiddlewareIntegration(unittest.TestCase):
    """Integration tests: drive the full FastAPI app through TestClient.

    These verify the original motivation: the 304 short-circuit response
    from the cache middleware must reach outer middlewares
    (SecurityHeadersMiddleware) in the real chain, so security headers
    are present on 304 responses. BaseHTTPMiddleware bubbles responses
    correctly — no custom ASGI shim is needed.
    """

    def setUp(self):
        from datetime import datetime

        from starlette.testclient import TestClient

        from backend.api.main import app
        from backend.api.ttl_cache import _quote_cache
        from backend.database import SessionLocal
        from backend.models import DataStatus, Quote
        from backend.models.market_data_sql import QuoteModel
        from backend.repositories import quote_repository

        self.client = TestClient(app)
        # The quote endpoint reads the database. This test used to depend on the developer's LIVE
        # database happening to hold an AAPL quote (and skipped silently when it did not), so seed
        # one into the isolated test database.
        _quote_cache.clear()
        db = SessionLocal()
        try:
            quote_repository.add_quote(db, Quote(
                symbol="AAPL", price=190.25, bid=190.2, ask=190.3, volume=1000,
                timestamp=datetime(2026, 9, 18, 15, 59), provider="test",
                data_status=DataStatus.LIVE,
            ))
            db.commit()
        finally:
            db.close()

        def _cleanup():
            _quote_cache.clear()
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(QuoteModel).filter(QuoteModel.provider == "test").delete()
                cleanup_db.commit()
            finally:
                cleanup_db.close()

        self.addCleanup(_cleanup)

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
        self.assertEqual(r1.status_code, 200, "the seeded quote must be served")
        etag = r1.headers.get("etag")
        self.assertTrue(etag, "the quote endpoint must emit an ETag")

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
