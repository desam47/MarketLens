"""
Phase 18 — FastAPI router tests for /api/aux-data/*.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


class TestAuxDataEndpoints(unittest.TestCase):

    def setUp(self):
        # Force all three categories off for the duration of each test,
        # then restore the real values in tearDown so this class can't
        # leak a mutated global settings singleton into later tests
        # (e.g. test_aux_data_settings' singleton-vs-fresh comparison).
        from backend.aux_data.services import manager as mgr_module

        self._mgr_module = mgr_module
        aux = mgr_module._settings.aux_data
        self._saved_enabled = (
            aux.news.enabled, aux.fundamentals.enabled, aux.options.enabled
        )
        aux.news.enabled = False
        aux.fundamentals.enabled = False
        aux.options.enabled = False

    def tearDown(self):
        aux = self._mgr_module._settings.aux_data
        aux.news.enabled, aux.fundamentals.enabled, aux.options.enabled = self._saved_enabled

    def test_news_503_when_disabled(self):
        resp = client.get("/api/aux-data/news/AAPL")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        # detail is a dict: {"detail": ..., "hint": ..., "timestamp": ...}
        self.assertIn("news", body["detail"]["detail"].lower())
        self.assertIn("AUX_NEWS_ENABLED", body["detail"]["hint"])

    def test_fundamentals_503_when_disabled(self):
        resp = client.get("/api/aux-data/fundamentals/AAPL")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("fundamentals", body["detail"]["detail"].lower())

    def test_options_503_when_disabled(self):
        resp = client.get("/api/aux-data/options/AAPL")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("options", body["detail"]["detail"].lower())

    def test_news_endpoint_200_when_enabled(self):
        from backend.aux_data.services import manager as mgr_module
        mgr_module._settings.aux_data.news.enabled = True
        try:
            with patch(
                "backend.aux_data.services.manager.AuxDataManager.get_news"
            ) as mock_get:
                from datetime import datetime

                from backend.models.aux_data import NewsItem, NewsResponse
                mock_get.return_value = NewsResponse(
                    symbol="AAPL",
                    items=[
                        NewsItem(
                            headline="Apple beats",
                            source="Reuters",
                            timestamp=datetime.utcnow(),
                            symbol="AAPL",
                            relevance=0.9,
                        )
                    ],
                    provider="yfinance_news",
                    timestamp=datetime.utcnow(),
                )
                resp = client.get("/api/aux-data/news/AAPL?limit=10")
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertEqual(body["symbol"], "AAPL")
                self.assertEqual(len(body["items"]), 1)
                self.assertEqual(body["items"][0]["headline"], "Apple beats")
        finally:
            mgr_module._settings.aux_data.news.enabled = False

    def test_fundamentals_endpoint_200_when_enabled(self):
        from backend.aux_data.services import manager as mgr_module
        mgr_module._settings.aux_data.fundamentals.enabled = True
        try:
            with patch(
                "backend.aux_data.services.manager.AuxDataManager.get_fundamentals"
            ) as mock_get:
                from datetime import datetime

                from backend.models.aux_data import FundamentalsItem, FundamentalsResponse
                mock_get.return_value = FundamentalsResponse(
                    symbol="AAPL",
                    data=FundamentalsItem(
                        symbol="AAPL",
                        company_name="Apple Inc.",
                        market_cap=3_000_000_000_000.0,
                        eps=6.42,
                    ),
                    provider="yfinance_fundamentals",
                    timestamp=datetime.utcnow(),
                )
                resp = client.get("/api/aux-data/fundamentals/AAPL")
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertEqual(body["data"]["company_name"], "Apple Inc.")
        finally:
            mgr_module._settings.aux_data.fundamentals.enabled = False

    def test_options_endpoint_200_when_enabled(self):
        from backend.aux_data.services import manager as mgr_module
        mgr_module._settings.aux_data.options.enabled = True
        try:
            with patch(
                "backend.aux_data.services.manager.AuxDataManager.get_options"
            ) as mock_get:
                from datetime import datetime

                from backend.models.aux_data import OptionsResponse
                mock_get.return_value = OptionsResponse(
                    symbol="AAPL",
                    expirations=["2026-12-18"],
                    near_term_iv=0.30,
                    iv_rank=25.0,
                    provider="yfinance_options",
                    timestamp=datetime.utcnow(),
                )
                resp = client.get("/api/aux-data/options/AAPL?expiration=2026-12-18")
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertEqual(body["symbol"], "AAPL")
                self.assertEqual(body["iv_rank"], 25.0)
        finally:
            mgr_module._settings.aux_data.options.enabled = False

    def test_providers_endpoint_always_200(self):
        resp = client.get("/api/aux-data/providers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("news", body)
        self.assertIn("fundamentals", body)
        self.assertIn("options", body)
        # Default is yfinance registered as the only provider per category.
        self.assertGreaterEqual(len(body["news"]), 1)


if __name__ == "__main__":
    unittest.main()
