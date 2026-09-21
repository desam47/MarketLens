"""Tests for the NL search controlled query schema."""

import unittest

from pydantic import ValidationError

from backend.nl_search.schema import (
    NLFilters,
    NLSearchResponse,
    ScannedResultItem,
)


class TestNLFiltersDefaults(unittest.TestCase):
    def test_default_ranking(self):
        f = NLFilters()
        self.assertEqual(f.ranking, "strongest_bullish")

    def test_default_top_n(self):
        f = NLFilters()
        self.assertEqual(f.top_n, 10)

    def test_default_scope(self):
        f = NLFilters()
        self.assertEqual(f.scope, "watchlist")

    def test_default_min_confidence(self):
        f = NLFilters()
        self.assertEqual(f.min_confidence, 0.5)

    def test_default_match_all(self):
        f = NLFilters()
        self.assertFalse(f.match_all)


class TestNLFiltersCrossValidation(unittest.TestCase):
    def test_trend_min_gt_trend_max_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            NLFilters(trend_min=80, trend_max=20)
        self.assertIn("trend_min", str(ctx.exception))

    def test_trend_min_eq_trend_max_is_fine(self):
        f = NLFilters(trend_min=50, trend_max=50)
        self.assertEqual(f.trend_min, 50)
        self.assertEqual(f.trend_max, 50)

    def test_rsi_oversold_below_ge_overbought_above_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            NLFilters(rsi_oversold_below=40, rsi_overbought_above=40)
        self.assertIn("rsi_oversold", str(ctx.exception))

    def test_rsi_oversold_lt_overbought_is_fine(self):
        f = NLFilters(rsi_oversold_below=30, rsi_overbought_above=70)
        self.assertEqual(f.rsi_oversold_below, 30)
        self.assertEqual(f.rsi_overbought_above, 70)

    def test_outperforms_upper_cased(self):
        f = NLFilters(outperforms="qqq")
        self.assertEqual(f.outperforms, "QQQ")

    def test_outperforms_strips_whitespace(self):
        f = NLFilters(outperforms="  SPY  ")
        self.assertEqual(f.outperforms, "SPY")


class TestNLFiltersSignalsAllowlist(unittest.TestCase):
    def test_unknown_signals_are_stripped(self):
        f = NLFilters(signals=["RSI_OVERSOLD", "UNKNOWN_SIGNAL", "HIGH_VOLUME"])
        self.assertEqual(f.signals, ["RSI_OVERSOLD", "HIGH_VOLUME"])

    def test_signals_deduped(self):
        f = NLFilters(signals=["RSI_OVERSOLD", "RSI_OVERSOLD"])
        self.assertEqual(f.signals, ["RSI_OVERSOLD"])

    def test_signals_case_insensitive(self):
        f = NLFilters(signals=["rsi_oversold", "HIGH_VOLUME"])
        self.assertEqual(f.signals, ["RSI_OVERSOLD", "HIGH_VOLUME"])

    def test_empty_signal_stripped(self):
        f = NLFilters(signals=["RSI_OVERSOLD", "", "  ", "HIGH_VOLUME"])
        self.assertEqual(f.signals, ["RSI_OVERSOLD", "HIGH_VOLUME"])


class TestNLFiltersLiterals(unittest.TestCase):
    def test_timeframe_must_be_literal(self):
        f = NLFilters(timeframe="1d")
        self.assertEqual(f.timeframe, "1d")

    def test_timeframe_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            NLFilters(timeframe="3m")

    def test_direction_bullish(self):
        f = NLFilters(direction="bullish")
        self.assertEqual(f.direction, "bullish")

    def test_direction_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            NLFilters(direction="sideways")

    def test_ranking_all_valid(self):
        for r in [
            "strongest_bullish",
            "strongest_bearish",
            "strongest_momentum",
            "biggest_improvement",
            "biggest_deterioration",
            "best_mtf_alignment",
            "strongest_relative_strength",
        ]:
            f = NLFilters(ranking=r)
            self.assertEqual(f.ranking, r)

    def test_ranking_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            NLFilters(ranking="best_performer")

    def test_macd_bullish(self):
        f = NLFilters(macd="bullish")
        self.assertEqual(f.macd, "bullish")

    def test_macd_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            NLFilters(macd="neutral")

    def test_transition_just_became_bullish(self):
        f = NLFilters(transition="just_became_bullish")
        self.assertEqual(f.transition, "just_became_bullish")

    def test_transition_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            NLFilters(transition="became_bearish")

    def test_scope_watchlist(self):
        f = NLFilters(scope="watchlist")
        self.assertEqual(f.scope, "watchlist")

    def test_scope_market(self):
        f = NLFilters(scope="market")
        self.assertEqual(f.scope, "market")

    def test_scope_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            NLFilters(scope="all")


class TestNLFiltersNumericRanges(unittest.TestCase):
    def test_trend_min_out_of_range_low(self):
        with self.assertRaises(ValidationError):
            NLFilters(trend_min=-1)

    def test_trend_min_out_of_range_high(self):
        with self.assertRaises(ValidationError):
            NLFilters(trend_min=101)

    def test_rsi_below_out_of_range(self):
        with self.assertRaises(ValidationError):
            NLFilters(rsi_oversold_below=150)

    def test_top_n_min(self):
        f = NLFilters(top_n=1)
        self.assertEqual(f.top_n, 1)

    def test_top_n_max(self):
        f = NLFilters(top_n=50)
        self.assertEqual(f.top_n, 50)

    def test_top_n_out_of_range(self):
        with self.assertRaises(ValidationError):
            NLFilters(top_n=51)


class TestScannedResultItem(unittest.TestCase):
    def test_required_fields(self):
        item = ScannedResultItem(symbol="AAPL", total_score=75.0, rank=1, signals=[])
        self.assertEqual(item.symbol, "AAPL")
        self.assertEqual(item.total_score, 75.0)
        self.assertEqual(item.rank, 1)

    def test_optional_fields_default_none(self):
        item = ScannedResultItem(symbol="AAPL", total_score=0, rank=None, signals=[])
        self.assertIsNone(item.rsi)
        self.assertIsNone(item.macd)
        self.assertIsNone(item.adx)
        self.assertIsNone(item.price)
        self.assertEqual(item.trend_directions, {})


class TestNLSearchResponse(unittest.TestCase):
    def test_required_fields(self):
        resp = NLSearchResponse(
            query="strongest bullish stocks",
            schema=NLFilters(),
            filter_description="AAPL",
            results=[],
            ranking="strongest_bullish",
            ai_explanation_used=False,
            ai_translation_used=False,
            timestamp="2026-08-26T12:00:00Z",
        )
        self.assertEqual(resp.query, "strongest bullish stocks")
        self.assertIsNone(resp.explanation)
        self.assertIsNone(resp.reason)
        self.assertEqual(resp.parser_used, "rules")

    def test_explanation_and_reason_set(self):
        resp = NLSearchResponse(
            query="xyz",
            schema=NLFilters(),
            filter_description="(match all)",
            results=[],
            ranking="strongest_bullish",
            ai_explanation_used=False,
            ai_translation_used=False,
            explanation="Theme here",
            reason="No symbols matched",
            timestamp="2026-08-26T12:00:00Z",
        )
        self.assertEqual(resp.explanation, "Theme here")
        self.assertEqual(resp.reason, "No symbols matched")


if __name__ == "__main__":
    unittest.main()
