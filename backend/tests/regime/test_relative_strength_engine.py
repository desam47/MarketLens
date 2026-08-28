"""
Tests for RelativeStrengthEngine — Phase 8 spec.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.regime.relative_strength_engine import (
    RelativeStrengthClassification,
    RelativeStrengthEngine,
    RelativeStrengthSignal,
)


class TestRelativeStrengthEngine(unittest.TestCase):

    def setUp(self):
        self.symbol = "AAPL"
        self.engine = RelativeStrengthEngine(self.symbol, lookback_days=5)

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def test_engine_initializes_3_engines(self):
        """Engine has TrendEngines for the symbol, SPY, and QQQ."""
        self.assertEqual(len(self.engine._engines), 3)
        for sym in (self.symbol, "SPY", "QQQ"):
            self.assertIn(sym, self.engine._engines)

    def test_default_lookback_from_settings(self):
        """No lookback param → reads from settings."""
        from backend.config.settings import settings
        engine = RelativeStrengthEngine(self.symbol)
        self.assertEqual(engine.lookback_days, settings.relative_strength.lookback_days)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def test_update_unknown_symbol_ignored(self):
        """An unknown symbol is logged and ignored."""
        now = datetime.now()
        # Should not raise
        self.engine.update(price=100.0, volume=1000, timestamp=now, symbol="GARBAGE")

    def test_update_prunes_history(self):
        """After enough updates, old entries are pruned."""
        now = datetime.now()
        for i in range(20):
            # Price + timestamp advance the history
            self.engine.update(price=100.0 + i * 0.1, volume=1000, timestamp=now + timedelta(minutes=i), symbol=self.symbol)
        self.assertLessEqual(len(self.engine._price_history[self.symbol]), self.engine.lookback_days * 3)

    # ------------------------------------------------------------------
    # Classification thresholds
    # ------------------------------------------------------------------

    def test_classify_strong_outperformer(self):
        """Alpha > +5% → STRONG_OUTPERFORMER."""
        cls = self.engine._classify(0.06)
        self.assertEqual(cls, RelativeStrengthClassification.STRONG_OUTPERFORMER)

    def test_classify_outperformer(self):
        """Alpha > +1% → OUTPERFORMER."""
        cls = self.engine._classify(0.02)
        self.assertEqual(cls, RelativeStrengthClassification.OUTPERFORMER)

    def test_classify_inline(self):
        """-1% <= alpha <= +1% → INLINE."""
        cls = self.engine._classify(0.005)
        self.assertEqual(cls, RelativeStrengthClassification.INLINE)
        cls2 = self.engine._classify(-0.003)
        self.assertEqual(cls2, RelativeStrengthClassification.INLINE)

    def test_classify_underperformer(self):
        """Alpha < -1% → UNDERPERFORMER."""
        cls = self.engine._classify(-0.02)
        self.assertEqual(cls, RelativeStrengthClassification.UNDERPERFORMER)

    def test_classify_strong_underperformer(self):
        """Alpha < -5% → STRONG_UNDERPERFORMER."""
        cls = self.engine._classify(-0.06)
        self.assertEqual(cls, RelativeStrengthClassification.STRONG_UNDERPERFORMER)

    def test_return_over_lookback_simple(self):
        """Simple return calculation works correctly."""
        # Price goes from 100 to 110 → 10% return
        hist = [(datetime.now(), 100.0), (datetime.now(), 110.0)]
        self.engine._price_history[self.symbol] = hist
        ret = self.engine._return_over_lookback(hist)
        self.assertAlmostEqual(ret, 0.10, places=3)

    def test_return_over_lookback_insufficient_data(self):
        """Insufficient history → 0.0."""
        ret = self.engine._return_over_lookback([])
        self.assertEqual(ret, 0.0)

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def test_compute_insufficient_history(self):
        """Not enough price history → UNKNOWN signals."""
        signals = self.engine.compute()
        for sig in signals:
            self.assertEqual(sig.classification, RelativeStrengthClassification.UNKNOWN)

    def test_signal_to_dict(self):
        """Signal serialises to a clean dict."""
        sig = RelativeStrengthSignal(
            symbol="AAPL",
            benchmark="SPY",
            rs_pct=0.03,
            classification=RelativeStrengthClassification.OUTPERFORMER,
            symbol_return_pct=0.05,
            benchmark_return_pct=0.02,
            lookback_days=5,
            timestamp=datetime(2025, 1, 1),
        )
        d = sig.to_dict()
        self.assertEqual(d["symbol"], "AAPL")
        self.assertEqual(d["benchmark"], "SPY")
        self.assertEqual(d["classification"], "outperformer")
        # to_dict converts fraction → percent
        self.assertEqual(d["rs_pct"], 3.0)


class TestThresholdConfigurable(unittest.TestCase):
    """Principle 11: thresholds come from settings, not hard-coded."""

    def test_outperformer_threshold_from_settings(self):
        """Engine reads outperformer_threshold from settings."""
        from backend.config.settings import settings
        engine = RelativeStrengthEngine("AAPL")
        self.assertEqual(engine._cfg.outperformer_threshold,
                         settings.relative_strength.outperformer_threshold)

    def test_benchmarks_configurable(self):
        """Engine reads benchmark list from settings, not hardcoded."""
        from unittest.mock import patch

        from backend.config.settings import settings
        engine = RelativeStrengthEngine("AAPL")
        # Default is SPY + QQQ per the Phase 8 spec.
        self.assertEqual(engine._cfg.benchmark_list(), ("SPY", "QQQ"))
        self.assertEqual(engine._all_symbols(), ["AAPL", "SPY", "QQQ"])

        # When the config string changes, the engine follows.
        with patch.object(
            settings.relative_strength, "benchmarks", "SPY,QQQ,IWM"
        ):
            self.assertEqual(
                engine._cfg.benchmark_list(), ("SPY", "QQQ", "IWM")
            )
            self.assertEqual(
                engine._all_symbols(), ["AAPL", "SPY", "QQQ", "IWM"]
            )

    def test_benchmark_list_drops_empty_entries(self):
        """Trailing/empty entries in the benchmarks string are dropped."""
        from backend.config.settings import RelativeStrengthSettings
        cfg = RelativeStrengthSettings(benchmarks="SPY,,QQQ, ,IWM")
        self.assertEqual(cfg.benchmark_list(), ("SPY", "QQQ", "IWM"))


if __name__ == '__main__':
    unittest.main()
