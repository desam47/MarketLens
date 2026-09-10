"""
Regression for a live bug (2026-09-09): backend.ai.context.build_context()
step 8 imported a `trend_transition_engine` singleton and called
`.get_history(symbol=...)` on it — neither exists in
backend.transitions.trend_transition_engine (only the `TrendTransitionEngine`
class, with `.detect()`/`.latest()` taking a raw scores sequence). The
broad `except Exception: pass` silently swallowed the resulting
ImportError/AttributeError every time, so `trend_transition` has been
an empty dict in every AI analysis ever produced.

Fixed to pull the already-warmed TrendEngine's own score history (via
the same registry the scanner already uses) and run
TrendTransitionEngine.latest() on it directly — no new bar fetch, no
non-existent singleton.
"""
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from backend.ai.context import build_context
from backend.scanner.scanner import ScanResult


def _fake_scan_result() -> ScanResult:
    """Minimal ScanResult with enough populated fields for
    build_context — same shape as test_phase16_analyze.py's fixture
    of the same name (duplicated locally; no cross-test-file import
    precedent exists in this codebase)."""
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


class TestBuildContextTransitionFix(unittest.TestCase):

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_populates_trend_transition_from_real_score_history(
        self, mock_scanner, mock_get_engine
    ):
        from backend.engines.timeframe import Timeframe

        mock_scanner.scan_symbol.return_value = _fake_scan_result()

        # A genuine bullish reversal within the default window=5 lookback.
        scores = [-20, -15, -10, -5, 0, 5, 15]
        base = datetime(2026, 1, 1, tzinfo=UTC)
        signals = []
        for i, sc in enumerate(scores):
            sig = MagicMock()
            sig.score = sc
            sig.timestamp = base + timedelta(days=i)
            signals.append(sig)

        mock_engine = MagicMock()
        mock_engine.trend_history = {Timeframe.ONE_DAY: signals}
        mock_get_engine.return_value = mock_engine

        ctx = build_context("AAPL", "1d")

        self.assertNotEqual(ctx.trend_transition, {})
        self.assertEqual(ctx.trend_transition["type"], "bullish_reversal")
        self.assertEqual(ctx.trend_transition["direction"], "bullish")
        self.assertIn("delta", ctx.trend_transition)

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_insufficient_history_degrades_to_empty_dict(
        self, mock_scanner, mock_get_engine
    ):
        """Fewer than 7 signals (the > 6 gate) must not crash — just no
        transition reported, same as any other degraded sub-engine."""
        from backend.engines.timeframe import Timeframe

        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_engine = MagicMock()
        mock_engine.trend_history = {Timeframe.ONE_DAY: []}
        mock_get_engine.return_value = mock_engine

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.trend_transition, {})

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_registry_exception_degrades_gracefully(
        self, mock_scanner, mock_get_engine
    ):
        """A broken trend registry must not take the whole context
        down — matches every other section's _safe_call-style
        degrade-to-empty behavior."""
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.side_effect = RuntimeError("registry unavailable")

        ctx = build_context("AAPL", "1d")
        self.assertEqual(ctx.trend_transition, {})
        # The rest of the context must still be populated.
        self.assertIsNotNone(ctx.price)


if __name__ == "__main__":
    unittest.main()
