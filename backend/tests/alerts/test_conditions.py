"""
Unit tests for the condition evaluator dispatcher.
"""
import unittest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from backend.alerts.conditions import (
    evaluate,
    VALID_CONDITION_TYPES,
    _eval_signal_equals,
    _eval_price_above,
    _eval_price_below,
    _eval_pct_change_above,
)


class TestConditionEvaluators(unittest.TestCase):

    # --- signal_equals ---------------------------------------------------

    def test_signal_equals_true(self):
        self.assertTrue(_eval_signal_equals("RSI_OVERSOLD", ["RSI_OVERSOLD", "MACD_BULLISH"]))

    def test_signal_equals_false(self):
        self.assertFalse(_eval_signal_equals("RSI_OVERSOLD", ["MACD_BULLISH"]))

    def test_signal_equals_empty_list(self):
        self.assertFalse(_eval_signal_equals("RSI_OVERSOLD", []))

    def test_signal_equals_non_iterable(self):
        self.assertFalse(_eval_signal_equals("RSI_OVERSOLD", "RSI_OVERSOLD"))

    def test_signal_equals_tuple(self):
        self.assertTrue(_eval_signal_equals("BULLISH", ("BULLISH", "NEUTRAL")))

    # --- price_above -----------------------------------------------------

    def test_price_above_true(self):
        self.assertTrue(_eval_price_above("150.0", 155.0))

    def test_price_above_false(self):
        self.assertFalse(_eval_price_above("150.0", 149.0))

    def test_price_above_equal_boundary(self):
        self.assertFalse(_eval_price_above("150.0", 150.0))

    def test_price_above_non_numeric_value(self):
        self.assertFalse(_eval_price_above("150.0", "not a number"))

    def test_price_above_invalid_parameter(self):
        self.assertFalse(_eval_price_above("not a number", 100.0))

    # --- price_below -----------------------------------------------------

    def test_price_below_true(self):
        self.assertTrue(_eval_price_below("150.0", 145.0))

    def test_price_below_false(self):
        self.assertFalse(_eval_price_below("150.0", 155.0))

    def test_price_below_equal_boundary(self):
        self.assertFalse(_eval_price_below("150.0", 150.0))

    def test_price_below_non_numeric_value(self):
        self.assertFalse(_eval_price_below("150.0", None))

    # --- pct_change_above ------------------------------------------------

    def test_pct_change_above_true(self):
        self.assertTrue(_eval_pct_change_above("5.0", 7.5))

    def test_pct_change_above_false(self):
        self.assertFalse(_eval_pct_change_above("5.0", 2.0))

    def test_pct_change_above_equal_boundary(self):
        self.assertFalse(_eval_pct_change_above("5.0", 5.0))

    def test_pct_change_above_negative_change(self):
        self.assertFalse(_eval_pct_change_above("5.0", -3.0))


class TestEvaluateDispatcher(unittest.TestCase):

    def test_valid_condition_types_exported(self):
        self.assertEqual(
            set(VALID_CONDITION_TYPES),
            {"signal_equals", "price_above", "price_below", "pct_change_above"},
        )

    def test_dispatcher_signal_equals(self):
        self.assertTrue(evaluate("signal_equals", "RSI_OVERSOLD", ["RSI_OVERSOLD"]))
        self.assertFalse(evaluate("signal_equals", "RSI_OVERSOLD", []))

    def test_dispatcher_price_above(self):
        self.assertTrue(evaluate("price_above", "100", 110))
        self.assertFalse(evaluate("price_above", "100", 90))

    def test_dispatcher_price_below(self):
        self.assertTrue(evaluate("price_below", "100", 90))
        self.assertFalse(evaluate("price_below", "100", 110))

    def test_dispatcher_pct_change_above(self):
        self.assertTrue(evaluate("pct_change_above", "3.0", 5.0))
        self.assertFalse(evaluate("pct_change_above", "3.0", 1.0))

    def test_dispatcher_unknown_condition_returns_false(self):
        self.assertFalse(evaluate("unknown_condition", "foo", None))

    def test_dispatcher_bad_parameter_returns_false(self):
        self.assertFalse(evaluate("price_above", "not a float", 100.0))


if __name__ == "__main__":
    unittest.main()
