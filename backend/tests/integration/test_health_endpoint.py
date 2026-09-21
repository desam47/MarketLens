"""
End-to-end integration tests for the FastAPI app.

These tests use ``fastapi.testclient.TestClient`` to mount the full app
and exercise it through real HTTP semantics — middleware, route
resolution, response serialization, and (where applicable) the DB layer.

The goal is to catch regressions that unit tests miss: middleware
ordering, request/response shape drift, and the wiring between routers
and the app object.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

# Ensure the repo root is on sys.path so `from backend.X import Y` works.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Force the same env defaults CI uses so the test client is deterministic.
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("OBSERVABILITY_TRACING_ENABLED", "false")


class TestHealthEndpoint(unittest.TestCase):
    """Smoke test for GET /api/health.

    The /api/health endpoint is the canary — if it stops responding the
    container healthcheck fails and the service is taken out of rotation.
    """

    def setUp(self) -> None:
        # Import inside setUp so env vars are set first.
        from fastapi.testclient import TestClient

        from backend.api.main import app

        self.client = TestClient(app)

    def test_health_returns_200(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200, msg=resp.text)

    def test_health_response_shape(self) -> None:
        resp = self.client.get("/api/health")
        body = resp.json()
        # Must include the standard health fields.
        self.assertIn("status", body)
        self.assertIn("service", body)
        self.assertIn("version", body)
        self.assertEqual(body["status"], "healthy")

    def test_health_carries_security_headers(self) -> None:
        # SecurityHeadersMiddleware adds a static header set; the test
        # confirms the middleware chain is wired up.
        resp = self.client.get("/api/health")
        self.assertIn("x-content-type-options", {k.lower() for k in resp.headers})

    def test_health_carries_correlation_id(self) -> None:
        # Observability middleware stamps an X-Correlation-ID on every response.
        resp = self.client.get("/api/health")
        # The correlation-id middleware should always set this header.
        self.assertIn("x-correlation-id", {k.lower() for k in resp.headers})


class TestSystemStatusEndpoint(unittest.TestCase):
    """Smoke test for GET /api/system/status."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from backend.api.main import app
        self.client = TestClient(app)

    def test_system_status_returns_200(self) -> None:
        resp = self.client.get("/api/system/status")
        # Endpoint should be reachable; the body is shape-flexible.
        self.assertIn(resp.status_code, (200, 503), msg=resp.text)


class TestOpenAPISchema(unittest.TestCase):
    """The OpenAPI schema is generated from the live app — verifies all
    routes are registered correctly."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from backend.api.main import app
        self.client = TestClient(app)

    def test_openapi_json_is_valid(self) -> None:
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)
        schema = resp.json()
        self.assertIn("openapi", schema)
        self.assertIn("paths", schema)
        self.assertIn("info", schema)

    def test_openapi_documents_known_routes(self) -> None:
        resp = self.client.get("/openapi.json")
        schema = resp.json()
        paths = schema["paths"]
        # A small set of well-known routes that must always be present.
        for path in ["/api/health", "/api/system/status", "/api/watchlists/"]:
            self.assertIn(path, paths, f"Expected {path} in OpenAPI schema")


class TestMiddlewareOrder(unittest.TestCase):
    """A request that triggers the rate limiter should still carry the
    security headers middleware output (the rate limiter inlines the
    header set when it short-circuits)."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from backend.api.main import app
        self.client = TestClient(app)

    def test_rate_limited_response_carries_security_headers(self) -> None:
        # Force the rate limiter to deny every request.
        from backend.api.main import _write_limiter
        with patch.object(_write_limiter, "is_allowed", return_value=(False, 0)):
            resp = self.client.post("/api/ai/config", json={})
        # 429 from the middleware.
        self.assertEqual(resp.status_code, 429)
        # The response must include Retry-After (rate-limit) AND the
        # static security headers inlined by the limiter.
        self.assertIn("retry-after", {k.lower() for k in resp.headers})
        self.assertIn("x-content-type-options", {k.lower() for k in resp.headers})


if __name__ == "__main__":
    unittest.main()
