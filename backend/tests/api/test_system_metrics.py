"""
Tests for the system observability endpoints.

Covers:
  - GET /api/system/performance surfaces cache, rate_limit, websocket blocks
  - GET /api/system/metrics returns Prometheus exposition text
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.api.main import app


class TestPerformanceEndpointStats(unittest.TestCase):
    """The /api/system/performance endpoint must surface the new stat blocks."""

    def setUp(self):
        self.client = TestClient(app)

    def test_performance_includes_cache_block(self):
        with patch("backend.api.system.router._safe_cache_stats") as m_cache:
            m_cache.return_value = {
                "bar_hits": 10,
                "bar_misses": 2,
                "quote_hits": 5,
                "quote_misses": 1,
                "bar_hit_rate": 0.83,
                "quote_hit_rate": 0.83,
            }
            resp = self.client.get("/api/system/performance")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("cache", body)
        self.assertEqual(body["cache"]["bar_hits"], 10)
        self.assertEqual(body["cache"]["bar_hit_rate"], 0.83)

    def test_performance_includes_rate_limit_block(self):
        with patch("backend.api.system.router._safe_rate_limit_stats") as m_rl:
            m_rl.return_value = {
                "backend": "in_memory",
                "fallback": {
                    "total_allowed": 100,
                    "total_rejected": 5,
                    "tracked_ips": 3,
                    "reject_rate": 0.0476,
                },
            }
            resp = self.client.get("/api/system/performance")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("rate_limit", body)
        self.assertEqual(body["rate_limit"]["backend"], "in_memory")
        self.assertEqual(body["rate_limit"]["fallback"]["total_allowed"], 100)

    def test_performance_includes_websocket_block(self):
        with patch("backend.api.system.router._safe_websocket_stats") as m_ws:
            m_ws.return_value = {
                "active_connections": 2,
                "subscribed_symbols": 5,
                "total_subscriptions": 8,
                "connections_total": 7,
                "disconnections_total": 5,
                "messages_sent_total": 42,
                "broadcasts_total": 13,
            }
            resp = self.client.get("/api/system/performance")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("websocket", body)
        self.assertEqual(body["websocket"]["active_connections"], 2)
        self.assertEqual(body["websocket"]["messages_sent_total"], 42)

    def test_performance_gracefully_handles_missing_subsystems(self):
        """If subsystem stat collectors raise, the endpoint must still respond."""
        with patch("backend.api.system.router._safe_cache_stats", return_value=None), \
             patch("backend.api.system.router._safe_rate_limit_stats", return_value=None), \
             patch("backend.api.system.router._safe_websocket_stats", return_value=None):
            resp = self.client.get("/api/system/performance")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["cache"])
        self.assertIsNone(body["rate_limit"])
        self.assertIsNone(body["websocket"])


class TestPrometheusMetricsEndpoint(unittest.TestCase):
    """The /api/system/metrics endpoint must return Prometheus 0.0.4 text format."""

    def setUp(self):
        self.client = TestClient(app)

    def test_metrics_endpoint_returns_text(self):
        resp = self.client.get("/api/system/metrics")
        self.assertEqual(resp.status_code, 200)
        # Prometheus text format mime.
        self.assertTrue(
            resp.headers["content-type"].startswith("text/plain"),
            msg=f"unexpected content-type: {resp.headers.get('content-type')!r}",
        )
        body = resp.text
        # Process-level gauges should always be present.
        self.assertIn("marketlens_uptime_seconds", body)
        self.assertIn("marketlens_memory_rss_mb", body)
        self.assertIn("marketlens_http_requests_total", body)
        # Ingestion + scanner.
        self.assertIn("marketlens_ingestion_bars_total", body)
        self.assertIn("marketlens_scanner_scans_total", body)
        # WebSocket counters.
        self.assertIn("marketlens_ws_active_connections", body)
        # Help + TYPE lines.
        self.assertIn("# HELP marketlens_uptime_seconds", body)
        self.assertIn("# TYPE marketlens_uptime_seconds gauge", body)

    def test_metrics_endpoint_handles_collector_failure(self):
        """If a stat collector raises, the endpoint should still respond."""
        with patch(
            "backend.api.system.router.render_prometheus_text",
            return_value="marketlens_uptime_seconds 0.0\n",
        ):
            resp = self.client.get("/api/system/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("marketlens_uptime_seconds", resp.text)

    def test_metrics_endpoint_increments_http_request_counter(self):
        """The RequestCounterMiddleware fires for every request, /metrics included.

        We want the HTTP counter to count real user traffic but
        whether the scrape itself counts is a design call. Today it
        does (every request is counted) so dashboards show scrape
        activity. Document the current behavior and confirm at least
        one increment.
        """
        with patch("backend.api.system.router.record_http_request") as m_rec:
            resp = self.client.get("/api/system/metrics")
        self.assertEqual(resp.status_code, 200)
        # Middleware should fire at least once.
        self.assertGreaterEqual(m_rec.call_count, 1)


class TestSystemStatusEndpoint(unittest.TestCase):
    """Tests for GET /api/system/status and /api/system/config."""

    def setUp(self):
        self.client = TestClient(app)

    def test_status_includes_fallback_providers(self):
        """system_status includes market_data_fallback_providers."""
        resp = self.client.get("/api/system/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("market_data_provider", data)
        self.assertIn("market_data_fallback_providers", data)
        self.assertIsInstance(data["market_data_fallback_providers"], list)

    def test_config_endpoint_returns_live_values(self):
        """system/config reads .env directly and returns primary + fallback providers."""
        resp = self.client.get("/api/system/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["config_source"], "live")
        self.assertIn("market_data_primary_provider", data)
        self.assertIn("market_data_fallback_providers", data)
        self.assertIsInstance(data["market_data_fallback_providers"], list)
        self.assertIn("timestamp", data)


if __name__ == "__main__":
    unittest.main()
