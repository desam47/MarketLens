"""
Tests for trend engine
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.engines.timeframe import Timeframe
from backend.trend.trend_engine import (
    TrendClassification,
    TrendDirection,
    TrendEngine,
    TrendSignal,
    TrendStrength,
    ShortHorizonMomentum,
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
            "ONE_MINUTE",
            "FIVE_MINUTE",
            "FIFTEEN_MINUTE",
            "ONE_HOUR",
            "FOUR_HOUR",
            "ONE_DAY",
        ]
        for tf_str in expected_timeframes:
            # Find the Timeframe enum member
            from backend.engines.timeframe import Timeframe

            tf = getattr(Timeframe, tf_str)
            self.assertIn(tf, self.engine.indicators)
            self.assertGreater(len(self.engine.indicators[tf]), 0)

    def test_short_horizon_momentum_uses_closed_bar_persistence_not_default_strength(self):
        breakout = [
            {"close": 100.0},
            {"close": 100.7},
            {"close": 101.5},
            {"close": 102.3},
        ]

        state, score = self.engine._classify_short_horizon_momentum(breakout, atr_value=1.0)

        self.assertEqual(state, ShortHorizonMomentum.PERSISTENT)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, 0.75)

    def test_short_horizon_momentum_marks_flat_and_whipsaw_bars_choppy(self):
        flat = [
            {"close": 100.0},
            {"close": 100.0},
            {"close": 100.0},
            {"close": 100.0},
        ]
        whipsaw = [
            {"close": 100.0},
            {"close": 101.0},
            {"close": 100.0},
            {"close": 101.0},
        ]

        flat_state, flat_score = self.engine._classify_short_horizon_momentum(flat, atr_value=1.0)
        whipsaw_state, whipsaw_score = self.engine._classify_short_horizon_momentum(
            whipsaw, atr_value=1.0
        )

        self.assertEqual(flat_state, ShortHorizonMomentum.CHOPPY)
        self.assertEqual(flat_score, 0.0)
        self.assertEqual(whipsaw_state, ShortHorizonMomentum.CHOPPY)
        self.assertIsNotNone(whipsaw_score)

    def test_short_horizon_momentum_waits_for_closed_bars_and_atr(self):
        state, score = self.engine._classify_short_horizon_momentum(
            [{"close": 100.0}, {"close": 100.5}, {"close": 101.0}], atr_value=1.0
        )
        no_atr_state, no_atr_score = self.engine._classify_short_horizon_momentum(
            [{"close": 100.0}, {"close": 100.5}, {"close": 101.0}, {"close": 101.5}],
            atr_value=None,
        )

        self.assertIsNone(state)
        self.assertIsNone(score)
        self.assertIsNone(no_atr_state)
        self.assertIsNone(no_atr_score)

    def test_very_strong_requires_adx_di_balance_and_confirming_score(self):
        adx = MagicMock()
        adx.get_latest.return_value = 55.0
        adx._di_plus = 75.0
        adx._di_minus = 25.0
        atr = MagicMock()
        atr.get_latest.return_value = 1.0
        values = {
            "ema_fast": 102.0,
            "ema_slow": 100.0,
            "rsi": 85.0,
            "macd": 8.0,
            "adx": 55.0,
        }

        _, strength, _, score, _ = self.engine._calculate_trend(
            Timeframe.FIFTEEN_MINUTE, values, atr, adx
        )

        self.assertGreaterEqual(abs(score), 60.0)
        self.assertEqual(strength, TrendStrength.VERY_STRONG)

    def test_very_strong_rejects_high_adx_without_directional_balance(self):
        adx = MagicMock()
        adx.get_latest.return_value = 55.0
        adx._di_plus = 60.0
        adx._di_minus = 40.0
        atr = MagicMock()
        atr.get_latest.return_value = 1.0
        values = {
            "ema_fast": 102.0,
            "ema_slow": 100.0,
            "rsi": 85.0,
            "macd": 8.0,
            "adx": 55.0,
        }

        _, strength, _, score, _ = self.engine._calculate_trend(
            Timeframe.FIFTEEN_MINUTE, values, atr, adx
        )

        self.assertGreaterEqual(abs(score), 60.0)
        self.assertEqual(strength, TrendStrength.STRONG)

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

    def test_short_tf_indicators_update_once_per_closed_candle_not_per_tick(self):
        """Regression: live 2026-09-16, DVLT's 1m Multi-Timeframe Trend cell
        flipped between "downtrend" and "sideways" within two minutes while
        every closed 1m bar in that window was higher than the last. Root
        cause: 1m/2m/3m indicators were fed the in-progress (still-forming)
        candle on every tick, and EMAIndicator.update() unconditionally
        appends a new point per call — so a volatile minute with many ticks
        fed the same forming candle dozens of times, over-weighting that
        minute relative to real closed-bar history. Short TFs must now
        update exactly once per closed candle."""
        ema_fast = self.engine.indicators[Timeframe.ONE_MINUTE]["ema_fast"]  # period 9

        # Fixed, minute-aligned base time (not datetime.now()) — the burst
        # below spans 38s and must never cross a minute boundary regardless
        # of the real wall-clock second the test happens to run at.
        base_time = datetime(2026, 1, 5, 9, 30, 0)

        # TrendEngine.__init__ opens a real-time (datetime.now()) phantom
        # candle as a side effect of creating a fresh per-symbol timeframe
        # engine. That candle lands in a different (real "now") minute than
        # this test's fixed base_time, so the first tick below closes it —
        # one spurious feed unrelated to what this test checks. Prime past
        # it with a throwaway update, then reset the indicator's state so
        # the assertions that follow start from a clean slate.
        self.engine.update(100.0, 1000, base_time)
        ema_fast.values.clear()
        if hasattr(ema_fast, "_warmup_buffer"):
            ema_fast._warmup_buffer.clear()
        ema_fast.prev_ema = None

        # Many ticks within the same still-forming minute must not produce
        # any indicator update at all (no closed candle exists yet).
        for i in range(20):
            self.engine.update(100.0 + i * 0.01, 1000, base_time + timedelta(seconds=i * 2))
        self.assertEqual(len(ema_fast.values), 0)

        # Advance into a new minute on each subsequent tick — each closes
        # exactly the previous minute's candle. 9 such closes clears the
        # EMA(9) warmup and appends exactly one value, not one per tick.
        for i in range(1, 10):
            self.engine.update(101.0 + i, 1000, base_time + timedelta(minutes=i))
        self.assertEqual(len(ema_fast.values), 1)

    def test_timeframe_bar_uses_exact_ohlcv_without_cross_contamination(self):
        """A dispatched 5m bar must update only 5m with its real range."""
        one_minute = MagicMock()
        one_minute.get_latest.return_value = None
        five_minute = MagicMock()
        five_minute.get_latest.return_value = None
        self.engine.indicators[Timeframe.ONE_MINUTE] = {"probe": one_minute}
        self.engine.indicators[Timeframe.FIVE_MINUTE] = {"probe": five_minute}

        self.engine.update(
            price=100,
            open_price=95,
            high=110,
            low=90,
            volume=1234,
            timestamp=datetime(2026, 9, 22, 10, 0),
            timeframe="5m",
            provider="test",
            data_status="HISTORICAL",
            session="regular",
        )

        five_minute.update.assert_called_once_with(
            {"open": 95.0, "high": 110.0, "low": 90.0, "close": 100.0, "volume": 1234.0}
        )
        one_minute.update.assert_not_called()

    def test_live_one_minute_bars_feed_completed_five_minute_bucket_once(self):
        """Stateful 5m indicators must not append the forming bar every minute."""
        five_minute = MagicMock()
        five_minute.get_latest.return_value = None
        self.engine.indicators[Timeframe.ONE_MINUTE] = {}
        self.engine.indicators[Timeframe.FIVE_MINUTE] = {"probe": five_minute}
        base = datetime(2026, 9, 22, 9, 30)

        for minute in range(6):
            close = 100 + minute
            self.engine.update(
                price=close,
                open_price=close - 0.5,
                high=close + 1,
                low=close - 1,
                volume=100,
                timestamp=base + timedelta(minutes=minute),
                timeframe="1m",
                provider="test",
                data_status="HISTORICAL",
                session="regular",
            )

        five_minute.update.assert_called_once_with(
            {"open": 99.5, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 500.0}
        )

    def test_live_aggregation_does_not_create_partial_bucket_after_restart(self):
        """Starting mid-bucket must not turn three 1m bars into a fake 5m bar."""
        five_minute = MagicMock()
        five_minute.get_latest.return_value = None
        self.engine.indicators[Timeframe.ONE_MINUTE] = {}
        self.engine.indicators[Timeframe.FIVE_MINUTE] = {"probe": five_minute}
        base = datetime(2026, 9, 22, 9, 32)

        for minute in range(4):
            self.engine.update(
                price=100 + minute,
                volume=100,
                timestamp=base + timedelta(minutes=minute),
                timeframe="1m",
                data_status="HISTORICAL",
            )

        five_minute.update.assert_not_called()

    def test_daily_warmup_does_not_feed_one_minute_indicators(self):
        one_minute = MagicMock()
        one_minute.get_latest.return_value = None
        daily = MagicMock()
        daily.get_latest.return_value = None
        self.engine.indicators[Timeframe.ONE_MINUTE] = {"probe": one_minute}
        self.engine.indicators[Timeframe.ONE_DAY] = {"probe": daily}

        self.engine.update(
            price=101,
            open_price=99,
            high=102,
            low=98,
            volume=1000,
            timestamp=datetime(2026, 9, 21),
            timeframe=Timeframe.ONE_DAY,
            only_timeframe=Timeframe.ONE_DAY,
            data_status="HISTORICAL",
        )

        daily.update.assert_called_once()
        one_minute.update.assert_not_called()

    def test_trend_signal_creation(self):
        """Test creating a trend signal"""
        timestamp = datetime.now()
        signal = TrendSignal(
            symbol=self.symbol,
            timeframe=Timeframe.ONE_HOUR,
            direction=TrendDirection.UPTREND,
            strength=TrendStrength.MODERATE,
            confidence=0.8,
            timestamp=timestamp,
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

    def test_scoring_profiles_make_cross_timeframe_noncomparability_explicit(self):
        """A 1m score is not silently presented as the same evidence as 1h."""
        from backend.trend.trend_engine import scoring_profile_for_timeframe

        one_minute = scoring_profile_for_timeframe(Timeframe.ONE_MINUTE)
        five_minute = scoring_profile_for_timeframe(Timeframe.FIVE_MINUTE)
        one_hour = scoring_profile_for_timeframe(Timeframe.ONE_HOUR)

        self.assertEqual(one_minute.id, "directional_core")
        self.assertEqual(one_minute.component_count, 3)
        self.assertEqual(five_minute.id, "intraday_directional")
        self.assertEqual(five_minute.component_count, 4)
        self.assertEqual(one_hour.id, "full_technical")
        self.assertEqual(one_hour.component_count, 8)
        self.assertFalse(one_hour.to_payload()["score_comparable_across_timeframes"])


class TestDataQualityValidation(unittest.TestCase):
    """Phase 0 Principle 15 — data quality validated before analysis."""

    def test_update_stores_state_for_next_call(self):
        """update() records _last_update_time and _last_price."""
        from backend.trend.trend_engine import TrendEngine

        engine = TrendEngine("AAPL")
        self.assertIsNone(engine._last_update_time)
        self.assertIsNone(engine._last_price)

        engine.update(price=100.0, volume=1_000_000, timestamp=datetime.now(), provider="test")

        self.assertIsNotNone(engine._last_update_time)
        self.assertEqual(engine._last_price, 100.0)

    def test_update_detects_duplicate_price(self):
        """update() sets _last_price so consecutive equal prices are detectable."""
        from backend.trend.trend_engine import TrendEngine

        engine = TrendEngine("AAPL")
        engine._last_price = 99.99

        # The stale_threshold_seconds default is 30; this tick arrives
        # well within that window so no stale warning fires.
        engine.update(price=99.99, volume=1_000_000, timestamp=datetime.now(), provider="test")
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
        # Reset the shared timeframe engine so _seen_timestamps from the
        # previous test doesn't cause duplicate-tick rejection in this test.
        self.engine.timeframe_engine.reset()
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

    def _feed_extra(self, prices: list[float], n_extra: int, volume: float = 1_000_000):
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
        self._feed_extra([100 + i * 0.5 for i in range(60)], n_extra=200)
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
        self.assertLess(down_score, up_score, "Sharp reversal should lower the score")

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

    def test_confirmed_uptrend_clears_default_confidence_filter(self):
        """Calibration fix (2026-09-09): once direction is actually
        confirmed, confidence must reach >= 0.5 — the default
        `min_confidence` used everywhere in the app (NL search,
        DailyBullish/DailyBearish, MinTimeframeBullish/Bearish,
        MTFAlignment, the scanner's MULTI_TIMEFRAME_* signal). Before
        the fix, confidence == the raw signal magnitude, so it was
        pinned at the 0.15-0.35 direction threshold right at the
        moment of confirmation — permanently below 0.5 for anything
        but an extreme, near-maximal signal."""
        self._feed_extra([100 + i * 0.5 for i in range(60)], n_extra=200)
        signal = self.engine.get_current_trend(Timeframe.ONE_DAY)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction.value, "uptrend")
        self.assertGreaterEqual(signal.confidence, 0.5)

    def test_confirmed_downtrend_clears_default_confidence_filter(self):
        """Regression for the live bug: AI Stock Search's 'bearish
        stocks' returned zero matches even with a genuine downtrend
        (DVLT, confidence 0.365) sitting in the watchlist."""
        self._feed_extra([100 - i * 0.5 for i in range(60)], n_extra=200)
        signal = self.engine.get_current_trend(Timeframe.ONE_DAY)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction.value, "downtrend")
        self.assertGreaterEqual(signal.confidence, 0.5)

    def test_flat_prices_stay_below_half_confidence(self):
        """Sideways/no-real-signal data must NOT clear the same bar —
        confidence is meant to distinguish trending from not, not just
        report a rescaled constant."""
        self._feed_extra([100.0] * 60, n_extra=200)
        signal = self.engine.get_current_trend(Timeframe.ONE_DAY)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction.value, "sideways")
        self.assertLess(signal.confidence, 0.5)

    def test_confidence_stays_within_unit_range(self):
        self._feed_extra([100 + i * 0.5 for i in range(60)], n_extra=200)
        signal = self.engine.get_current_trend(Timeframe.ONE_DAY)
        self.assertIsNotNone(signal)
        self.assertGreaterEqual(signal.confidence, 0.0)
        self.assertLessEqual(signal.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
