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
        with (
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository") as alert_cls,
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            alert_cls.return_value.get_all_enabled.return_value = []
            a = market_baseline.build_market_baseline()
            b = market_baseline.build_market_baseline()
        self.assertIs(a, b)  # 2nd call served from cache
        c = market_baseline.build_market_baseline(use_cache=False)
        self.assertIsNot(a, c)  # forced rebuild

    def test_invalidate_cache_forces_next_call_to_rebuild(self):
        with (
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository") as alert_cls,
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            alert_cls.return_value.get_all_enabled.return_value = []
            a = market_baseline.build_market_baseline()
            market_baseline.invalidate_cache()
            b = market_baseline.build_market_baseline()
        self.assertIsNot(a, b)  # cache was dropped, not served stale

    def test_invalidate_cache_is_safe_with_no_prior_build(self):
        market_baseline.invalidate_cache()  # must not raise

    def test_degrades_to_empty_sections_when_sources_missing(self):
        repo = MagicMock()
        repo.get_latest.return_value = None
        engine = MagicMock()
        engine.get_current_context.return_value = None
        alert_repo = MagicMock()
        alert_repo.get_all_enabled.return_value = []
        with (
            patch(
                "backend.repositories.ai_digest_repository.AIDigestRepository", return_value=repo
            ),
            patch("backend.api.market_context.router.get_engine", return_value=engine),
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository", return_value=alert_repo),
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            out = market_baseline.build_market_baseline()

        self.assertEqual(out["regime_live"], {})
        self.assertEqual(out["digest"], {})
        self.assertEqual(out["watchlist_snapshot"], {"scored": [], "all_symbols": []})
        self.assertEqual(out["watchlists"], [])
        self.assertEqual(out["active_alerts"], [])
        self.assertIn("as_of", out)

    def test_active_alerts_degrades_to_empty_on_repo_error(self):
        with (
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch(
                "backend.repositories.alert_repository.AlertRepository",
                side_effect=RuntimeError("db down"),
            ),
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            out = market_baseline.build_market_baseline()
        self.assertEqual(out["active_alerts"], [])

    def test_active_alerts_carries_alert_fields(self):
        alert = MagicMock()
        alert.id = 7
        alert.symbol = "NVDA"
        alert.name = "NVDA breakout"
        alert.condition_type = "price_above"
        alert.parameter = "220"
        alert_repo = MagicMock()
        alert_repo.get_all_enabled.return_value = [alert]
        with (
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository", return_value=alert_repo),
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            out = market_baseline.build_market_baseline()
        self.assertEqual(
            out["active_alerts"],
            [
                {
                    "id": 7,
                    "symbol": "NVDA",
                    "name": "NVDA breakout",
                    "condition_type": "price_above",
                    "parameter": "220",
                },
            ],
        )

    def test_never_calls_analyze_symbol(self):
        with (
            patch("backend.ai.analyze.analyze_symbol") as analyze,
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as repo_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository") as alert_cls,
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            repo_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            alert_cls.return_value.get_all_enabled.return_value = []
            market_baseline.build_market_baseline()
        analyze.assert_not_called()

    def test_carries_digest_generated_at_and_movers(self):
        from datetime import datetime

        row = MagicMock()
        row.session = "close"
        row.generated_at = datetime(2026, 9, 10, 16, 5)
        row.market_regime = "RISK_ON"
        row.narrative = "Broad strength."
        row.payload = json.dumps(
            {
                "movers": {"top_bullish": [{"symbol": "NVDA"}], "top_bearish": []},
                "rsi_extremes": [{"symbol": "AAPL", "rsi": 78}],
                "mtf_alignment_counts": {"bullish": 5, "bearish": 1},
            }
        )
        repo = MagicMock()
        repo.get_latest.side_effect = lambda s: row if s == "close" else None
        engine = MagicMock()
        engine.get_current_context.return_value = None
        with (
            patch(
                "backend.repositories.ai_digest_repository.AIDigestRepository", return_value=repo
            ),
            patch("backend.api.market_context.router.get_engine", return_value=engine),
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository") as alert_cls,
            patch("backend.ai.market_baseline._watchlist_index", return_value=[]),
        ):
            alert_cls.return_value.get_all_enabled.return_value = []
            out = market_baseline.build_market_baseline()

        self.assertEqual(out["digest"]["generated_at"], "2026-09-10T16:05:00")
        self.assertEqual(out["digest"]["market_regime"], "RISK_ON")
        self.assertEqual(out["digest"]["movers"]["top_bullish"], [{"symbol": "NVDA"}])
        self.assertEqual(out["digest"]["mtf_alignment_counts"], {"bullish": 5, "bearish": 1})

    def test_watchlists_carries_name_and_symbol_count(self):
        wl = MagicMock()
        wl.name = "Swing Setups"
        enabled = MagicMock(is_enabled=True)
        disabled = MagicMock(is_enabled=False)
        wl.symbols = [enabled, enabled, disabled]
        repo = MagicMock()
        repo.get_watchlists.return_value = [wl]
        with (
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as digest_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository") as alert_cls,
            patch("backend.database.SessionLocal"),
            patch(
                "backend.repositories.watchlist_repository.WatchlistRepository", return_value=repo
            ),
        ):
            digest_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            alert_cls.return_value.get_all_enabled.return_value = []
            out = market_baseline.build_market_baseline()

        self.assertEqual(out["watchlists"], [{"name": "Swing Setups", "symbol_count": 2}])
        repo.get_watchlists.assert_called_once_with(active_only=True)

    def test_watchlists_degrades_to_empty_on_repo_error(self):
        with (
            patch("backend.repositories.ai_digest_repository.AIDigestRepository") as digest_cls,
            patch("backend.api.market_context.router.get_engine") as get_engine,
            patch("backend.api.main_helpers._watched_symbols", return_value=[]),
            patch("backend.repositories.alert_repository.AlertRepository") as alert_cls,
            patch("backend.database.SessionLocal"),
            patch(
                "backend.repositories.watchlist_repository.WatchlistRepository",
                side_effect=RuntimeError("db down"),
            ),
        ):
            digest_cls.return_value.get_latest.return_value = None
            get_engine.return_value.get_current_context.return_value = None
            alert_cls.return_value.get_all_enabled.return_value = []
            out = market_baseline.build_market_baseline()

        self.assertEqual(out["watchlists"], [])


if __name__ == "__main__":
    unittest.main()
