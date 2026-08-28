"""
Tests for Indicator Engine
"""
import unittest

from backend.indicators import (
    EMAIndicator,
    IndicatorEngine,
    RSIIndicator,
    SMAIndicator,
)


class TestIndicatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IndicatorEngine()

    def test_add_remove_indicator(self):
        """Test adding and removing indicators"""
        ema = EMAIndicator(period=5)
        sma = SMAIndicator(period=10)

        # Add indicators
        self.engine.add_indicator(ema)
        self.engine.add_indicator(sma)

        # Check they were added
        self.assertIn("EMA", self.engine.list_indicators())
        self.assertIn("SMA", self.engine.list_indicators())
        self.assertEqual(len(self.engine.list_indicators()), 2)

        # Remove one indicator
        self.engine.remove_indicator("EMA")

        # Check it was removed
        self.assertNotIn("EMA", self.engine.list_indicators())
        self.assertIn("SMA", self.engine.list_indicators())
        self.assertEqual(len(self.engine.list_indicators()), 1)

    def test_update_data(self):
        """Test updating engine with new data"""
        ema = EMAIndicator(period=3)
        self.engine.add_indicator(ema)

        # Test data
        data = [
            {'close': 10, 'high': 11, 'low': 9, 'volume': 100},
            {'close': 12, 'high': 13, 'low': 11, 'volume': 120},
            {'close': 14, 'high': 15, 'low': 13, 'volume': 140},
            {'close': 13, 'high': 14, 'low': 12, 'volume': 130},
            {'close': 15, 'high': 16, 'low': 14, 'volume': 150}
        ]

        # Update engine with each data point
        for datum in data:
            self.engine.update_data(datum)

        # Check that data history was stored
        self.assertEqual(len(self.engine.data_history), 5)

        # Check that indicator was updated
        latest_values = self.engine.get_latest_values()
        self.assertIn("EMA", latest_values)
        self.assertIsNotNone(latest_values["EMA"])

    def test_calculate_all(self):
        """Test calculating all indicators for given data"""
        ema = EMAIndicator(period=3)
        sma = SMAIndicator(period=3)
        rsi = RSIIndicator(period=3)

        self.engine.add_indicator(ema)
        self.engine.add_indicator(sma)
        self.engine.add_indicator(rsi)

        # Test data
        data = [
            {'close': 10, 'high': 11, 'low': 9, 'volume': 100},
            {'close': 12, 'high': 13, 'low': 11, 'volume': 120},
            {'close': 14, 'high': 15, 'low': 13, 'volume': 140},
            {'close': 13, 'high': 14, 'low': 12, 'volume': 130},
            {'close': 15, 'high': 16, 'low': 14, 'volume': 150},
            {'close': 11, 'high': 12, 'low': 10, 'volume': 110},
            {'close': 13, 'high': 14, 'low': 12, 'volume': 130}
        ]

        # Calculate all indicators
        results = self.engine.calculate_all(data)

        # Check results
        self.assertIn("EMA", results)
        self.assertIn("SMA", results)
        self.assertIn("RSI", results)

        # Should have calculated values
        self.assertGreater(len(results["EMA"]), 0)
        self.assertGreater(len(results["SMA"]), 0)
        self.assertGreater(len(results["RSI"]), 0)

        # Values should be numbers
        for value in results["EMA"]:
            self.assertIsInstance(value, (int, float))

    def test_get_indicator(self):
        """Test getting a specific indicator"""
        ema = EMAIndicator(period=5)
        self.engine.add_indicator(ema)

        # Get the indicator
        retrieved_ema = self.engine.get_indicator("EMA")

        # Check it's the same object
        self.assertIs(retrieved_ema, ema)

        # Test getting non-existent indicator
        none_indicator = self.engine.get_indicator("NONEXISTENT")
        self.assertIsNone(none_indicator)

    def test_create_indicator_factory(self):
        """create_indicator() should return the right BaseIndicator subclass
        for each known kind, using the params it was given."""
        from backend.indicators.adx import ADXIndicator
        from backend.indicators.bollinger_bands import BollingerBandsIndicator
        from backend.indicators.ema import EMAIndicator
        from backend.indicators.macd import MACDIndicator
        from backend.indicators.rsi import RSIIndicator
        from backend.indicators.supertrend import SuperTrendIndicator

        ema = IndicatorEngine.create_indicator("ema", {"period": 12})
        self.assertIsInstance(ema, EMAIndicator)
        self.assertEqual(ema.period, 12)

        rsi = IndicatorEngine.create_indicator("rsi", {"period": 7})
        self.assertIsInstance(rsi, RSIIndicator)
        self.assertEqual(rsi.period, 7)

        macd = IndicatorEngine.create_indicator("macd")
        self.assertIsInstance(macd, MACDIndicator)
        self.assertEqual(macd.fast, 12)
        self.assertEqual(macd.slow, 26)
        self.assertEqual(macd.signal, 9)

        adx = IndicatorEngine.create_indicator("adx", {"period": 21})
        self.assertIsInstance(adx, ADXIndicator)
        self.assertEqual(adx.period, 21)

        st = IndicatorEngine.create_indicator(
            "supertrend", {"atr_period": 7, "multiplier": 2.5}
        )
        self.assertIsInstance(st, SuperTrendIndicator)
        self.assertEqual(st.atr_period, 7)
        self.assertEqual(st.multiplier, 2.5)

        bb = IndicatorEngine.create_indicator("bollinger_bands")
        self.assertIsInstance(bb, BollingerBandsIndicator)
        self.assertEqual(bb.period, 20)
        self.assertEqual(bb.std_dev, 2.0)

        # Empty params dict is allowed (uses class defaults).
        rsi2 = IndicatorEngine.create_indicator("rsi", {})
        self.assertIsInstance(rsi2, RSIIndicator)
        self.assertEqual(rsi2.period, 14)

    def test_create_indicator_unknown_kind_raises(self):
        """Unknown indicator kinds should raise ValueError, not return None."""
        with self.assertRaises(ValueError) as ctx:
            IndicatorEngine.create_indicator("not_a_real_indicator", {})
        self.assertIn("Unknown indicator kind", str(ctx.exception))

    def test_create_indicator_all_14_kinds(self):
        """All 14 indicator kinds resolve to the right concrete class.

        This is the regression guard: if a future refactor drops a kind
        from ``IndicatorEngine._KIND_MAP``, this test fires.
        """
        from backend.indicators.adx import ADXIndicator
        from backend.indicators.atr import ATRIndicator
        from backend.indicators.bollinger_bands import BollingerBandsIndicator
        from backend.indicators.ema import EMAIndicator
        from backend.indicators.macd import MACDIndicator
        from backend.indicators.obv import OBVIndicator
        from backend.indicators.relative_volume import RelativeVolumeIndicator
        from backend.indicators.roc import ROCIndicator
        from backend.indicators.rsi import RSIIndicator
        from backend.indicators.sma import SMAIndicator
        from backend.indicators.supertrend import SuperTrendIndicator
        from backend.indicators.swing_high import SwingHighIndicator
        from backend.indicators.swing_low import SwingLowIndicator
        from backend.indicators.volume_sma import VolumeSMAIndicator

        # ``params`` here is the kind-specific payload. Some indicators
        # accept only keyword arguments (e.g. period=20); we use the
        # canonical defaults for kinds that need them.
        cases = [
            ("ema", {"period": 5}, EMAIndicator),
            ("sma", {"period": 5}, SMAIndicator),
            ("rsi", {"period": 14}, RSIIndicator),
            ("macd", {}, MACDIndicator),
            ("adx", {"period": 14}, ADXIndicator),
            ("atr", {"period": 14}, ATRIndicator),
            ("supertrend", {"atr_period": 10, "multiplier": 3.0}, SuperTrendIndicator),
            ("bollinger_bands", {"period": 20, "std_dev": 2.0}, BollingerBandsIndicator),
            ("volume_sma", {"period": 20}, VolumeSMAIndicator),
            ("relative_volume", {"period": 20}, RelativeVolumeIndicator),
            ("obv", {}, OBVIndicator),
            ("roc", {"period": 12}, ROCIndicator),
            ("swing_high", {"lookback_period": 5}, SwingHighIndicator),
            ("swing_low", {"lookback_period": 5}, SwingLowIndicator),
        ]
        for kind, params, expected_cls in cases:
            with self.subTest(kind=kind):
                indicator = IndicatorEngine.create_indicator(kind, params)
                self.assertIsInstance(indicator, expected_cls)

    def test_build_timeframe_stack_uses_defaults(self):
        """build_timeframe_stack() should produce a full stack driven by
        IndicatorDefaults — no hard-coded values inside the engine."""
        from backend.config.settings import IndicatorDefaults

        defaults = IndicatorDefaults(
            rsi_period=21,
            macd_fast=8,
            macd_slow=17,
            macd_signal=5,
            adx_period=14,
            supertrend_atr_period=11,
            supertrend_multiplier=2.5,
            bollinger_period=18,
            bollinger_std_dev=1.5,
        )

        stack = IndicatorEngine.build_timeframe_stack(20, 50, defaults)

        # Stack contains all the standard slots.
        expected = {
            "ema_fast", "ema_slow", "rsi", "macd", "adx",
            "supertrend", "bollinger_bands",
        }
        self.assertEqual(set(stack.keys()), expected)

        # Parameters should be exactly what was passed in defaults.
        self.assertEqual(stack["rsi"].period, 21)
        self.assertEqual(stack["macd"].fast, 8)
        self.assertEqual(stack["macd"].slow, 17)
        self.assertEqual(stack["macd"].signal, 5)
        self.assertEqual(stack["adx"].period, 14)
        self.assertEqual(stack["supertrend"].atr_period, 11)
        self.assertEqual(stack["supertrend"].multiplier, 2.5)
        self.assertEqual(stack["bollinger_bands"].period, 18)
        self.assertEqual(stack["bollinger_bands"].std_dev, 1.5)
        self.assertEqual(stack["ema_fast"].period, 20)
        self.assertEqual(stack["ema_slow"].period, 50)


if __name__ == '__main__':
    unittest.main()
