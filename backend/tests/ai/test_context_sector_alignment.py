"""
Regression for a live bug (2026-09-10): backend.ai.context.build_context()
step 5 called SectorEngine(sym) with no injected engines. SectorEngine
then builds three brand-new, never-fed TrendEngine instances (stock,
sector ETF, SPY) from scratch — zero seed data, zero ticks — so
get_overall_trend() was always None and every alignment came back
"unknown"/"insufficient_data" regardless of how much real trend data
actually existed elsewhere in the app. SectorEngine's own docstring
says exactly what should happen instead: "accept injected engines to
share with other callers ... looked up via the shared registry so any
other component that also needs SPY or XLK gets the same instance" —
but this call site (and backend/api/regime/router.py's
_get_sector_engine, also fixed) wasn't doing that injection.

Fixed by pulling the same shared, DB-seeded TrendEngine singletons
every other trend-consuming feature in the app already uses
(backend.api.trend.registry.get_engine).
"""

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from backend.ai.context import build_context
from backend.scanner.scanner import ScanResult
from backend.trend.trend_engine import TrendDirection


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


def _mock_engine_with_trend(direction: TrendDirection) -> MagicMock:
    eng = MagicMock()
    eng.get_overall_trend.return_value = MagicMock(direction=direction)
    return eng


class TestBuildContextSectorAlignment(unittest.TestCase):
    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_populates_real_alignment_when_registry_engines_are_warm(
        self, mock_scanner, mock_get_engine
    ):
        """AAPL/Technology/XLK all uptrend, SPY (market) uptrend — a
        genuine 'perfect' alignment, not the old always-'unknown'/
        'insufficient_data' result."""
        mock_scanner.scan_symbol.return_value = _fake_scan_result()

        engines_by_symbol = {
            "AAPL": _mock_engine_with_trend(TrendDirection.UPTREND),
            "XLK": _mock_engine_with_trend(TrendDirection.UPTREND),
            "SPY": _mock_engine_with_trend(TrendDirection.UPTREND),
        }

        def _get_engine_for_trend_history(sym):
            # First call in build_context is for the trend_transition
            # section (sym itself) — reuse the same mock, only
            # .trend_history matters there and MagicMock provides it.
            return engines_by_symbol.get(sym, MagicMock(trend_history={}))

        mock_get_engine.side_effect = _get_engine_for_trend_history

        ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.sector_alignment.get("stock_trend"), "uptrend")
        self.assertEqual(ctx.sector_alignment.get("sector_trend"), "uptrend")
        self.assertEqual(ctx.sector_alignment.get("market_trend"), "uptrend")
        self.assertEqual(ctx.sector_alignment.get("alignment_level"), "perfect")
        self.assertNotEqual(
            ctx.sector_alignment.get("contributing_factors", {}).get("reason"),
            "insufficient_data",
        )

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_two_of_three_known_still_computes_an_alignment(self, mock_scanner, mock_get_engine):
        """A real gap (e.g. the sector ETF was never ingested, so its
        registry engine is genuinely cold) shouldn't force the whole
        signal back to insufficient_data as long as 2 of 3 are known —
        matches SectorEngine's own unknown_count >= 2 threshold."""
        mock_scanner.scan_symbol.return_value = _fake_scan_result()

        engines_by_symbol = {
            "AAPL": _mock_engine_with_trend(TrendDirection.UPTREND),
            "XLK": MagicMock(get_overall_trend=MagicMock(return_value=None), trend_history={}),
            "SPY": _mock_engine_with_trend(TrendDirection.UPTREND),
        }
        mock_get_engine.side_effect = lambda sym: engines_by_symbol.get(
            sym, MagicMock(trend_history={})
        )

        ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.sector_alignment.get("stock_trend"), "uptrend")
        self.assertEqual(ctx.sector_alignment.get("sector_trend"), "unknown")
        self.assertEqual(ctx.sector_alignment.get("market_trend"), "uptrend")
        self.assertNotEqual(
            ctx.sector_alignment.get("contributing_factors", {}).get("reason"),
            "insufficient_data",
        )

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_registry_exception_degrades_gracefully(self, mock_scanner, mock_get_engine):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.side_effect = RuntimeError("registry unavailable")

        ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.sector_alignment, {})
        # The rest of the context must still be populated.
        self.assertIsNotNone(ctx.price)


if __name__ == "__main__":
    unittest.main()
