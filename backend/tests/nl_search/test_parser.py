"""Tests for the rule-based NL query parser."""
import unittest
from unittest.mock import patch

from backend.nl_search.parser import (
    _apply_conflict_rules,
    _apply_rules,
    _split_clauses,
    parse_query,
    parse_query_rule_based,
)


class TestSplitClauses(unittest.TestCase):

    def test_split_on_but(self):
        left, right = _split_clauses("bullish daily but bearish 5m")
        self.assertEqual(left, "bullish daily")
        self.assertEqual(right, "bearish 5m")

    def test_split_on_yet(self):
        left, right = _split_clauses("uptrend yet bearish 1h")
        self.assertEqual(left, "uptrend")
        self.assertEqual(right, "bearish 1h")

    def test_split_on_while(self):
        left, right = _split_clauses("bullish while spy bearish")
        self.assertEqual(left, "bullish")
        self.assertEqual(right, "spy bearish")

    def test_no_split(self):
        left, right = _split_clauses("strongest bullish stocks")
        self.assertEqual(left, "strongest bullish stocks")
        self.assertIsNone(right)


class TestApplyConflictRules(unittest.TestCase):

    def test_bearish_5m(self):
        result = _apply_conflict_rules("bearish 5m")
        self.assertEqual(result.get("timeframe"), "5m")
        self.assertEqual(result.get("direction"), "bearish")

    def test_bullish_daily(self):
        result = _apply_conflict_rules("bullish daily")
        self.assertEqual(result.get("timeframe"), "1d")
        self.assertEqual(result.get("direction"), "bullish")

    def test_no_match(self):
        result = _apply_conflict_rules("xyz garbage")
        self.assertNotIn("timeframe", result)


class TestApplyRules(unittest.TestCase):

    def test_strongest_bullish(self):
        merged, conflict = _apply_rules("Show me the strongest bullish stocks.")
        self.assertEqual(merged["ranking"], "strongest_bullish")
        self.assertEqual(merged["direction"], "bullish")
        self.assertIsNone(conflict)

    def test_strongest_bearish(self):
        merged, conflict = _apply_rules("strongest bearish stocks")
        self.assertEqual(merged["ranking"], "strongest_bearish")
        self.assertEqual(merged["direction"], "bearish")
        self.assertIsNone(conflict)

    def test_outperforming_qqq(self):
        merged, conflict = _apply_rules("stocks outperforming QQQ")
        self.assertEqual(merged["outperforms"], "QQQ")
        self.assertEqual(merged["ranking"], "strongest_relative_strength")
        self.assertIsNone(conflict)

    def test_bullish_daily(self):
        merged, conflict = _apply_rules("bullish daily trends")
        self.assertEqual(merged["timeframe"], "1d")
        self.assertEqual(merged["direction"], "bullish")

    def test_bearish_5m(self):
        merged, conflict = _apply_rules("bearish 5m trends")
        self.assertEqual(merged["timeframe"], "5m")
        self.assertEqual(merged["direction"], "bearish")

    def test_bullish_1h(self):
        merged, conflict = _apply_rules("bullish 1h setup")
        self.assertEqual(merged["timeframe"], "1h")
        self.assertEqual(merged["direction"], "bullish")

    def test_bearish_4h(self):
        merged, conflict = _apply_rules("bearish 4h chart")
        self.assertEqual(merged["timeframe"], "4h")
        self.assertEqual(merged["direction"], "bearish")

    def test_just_transitioned_bullish(self):
        merged, conflict = _apply_rules("stocks that just transitioned bullish")
        self.assertEqual(merged["transition"], "just_became_bullish")
        self.assertIsNone(conflict)

    def test_just_became_bearish(self):
        merged, conflict = _apply_rules("just became bearish")
        self.assertEqual(merged["transition"], "just_became_bearish")

    def test_strong_trend(self):
        merged, conflict = _apply_rules("strong trend and volume")
        self.assertEqual(merged["adx_strong_above"], 25.0)
        self.assertIsNone(conflict)

    def test_high_volume(self):
        merged, conflict = _apply_rules("strong volume confirmation")
        self.assertEqual(merged["signals"], ["HIGH_VOLUME"])
        self.assertIsNone(conflict)

    def test_spy_bullish_while_spy_bearish(self):
        merged, conflict = _apply_rules("bullish while SPY is bearish")
        self.assertTrue(merged["spy_bearish_while_stock_bullish"])
        self.assertEqual(merged["direction"], "bullish")
        self.assertIsNotNone(conflict)  # conflict clause is "spy is bearish"

    def test_rsi_oversold(self):
        merged, conflict = _apply_rules("RSI oversold stocks")
        self.assertEqual(merged["rsi_oversold_below"], 30.0)

    def test_rsi_overbought(self):
        merged, conflict = _apply_rules("rsi overbought")
        self.assertEqual(merged["rsi_overbought_above"], 70.0)

    def test_trend_score_over_80(self):
        merged, conflict = _apply_rules("trend score over 80")
        self.assertEqual(merged["trend_min"], 80.0)

    def test_trend_score_above_70(self):
        merged, conflict = _apply_rules("trend score above 70")
        self.assertEqual(merged["trend_min"], 70.0)

    def test_cross_tf_but_clause(self):
        merged, conflict = _apply_rules("bullish daily but bearish 5m")
        self.assertEqual(merged["timeframe"], "1d")
        self.assertEqual(merged["direction"], "bullish")
        self.assertEqual(conflict, "bearish 5m")

    def test_cross_tf_yet_clause(self):
        merged, conflict = _apply_rules("uptrend yet bearish 1h")
        self.assertEqual(merged["transition"], "just_became_bullish")
        self.assertEqual(conflict, "bearish 1h")

    def test_all_timeframes_aligned(self):
        merged, conflict = _apply_rules("all timeframes bullish")
        self.assertTrue(merged["mtf_alignment"])
        self.assertEqual(merged["min_bullish_timeframes"], 3)

    def test_garbage_returns_empty(self):
        merged, conflict = _apply_rules("asdfghjkl qwerty")
        self.assertEqual(merged, {})
        self.assertIsNone(conflict)


class TestParseQueryRuleBased(unittest.TestCase):

    def test_strongest_bullish(self):
        result = parse_query_rule_based("Show me the strongest bullish stocks.")
        self.assertIsNotNone(result)
        f, extras = result
        self.assertEqual(f.ranking, "strongest_bullish")
        self.assertEqual(f.direction, "bullish")
        self.assertIsNone(extras)

    def test_cross_tf_but(self):
        result = parse_query_rule_based("bullish daily but bearish 5m")
        self.assertIsNotNone(result)
        f, extras = result
        self.assertEqual(f.timeframe, "1d")
        self.assertEqual(f.direction, "bullish")
        self.assertIsNotNone(extras)
        self.assertIn("conflict", extras)
        self.assertEqual(extras["conflict"]["timeframe"], "5m")
        self.assertEqual(extras["conflict"]["direction"], "bearish")

    def test_outperforming_qqq(self):
        result = parse_query_rule_based("outperforming QQQ")
        self.assertIsNotNone(result)
        f, extras = result
        self.assertEqual(f.outperforms, "QQQ")
        self.assertEqual(f.ranking, "strongest_relative_strength")

    def test_garbage_returns_none(self):
        result = parse_query_rule_based("asdfghjkl")
        self.assertIsNone(result)

    def test_base_param_preserved(self):
        # When no rule fires, base is not merged unless there was a conflict clause.
        result = parse_query_rule_based("asdfgh", base={"scope": "watchlist"})
        self.assertIsNone(result)

    def test_base_not_overwritten_by_rule(self):
        result = parse_query_rule_based(
            "strongest bullish",
            base={"top_n": 5},
        )
        self.assertIsNotNone(result)
        f, _ = result
        self.assertEqual(f.ranking, "strongest_bullish")


class TestParseQuery(unittest.TestCase):

    def test_rule_based_used(self):
        f, extras, used = parse_query("strongest bullish stocks")
        self.assertEqual(used, "rules")

    def test_garbage_falls_to_default(self):
        # This test is about the rules-miss -> AI-miss -> default
        # fallback chain, not about what a live AI backend happens to
        # do with gibberish input — mock AI unavailable so the result
        # doesn't depend on whether this machine's .env has AI_ENABLED
        # on (found live 2026-09-09: with a real Ollama fallback
        # reachable, "asdfghjkl" got a confident-but-wrong AI parse
        # instead of falling through to "default").
        with patch("backend.nl_search.parser.ai_manager") as mock_ai:
            mock_ai.is_available.return_value = False
            f, extras, used = parse_query("asdfghjkl")
        self.assertEqual(used, "default")
        self.assertTrue(f.match_all)


if __name__ == "__main__":
    unittest.main()
