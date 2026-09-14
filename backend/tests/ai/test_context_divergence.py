"""
Tests for build_context()'s divergence section (Phase 9 engine,
never reached the AI analysis prompt before this change).

Mocks DivergenceEngine.detect() with a real Divergence dataclass
instance (rather than hand-tuning a synthetic bar series to reliably
trigger real pivot detection, which the existing router-level test
for the same engine — test_analysis_router.py's
TestDivergencesEndpoint — doesn't attempt either) so the assertion
is exact: build_context()'s divergence dict must equal the engine's
own .to_dict() output for the most recent detected divergence.
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from backend.ai.context import build_context
from backend.divergence.divergence_engine import Divergence, DivergenceDirection, DivergenceType
from backend.scanner.scanner import ScanResult


def _fake_scan_result() -> ScanResult:
    result = ScanResult("AAPL", datetime.now(UTC))
    result.quote = MagicMock()
    result.quote.price = 185.0
    result.quote.timestamp = datetime.now(UTC)
    result.trend_signals = {
        "ONE_DAY": {"direction": "strong_bullish", "strength": "strong", "confidence": 0.85},
    }
    result.indicator_values = {"rsi": 62.0, "macd": 1.5, "volume": 50_000_000}
    result.scores = {"total_score": 72.5}
    result.signals = ["bullish_trend", "high_volume"]
    return result


def _fake_bars(n: int = 60) -> list[dict]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        {
            "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
            "close": 100.5 + i, "volume": 1_000_000, "timestamp": base,
        }
        for i in range(n)
    ]


class TestBuildContextDivergence(unittest.TestCase):

    @patch("backend.divergence.DivergenceEngine.detect")
    @patch("backend.analysis.series.load_bars")
    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_most_recent_divergence_matches_engine_output(
        self, mock_scanner, mock_get_engine, mock_load_bars, mock_detect
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        mock_load_bars.return_value = _fake_bars()

        older = Divergence(
            type=DivergenceType.BULLISH_RSI,
            direction=DivergenceDirection.BULLISH,
            symbol="AAPL", timeframe="1d",
            pivot_a_index=5, pivot_b_index=20,
            pivot_a_price=95.0, pivot_b_price=90.0,
            pivot_a_indicator=25.0, pivot_b_indicator=35.0,
            timestamp=datetime(2026, 1, 5, tzinfo=UTC), strength=0.6,
        )
        newest = Divergence(
            type=DivergenceType.BEARISH_RSI,
            direction=DivergenceDirection.BEARISH,
            symbol="AAPL", timeframe="1d",
            pivot_a_index=30, pivot_b_index=55,
            pivot_a_price=110.0, pivot_b_price=120.0,
            pivot_a_indicator=75.0, pivot_b_indicator=65.0,
            timestamp=datetime(2026, 2, 1, tzinfo=UTC), strength=0.8,
        )
        mock_detect.return_value = [older, newest]

        ctx = build_context("AAPL", "1d")

        # Only the most recent (last in the list) divergence is kept,
        # mirroring how trend_transition keeps only the latest transition.
        self.assertEqual(ctx.divergence, newest.to_dict())
        self.assertEqual(ctx.divergence["type"], "bearish_rsi_divergence")

    @patch("backend.analysis.series.load_bars")
    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_too_few_bars_degrades_to_empty_dict(
        self, mock_scanner, mock_get_engine, mock_load_bars
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        mock_load_bars.return_value = _fake_bars(n=10)  # below the 30-bar floor

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.divergence, {})

    @patch("backend.analysis.series.load_bars")
    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_no_divergences_found_degrades_to_empty_dict(
        self, mock_scanner, mock_get_engine, mock_load_bars
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        mock_load_bars.return_value = _fake_bars()

        with patch("backend.divergence.DivergenceEngine.detect", return_value=[]):
            ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.divergence, {})

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_include_divergence_false_skips_the_extra_bar_load(
        self, mock_scanner, mock_get_engine
    ):
        """include_divergence=False must skip divergence's own
        load_bars call. Support/resistance (section 7, unconditional)
        also calls load_bars with the same (symbol, timeframe) args
        now, so the right assertion is "one fewer call than with
        divergence on", not "zero calls total" — this test used to
        assert the latter, back when load_bars had only one caller.

        S/R also pulls load_reference_bars()'s daily series (which
        calls load_bars itself) through a 60s TTL cache — warm it by
        an earlier test in this file and the first block counts one
        call fewer than the second, so both measurements must start
        cold."""
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})

        from backend.analysis.series import _reference_bars_cache

        _reference_bars_cache.clear()
        with patch("backend.analysis.series.load_bars", return_value=_fake_bars()) as mock_lb:
            build_context("AAPL", "1d", include_divergence=True)
            with_divergence_calls = mock_lb.call_count

        _reference_bars_cache.clear()
        with patch("backend.analysis.series.load_bars", return_value=_fake_bars()) as mock_lb:
            ctx = build_context("AAPL", "1d", include_divergence=False)
            without_divergence_calls = mock_lb.call_count

        self.assertEqual(without_divergence_calls, with_divergence_calls - 1)
        self.assertEqual(ctx.divergence, {})


if __name__ == "__main__":
    unittest.main()
