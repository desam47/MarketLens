"""
Tests for backend.ai.market_baseline.build_market_baseline.

Guards two things: it never raises when its sources are empty, and it
never triggers an AI call (no analyze_symbol / build_digest_payload).
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from backend.ai import market_baseline


class TestBuildMarketBaseline(unittest.TestCase):
    def setUp(self):
        # the module now caches for 20s — clear it between tests
        market_baseline._cached = None
        market_baseline._cached_at = 0.0

    def test_cache_returns_same_object_within_ttl(self):
        with patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls, \
             patch("backend.api.market_context.router.get_engine") as get_engine, \
             patch("backend.api.main_helpers._watched_symbols", return_value=[]):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            a = market_baseline.build_market_baseline()
            b = market_baseline.build_market_baseline()
        self.assertIs(a, b)  # 2nd call served from cache
        c = market_baseline.build_market_baseline(use_cache=False)
        self.assertIsNot(a, c)  # forced rebuild

    def test_degrades_to_empty_sections_when_sources_missing(self):
        repo = MagicMock()
        repo.get_latest.return_value = None
        engine = MagicMock()
        engine.get_current_context.return_value = None
        with patch("backend.repositories.ai_digest_repository.AIDigestRepository", return_value=repo), \
             patch("backend.api.market_context.router.get_engine", return_value=engine), \
             patch("backend.api.main_helpers._watched_symbols", return_value=[]):
            out = market_baseline.build_market_baseline()

        self.assertEqual(out["regime_live"], {})
        self.assertEqual(out["digest"], {})
        self.assertEqual(out["watchlist_snapshot"], {"scored": [], "all_symbols": []})
        self.assertIn("as_of", out)

    def test_never_calls_analyze_symbol(self):
        with patch("backend.ai.analyze.analyze_symbol") as analyze, \
             patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls, \
             patch("backend.api.market_context.router.get_engine") as get_engine, \
             patch("backend.api.main_helpers._watched_symbols", return_value=[]):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            market_baseline.build_market_baseline()
        analyze.assert_not_called()

    def test_carries_digest_generated_at_and_movers(self):
        from datetime import datetime

        row = MagicMock()
        row.session = "close"
        row.generated_at = datetime(2026, 9, 10, 16, 5)
        row.market_regime = "RISK_ON"
        row.narrative = "Broad strength."
        row.payload = json.dumps({
            "movers": {"top_bullish": [{"symbol": "NVDA"}], "top_bearish": []},
            "rsi_extremes": [{"symbol": "AAPL", "rsi": 78}],
            "mtf_alignment_counts": {"bullish": 5, "bearish": 1},
        })
        repo = MagicMock()
        repo.get_latest.side_effect = lambda s: row if s == "close" else None
        engine = MagicMock()
        engine.get_current_context.return_value = None
        with patch("backend.repositories.ai_digest_repository.AIDigestRepository", return_value=repo), \
             patch("backend.api.market_context.router.get_engine", return_value=engine), \
             patch("backend.api.main_helpers._watched_symbols", return_value=[]):
            out = market_baseline.build_market_baseline()

        self.assertEqual(out["digest"]["generated_at"], "2026-09-10T16:05:00")
        self.assertEqual(out["digest"]["market_regime"], "RISK_ON")
        self.assertEqual(out["digest"]["movers"]["top_bullish"], [{"symbol": "NVDA"}])
        self.assertEqual(out["digest"]["mtf_alignment_counts"], {"bullish": 5, "bearish": 1})


if __name__ == "__main__":
    unittest.main()
