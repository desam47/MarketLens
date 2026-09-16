"""Tests for the NL search executor."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.market_data import DataStatus, Quote
from backend.nl_search.executor import (
    JustTransitionedFilter,
    OutperformsBenchmark,
    _build_filter,
    execute_query,
)
from backend.nl_search.schema import NLFilters
from backend.scanner.scanner import ScanResult


def _make_quote(symbol: str, price: float = 100.0, volume: int = 1_000_000) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        timestamp=datetime(2026, 1, 1),
        provider="test",
        data_status=DataStatus.DELAYED,
        volume=volume,
        bid=price - 0.5,
        ask=price + 0.5,
    )


def _make_result(
    symbol: str,
    *,
    trend_signals: dict | None = None,
    indicator_values: dict | None = None,
    scores: dict | None = None,
    signals: list[str] | None = None,
    change_pct: float | None = 2.5,
) -> ScanResult:
    r = ScanResult(symbol, datetime(2026, 1, 1))
    r.quote = _make_quote(symbol)
    r.change_pct = change_pct
    r.trend_signals = trend_signals or {
        "ONE_MINUTE": {"direction": "uptrend", "confidence": 0.7},
        "ONE_DAY": {"direction": "uptrend", "confidence": 0.8},
    }
    r.indicator_values = indicator_values or {
        "price": 100.0, "rsi": 50.0, "macd": 1.0, "adx": 30.0, "volume": 1_000_000
    }
    r.scores = scores or {"momentum": 60.0, "volume": 70.0, "trend_strength": 75.0}
    r.signals = list(signals or [])
    return r


class TestBuildFilter(unittest.TestCase):

    def test_direction_only(self):
        f = NLFilters(direction="bullish")
        filt, desc = _build_filter(f)
        self.assertIsNotNone(filt)
        self.assertIn("bullish", desc.lower())

    def test_trend_min(self):
        f = NLFilters(trend_min=50)
        filt, desc = _build_filter(f)
        self.assertIn("trend", desc.lower())

    def test_rsi_oversold(self):
        f = NLFilters(rsi_oversold_below=30)
        filt, desc = _build_filter(f)
        self.assertIn("rsi", desc.lower())

    def test_signals(self):
        f = NLFilters(signals=["HIGH_VOLUME"])
        filt, desc = _build_filter(f)
        self.assertIn("HIGH_VOLUME", desc)

    def test_min_bullish_timeframes(self):
        f = NLFilters(min_bullish_timeframes=3)
        filt, desc = _build_filter(f)
        self.assertIn("3", desc)

    def test_transition_filter_added(self):
        f = NLFilters(transition="just_became_bullish")
        filt, desc = _build_filter(f)
        self.assertIn("recently", desc.lower())

    def test_match_all(self):
        f = NLFilters(match_all=True)
        filt, desc = _build_filter(f)
        self.assertIn("match all", desc)

    def test_match_all_actually_matches_non_bullish_symbols(self):
        """Regression: the match-all fallback used to be
        DailyBullish(min_confidence=-1.0) — the confidence floor was
        disabled, but TimeframeDirection.matches() still hardcoded
        direction == "uptrend", so "match all" silently excluded every
        symbol whose daily trend wasn't literally an uptrend. A real
        match-all must match everything, direction included."""
        f = NLFilters(match_all=True)
        filt, _ = _build_filter(f)

        downtrend = _make_result(
            "XYZ",
            trend_signals={"ONE_DAY": {"direction": "downtrend", "confidence": 0.9}},
        )
        no_signal = _make_result("ABC", trend_signals={})

        self.assertTrue(filt.matches(downtrend))
        self.assertTrue(filt.matches(no_signal))

    def test_conflict_extra(self):
        f = NLFilters(mtf_conflict=True)
        extras = {"conflict": {"timeframe": "5m", "direction": "bearish"}}
        filt, desc = _build_filter(f, extras=extras)
        # The conflict produces an additional TimeframeDirection (5m=bearish)
        self.assertIn("5m", desc)
        self.assertIn("bearish", desc.lower())

    def test_outperforms_qqq(self):
        f = NLFilters(outperforms="QQQ")
        filt, desc = _build_filter(f)
        self.assertIn("QQQ", desc)

    def test_combined_filters(self):
        f = NLFilters(
            direction="bullish",
            timeframe="1d",
            trend_min=50,
            volume_min=1_000_000,
        )
        filt, desc = _build_filter(f)
        # Description has multiple components
        self.assertIn("1d", desc)
        self.assertIn("bullish", desc.lower())
        self.assertIn("volume", desc.lower())


class TestCustomFilters(unittest.TestCase):

    def test_outperforms_qqq_match(self):
        result = _make_result("AAPL", indicator_values={"rs_pct_QQQ": 5.0})
        f = OutperformsBenchmark("QQQ", min_pct=0.0)
        self.assertTrue(f.matches(result))

    def test_outperforms_qqq_no_match_when_below(self):
        result = _make_result("AAPL", indicator_values={"rs_pct_QQQ": -5.0})
        f = OutperformsBenchmark("QQQ", min_pct=0.0)
        self.assertFalse(f.matches(result))

    def test_outperforms_qqq_no_match_when_missing(self):
        result = _make_result("AAPL", indicator_values={})
        f = OutperformsBenchmark("QQQ")
        self.assertFalse(f.matches(result))

    def test_just_transitioned_filter_bullish(self):
        result = _make_result("AAPL")
        f = JustTransitionedFilter("just_became_bullish")
        # No mocked engine — should return False (no history)
        self.assertFalse(f.matches(result))

    @patch("backend.api.trend.registry.get_engine")
    def test_just_transitioned_filter_with_history(self, mock_get_engine):
        """Regression for a live bug (2026-09-09): this filter used to
        import a `trend_transition_engine` singleton and call
        `.get_history(symbol=...)` on it — neither exists, so the
        filter always returned False regardless of real transitions.
        Fixed to pull the TrendEngine's own score history and run
        TrendTransitionEngine.latest() on it directly (same fix as
        backend.ai.context.build_context's identical bug)."""
        from backend.engines.timeframe import Timeframe

        # A score series that crosses from strongly negative to
        # strongly positive within the window=5 lookback — a genuine
        # bullish reversal, not a mocked transition object.
        scores = [-20, -15, -10, -5, 0, 5, 15]
        base = datetime(2026, 1, 1)
        signals = []
        for i, sc in enumerate(scores):
            sig = MagicMock()
            sig.score = sc
            sig.timestamp = base + timedelta(days=i)
            signals.append(sig)

        mock_engine = MagicMock()
        mock_engine.trend_history = {Timeframe.ONE_DAY: signals}
        mock_get_engine.return_value = mock_engine

        result = _make_result("AAPL")
        f = JustTransitionedFilter("just_became_bullish")
        self.assertTrue(f.matches(result))

        # A bearish-expectation filter must NOT match the same bullish
        # transition.
        f_bearish = JustTransitionedFilter("just_became_bearish")
        self.assertFalse(f_bearish.matches(result))


class TestExecuteQuery(unittest.TestCase):

    @patch("backend.nl_search.executor.market_scanner")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    def test_direction_filters_cache(self, mock_resolve, mock_scanner):
        aapl = _make_result("AAPL", scores={"momentum": 80.0})
        msft = _make_result("MSFT", scores={"momentum": 50.0})
        aapl.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.8}}
        msft.trend_signals = {"ONE_DAY": {"direction": "downtrend", "confidence": 0.8}}

        mock_scanner.scan_results = {"AAPL": aapl, "MSFT": msft}
        mock_scanner.scan_symbols = AsyncMock()
        mock_resolve.return_value = ["AAPL", "MSFT"]

        f = NLFilters(direction="bullish")
        result = execute_query(f, watchlist_id=1)

        self.assertEqual(result.matched_count, 1)
        self.assertEqual(len(result.top_n), 1)
        self.assertEqual(result.top_n[0].symbol, "AAPL")

    @patch("backend.nl_search.executor.market_scanner")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    def test_empty_cache_returns_empty(self, mock_resolve, mock_scanner):
        mock_scanner.scan_results = {}
        mock_resolve.return_value = []

        f = NLFilters(direction="bullish")
        result = execute_query(f, watchlist_id=1)

        self.assertEqual(result.matched_count, 0)
        self.assertEqual(result.top_n, [])
        self.assertEqual(result.universe_size, 0)

    @patch("backend.nl_search.executor.market_scanner")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    def test_ranking_ordering(self, mock_resolve, mock_scanner):
        # strongest_bullish ranks by live price change_pct, not the
        # momentum score — set change_pct to match the intended order.
        aapl = _make_result("AAPL", scores={"momentum": 90.0}, change_pct=9.0)
        msft = _make_result("MSFT", scores={"momentum": 70.0}, change_pct=5.0)
        goog = _make_result("GOOG", scores={"momentum": 50.0}, change_pct=1.0)
        for r in (aapl, msft, goog):
            r.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.8}}

        mock_scanner.scan_results = {"AAPL": aapl, "MSFT": msft, "GOOG": goog}
        mock_scanner.scan_symbols = AsyncMock()
        mock_resolve.return_value = ["AAPL", "MSFT", "GOOG"]

        f = NLFilters(direction="bullish", top_n=3, ranking="strongest_bullish")
        result = execute_query(f, watchlist_id=1)

        symbols = [item.symbol for item in result.top_n]
        # Sorted by change_pct desc — AAPL first
        self.assertEqual(symbols[0], "AAPL")

    @patch("backend.nl_search.executor.market_scanner")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    def test_top_n_caps_results(self, mock_resolve, mock_scanner):
        results = {}
        symbols = [f"SYM{i}" for i in range(10)]
        for s in symbols:
            r = _make_result(s, scores={"momentum": 50.0})
            r.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.8}}
            results[s] = r
        mock_scanner.scan_results = results
        mock_scanner.scan_symbols = AsyncMock()
        mock_resolve.return_value = symbols

        f = NLFilters(direction="bullish", top_n=3, ranking="strongest_bullish")
        result = execute_query(f, watchlist_id=1)

        self.assertEqual(result.matched_count, 10)
        self.assertEqual(len(result.top_n), 3)


class TestExecuteQueryWithRSPercent(unittest.TestCase):

    @patch("backend.nl_search.executor.market_scanner")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    def test_outperforms_filter(self, mock_resolve, mock_scanner):
        aapl = _make_result("AAPL", indicator_values={"rs_pct_QQQ": 5.0})
        msft = _make_result("MSFT", indicator_values={"rs_pct_QQQ": -5.0})
        for r in (aapl, msft):
            r.trend_signals = {"ONE_DAY": {"direction": "uptrend", "confidence": 0.8}}

        mock_scanner.scan_results = {"AAPL": aapl, "MSFT": msft}
        mock_scanner.scan_symbols = AsyncMock()
        mock_resolve.return_value = ["AAPL", "MSFT"]

        f = NLFilters(outperforms="QQQ")
        result = execute_query(f, watchlist_id=1)

        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.top_n[0].symbol, "AAPL")


if __name__ == "__main__":
    unittest.main()
