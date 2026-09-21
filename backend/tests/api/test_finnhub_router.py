"""
Tests for backend/api/finnhub/router.py — Finnhub API endpoints.

Drives the FastAPI app through TestClient. All FinnhubService calls
are mocked so no real HTTP requests are made.
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from starlette.testclient import TestClient

from backend.api.main import app
from backend.market_data.services.finnhub_service import FinnhubService
from backend.models.finnhub import (
    AnalystRecommendation,
    CompanyFinancials,
    CompanyMetrics,
    CompanyProfile,
    InsiderSentiment,
    NewsItem,
)


class TestFinnhubRouterResponses(unittest.TestCase):
    """Test HTTP status codes and response shapes for each endpoint."""

    def setUp(self):
        self.client = TestClient(app)

    def _patch_service(self, method_name: str, return_value=None, side_effect=None):
        """Patch a method on FinnhubService so the router's singleton calls it."""
        if side_effect is not None:
            return patch.object(FinnhubService, method_name, side_effect=side_effect)
        return patch.object(FinnhubService, method_name, return_value=return_value)

    # ── /health ─────────────────────────────────────────────────────────

    def test_health_ok(self):
        with self._patch_service("get_company_profile", return_value=CompanyProfile()):
            r = self.client.get("/api/finnhub/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_health_down(self):
        with self._patch_service("get_company_profile",
                                side_effect=RuntimeError("no connection")):
            r = self.client.get("/api/finnhub/health")
        self.assertEqual(r.status_code, 503)

    # ── /company/{symbol} ───────────────────────────────────────────────

    def test_company_profile_200(self):
        profile = CompanyProfile(
            ticker="AAPL", name="Apple Inc", country="US",
            finnhub_industry="Technology", market_capitalization=2500000.0,
        )
        with self._patch_service("get_company_profile", return_value=profile):
            r = self.client.get("/api/finnhub/company/AAPL")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ticker"], "AAPL")
        # Cache-Control header must be set
        self.assertIn("max-age=3600", r.headers.get("cache-control", ""))

    def test_company_profile_404(self):
        with self._patch_service("get_company_profile",
                                side_effect=ValueError("no profile")):
            r = self.client.get("/api/finnhub/company/INVALID")
        self.assertEqual(r.status_code, 404)

    def test_company_profile_503(self):
        with self._patch_service("get_company_profile",
                                side_effect=RuntimeError("rate limited")):
            r = self.client.get("/api/finnhub/company/AAPL")
        self.assertEqual(r.status_code, 503)

    # ── /metrics/{symbol} ───────────────────────────────────────────────

    def test_metrics_200(self):
        metrics = CompanyMetrics(symbol="AAPL", pe_basic_eps=28.5, beta=1.2,
                                high_52w=200.0, low_52w=120.0)
        with self._patch_service("get_company_metrics", return_value=metrics):
            r = self.client.get("/api/finnhub/metrics/AAPL")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["pe_basic_eps"], 28.5)

    def test_metrics_404(self):
        with self._patch_service("get_company_metrics",
                                side_effect=ValueError("no metrics")):
            r = self.client.get("/api/finnhub/metrics/INVALID")
        self.assertEqual(r.status_code, 404)

    # ── /financials/{symbol} ───────────────────────────────────────────

    def test_financials_200(self):
        fin = CompanyFinancials(symbol="AAPL", total_revenue=100000.0,
                                net_income=25000.0, total_assets=200000.0)
        with self._patch_service("get_company_financials", return_value=fin):
            r = self.client.get("/api/finnhub/financials/AAPL")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total_revenue"], 100000.0)

    # ── /news/{symbol} ─────────────────────────────────────────────────

    def test_company_news_200(self):
        news = [
            NewsItem(id=1, datetime=datetime(2024, 1, 15, tzinfo=UTC),
                     headline="Apple announces Q1 results",
                     source="Reuters", url="https://example.com/1"),
        ]
        with self._patch_service("get_company_news", return_value=news):
            r = self.client.get(
                "/api/finnhub/news/AAPL",
                params={"from_date": "2024-01-01", "to_date": "2024-01-31"}
            )
        self.assertEqual(r.status_code, 200)

    def test_company_news_default_dates(self):
        """Default dates (7 days ago to today) are applied when not provided."""
        with self._patch_service("get_company_news", return_value=[]):
            r = self.client.get("/api/finnhub/news/AAPL")
        self.assertEqual(r.status_code, 200)

    # ── /market-news ───────────────────────────────────────────────────

    def test_market_news_200(self):
        with self._patch_service("get_market_news", return_value=[]):
            r = self.client.get("/api/finnhub/market-news")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    # ── /recommendations/{symbol} ──────────────────────────────────────

    def test_recommendations_200(self):
        recs = [
            AnalystRecommendation(symbol="AAPL", period="2024-Q1",
                                buy=15, hold=10, sell=5,
                                strong_buy=5, strong_sell=1),
        ]
        with self._patch_service("get_analyst_recommendations", return_value=recs):
            r = self.client.get("/api/finnhub/recommendations/AAPL")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    # ── /insider/{symbol} ───────────────────────────────────────────────

    def test_insider_200(self):
        insider = [
            InsiderSentiment(symbol="AAPL", year=2024, month=1,
                             change=50000, sentiment=0.3),
        ]
        with self._patch_service("get_insider_sentiment", return_value=insider):
            r = self.client.get(
                "/api/finnhub/insider/AAPL",
                params={"from_date": "2024-01-01", "to_date": "2024-01-31"}
            )
        self.assertEqual(r.status_code, 200)

    def test_insider_default_dates(self):
        with self._patch_service("get_insider_sentiment", return_value=[]):
            r = self.client.get("/api/finnhub/insider/AAPL")
        self.assertEqual(r.status_code, 200)

    # ── /peers/{symbol} ───────────────────────────────────────────────

    def test_peers_200(self):
        with self._patch_service("get_peers", return_value=["MSFT", "GOOGL", "META"]):
            r = self.client.get("/api/finnhub/peers/AAPL")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["peers"], ["MSFT", "GOOGL", "META"])

    def test_peers_404(self):
        with self._patch_service("get_peers",
                                side_effect=ValueError("no peers")):
            r = self.client.get("/api/finnhub/peers/INVALID")
        self.assertEqual(r.status_code, 404)


class TestFinnhubRouterCacheHeaders(unittest.TestCase):
    """Verify Cache-Control headers on all Finnhub endpoints."""

    def setUp(self):
        self.client = TestClient(app)

    @staticmethod
    def _patch_service(method_name: str, return_value):
        return patch.object(FinnhubService, method_name, return_value=return_value)

    def _assert_cache_header(self, path: str, method_name: str, return_value):
        with self._patch_service(method_name, return_value):
            r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertIn("cache-control", r.headers)
        self.assertIn("max-age=3600", r.headers["cache-control"])

    def test_company_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/company/AAPL", "get_company_profile",
            CompanyProfile(ticker="AAPL", name="Apple", country="US"),
        )

    def test_metrics_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/metrics/AAPL", "get_company_metrics",
            CompanyMetrics(symbol="AAPL"),
        )

    def test_financials_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/financials/AAPL", "get_company_financials",
            CompanyFinancials(symbol="AAPL"),
        )

    def test_news_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/news/AAPL", "get_company_news", [],
        )

    def test_market_news_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/market-news", "get_market_news", [],
        )

    def test_recommendations_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/recommendations/AAPL", "get_analyst_recommendations", [],
        )

    def test_insider_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/insider/AAPL", "get_insider_sentiment", [],
        )

    def test_peers_cache_header(self):
        self._assert_cache_header(
            "/api/finnhub/peers/AAPL", "get_peers", [],
        )


if __name__ == "__main__":
    unittest.main()
