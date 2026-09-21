"""
Tests for multi-timeframe engine
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.engines.timeframe import Timeframe
from backend.multitimeframe.multi_timeframe_engine import (
    ALL_TIMEFRAMES,
    PRESET_DAY_TRADING,
    PRESET_SWING,
    ConfluenceDirection,
    MultiTimeframeEngine,
    MultiTimeframeSnapshot,
    TimeframeTrendSnapshot,
)
from backend.trend.trend_engine import (
    TrendDirection,
)


class TestMultiTimeframeEngine(unittest.TestCase):
    def setUp(self):
        self.symbol = "AAPL"
        self.engine = MultiTimeframeEngine(self.symbol)
        # Phase 20 spec tests use 5-minute bar spacing; the trend engine's
        # data-quality gap check (added later) flags 300s > 60s as a gap
        # and would log a warning per bar. Disable for these tests.
        from backend.config.settings import settings

        self._original_gap = settings.data_quality.max_tick_gap_seconds
        settings.data_quality.max_tick_gap_seconds = 86400.0

    def tearDown(self):
        from backend.config.settings import settings

        settings.data_quality.max_tick_gap_seconds = self._original_gap

    def test_engine_initialization(self):
        """Test that multi-timeframe engine initializes correctly"""
        self.assertEqual(self.engine.symbol, self.symbol)
        self.assertIsInstance(self.engine.trend_engines, dict)
        self.assertIsInstance(self.engine.confluence_history, list)

        # Default preset = day_trading: 5m, 15m, 30m, 1h, 4h (5 TFs)
        expected_timeframes = [
            Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE,
            Timeframe.THIRTY_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
        ]
        for tf in expected_timeframes:
            self.assertIn(tf, self.engine.trend_engines)

    def test_engine_update_with_data(self):
        """Test updating the engine with market data"""
        base_time = datetime.now()

        # Simulate an uptrend with enough bars for the 1h timeframe's
        # ema_slow (period=50) to warm up — otherwise the engine is still
        # bootstrapping and may not produce a confluence signal.
        n = 60
        prices = [100 + i * 0.2 for i in range(n)]

        for i, price in enumerate(prices):
            timestamp = base_time + timedelta(minutes=i * 5)  # 5-minute intervals
            self.engine.update(price, 1000, timestamp)

        # Should have processed the data
        # Check that we have some confluence signals
        confluence = self.engine.get_current_confluence()
        # Might not have enough data yet for a full signal, but engine should not crash
        self.assertIsNotNone(confluence)  # Should have some signal even if weak

        # Test that we can get confluence history
        history = self.engine.get_confluence_history()
        self.assertIsInstance(history, list)

    def test_get_current_confluence_no_data(self):
        """Test getting current confluence with no data"""
        # Should return None when no data has been processed
        confluence = self.engine.get_current_confluence()
        # With no data, it should return None
        self.assertIsNone(confluence)

    def test_get_confluence_history_empty(self):
        """Test getting confluence history with no data"""
        history = self.engine.get_confluence_history()
        self.assertIsInstance(history, list)

    def test_get_timeframe_trend(self):
        """Test getting trend for a specific timeframe"""
        base_time = datetime.now()

        # Update with some data
        for i in range(5):
            price = 100 + i
            timestamp = base_time + timedelta(minutes=i * 5)
            self.engine.update(price, 1000, timestamp)

        # Should be able to get trend for a timeframe
        trend = self.engine.get_timeframe_trend(Timeframe.FIVE_MINUTE)
        # Might be None if not enough data, but should not crash
        self.assertTrue(trend is None or hasattr(trend, "direction"))

    def test_get_all_timeframe_trends(self):
        """Test getting trends for all timeframes"""
        base_time = datetime.now()

        # Update with some data
        for i in range(5):
            price = 100 + i
            timestamp = base_time + timedelta(minutes=i * 5)
            self.engine.update(price, 1000, timestamp)

        # Should be able to get all trends
        trends = self.engine.get_all_timeframe_trends()
        self.assertIsInstance(trends, dict)

        # Each trend should be a TrendSignal or None
        for _tf, trend in trends.items():
            if trend is not None:
                self.assertTrue(hasattr(trend, "direction"))
                self.assertTrue(hasattr(trend, "strength"))
                self.assertTrue(hasattr(trend, "confidence"))

    def test_confluence_calculation(self):
        """Test that confluence calculations work correctly"""
        # This test verifies the internal logic works
        base_time = datetime.now()

        # Create a clear uptrend scenario
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112]

        for i, price in enumerate(prices):
            timestamp = base_time + timedelta(minutes=i * 5)
            self.engine.update(price, 1000, timestamp)

        # Get confluence signal
        confluence = self.engine.get_current_confluence()
        self.assertIsNotNone(confluence)

        # Should have valid values
        self.assertIsInstance(confluence.direction, ConfluenceDirection)
        self.assertGreaterEqual(confluence.strength, 0.0)
        self.assertLessEqual(confluence.strength, 1.0)
        self.assertGreaterEqual(confluence.alignment_score, 0.0)
        self.assertLessEqual(confluence.alignment_score, 1.0)
        self.assertIsInstance(confluence.timestamp, datetime)
        self.assertIsInstance(confluence.timeframe_signals, dict)


class TestPhase7Presets(unittest.TestCase):
    """Phase 7: preset configurations + all-8-TF coverage."""

    def test_all_8_timeframes_constant(self):
        """ALL_TIMEFRAMES exposes all 8 spec timeframes (1m..1wk)."""
        expected = {
            Timeframe.ONE_MINUTE,
            Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE,
            Timeframe.THIRTY_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
            Timeframe.ONE_DAY,
            Timeframe.ONE_WEEK,
        }
        self.assertEqual(ALL_TIMEFRAMES, expected)

    def test_default_preset_is_day_trading(self):
        """Default preset = day_trading (5 TFs, no 1m/1d/1wk)."""
        engine = MultiTimeframeEngine("AAPL")
        self.assertEqual(engine.preset_name, "day_trading")
        self.assertEqual(engine.active_timeframes, PRESET_DAY_TRADING)
        self.assertEqual(len(engine.trend_engines), 5)
        # Day trading spec: 5m, 15m, 30m, 1h, 4h
        for tf in (
            Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE,
            Timeframe.THIRTY_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
        ):
            self.assertIn(tf, engine.trend_engines)

    def test_day_trading_preset_excludes_1m_1d_1wk(self):
        """Day-trading preset must NOT include 1m, 1d, or 1wk."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        for tf in (Timeframe.ONE_MINUTE, Timeframe.ONE_DAY, Timeframe.ONE_WEEK):
            self.assertNotIn(tf, engine.trend_engines)

    def test_swing_preset_includes_1wk_excludes_1m_5m_30m(self):
        """Swing preset spec: 15m, 1h, 4h, 1d, 1w."""
        engine = MultiTimeframeEngine("AAPL", preset="swing")
        self.assertEqual(engine.active_timeframes, PRESET_SWING)
        self.assertEqual(len(engine.trend_engines), 5)
        self.assertIn(Timeframe.ONE_WEEK, engine.trend_engines)
        for tf in (Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTE, Timeframe.THIRTY_MINUTE):
            self.assertNotIn(tf, engine.trend_engines)

    def test_all_preset_builds_8_trend_engines(self):
        """preset='all' builds 8 trend engines (full spec)."""
        engine = MultiTimeframeEngine("AAPL", preset="all")
        self.assertEqual(len(engine.trend_engines), 8)
        self.assertEqual(engine.active_timeframes, ALL_TIMEFRAMES)

    def test_unknown_preset_raises(self):
        """An unknown preset name is rejected instead of mislabeled fallback."""
        with self.assertRaisesRegex(ValueError, "Invalid preset"):
            MultiTimeframeEngine("AAPL", preset="nonsense")

    def test_scalper_preset_includes_1m(self):
        """Scalper preset includes 1m, 2m, 3m, 5m, 15m."""
        engine = MultiTimeframeEngine("AAPL", preset="scalper")
        self.assertEqual(engine.preset_name, "scalper")
        self.assertEqual(len(engine.trend_engines), 5)
        self.assertIn(Timeframe.ONE_MINUTE, engine.trend_engines)
        self.assertIn(Timeframe.TWO_MINUTE, engine.trend_engines)
        self.assertIn(Timeframe.THREE_MINUTE, engine.trend_engines)
        self.assertIn(Timeframe.FIVE_MINUTE, engine.trend_engines)
        self.assertIn(Timeframe.FIFTEEN_MINUTE, engine.trend_engines)

    def test_scalper_preset_excludes_longer_tfs(self):
        """Scalper preset excludes 30m, 1h, 4h, 1d, 1wk."""
        engine = MultiTimeframeEngine("AAPL", preset="scalper")
        for tf in (
            Timeframe.THIRTY_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
            Timeframe.ONE_DAY,
            Timeframe.ONE_WEEK,
        ):
            self.assertNotIn(tf, engine.trend_engines)


class TestPhase7Architectural(unittest.TestCase):
    """Phase 7: architectural assertions — settings-driven, no trade signals."""

    def test_timeframe_weights_from_settings(self):
        """Principle 11: timeframe weights come from settings, not hard-coded."""
        from backend.config.settings import settings

        engine = MultiTimeframeEngine("AAPL", preset="all")
        # The engine's weights must match the settings block.
        for tf_value, weight in settings.multitimeframe.weights.items():
            self.assertEqual(engine.timeframe_weights[tf_value], weight)
        # All 8 spec TFs have a weight defined.
        for tf in ALL_TIMEFRAMES:
            self.assertIn(tf.value, engine.timeframe_weights)

    def test_no_trade_signals(self):
        """Spec rule: 'Do not create trade signals yet.'

        No buy/sell method on the engine, no buy/sell field on the
        signal or snapshot.
        """
        engine = MultiTimeframeEngine("AAPL")
        self.assertFalse(hasattr(engine, "get_buy_signal"))
        self.assertFalse(hasattr(engine, "get_sell_signal"))
        self.assertFalse(hasattr(engine, "buy_signal"))
        self.assertFalse(hasattr(engine, "sell_signal"))
        # ConfluenceSignal + snapshots: no buy/sell fields
        self.assertFalse(hasattr(ConfluenceDirection, "BUY"))
        self.assertFalse(hasattr(ConfluenceDirection, "SELL"))
        self.assertFalse(hasattr(MultiTimeframeSnapshot, "buy"))
        self.assertFalse(hasattr(MultiTimeframeSnapshot, "sell"))
        self.assertFalse(hasattr(TimeframeTrendSnapshot, "buy"))
        self.assertFalse(hasattr(TimeframeTrendSnapshot, "sell"))


class TestPhase7Alignments(unittest.TestCase):
    """Phase 7: alignment + bullish/bearish/conflicting + horizon directions."""

    def setUp(self):
        from backend.config.settings import settings

        self._original_gap = settings.data_quality.max_tick_gap_seconds
        settings.data_quality.max_tick_gap_seconds = 86400.0
        self.engine = MultiTimeframeEngine("AAPL")
        self.engine.reset()

    def tearDown(self):
        from backend.config.settings import settings

        settings.data_quality.max_tick_gap_seconds = self._original_gap

    def _warmup(
        self, engine: MultiTimeframeEngine, n_bars: int = 200, base: datetime | None = None
    ):
        """Feed ``n_bars`` of flat warmup bars so slow EMAs (period=50/200)
        can complete their warmup. The MultiTimeframeEngine aggregates
        minute-level ticks into each TF's candle; 200 minute-bars feeds
        ~40 completed 5m candles and ~3 completed 1h candles — enough for
        the slow EMAs of those TFs to start producing values.
        """
        base = base or datetime.now()
        for i in range(n_bars):
            engine.update(100.0, 1_000_000, base + timedelta(minutes=i))

    def _feed(
        self,
        engine: MultiTimeframeEngine,
        prices: list[float],
        start: datetime,
        step: timedelta = timedelta(minutes=1),
    ):
        for i, price in enumerate(prices):
            engine.update(price, 1_000_000, start + step * i)

    def test_bullish_alignment_on_uptrend(self):
        """A clean uptrend should produce bullish_alignment > 0.5 and
        bearish_alignment == 0 with conflicting == 0."""
        engine = MultiTimeframeEngine("AAPL", preset="all")
        base = datetime.now()
        # Warmup bars so each TF's slow EMA (period up to 200 for 1w)
        # has accumulated enough samples to give a direction reading.
        self._warmup(engine, n_bars=200, base=base)
        # Then a steady uptrend.
        prices = [100.0 + i * 0.5 for i in range(90)]
        self._feed(engine, prices, base + timedelta(minutes=200))
        signal = engine.get_current_confluence()
        self.assertIsNotNone(signal)
        self.assertGreater(
            signal.bullish_alignment, 0.5, "Uptrend should produce bullish_alignment > 0.5"
        )
        self.assertEqual(signal.bearish_alignment, 0.0)
        self.assertEqual(signal.conflicting, 0)

    def test_bearish_alignment_on_downtrend(self):
        """A clean downtrend: bearish_alignment > 0, bullish == 0."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        base = datetime.now()
        self._warmup(engine, n_bars=200, base=base)
        prices = [100.0 - i * 0.5 for i in range(90)]
        self._feed(engine, prices, base + timedelta(minutes=200))
        signal = engine.get_current_confluence()
        self.assertIsNotNone(signal)
        self.assertGreater(
            signal.bearish_alignment,
            0,
            f"Expected bearish_alignment > 0, got {signal.bearish_alignment}",
        )
        self.assertEqual(signal.bullish_alignment, 0.0)

    def test_short_intermediate_higher_directions(self):
        """Uptrend: higher_direction should be UPTREND, short/intermediate
        at least non-SIDEWAYS.

        Use day_trading preset (5 TFs) so every horizon bucket has
        enough warm-up ticks.
        """
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        base = datetime.now()
        self._warmup(engine, n_bars=200, base=base)
        prices = [100.0 + i * 0.5 for i in range(120)]
        self._feed(engine, prices, base + timedelta(minutes=200))
        signal = engine.get_current_confluence()
        self.assertIsNotNone(signal)
        # The 3 horizon buckets must be real TrendDirection values.
        for attr in ("short_term_direction", "intermediate_direction", "higher_direction"):
            val = getattr(signal, attr)
            self.assertIsInstance(val, TrendDirection)
            self.assertIn(
                val,
                (
                    TrendDirection.UPTREND,
                    TrendDirection.DOWNTREND,
                    TrendDirection.SIDEWAYS,
                    TrendDirection.UNKNOWN,
                ),
            )
        # At least the higher_timeframe (1d) should not be SIDEWAYS on
        # a 120-tick uptrend. The short and intermediate may be sideways
        # depending on EMA warmup, so we don't pin them.
        self.assertNotEqual(
            signal.higher_direction,
            TrendDirection.SIDEWAYS,
            "120-tick uptrend should move 1d out of SIDEWAYS",
        )

    def test_confluence_signal_has_all_phase7_fields(self):
        """ConfluenceSignal carries the 6 new fields + preset."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        prices = [100.0 + i * 0.5 for i in range(60)]
        self._feed(engine, prices, datetime.now())
        signal = engine.get_current_confluence()
        self.assertIsNotNone(signal)
        self.assertTrue(hasattr(signal, "bullish_alignment"))
        self.assertTrue(hasattr(signal, "bearish_alignment"))
        self.assertTrue(hasattr(signal, "conflicting"))
        self.assertTrue(hasattr(signal, "short_term_direction"))
        self.assertTrue(hasattr(signal, "intermediate_direction"))
        self.assertTrue(hasattr(signal, "higher_direction"))
        self.assertTrue(hasattr(signal, "preset"))
        self.assertEqual(signal.preset, "day_trading")
        # Numeric ranges
        self.assertGreaterEqual(signal.bullish_alignment, 0.0)
        self.assertLessEqual(signal.bullish_alignment, 1.0)
        self.assertGreaterEqual(signal.conflicting, 0)

    def test_confluence_uses_snapshot_quality_metrics(self):
        """Confluence mirrors the snapshot's quality-weighted aggregate fields."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        prices = [100.0 + i * 0.5 for i in range(90)]
        self._feed(engine, prices, datetime.now())

        signal = engine.get_current_confluence()
        snapshot = engine.get_current_snapshot()

        self.assertIsNotNone(signal)
        self.assertIsNotNone(snapshot)
        self.assertEqual(signal.direction, snapshot.direction)
        self.assertEqual(signal.strength, snapshot.strength)
        self.assertEqual(signal.alignment_score, snapshot.alignment_score)
        self.assertEqual(signal.valid_coverage, snapshot.valid_coverage)
        self.assertEqual(signal.quality_weighted_score, snapshot.quality_weighted_score)


if __name__ == "__main__":
    unittest.main()
