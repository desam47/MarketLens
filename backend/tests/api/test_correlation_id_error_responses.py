"""
Tests for X-Correlation-ID on error responses.

The CorrelationIdMiddleware attaches the header to the response when
``call_next`` returns. But Starlette's error middleware raises before
``call_next`` resolves for 5xx errors, and the response-building path
for 404/422 can also bypass it. The exception handlers in
``backend.api.main`` are the safety net: they read the correlation ID
from request state (set by the middleware) or the context variable,
and always include it on the error response.

These tests cover the three error paths that previously dropped the
header:
  - 404 from a missing route
  - 500 from an unhandled endpoint exception
  - HTTPException raised from an endpoint (e.g. 404 from a router)
"""
import os
import sys
import unittest
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.api.main import app


class TestCorrelationIdOnErrorResponses(unittest.TestCase):
    """X-Correlation-ID must reach the client even on error responses."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_404_includes_correlation_id_from_header(self):
        """Client-supplied X-Correlation-ID appears in the 404 response."""
        corr_id = "client-supplied-" + uuid.uuid4().hex[:8]
        resp = self.client.get("/this/path/does/not/exist", headers={"X-Correlation-ID": corr_id})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers.get("X-Correlation-ID"), corr_id)

    def test_404_generates_correlation_id_when_absent(self):
        """A request without a correlation ID still gets one on the 404."""
        resp = self.client.get("/this/path/does/not/exist")
        self.assertEqual(resp.status_code, 404)
        header_value = resp.headers.get("X-Correlation-ID")
        self.assertIsNotNone(header_value)
        # Should be a UUID-like value (default generator).
        self.assertGreaterEqual(len(header_value), 16)

    def test_200_response_still_has_correlation_id(self):
        """Sanity check: the happy path is unchanged."""
        corr_id = "happy-path-" + uuid.uuid4().hex[:8]
        resp = self.client.get("/api/health", headers={"X-Correlation-ID": corr_id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Correlation-ID"), corr_id)


class TestCorrelationIdOn500(unittest.TestCase):
    """Unhandled exceptions (500) must include the correlation ID."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        # Add a temporary route that always raises.
        @app.get("/_test/raise_500")
        def _raise():
            raise RuntimeError("intentional test failure")

    def tearDown(self):
        # Remove the test route so it doesn't leak across test files.
        for route in list(app.router.routes):
            if getattr(route, "path", "") == "/_test/raise_500":
                app.router.routes.remove(route)
                break

    def test_500_includes_correlation_id_from_header(self):
        corr_id = "five-hundred-" + uuid.uuid4().hex[:8]
        resp = self.client.get("/_test/raise_500", headers={"X-Correlation-ID": corr_id})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.headers.get("X-Correlation-ID"), corr_id)

    def test_500_includes_generated_correlation_id(self):
        resp = self.client.get("/_test/raise_500")
        self.assertEqual(resp.status_code, 500)
        self.assertIsNotNone(resp.headers.get("X-Correlation-ID"))


class TestCorrelationIdOnHTTPException(unittest.TestCase):
    """HTTPException raised from a route keeps the correlation ID."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

        @app.get("/_test/raise_http_exception")
        def _raise():
            raise HTTPException(status_code=409, detail="conflict for test")

    def tearDown(self):
        for route in list(app.router.routes):
            if getattr(route, "path", "") == "/_test/raise_http_exception":
                app.router.routes.remove(route)
                break

    def test_http_exception_includes_correlation_id(self):
        corr_id = "http-exc-" + uuid.uuid4().hex[:8]
        resp = self.client.get(
            "/_test/raise_http_exception", headers={"X-Correlation-ID": corr_id}
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.headers.get("X-Correlation-ID"), corr_id)
        # The body still contains the original detail — we didn't strip it.
        self.assertEqual(resp.json()["detail"], "conflict for test")


if __name__ == "__main__":
    unittest.main()
