"""
Unit tests for the condition evaluator dispatcher.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from backend.alerts.conditions import (
    VALID_CONDITION_TYPES,
    _eval_breakdown,
    _eval_breakout,
    _eval_divergence,
    _eval_full_timeframe_alignment,
    _eval_insider_sentiment_change,
    _eval_market_regime_change,
    _eval_news_arrival,
    _eval_options_activity_change,
    _eval_pct_change_above,
    _eval_price_above,
    _eval_price_below,
    _eval_signal_equals,
    _eval_timeframe_conflict,
    _eval_trend_crosses_above_70,
    _eval_trend_crosses_below_70,
    _eval_trend_direction_changes,
    _eval_trend_strengthens,
    _eval_trend_weakens,
    _eval_volume_expansion,
    evaluate,
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

    # --- pct_change_above -----------------------------------------------

    def test_pct_change_above_true(self):
        self.assertTrue(_eval_pct_change_above("5.0", 7.5))

    def test_pct_change_above_false(self):
        self.assertFalse(_eval_pct_change_above("5.0", 2.0))

    def test_pct_change_above_equal_boundary(self):
        self.assertFalse(_eval_pct_change_above("5.0", 5.0))

    def test_pct_change_above_negative_change(self):
        self.assertFalse(_eval_pct_change_above("5.0", -3.0))

    # --- trend_crosses_above_70 ---------------------------------------

    def test_trend_crosses_above_70_fires(self):
        value = {"current": 72.0, "previous": 68.0}
        self.assertTrue(_eval_trend_crosses_above_70("", value))

    def test_trend_crosses_above_70_below_threshold(self):
        value = {"current": 65.0, "previous": 60.0}
        self.assertFalse(_eval_trend_crosses_above_70("", value))

    def test_trend_crosses_above_70_was_above(self):
        value = {"current": 75.0, "previous": 72.0}
        self.assertFalse(_eval_trend_crosses_above_70("", value))

    def test_trend_crosses_above_70_exact_cross(self):
        value = {"current": 71.0, "previous": 70.0}
        self.assertTrue(_eval_trend_crosses_above_70("", value))

    def test_trend_crosses_above_70_non_dict(self):
        self.assertFalse(_eval_trend_crosses_above_70("", [72, 68]))

    # --- trend_crosses_below_70 -----------------------------------------

    def test_trend_crosses_below_70_fires(self):
        value = {"current": 68.0, "previous": 72.0}
        self.assertTrue(_eval_trend_crosses_below_70("", value))

    def test_trend_crosses_below_70_above_threshold(self):
        value = {"current": 75.0, "previous": 80.0}
        self.assertFalse(_eval_trend_crosses_below_70("", value))

    def test_trend_crosses_below_70_exact_cross(self):
        value = {"current": 69.0, "previous": 70.0}
        self.assertTrue(_eval_trend_crosses_below_70("", value))

    # --- trend_direction_changes ----------------------------------------

    def test_trend_direction_changes_bullish_to_bearish(self):
        value = {"current_direction": "bullish", "previous_direction": "bearish"}
        self.assertTrue(_eval_trend_direction_changes("", value))

    def test_trend_direction_changes_same(self):
        value = {"current_direction": "bullish", "previous_direction": "bullish"}
        self.assertFalse(_eval_trend_direction_changes("", value))

    def test_trend_direction_changes_unknown_to_known(self):
        value = {"current_direction": "unknown", "previous_direction": "bearish"}
        self.assertFalse(_eval_trend_direction_changes("", value))

    def test_trend_direction_changes_non_dict(self):
        self.assertFalse(_eval_trend_direction_changes("", ["bullish", "bearish"]))

    # --- trend_strengthens ----------------------------------------------

    def test_trend_strengthens_fires(self):
        value = {"current": 60.0, "previous": 50.0}
        self.assertTrue(_eval_trend_strengthens("5", value))

    def test_trend_strengthens_insufficient_delta(self):
        value = {"current": 52.0, "previous": 50.0}
        self.assertFalse(_eval_trend_strengthens("5", value))

    def test_trend_strengthens_negative_territory(self):
        value = {"current": -30.0, "previous": -50.0}
        self.assertFalse(_eval_trend_strengthens("5", value))

    def test_trend_strengthens_custom_threshold(self):
        value = {"current": 58.0, "previous": 55.0}
        self.assertTrue(_eval_trend_strengthens("2", value))
        self.assertFalse(_eval_trend_strengthens("5", value))

    # --- trend_weakens -------------------------------------------------

    def test_trend_weakens_fires(self):
        value = {"current": 40.0, "previous": 55.0}
        self.assertTrue(_eval_trend_weakens("5", value))

    def test_trend_weakens_insufficient_delta(self):
        value = {"current": 52.0, "previous": 55.0}
        self.assertFalse(_eval_trend_weakens("5", value))

    def test_trend_weakens_from_neutral(self):
        value = {"current": 10.0, "previous": 15.0}
        self.assertFalse(_eval_trend_weakens("5", value))

    # --- full_timeframe_alignment ---------------------------------------

    def test_full_alignment_all_bullish(self):
        value = ["bullish", "bullish", "bullish"]
        self.assertTrue(_eval_full_timeframe_alignment("", value))

    def test_full_alignment_mixed(self):
        value = ["bullish", "bearish", "bullish"]
        self.assertFalse(_eval_full_timeframe_alignment("", value))

    def test_full_alignment_single(self):
        value = ["bullish"]
        self.assertFalse(_eval_full_timeframe_alignment("", value))

    def test_full_alignment_with_unknown(self):
        value = ["bullish", "unknown", "bullish"]
        self.assertTrue(_eval_full_timeframe_alignment("", value))

    def test_full_alignment_non_list(self):
        self.assertFalse(_eval_full_timeframe_alignment("", "bullish"))

    # --- timeframe_conflict ---------------------------------------------

    def test_timeframe_conflict_fires(self):
        value = ["bullish", "bearish"]
        self.assertTrue(_eval_timeframe_conflict("", value))

    def test_timeframe_conflict_all_same(self):
        value = ["bullish", "bullish"]
        self.assertFalse(_eval_timeframe_conflict("", value))

    def test_timeframe_conflict_single(self):
        value = ["bullish"]
        self.assertFalse(_eval_timeframe_conflict("", value))

    # --- volume_expansion ----------------------------------------------

    def test_volume_expansion_fires(self):
        value = {"current_volume": 100000, "avg_volume": 40000, "symbol": "AAPL"}
        self.assertTrue(_eval_volume_expansion("2.0", value))

    def test_volume_expansion_at_threshold(self):
        value = {"current_volume": 80000, "avg_volume": 40000, "symbol": "AAPL"}
        self.assertTrue(_eval_volume_expansion("2.0", value))

    def test_volume_expansion_below_threshold(self):
        value = {"current_volume": 50000, "avg_volume": 40000, "symbol": "AAPL"}
        self.assertFalse(_eval_volume_expansion("2.0", value))

    def test_volume_expansion_non_dict(self):
        self.assertFalse(_eval_volume_expansion("2.0", 50000))

    # --- auxiliary provider events --------------------------------------

    def test_news_arrival_relevance_floor(self):
        self.assertTrue(_eval_news_arrival("0.5", {"new_count": 1, "max_relevance": 0.8}))
        self.assertFalse(_eval_news_arrival("0.9", {"new_count": 1, "max_relevance": 0.8}))
        self.assertFalse(_eval_news_arrival("0.0", {"new_count": 0, "max_relevance": 1.0}))

    def test_insider_sentiment_change_threshold(self):
        value = {"current_sentiment": 0.4, "previous_sentiment": -0.1}
        self.assertTrue(_eval_insider_sentiment_change("0.4", value))
        self.assertFalse(_eval_insider_sentiment_change("0.6", value))

    def test_options_activity_change_checks_volume_and_open_interest(self):
        self.assertTrue(_eval_options_activity_change("50", {"volume_change_pct": 60}))
        self.assertTrue(_eval_options_activity_change("50", {"open_interest_change_pct": -55}))
        self.assertFalse(_eval_options_activity_change("50", {"volume_change_pct": 10, "open_interest_change_pct": 20}))

    # --- divergence ----------------------------------------------------

    def test_negative_divergence_price_up_rsi_down(self):
        value = {"price_change_pct": 2.5, "rsi_like": 45.0}
        self.assertTrue(_eval_divergence("negative", value))

    def test_negative_divergence_price_down_rsi_up(self):
        value = {"price_change_pct": -2.5, "rsi_like": 55.0}
        self.assertFalse(_eval_divergence("negative", value))

    def test_positive_divergence_price_down_rsi_up(self):
        value = {"price_change_pct": -2.5, "rsi_like": 55.0}
        self.assertTrue(_eval_divergence("positive", value))

    def test_positive_divergence_price_up_rsi_down(self):
        value = {"price_change_pct": 2.5, "rsi_like": 45.0}
        self.assertFalse(_eval_divergence("positive", value))

    def test_divergence_non_dict(self):
        self.assertFalse(_eval_divergence("negative", [2.5, 45.0]))

    # --- breakout -------------------------------------------------------

    def test_breakout_fires(self):
        value = {"current_price": 155.0, "highest_high": 150.0, "symbol": "AAPL"}
        self.assertTrue(_eval_breakout("20", value))

    def test_breakout_at_high(self):
        value = {"current_price": 150.0, "highest_high": 150.0, "symbol": "AAPL"}
        self.assertFalse(_eval_breakout("20", value))

    def test_breakout_below_high(self):
        value = {"current_price": 145.0, "highest_high": 150.0, "symbol": "AAPL"}
        self.assertFalse(_eval_breakout("20", value))

    def test_breakout_non_dict(self):
        self.assertFalse(_eval_breakout("20", 155.0))

    # --- breakdown ------------------------------------------------------

    def test_breakdown_fires(self):
        value = {"current_price": 145.0, "lowest_low": 150.0, "symbol": "AAPL"}
        self.assertTrue(_eval_breakdown("20", value))

    def test_breakdown_at_low(self):
        value = {"current_price": 150.0, "lowest_low": 150.0, "symbol": "AAPL"}
        self.assertFalse(_eval_breakdown("20", value))

    def test_breakdown_above_low(self):
        value = {"current_price": 155.0, "lowest_low": 150.0, "symbol": "AAPL"}
        self.assertFalse(_eval_breakdown("20", value))


class TestMarketRegimeChange(unittest.TestCase):

    def test_regime_change_fires_on_risk_on_to_risk_off(self):
        """RISK_ON → RISK_OFF triggers the alert."""
        value = {"current_regime": "RISK_ON", "previous_regime": "RISK_OFF"}
        self.assertTrue(_eval_market_regime_change("", value))

    def test_regime_change_fires_on_risk_off_to_risk_on(self):
        """RISK_OFF → RISK_ON triggers the alert (bidirectional)."""
        value = {"current_regime": "RISK_OFF", "previous_regime": "RISK_ON"}
        self.assertTrue(_eval_market_regime_change("", value))

    def test_regime_change_fires_on_neutral_to_risk_on(self):
        """NEUTRAL → RISK_ON triggers the alert."""
        value = {"current_regime": "RISK_ON", "previous_regime": "NEUTRAL"}
        self.assertTrue(_eval_market_regime_change("", value))

    def test_regime_change_false_when_same(self):
        """No trigger when regime doesn't change."""
        value = {"current_regime": "RISK_ON", "previous_regime": "RISK_ON"}
        self.assertFalse(_eval_market_regime_change("", value))

    def test_regime_change_false_when_previous_unknown(self):
        """No trigger when previous regime is UNKNOWN (cold start)."""
        value = {"current_regime": "RISK_ON", "previous_regime": "UNKNOWN"}
        self.assertFalse(_eval_market_regime_change("", value))

    def test_regime_change_false_when_current_unknown(self):
        """No trigger when current regime is UNKNOWN (not enough data)."""
        value = {"current_regime": "UNKNOWN", "previous_regime": "RISK_ON"}
        self.assertFalse(_eval_market_regime_change("", value))

    def test_regime_change_filters_to_risk_on(self):
        """With parameter=RISK_ON, only fires when transitioning TO RISK_ON."""
        value = {"current_regime": "RISK_ON", "previous_regime": "RISK_OFF"}
        self.assertTrue(_eval_market_regime_change("risk_on", value))

    def test_regime_change_filters_to_risk_off(self):
        """With parameter=RISK_OFF, only fires when transitioning TO RISK_OFF."""
        value = {"current_regime": "RISK_OFF", "previous_regime": "RISK_ON"}
        self.assertTrue(_eval_market_regime_change("risk_off", value))

    def test_regime_change_filter_does_not_fire_on_other(self):
        """With parameter=RISK_ON, doesn't fire for other transitions."""
        value = {"current_regime": "RISK_OFF", "previous_regime": "RISK_ON"}
        self.assertFalse(_eval_market_regime_change("risk_on", value))

    def test_regime_change_case_insensitive(self):
        """Regime names are compared case-insensitively."""
        value = {"current_regime": "risk_on", "previous_regime": "RISK_OFF"}
        self.assertTrue(_eval_market_regime_change("", value))

    def test_regime_change_non_dict_value(self):
        """Returns False for non-dict value."""
        self.assertFalse(_eval_market_regime_change("", "not a dict"))

    def test_regime_change_missing_previous_regime(self):
        """Missing previous_regime is treated as UNKNOWN so no change fires."""
        value = {"current_regime": "RISK_ON"}  # missing previous_regime
        self.assertFalse(_eval_market_regime_change("", value))

    def test_regime_change_missing_current_regime(self):
        """Missing current_regime returns False."""
        value = {"previous_regime": "RISK_ON"}  # missing current_regime
        self.assertFalse(_eval_market_regime_change("", value))


class TestEvaluateDispatcher(unittest.TestCase):

    def test_valid_condition_types_exported(self):
        expected = {
            "signal_equals", "price_above", "price_below", "pct_change_above",
            "trend_crosses_above_70", "trend_crosses_below_70",
            "trend_direction_changes", "trend_strengthens", "trend_weakens",
            "full_timeframe_alignment", "timeframe_conflict",
            "volume_expansion", "divergence", "breakout", "breakdown",
            "market_regime_change", "signal_profile", "news_arrival",
            "insider_sentiment_change", "options_activity_change",
        }
        self.assertEqual(set(VALID_CONDITION_TYPES), expected)

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

    def test_signal_profile_filters_direction_score_strength_regime_and_timeframe(self):
        profile = json.dumps({
            "direction": "bullish",
            "min_score": 70,
            "min_strength": 0.7,
            "market_regime": "risk_on",
            "timeframe": "1d",
        })
        value = {
            "current": 80,
            "current_direction": "bullish",
            "strength": 0.8,
            "current_regime": "risk_on",
            "timeframe": "1d",
        }
        self.assertTrue(evaluate("signal_profile", profile, value))
        self.assertFalse(evaluate("signal_profile", profile, {**value, "timeframe": "1h"}))
        self.assertFalse(evaluate("signal_profile", profile, {**value, "strength": 0.6}))

    def test_dispatcher_trend_crosses_above_70(self):
        self.assertTrue(evaluate("trend_crosses_above_70", "", {"current": 72.0, "previous": 68.0}))
        self.assertFalse(evaluate("trend_crosses_above_70", "", {"current": 65.0, "previous": 60.0}))

    def test_dispatcher_trend_crosses_below_70(self):
        self.assertTrue(evaluate("trend_crosses_below_70", "", {"current": 68.0, "previous": 72.0}))
        self.assertFalse(evaluate("trend_crosses_below_70", "", {"current": 75.0, "previous": 80.0}))

    def test_dispatcher_trend_direction_changes(self):
        self.assertTrue(evaluate("trend_direction_changes", "",
                                 {"current_direction": "bullish", "previous_direction": "bearish"}))
        self.assertFalse(evaluate("trend_direction_changes", "",
                                 {"current_direction": "bullish", "previous_direction": "bullish"}))

    def test_dispatcher_trend_strengthens(self):
        self.assertTrue(evaluate("trend_strengthens", "5", {"current": 60.0, "previous": 50.0}))
        self.assertFalse(evaluate("trend_strengthens", "5", {"current": 52.0, "previous": 50.0}))

    def test_dispatcher_trend_weakens(self):
        self.assertTrue(evaluate("trend_weakens", "5", {"current": 40.0, "previous": 55.0}))
        self.assertFalse(evaluate("trend_weakens", "5", {"current": 52.0, "previous": 55.0}))

    def test_dispatcher_full_timeframe_alignment(self):
        self.assertTrue(evaluate("full_timeframe_alignment", "", ["bullish", "bullish"]))
        self.assertFalse(evaluate("full_timeframe_alignment", "", ["bullish", "bearish"]))

    def test_dispatcher_timeframe_conflict(self):
        self.assertTrue(evaluate("timeframe_conflict", "", ["bullish", "bearish"]))
        self.assertFalse(evaluate("timeframe_conflict", "", ["bullish", "bullish"]))

    def test_dispatcher_volume_expansion(self):
        self.assertTrue(evaluate("volume_expansion", "2.0", {"current_volume": 100000, "avg_volume": 40000}))
        self.assertFalse(evaluate("volume_expansion", "2.0", {"current_volume": 50000, "avg_volume": 40000}))

    def test_dispatcher_divergence_negative(self):
        self.assertTrue(evaluate("divergence", "negative", {"price_change_pct": 2.5, "rsi_like": 45.0}))
        self.assertFalse(evaluate("divergence", "negative", {"price_change_pct": -2.5, "rsi_like": 55.0}))

    def test_dispatcher_divergence_positive(self):
        self.assertTrue(evaluate("divergence", "positive", {"price_change_pct": -2.5, "rsi_like": 55.0}))
        self.assertFalse(evaluate("divergence", "positive", {"price_change_pct": 2.5, "rsi_like": 45.0}))

    def test_dispatcher_breakout(self):
        self.assertTrue(evaluate("breakout", "20", {"current_price": 155.0, "highest_high": 150.0}))
        self.assertFalse(evaluate("breakout", "20", {"current_price": 145.0, "highest_high": 150.0}))

    def test_dispatcher_breakdown(self):
        self.assertTrue(evaluate("breakdown", "20", {"current_price": 145.0, "lowest_low": 150.0}))
        self.assertFalse(evaluate("breakdown", "20", {"current_price": 155.0, "lowest_low": 150.0}))

    def test_dispatcher_market_regime_change_fires(self):
        self.assertTrue(evaluate(
            "market_regime_change", "",
            {"current_regime": "RISK_ON", "previous_regime": "RISK_OFF"},
        ))

    def test_dispatcher_market_regime_change_no_fire_on_same(self):
        self.assertFalse(evaluate(
            "market_regime_change", "",
            {"current_regime": "RISK_ON", "previous_regime": "RISK_ON"},
        ))

    def test_dispatcher_market_regime_change_filter_to_regime(self):
        self.assertTrue(evaluate(
            "market_regime_change", "risk_off",
            {"current_regime": "RISK_OFF", "previous_regime": "RISK_ON"},
        ))
        self.assertFalse(evaluate(
            "market_regime_change", "risk_on",
            {"current_regime": "RISK_OFF", "previous_regime": "RISK_ON"},
        ))

    def test_dispatcher_unknown_condition_returns_false(self):
        self.assertFalse(evaluate("unknown_condition", "foo", None))

    def test_dispatcher_bad_parameter_returns_false(self):
        self.assertFalse(evaluate("price_above", "not a float", 100.0))


if __name__ == "__main__":
    unittest.main()
