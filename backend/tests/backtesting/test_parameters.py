"""
Tests for ``backend.backtesting.parameters``.

Validates that ``ExperimentParameters`` defaults match
``IndicatorDefaults`` / ``TrendSignalWeights`` and that Pydantic
field constraints reject out-of-range values.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.backtesting.parameters import ExperimentParameters
from backend.config.settings import settings


class TestExperimentParametersDefaults(unittest.TestCase):
    """Default values mirror the server-side config so callers can omit
    any field and inherit the same numbers as the live scanner."""

    def test_rsi_period_default(self):
        self.assertEqual(
            ExperimentParameters().rsi_period,
            settings.trend.indicators.rsi_period,
        )

    def test_macd_defaults(self):
        params = ExperimentParameters()
        ind = settings.trend.indicators
        self.assertEqual(params.macd_fast, ind.macd_fast)
        self.assertEqual(params.macd_slow, ind.macd_slow)
        self.assertEqual(params.macd_signal, ind.macd_signal)

    def test_rsi_thresholds(self):
        params = ExperimentParameters()
        self.assertEqual(params.rsi_oversold, 30.0)
        self.assertEqual(params.rsi_overbought, 70.0)

    def test_weight_defaults_match_trend_signal_weights(self):
        params = ExperimentParameters()
        w = settings.trend.signal_weights
        self.assertEqual(params.weight_ema, w.ema)
        self.assertEqual(params.weight_rsi, w.rsi)
        self.assertEqual(params.weight_macd, w.macd)
        self.assertEqual(params.weight_adx, w.adx)
        self.assertEqual(params.weight_volume, w.relative_volume)
        self.assertEqual(params.weight_momentum, w.momentum)
        self.assertEqual(params.weight_supertrend, w.supertrend)
        self.assertEqual(params.weight_bollinger, w.bollinger)


class TestExperimentParametersConstraints(unittest.TestCase):
    def test_rsi_period_must_be_at_least_2(self):
        with self.assertRaises(Exception):
            ExperimentParameters(rsi_period=1)

    def test_rsi_period_must_be_at_most_100(self):
        with self.assertRaises(Exception):
            ExperimentParameters(rsi_period=101)

    def test_rsi_oversold_below_1_rejected(self):
        with self.assertRaises(Exception):
            ExperimentParameters(rsi_oversold=0.5)

    def test_rsi_oversold_above_50_rejected(self):
        with self.assertRaises(Exception):
            ExperimentParameters(rsi_oversold=51.0)

    def test_rsi_overbought_below_50_rejected(self):
        with self.assertRaises(Exception):
            ExperimentParameters(rsi_overbought=49.0)

    def test_negative_weight_rejected(self):
        with self.assertRaises(Exception):
            ExperimentParameters(weight_ema=-0.1)


class TestToJsonDict(unittest.TestCase):
    def test_to_json_dict_contains_all_fields(self):
        params = ExperimentParameters()
        d = params.to_json_dict()
        self.assertIn("rsi_period", d)
        self.assertIn("rsi_oversold", d)
        self.assertIn("weight_ema", d)
        # All values should be primitive (int/float).
        for v in d.values():
            self.assertIsInstance(v, (int, float))


if __name__ == "__main__":
    unittest.main()
