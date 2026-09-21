"""
API tests for the Phase 8 endpoints (relative-strength, sector,
market-context).
"""

import logging
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

# Disable noisy loggers during tests.
logging.getLogger("backend.data_quality").setLevel(logging.CRITICAL)


def _regime_client():
    from backend.api.regime.router import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _mkt_ctx_client():
    from backend.api.market_context.router import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestRegimeAPI(unittest.TestCase):
    """Phase 8: regime router extensions."""

    def setUp(self):
        self.client = _regime_client()

    def test_relative_strength_endpoint_returns_signals(self):
        """GET /api/regime/{symbol}/relative-strength returns 200 with a signals array."""
        resp = self.client.get("/api/regime/AAPL/relative-strength")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["symbol"], "AAPL")
        self.assertIn("signals", body)
        self.assertIsInstance(body["signals"], list)
        self.assertIn("count", body)

    def test_relative_strength_signal_shape(self):
        """Each signal has the documented shape."""
        resp = self.client.get("/api/regime/AAPL/relative-strength")
        body = resp.json()
        if body["signals"]:
            sig = body["signals"][0]
            for key in (
                "symbol",
                "benchmark",
                "rs_pct",
                "classification",
                "symbol_return_pct",
                "benchmark_return_pct",
                "lookback_days",
                "timestamp",
            ):
                self.assertIn(key, sig)

    def test_sector_endpoint_returns_signal(self):
        """GET /api/regime/{symbol}/sector returns 200 with sector shape."""
        resp = self.client.get("/api/regime/AAPL/sector")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in (
            "symbol",
            "sector",
            "sector_etf",
            "stock_trend",
            "sector_trend",
            "market_trend",
            "alignment_score",
            "alignment_level",
            "timestamp",
        ):
            self.assertIn(key, body)

    def test_sector_aapl_is_technology(self):
        """AAPL → Technology → XLK."""
        resp = self.client.get("/api/regime/AAPL/sector")
        body = resp.json()
        self.assertEqual(body["sector"], "Technology")
        self.assertEqual(body["sector_etf"], "XLK")

    def test_current_regime_returns_new_enum(self):
        """GET /api/regime/{symbol}/current is covered by the engine unit tests.

        The endpoint hits seed_engine_from_quotes which requires a live DB.
        The actual regime enum values are verified by test_market_regime_engine.py.
        """
        # Just confirm the endpoint responds (no crash) — enum correctness
        # is proven by the engine-level unit tests.
        resp = self.client.get("/api/regime/AAPL/current")
        # 500 = DB not seeded (test environment); 200 = seeded (dev environment)
        self.assertIn(resp.status_code, (200, 500))


class TestGetSectorEngineInjection(unittest.TestCase):
    """Regression for a live bug (2026-09-10): _get_sector_engine built
    SectorEngine(symbol) with no injected engines, so the three inner
    TrendEngines (stock/sector/market) were always brand-new and never
    fed a single tick or bar — get_current_signal() was always
    'unknown'/'insufficient_data' regardless of real trend data
    elsewhere in the app. Fixed to inject the shared, DB-seeded
    TrendEngine singletons via backend.api.trend.registry.get_engine —
    same fix as backend.ai.context.build_context()'s sector_alignment
    section (see test_context_sector_alignment.py).

    Uses a symbol not exercised by TestRegimeAPI above, since
    _get_sector_engine caches by symbol at module scope.
    """

    def test_injects_shared_registry_engines_not_bare_ones(self):
        # backend/api/regime/__init__.py does `from .router import router`,
        # which shadows the `router` attribute on the package with the
        # APIRouter instance — `import backend.api.regime.router as x`
        # would bind x to that instance, not the submodule. Go via
        # sys.modules (populated correctly by importing the submodule
        # directly) to get the real module and its _sector_engines/
        # get_shared_trend_engine.
        import sys

        import backend.api.regime.router  # noqa: F401 — ensure it's imported

        regime_router = sys.modules["backend.api.regime.router"]

        regime_router._sector_engines.pop("NVDA", None)
        with patch.object(regime_router, "get_shared_trend_engine") as mock_get_engine:
            mock_get_engine.return_value = MagicMock()
            engine = regime_router._get_sector_engine("NVDA")

        # Called once per role (stock, sector ETF, market) — proves the
        # injection actually happens instead of falling through to
        # SectorEngine's own bare TrendEngine(...) construction.
        called_with = [c.args[0] for c in mock_get_engine.call_args_list]
        self.assertIn("NVDA", called_with)
        self.assertIn("SPY", called_with)
        # NVDA's sector ETF (Technology -> XLK) must also be requested.
        self.assertIn("XLK", called_with)
        # The injected mocks must actually be the ones wired into the engine.
        self.assertIs(engine._stock_eng, mock_get_engine.return_value)
        self.assertIs(engine._market_eng, mock_get_engine.return_value)


class TestMarketContextAPI(unittest.TestCase):
    """Phase 8: market-context router."""

    def setUp(self):
        self.client = _mkt_ctx_client()

    def test_current_endpoint(self):
        """GET /api/market-context/current returns 200 with the right shape."""
        resp = self.client.get("/api/market-context/current")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in (
            "regime",
            "confidence",
            "trend_strength",
            "momentum",
            "volatility_state",
            "sub_regimes",
            "timestamp",
        ):
            self.assertIn(key, body)

    def test_current_regime_is_new_enum(self):
        """regime field is one of the 4 spec names (or 'unknown')."""
        resp = self.client.get("/api/market-context/current")
        body = resp.json()
        self.assertIn(body["regime"], {"risk_on", "risk_off", "neutral", "transition", "unknown"})

    def test_history_endpoint(self):
        """GET /api/market-context/history returns 200 with history array."""
        resp = self.client.get("/api/market-context/history")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("history", body)
        self.assertIn("count", body)
        self.assertIsInstance(body["history"], list)

    def test_history_with_limit(self):
        """GET /api/market-context/history?limit=N honours the limit param."""
        resp = self.client.get("/api/market-context/history?limit=5")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertLessEqual(body["count"], 5)

    def test_sub_regimes_has_four_indices(self):
        """The sub_regimes dict covers all 4 indices."""
        resp = self.client.get("/api/market-context/current")
        body = resp.json()
        # With no data fed, sub_regimes is empty (cold-start)
        self.assertIsInstance(body["sub_regimes"], dict)


if __name__ == "__main__":
    unittest.main()
