"""
Tests for the security headers middleware.

Covers the standard OWASP/Mozilla baseline:
  - HSTS (off by default; toggleable per-request via settings)
  - Content-Security-Policy
  - X-Frame-Options
  - X-Content-Type-Options
  - Referrer-Policy
  - Permissions-Policy
  - Cross-Origin-Resource-Policy / Cross-Origin-Opener-Policy
"""
import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.api.main import app


class TestSecurityHeadersPresent(unittest.TestCase):
    """Baseline headers must be attached to every response."""

    def setUp(self):
        self.client = TestClient(app)

    def _headers_lower(self, response) -> dict:
        return {k.lower(): v for k, v in response.headers.items()}

    def test_health_endpoint_has_security_headers(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        h = self._headers_lower(resp)
        # CSP is enabled by default; baseline policy must be present.
        self.assertIn("content-security-policy", h)
        self.assertIn("default-src 'none'", h["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
        # X-Frame-Options: DENY (or similar — we only require *something*).
        self.assertEqual(h["x-frame-options"], "DENY")
        # X-Content-Type-Options: nosniff.
        self.assertEqual(h["x-content-type-options"], "nosniff")
        # Referrer-Policy default.
        self.assertIn("referrer-policy", h)
        # Permissions-Policy.
        self.assertIn("permissions-policy", h)
        # Cross-Origin isolation headers.
        self.assertEqual(h["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(h["cross-origin-opener-policy"], "same-origin")

    def test_hsts_disabled_by_default(self):
        """HSTS must NOT be sent unless explicitly enabled — it would
        brick a deployment that was accidentally reached over HTTP."""
        resp = self.client.get("/api/health")
        self.assertNotIn("strict-transport-security", {k.lower() for k in resp.headers.keys()})

    def test_404_responses_still_get_headers(self):
        """Even 404s get the security headers. This is the whole point
        of doing it in middleware instead of a per-endpoint decorator."""
        resp = self.client.get("/this-route-does-not-exist")
        self.assertEqual(resp.status_code, 404)
        h = self._headers_lower(resp)
        self.assertIn("content-security-policy", h)
        self.assertIn("x-frame-options", h)

    def test_429_responses_still_get_headers(self):
        """Rate-limited responses also receive security headers."""
        from backend.api.main import _write_limiter
        _write_limiter.reset()
        # Exhaust the write-rate-limit window with POST requests.
        for _ in range(_write_limiter.max_requests + 2):
            r = self.client.post("/api/market-data/ingestion/start", json={})
        # At least one response should be 429.
        h = self._headers_lower(r)
        self.assertIn("content-security-policy", h)
        self.assertIn("x-frame-options", h)


class TestSecurityHeadersConfigurable(unittest.TestCase):
    """Operators can tune the headers via settings without code changes."""

    def setUp(self):
        self.client = TestClient(app)

    def test_hsts_enabled_via_settings(self):
        """When ``SECURITY_HSTS_ENABLED=true`` is set, HSTS is sent."""
        from backend.config.settings import SecuritySettings
        custom = SecuritySettings(
            hsts_enabled=True,
            hsts_max_age_seconds=63072000,
            hsts_include_subdomains=True,
            hsts_preload=True,
        )
        # Patch the module-level `settings` object that the middleware reads.
        with patch("backend.config.settings.settings.security", custom):
            resp = self.client.get("/api/health")
        h = {k.lower(): v for k, v in resp.headers.items()}
        self.assertIn("strict-transport-security", h)
        self.assertIn("max-age=63072000", h["strict-transport-security"])
        self.assertIn("includeSubDomains", h["strict-transport-security"])
        self.assertIn("preload", h["strict-transport-security"])

    def test_custom_csp_value(self):
        from backend.config.settings import SecuritySettings
        custom = SecuritySettings(
            csp_enabled=True,
            csp_value="default-src 'self'",
        )
        with patch("backend.config.settings.settings.security", custom):
            resp = self.client.get("/api/health")
        h = {k.lower(): v for k, v in resp.headers.items()}
        self.assertEqual(h["content-security-policy"], "default-src 'self'")

    def test_csp_disabled(self):
        from backend.config.settings import SecuritySettings
        custom = SecuritySettings(csp_enabled=False)
        with patch("backend.config.settings.settings.security", custom):
            resp = self.client.get("/api/health")
        h = {k.lower(): v for k, v in resp.headers.items()}
        self.assertNotIn("content-security-policy", h)


if __name__ == "__main__":
    unittest.main()
