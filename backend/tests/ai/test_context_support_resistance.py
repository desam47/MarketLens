"""
Regression for a live bug (2026-09-10): backend.ai.context.build_context()
step 7 imported a `support_resistance_engine` singleton from
`backend.support_resistance.support_resistance_engine` and called
`.detect_levels(sym, timeframe)` on it — neither the module nor the
method exist. The real module is `backend.support_resistance.sr_engine`
(re-exported as `backend.support_resistance.SupportResistanceEngine`),
a class whose `.detect(bars, symbol, timeframe)` method returns a flat
`.levels` list, not separate `.supports`/`.resistances` attributes.
Same failure class as the trend_transition bug (test_context_transitions.py):
a broad `except Exception: pass` silently swallowed the resulting
ImportError/AttributeError every time, so `support_resistance` had been
an empty dict in every AI context ever built.

Fixed by calling the engine the way the existing, working
    `/api/analysis/{symbol}/price-range` endpoint does
    (backend/api/analysis/router.py) — same bar source, same engine
    construction — then bucketing the flat level list into supports/
    resistances by SRType semantics (a swing_high is resistance by
    definition, regardless of where the latest close sits).
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from backend.ai.context import build_context
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
    """A simple uptrending bar series with real high/low spread, so
    swing/pivot/ATH/ATL levels actually get detected on both sides of
    the latest close (SupportResistanceEngine needs >= 2*lookback+2
    bars; build_context's own gate is >= 20)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(n):
        close = 100.0 + i * 0.5
        bars.append({
            "open": close - 0.3, "high": close + 1.0, "low": close - 1.0,
            "close": close, "volume": 1_000_000, "timestamp": base,
        })
    # load_bars returns desc=True (newest first) — reverse so index 0
    # is the highest close, matching real ordering.
    return list(reversed(bars))


class TestBuildContextSupportResistance(unittest.TestCase):

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_populates_supports_and_resistances_from_real_bars(
        self, mock_scanner, mock_get_engine
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})

        with patch("backend.analysis.series.load_bars", return_value=_fake_bars()):
            ctx = build_context("AAPL", "1d")

        self.assertNotEqual(ctx.support_resistance, {})
        self.assertIn("supports", ctx.support_resistance)
        self.assertIn("resistances", ctx.support_resistance)
        self.assertGreater(len(ctx.support_resistance["supports"]), 0)
        self.assertGreater(len(ctx.support_resistance["resistances"]), 0)
        for level in ctx.support_resistance["supports"] + ctx.support_resistance["resistances"]:
            self.assertIn("price", level)
            self.assertIn("strength", level)

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_supports_are_structural_supports_resistances_structural_resistances(
        self, mock_scanner, mock_get_engine
    ):
        """Bucketing is by TYPE semantics, not by price vs close.

        Found live 2026-09-16: the old `price <= latest_close → support`
        rule mislabeled swing highs as support whenever the close sat
        just below one (NVDA close 213.90 vs swing high 213.75 → reported
        as a "213.75 support" level, which is backwards). A swing_high
        is resistance by definition — price was rejected there — so the
        bucket now keys off SRType. The old price invariant is only
        retained for consolidation_zone, which has no inherent side.
        """
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        bars = _fake_bars()

        with patch("backend.analysis.series.load_bars", return_value=bars):
            ctx = build_context("AAPL", "1d")

        structural_supports = {
            "today_low", "prev_day_low", "this_week_low", "prev_week_low",
            "week_52_low", "pivot_pp", "pivot_s1", "pivot_s2", "pivot_s3",
            "swing_low",
        }
        structural_resistances = {
            "today_high", "prev_day_high", "this_week_high", "prev_week_high",
            "week_52_high", "pivot_r1", "pivot_r2", "pivot_r3",
            "swing_high",
        }
        for level in ctx.support_resistance["supports"]:
            self.assertIn(level["type"], structural_supports | {"consolidation_zone"})
        for level in ctx.support_resistance["resistances"]:
            self.assertIn(level["type"], structural_resistances | {"consolidation_zone"})

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_too_few_bars_degrades_to_empty_dict(self, mock_scanner, mock_get_engine):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})

        with patch("backend.analysis.series.load_bars", return_value=_fake_bars(5)):
            ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.support_resistance, {})

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_load_bars_exception_degrades_gracefully(self, mock_scanner, mock_get_engine):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})

        with patch("backend.analysis.series.load_bars", side_effect=RuntimeError("db down")):
            ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.support_resistance, {})
        # The rest of the context must still be populated.
        self.assertIsNotNone(ctx.price)


if __name__ == "__main__":
    unittest.main()
