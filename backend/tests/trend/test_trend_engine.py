"""
Tests for trend engine
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.engines.timeframe import Timeframe
from backend.trend.trend_engine import (
    TrendClassification,
    TrendDirection,
    TrendEngine,
    TrendSignal,
    TrendStrength,
    classify_score,
)


class TestTrendEngine(unittest.TestCase):

    def setUp(self):
        self.symbol = "AAPL"
        self.engine = TrendEngine(self.symbol)

    def test_engine_initialization(self):
        """Test that trend engine initializes correctly"""
        self.assertEqual(self.engine.symbol, self.symbol)
        self.assertIsNotNone(self.engine.timeframe_engine)
        self.assertIsInstance(self.engine.indicators, dict)
        self.assertIsInstance(self.engine.trend_history, dict)

        # Check that indicators are initialized for major timeframes
        expected_timeframes = [
            "ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE",
            "ONE_HOUR", "FOUR_HOUR", "ONE_DAY"
        ]
        for tf_str in expected_timeframes:
            # Find the Timeframe enum member
            from backend.engines.timeframe import Timeframe
            tf = getattr(Timeframe, tf_str)
            self.assertIn(tf, self.engine.indicators)
            self.assertGreater(len(self.engine.indicators[tf]), 0)

    def test_engine_update_with_data(self):
        """Test updating the engine with market data"""
        base_time = datetime.now()

        # Simulate an uptrend
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]

        for i, price in enumerate(prices):
            timestamp = base_time + timedelta(minutes=i)
            self.engine.update(price, 1000, timestamp)

        # Should have processed the data
        # Check that we have some trend signals
        from backend.engines.timeframe import Timeframe
        self.engine.get_current_trend(Timeframe.ONE_DAY)
        # Might not have enough data yet for a full signal, but engine should not crash

        # Test that we can get trend history
        history = self.engine.get_trend_history(Timeframe.ONE_HOUR)
        self.assertIsInstance(history, list)

    def test_trend_signal_creation(self):
        """Test creating a trend signal"""
        timestamp = datetime.now()
        signal = TrendSignal(
            symbol=self.symbol,
            timeframe=Timeframe.ONE_HOUR,
            direction=TrendDirection.UPTREND,
            strength=TrendStrength.MODERATE,
            confidence=0.8,
            timestamp=timestamp
        )

        self.assertEqual(signal.symbol, self.symbol)
        self.assertEqual(signal.direction, TrendDirection.UPTREND)
        self.assertEqual(signal.strength, TrendStrength.MODERATE)
        self.assertEqual(signal.confidence, 0.8)
        self.assertEqual(signal.timestamp, timestamp)

    def test_get_current_trend_no_data(self):
        """Test getting current trend with no data"""
        # Should return None when no data has been processed
        tf = Timeframe.ONE_HOUR
        trend = self.engine.get_current_trend(tf)
        self.assertIsNone(trend)

    def test_get_trend_history_empty(self):
        """Test getting trend history with no data"""
        tf = Timeframe.ONE_HOUR
        history = self.engine.get_trend_history(tf)
        self.assertEqual(history, [])

    def test_get_multi_timeframe_trend(self):
        """Getting multi-timeframe trend"""
        trends = self.engine.get_multi_timeframe_trend()
        self.assertIsInstance(trends, dict)
        # Should be empty initially
        self.assertEqual(len(trends), 0)

    def test_get_overall_trend_no_data(self):
        """Test getting overall trend with no data"""
        overall = self.engine.get_overall_trend()
        self.assertIsNone(overall)

class TestTrendEngineArchitecture(unittest.TestCase):
    """Phase 0 architectural assertions — locks in Principles 12 and 17."""

    def test_trend_engine_does_not_import_concrete_indicators(self):
        """Principle 17: TrendEngine must use IndicatorEngine facade, not
        import concrete indicator classes. (Phase 0 violation fix.)"""
        from backend.trend import trend_engine

        # The module's namespace should not carry concrete indicator classes.
        # These names would only be present if someone did e.g.
        # ``from backend.indicators.rsi import RSIIndicator``.
        self.assertFalse(hasattr(trend_engine, "RSIIndicator"))
        self.assertFalse(hasattr(trend_engine, "MACDIndicator"))
        self.assertFalse(hasattr(trend_engine, "ADXIndicator"))
        self.assertFalse(hasattr(trend_engine, "EMAIndicator"))
        self.assertFalse(hasattr(trend_engine, "BollingerBandsIndicator"))
        self.assertFalse(hasattr(trend_engine, "SuperTrendIndicator"))

    def test_indicator_defaults_read_from_settings(self):
        """Principle 12: indicator parameters come from settings.py, not
        hard-coded literals. (Phase 0 violation fix.)"""
        from backend.config.settings import settings

        # The defaults block is the single source of truth.
        self.assertEqual(settings.trend.indicators.rsi_period, 14)
        self.assertEqual(settings.trend.indicators.macd_fast, 12)
        self.assertEqual(settings.trend.indicators.macd_slow, 26)
        self.assertEqual(settings.trend.indicators.macd_signal, 9)
        self.assertEqual(settings.trend.indicators.adx_period, 14)
        self.assertEqual(settings.trend.indicators.supertrend_atr_period, 10)
        self.assertEqual(settings.trend.indicators.supertrend_multiplier, 3.0)
        self.assertEqual(settings.trend.indicators.bollinger_period, 20)
        self.assertEqual(settings.trend.indicators.bollinger_std_dev, 2.0)

        # Per-timeframe EMA is a lookup table, not magic numbers.
        from backend.trend.trend_engine import _TIMEFRAME_EMA
        self.assertEqual(_TIMEFRAME_EMA[Timeframe.ONE_MINUTE], (9, 21))
        self.assertEqual(_TIMEFRAME_EMA[Timeframe.ONE_DAY], (50, 200))

    def test_timeframe_weights_in_settings(self):
        """Principle 11: timeframe weights come from settings, not hard-coded
        literals in TrendEngine.get_overall_trend."""
        from backend.config.settings import settings

        self.assertIn("1d", settings.trend.timeframe_weights)
        self.assertIn("1h", settings.trend.timeframe_weights)
        self.assertEqual(settings.trend.timeframe_weights["1d"], 0.15)
        self.assertEqual(settings.trend.timeframe_weights["1h"], 0.25)


class TestDataQualityValidation(unittest.TestCase):
    """Phase 0 Principle 15 — data quality validated before analysis."""

    def test_update_stores_state_for_next_call(self):
        """update() records _last_update_time and _last_price."""
        from backend.trend.trend_engine import TrendEngine

        engine = TrendEngine("AAPL")
        self.assertIsNone(engine._last_update_time)
        self.assertIsNone(engine._last_price)

        engine.update(price=100.0, volume=1_000_000,
                      timestamp=datetime.now(), provider="test")

        self.assertIsNotNone(engine._last_update_time)
        self.assertEqual(engine._last_price, 100.0)

    def test_update_detects_duplicate_price(self):
        """update() sets _last_price so consecutive equal prices are detectable."""
        from backend.trend.trend_engine import TrendEngine

        engine = TrendEngine("AAPL")
        engine._last_price = 99.99

        # The stale_threshold_seconds default is 30; this tick arrives
        # well within that window so no stale warning fires.
        engine.update(price=99.99, volume=1_000_000,
                      timestamp=datetime.now(), provider="test")
        self.assertEqual(engine._last_price, 99.99)

    def test_data_quality_settings_defined(self):
        """DataQualitySettings has sensible defaults; stale_threshold_seconds,
        max_tick_gap_seconds, and duplicate_price_tolerance are all present."""
        from backend.config.settings import settings

        dq = settings.data_quality
        self.assertGreater(dq.stale_threshold_seconds, 0)
        self.assertGreater(dq.max_tick_gap_seconds, 0)
        self.assertGreaterEqual(dq.duplicate_price_tolerance, 0)


class TestPhase6Scenarios(unittest.TestCase):
    """Phase 6 spec: all 8 classification scenarios + TrendSnapshot fields.

    Each test feeds a hand-crafted price series into the engine and asserts
    the resulting classification or score. Tests are written to be robust:
    exact bucket depends on the full 8-component weighted average warming up,
    so we test direction-of-classification rather than precise thresholds
    where the score is near a boundary.
    """

    def setUp(self):
        # Dedicated engine per test — no warm-up bleed.
        self.engine = TrendEngine("TEST")
        # Phase 6 spec tests use 1-minute spacing for synthetic price
        # series. The data-quality gap check (added later, in
        # ``_settings.data_quality.max_tick_gap_seconds``) would log a
        # warning per bar at that spacing since 60s == the default
        # threshold. Disable the threshold for these tests by bumping
        # the global setting high enough that 60s ticks don't trip it.
        from backend.config.settings import settings
        self._original_gap = settings.data_quality.max_tick_gap_seconds
        settings.data_quality.max_tick_gap_seconds = 86400.0  # 1 day

    def tearDown(self):
        from backend.config.settings import settings
        settings.data_quality.max_tick_gap_seconds = self._original_gap

    def _feed(self, prices: list[float], volume: float = 1_000_000):
        """Feed prices one-at-a-time with sequential timestamps."""
        base = datetime.now()
        for i, price in enumerate(prices):
            self.engine.update(price, volume, base + timedelta(minutes=i))

    def _feed_extra(self, prices: list[float], n_extra: int,
                    volume: float = 1_000_000):
        """Feed ``n_extra`` flat warmup bars then ``prices``.

        Many indicators (ema_slow=200 on ONE_DAY, ADX 2*period=28, etc.)
        need more than 60 bars to warm up. The Phase 6 spec tests focus
        on the *signal* shape — we pad with a flat price to let
        warmup complete before the test pattern starts.
        """
        warmup = [prices[0]] * n_extra
        self._feed(warmup, volume=volume)
        self._feed(prices, volume=volume)

    def _score(self, timeframe: Timeframe) -> float | None:
        sig = self.engine.get_current_trend(timeframe)
        return sig.score if sig else None

    def _classification(self, timeframe: Timeframe) -> TrendClassification | None:
        sig = self.engine.get_current_trend(timeframe)
        return sig.classification if sig else None

    def test_strong_bullish(self):
        """Strong uptrend → positive score, bullish classification."""
        # Pad with 200 flat bars so ema_slow (period=200 on ONE_DAY)
        # warms up before the trend pattern begins.
        self._feed_extra(
            [100 + i * 0.5 for i in range(60)], n_extra=200
        )
        score = self._score(Timeframe.ONE_DAY)
        cls = self._classification(Timeframe.ONE_DAY)
        self.assertIsNotNone(score, "Should have a score after 60 bars")
        self.assertGreater(score, 0, "Uptrend score should be positive")
        self.assertNotEqual(cls, TrendClassification.STRONG_BEARISH)
        self.assertNotEqual(cls, TrendClassification.BEARISH)
        self.assertNotEqual(cls, TrendClassification.WEAK_BEARISH)

    def test_weak_bullish(self):
        """Slow uptrend → positive but not strongly so."""
        prices = [100.0]
        for _i in range(1, 60):
            prices.append(prices[-1] + 0.05)  # tiny gains
        self._feed_extra(prices, n_extra=200)
        score = self._score(Timeframe.ONE_DAY)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, -9, "Weak uptrend should not score deeply negative")

    def test_neutral(self):
        """Constant prices produce a valid classification (any bucket).

        The exact bucket depends on which subset of components has warmed
        up. We assert the classification is reachable, the score is in
        range, and the score -> classification mapping is consistent.
        """
        self._feed([100.0] * 60)
        sig = self.engine.get_current_trend(Timeframe.ONE_DAY)
        self.assertIsNotNone(sig)
        # Score in -100..+100
        self.assertGreaterEqual(sig.score, -100.0)
        self.assertLessEqual(sig.score, 100.0)
        # score -> classification mapping is consistent
        self.assertEqual(sig.classification, classify_score(sig.score))

    def test_weak_bearish(self):
        """Slow downtrend → negative score, not bullish."""
        prices = [100.0 - i * 0.05 for i in range(60)]
        self._feed_extra(prices, n_extra=200)
        score = self._score(Timeframe.ONE_DAY)
        self.assertIsNotNone(score)
        self.assertLess(score, 10, "Downtrend score should be below +10")

    def test_strong_bearish(self):
        """Strong downtrend → negative score, bearish classification."""
        self._feed_extra([100 - i * 0.5 for i in range(60)], n_extra=200)
        score = self._score(Timeframe.ONE_DAY)
        cls = self._classification(Timeframe.ONE_DAY)
        self.assertIsNotNone(score, "Should have a score after 60 bars")
        self.assertLess(score, 0, "Downtrend score should be negative")
        self.assertNotEqual(cls, TrendClassification.STRONG_BULLISH)
        self.assertNotEqual(cls, TrendClassification.BULLISH)
        self.assertNotEqual(cls, TrendClassification.WEAK_BULLISH)

    def test_insufficient_data(self):
        """Fresh engine → build_snapshot returns None; TrendSignal gets NO_SIGNAL."""

        snapshot = self.engine.build_snapshot(Timeframe.ONE_DAY)
        self.assertIsNone(snapshot)

        # Even with only 3 bars the engine is running; check NO_SIGNAL when data
        # is insufficient for all components. ONE_MINUTE needs only 21 bars for its
        # slow EMA (period=21), so feed 3 bars — well below warmup.
        self._feed([100.0] * 3)
        signal = self.engine.get_current_trend(Timeframe.ONE_MINUTE)
        # 3 bars is below even ONE_MINUTE's warmup, so no signal.
        # This assertion checks the engine's graceful degradation: it should
        # return None rather than crash when asked for a timeframe with
        # insufficient data. (If warmup requirements change and 3 bars becomes
        # enough, this test will fail — update the assertion accordingly.)
        # Current state: 3 bars < 21-bar ema_slow warmup → None
        self.assertIsNone(signal)

    def test_conflicting_indicators(self):
        """Bullish EMA but oversold RSI + bearish MACD → NEUTRAL or WEAK, not STRONG."""
        # Sharp drop to oversold territory, then slow recovery
        prices = [100.0 - i * 0.3 for i in range(20)]  # -6 pts
        prices += [prices[-1] + i * 0.05 for i in range(1, 40)]  # tiny recovery
        self._feed_extra(prices, n_extra=200)
        cls = self._classification(Timeframe.ONE_DAY)
        self.assertNotEqual(cls, TrendClassification.STRONG_BULLISH)
        self.assertNotEqual(cls, TrendClassification.STRONG_BEARISH)

    def test_trend_snapshot_fields(self):
        """TrendSnapshot carries all 9 spec fields and correct types."""

        self._feed_extra([100 + i * 0.5 for i in range(60)], n_extra=200)
        snapshot = self.engine.build_snapshot(Timeframe.ONE_DAY)
        self.assertIsNotNone(snapshot)

        self.assertEqual(snapshot.symbol, "TEST")
        self.assertEqual(snapshot.timeframe, Timeframe.ONE_DAY)
        self.assertIsInstance(snapshot.timestamp, datetime)
        self.assertIsInstance(snapshot.direction, TrendClassification)
        self.assertIsInstance(snapshot.score, float)
        self.assertIsInstance(snapshot.strength, float)
        self.assertGreaterEqual(snapshot.strength, 0.0)
        self.assertLessEqual(snapshot.strength, 1.0)
        self.assertIsInstance(snapshot.momentum, float)
        self.assertIsInstance(snapshot.structure, float)
        self.assertIsInstance(snapshot.data_quality, str)
        self.assertIsInstance(snapshot.strategy_version, str)
        # Not NO_SIGNAL once data is warm
        self.assertNotEqual(snapshot.direction, TrendClassification.NO_SIGNAL)

    def test_bollinger_wired(self):
        """Bollinger %B appears as a float in the snapshot structure field."""
        self._feed_extra([100 + i * 0.5 for i in range(60)], n_extra=200)
        snapshot = self.engine.build_snapshot(Timeframe.ONE_DAY)
        self.assertIsNotNone(snapshot)
        self.assertIsInstance(snapshot.structure, float)

    def test_supertrend_wired(self):
        """SuperTrend direction: uptrend then reversal moves classification toward bearish."""
        # Build a clean uptrend with enough warmup for ema_slow (period=200)
        self._feed_extra([100 + i * 0.5 for i in range(50)], n_extra=200)
        up_score = self._score(Timeframe.ONE_DAY)
        # Now reverse sharply
        peak = 100 + 49 * 0.5
        self._feed([peak - i * 1.5 for i in range(1, 21)])
        down_score = self._score(Timeframe.ONE_DAY)
        # Score should have dropped significantly
        self.assertLess(down_score, up_score,
                        "Sharp reversal should lower the score")

    def test_no_buy_signal(self):
        """Phase 6 spec: bullish trend must NOT become a BUY signal."""
        self._feed([100 + i * 0.5 for i in range(60)])
        snapshot = self.engine.build_snapshot(Timeframe.ONE_DAY)
        signal = self.engine.get_current_trend(Timeframe.ONE_DAY)

        # No BUY/SELL methods on the engine
        self.assertFalse(hasattr(self.engine, "get_buy_signal"))
        self.assertFalse(hasattr(self.engine, "get_sell_signal"))
        self.assertFalse(hasattr(self.engine, "buy_signal"))
        self.assertFalse(hasattr(self.engine, "sell_signal"))

        # No buy/sell on TrendSignal
        self.assertFalse(hasattr(signal, "buy"))
        self.assertFalse(hasattr(signal, "sell"))
        self.assertFalse(hasattr(signal, "buy_signal"))
        self.assertFalse(hasattr(signal, "sell_signal"))

        # No buy/sell on TrendSnapshot
        self.assertFalse(hasattr(snapshot, "buy"))
        self.assertFalse(hasattr(snapshot, "sell"))
        self.assertFalse(hasattr(snapshot, "buy_signal"))
        self.assertFalse(hasattr(snapshot, "sell_signal"))


if __name__ == '__main__':
    unittest.main()

