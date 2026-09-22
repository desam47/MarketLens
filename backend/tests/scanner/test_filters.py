"""
Tests for the composable Filter system (backend.scanner.filters).
"""

import os
import sys
import unittest

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.scanner.filters import (
    ADXStrong,
    AndFilter,
    BidAskImbalance,
    Breakdown,
    Breakout,
    DailyBearish,
    DailyBullish,
    HighVolume,
    LargePrintActivity,
    LiveVolumeAcceleration,
    MACDBearish,
    MACDBullish,
    MinTimeframeBearish,
    MinTimeframeBullish,
    MTFAlignment,
    OrFilter,
    OversoldReversal,
    PriceAbove,
    PriceAboveMovingAverage,
    PriceBelow,
    PriceBelowMovingAverage,
    RelativeStrengthAbove,
    RelativeStrengthBelow,
    RSIOverbought,
    RSIOversold,
    SignalPresent,
    SpreadWidening,
    TapePressure,
    TightSpread,
    TimeframeDirection,
    TradeRateSpike,
    TrendScoreGt,
    TrendScoreLt,
    TrueFilter,
    VolatilityContraction,
    VolatilityExpansion,
    VolumeExpansion,
    apply_filter,
    default_registry,
)
from backend.scanner.scanner import ScanResult


def _result(
    symbol: str = "AAPL",
    *,
    score: float = 0.0,
    rsi: float | None = None,
    macd: float | None = None,
    adx: float | None = None,
    volume: int | None = None,
    price: float | None = None,
    trend_signals: dict | None = None,
    signals: list[str] | None = None,
) -> ScanResult:
    """Build a ScanResult with convenient kwargs."""
    r = ScanResult(symbol, __import__("datetime").datetime.now())
    r.add_score("trend_strength", score)
    r.add_score("momentum", max(0.0, min(100.0, score)))
    if rsi is not None:
        r.add_indicator("rsi", rsi)
    if macd is not None:
        r.add_indicator("macd", macd)
    if adx is not None:
        r.add_indicator("adx", adx)
    if volume is not None:
        r.add_indicator("volume", volume)
    if price is not None:
        r.add_indicator("price", price)
    if trend_signals:
        r.trend_signals.update(trend_signals)
    if signals:
        r.signals.extend(signals)
    return r


class TestConcreteFilters(unittest.TestCase):
    def test_trend_score_gt(self):
        f = TrendScoreGt(50.0)
        r1 = _result(score=75.0)
        r2 = _result(score=25.0)
        self.assertTrue(f.matches(r1))
        self.assertFalse(f.matches(r2))

    def test_trend_score_lt(self):
        f = TrendScoreLt(30.0)
        self.assertTrue(f.matches(_result(score=20.0)))
        self.assertFalse(f.matches(_result(score=80.0)))

    def test_daily_bullish(self):
        f = DailyBullish(min_confidence=0.6)
        # Daily uptrend with confidence 0.8 → match
        bullish = _result(trend_signals={"ONE_DAY": {"direction": "uptrend", "confidence": 0.8}})
        # Daily downtrend → no match
        bearish = _result(trend_signals={"ONE_DAY": {"direction": "downtrend", "confidence": 0.9}})
        # Daily uptrend but low confidence → no match
        weak = _result(trend_signals={"ONE_DAY": {"direction": "uptrend", "confidence": 0.3}})
        self.assertTrue(f.matches(bullish))
        self.assertFalse(f.matches(bearish))
        self.assertFalse(f.matches(weak))

    def test_daily_bearish(self):
        f = DailyBearish(min_confidence=0.5)
        match = _result(trend_signals={"ONE_DAY": {"direction": "downtrend", "confidence": 0.6}})
        no_match = _result(trend_signals={"ONE_DAY": {"direction": "uptrend", "confidence": 0.9}})
        self.assertTrue(f.matches(match))
        self.assertFalse(f.matches(no_match))

    def test_timeframe_direction_aliased(self):
        # 1h, 4h, 1d aliases
        f = TimeframeDirection("1h", "uptrend", min_confidence=0.7)
        match = _result(trend_signals={"ONE_HOUR": {"direction": "uptrend", "confidence": 0.8}})
        no_match = _result(
            trend_signals={"ONE_HOUR": {"direction": "downtrend", "confidence": 0.8}}
        )
        self.assertTrue(f.matches(match))
        self.assertFalse(f.matches(no_match))

    def test_timeframe_direction_unknown_tf(self):
        f = TimeframeDirection("invalid", "uptrend")
        self.assertFalse(f.matches(_result()))

    def test_min_timeframe_bullish(self):
        f = MinTimeframeBullish(min_count=3)
        match = _result(
            trend_signals={
                "ONE_HOUR": {"direction": "uptrend", "confidence": 0.7},
                "FOUR_HOUR": {"direction": "uptrend", "confidence": 0.8},
                "ONE_DAY": {"direction": "uptrend", "confidence": 0.9},
            }
        )
        weak = _result(
            trend_signals={
                "ONE_HOUR": {"direction": "uptrend", "confidence": 0.7},
            }
        )
        self.assertTrue(f.matches(match))
        self.assertFalse(f.matches(weak))

    def test_min_timeframe_bearish(self):
        f = MinTimeframeBearish(min_count=2)
        match = _result(
            trend_signals={
                "ONE_DAY": {"direction": "downtrend", "confidence": 0.6},
                "FOUR_HOUR": {"direction": "downtrend", "confidence": 0.7},
            }
        )
        no_match = _result(
            trend_signals={
                "ONE_DAY": {"direction": "uptrend", "confidence": 0.6},
            }
        )
        self.assertTrue(f.matches(match))
        self.assertFalse(f.matches(no_match))

    def test_mtf_alignment_bullish(self):
        f = MTFAlignment(min_timeframes=3)
        aligned = _result(
            trend_signals={
                "ONE_HOUR": {"direction": "uptrend", "confidence": 0.7},
                "FOUR_HOUR": {"direction": "uptrend", "confidence": 0.7},
                "ONE_DAY": {"direction": "uptrend", "confidence": 0.7},
            }
        )
        conflict = _result(
            trend_signals={
                "ONE_HOUR": {"direction": "uptrend", "confidence": 0.7},
                "FOUR_HOUR": {"direction": "downtrend", "confidence": 0.7},
                "ONE_DAY": {"direction": "uptrend", "confidence": 0.7},
            }
        )
        self.assertTrue(f.matches(aligned))
        self.assertFalse(f.matches(conflict))

    def test_rsi_oversold_overbought(self):
        self.assertTrue(RSIOversold(30.0).matches(_result(rsi=25.0)))
        self.assertFalse(RSIOversold(30.0).matches(_result(rsi=50.0)))
        self.assertTrue(RSIOverbought(70.0).matches(_result(rsi=80.0)))
        self.assertFalse(RSIOverbought(70.0).matches(_result(rsi=50.0)))
        # Missing data
        self.assertFalse(RSIOversold(30.0).matches(_result(rsi=None)))

    def test_macd_directional(self):
        self.assertTrue(MACDBullish().matches(_result(macd=2.5)))
        self.assertFalse(MACDBullish().matches(_result(macd=-1.0)))
        self.assertTrue(MACDBearish().matches(_result(macd=-2.5)))
        self.assertFalse(MACDBearish().matches(_result(macd=1.0)))

    def test_high_volume(self):
        self.assertTrue(HighVolume(min_volume=1_000_000).matches(_result(volume=2_000_000)))
        self.assertFalse(HighVolume(min_volume=1_000_000).matches(_result(volume=500_000)))

    def test_signal_present(self):
        r = _result(signals=["MACD_BULLISH", "RSI_OVERSOLD"])
        self.assertTrue(SignalPresent(["macd_bullish"]).matches(r))
        self.assertFalse(SignalPresent(["VOLUME_SPIKE"]).matches(r))

    def test_price_above_below(self):
        self.assertTrue(PriceAbove(100.0).matches(_result(price=150.0)))
        self.assertFalse(PriceAbove(200.0).matches(_result(price=150.0)))
        self.assertTrue(PriceBelow(100.0).matches(_result(price=80.0)))
        self.assertFalse(PriceBelow(50.0).matches(_result(price=80.0)))

    def test_adx_strong(self):
        self.assertTrue(ADXStrong(25.0).matches(_result(adx=30.0)))
        self.assertFalse(ADXStrong(25.0).matches(_result(adx=20.0)))

    def test_composite_scanner_filters(self):
        r = _result(price=110.0, rsi=40.0)
        r.indicator_values.update(
            {
                "rsi_previous": 30.0,
                "price_change_pct": 1.5,
                "volume_ratio": 2.0,
                "volatility_ratio": 0.6,
                "sma_20": 100.0,
                "highest_high_20": 108.0,
                "lowest_low_20": 90.0,
                "rs_pct_SPY": 3.5,
            }
        )
        self.assertTrue(OversoldReversal(35, 2).matches(r))
        self.assertTrue(Breakout(20).matches(r))
        self.assertFalse(Breakdown(20).matches(r))
        self.assertTrue(VolumeExpansion(1.5).matches(r))
        self.assertTrue(PriceAboveMovingAverage(20).matches(r))
        self.assertFalse(PriceBelowMovingAverage(20).matches(r))
        self.assertTrue(VolatilityContraction(0.75).matches(r))
        self.assertFalse(VolatilityExpansion(1.25).matches(r))
        self.assertTrue(RelativeStrengthAbove("SPY", 2).matches(r))
        self.assertFalse(RelativeStrengthBelow("SPY", -1).matches(r))

    def test_microstructure_filters(self):
        r = _result()
        r.indicator_values.update({
            "spread_bps": 4.5,
            "spread_change_bps": 3.2,
            "tape_pressure": "heavy_buy",
            "tape_block_count": 2,
            "tape_acceleration": 1.8,
            "bid_ask_imbalance": 0.35,
            "tape_volume_acceleration": 2.1,
        })
        self.assertTrue(TightSpread(5).matches(r))
        self.assertFalse(TightSpread(4).matches(r))
        self.assertTrue(SpreadWidening(3).matches(r))
        self.assertTrue(TapePressure("buy").matches(r))
        self.assertFalse(TapePressure("sell").matches(r))
        self.assertTrue(LargePrintActivity(2).matches(r))
        self.assertTrue(TradeRateSpike(1.5).matches(r))
        self.assertTrue(BidAskImbalance("bid", 0.3).matches(r))
        self.assertFalse(BidAskImbalance("ask", 0.3).matches(r))
        self.assertTrue(LiveVolumeAcceleration(2).matches(r))


class TestComposition(unittest.TestCase):
    def test_and_filter(self):
        a = TrendScoreGt(50.0)
        b = DailyBullish()
        c = a & b

        both = _result(
            score=75.0, trend_signals={"ONE_DAY": {"direction": "uptrend", "confidence": 0.7}}
        )
        only_score = _result(score=75.0)
        self.assertTrue(c.matches(both))
        self.assertFalse(c.matches(only_score))

    def test_or_filter(self):
        a = RSIOversold(30.0)
        b = RSIOverbought(70.0)
        c = a | b

        self.assertTrue(c.matches(_result(rsi=25.0)))
        self.assertTrue(c.matches(_result(rsi=80.0)))
        self.assertFalse(c.matches(_result(rsi=50.0)))

    def test_not_filter(self):
        a = TrendScoreGt(50.0)
        b = ~a
        self.assertTrue(b.matches(_result(score=20.0)))
        self.assertFalse(b.matches(_result(score=80.0)))

    def test_describe(self):
        a = DailyBullish()
        b = HighVolume()
        c = a & b
        self.assertIn("AND", c.describe())
        self.assertIn("1d = uptrend", c.describe())


class TestTrueFilter(unittest.TestCase):
    """A genuine match-all — no direction/confidence check at all.

    Regression coverage for a live bug (2026-09-09/10): NL search's own
    match-all fallback used to be DailyBullish(min_confidence=-1.0),
    which still hardcoded direction == "uptrend" regardless of the
    disabled confidence floor — so "match all" silently excluded every
    non-uptrending-daily symbol. TrueFilter replaces it.
    """

    def test_matches_regardless_of_trend_signals(self):
        f = TrueFilter()
        self.assertTrue(
            f.matches(
                _result(
                    trend_signals={
                        "ONE_DAY": {"direction": "downtrend", "confidence": 0.9},
                    }
                )
            )
        )
        self.assertTrue(f.matches(_result(trend_signals={})))
        self.assertTrue(f.matches(_result()))

    def test_describe(self):
        self.assertEqual(TrueFilter().describe(), "match all")


class TestRegistry(unittest.TestCase):
    def test_list_types(self):
        types = default_registry.list_types()
        self.assertIn("trend_score_gt", types)
        self.assertIn("daily_bullish", types)
        self.assertIn("mtf_alignment", types)
        self.assertIn("breakout", types)
        self.assertIn("relative_strength_above", types)
        self.assertIn("true", types)

    def test_build_true_filter(self):
        f = default_registry.build({"type": "true"})
        self.assertIsInstance(f, TrueFilter)
        self.assertTrue(
            f.matches(
                _result(
                    trend_signals={
                        "ONE_DAY": {"direction": "downtrend", "confidence": 0.9},
                    }
                )
            )
        )

    def test_build(self):
        f = default_registry.build({"type": "trend_score_gt", "params": {"threshold": 60.0}})
        self.assertIsInstance(f, TrendScoreGt)
        self.assertEqual(f.threshold, 60.0)

    def test_build_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            default_registry.build({"type": "does_not_exist"})

    def test_build_no_type_raises(self):
        with self.assertRaises(ValueError):
            default_registry.build({})

    def test_build_bad_params_raise(self):
        with self.assertRaises(ValueError):
            default_registry.build({"type": "trend_score_gt", "params": {"nope": 1}})

    def test_conjunction(self):
        f = default_registry.build_conjunction(
            [
                {"type": "daily_bullish", "params": {"min_confidence": 0.5}},
                {"type": "high_volume", "params": {"min_volume": 100_000}},
            ]
        )
        self.assertIsInstance(f, AndFilter)
        # Should match a result that's both daily-bullish and high-volume
        r = _result(
            volume=200_000, trend_signals={"ONE_DAY": {"direction": "uptrend", "confidence": 0.6}}
        )
        self.assertTrue(f.matches(r))

    def test_disjunction(self):
        f = default_registry.build_disjunction(
            [
                {"type": "rsi_oversold", "params": {"threshold": 30.0}},
                {"type": "rsi_overbought", "params": {"threshold": 70.0}},
            ]
        )
        self.assertIsInstance(f, OrFilter)
        self.assertTrue(f.matches(_result(rsi=20.0)))
        self.assertTrue(f.matches(_result(rsi=80.0)))
        self.assertFalse(f.matches(_result(rsi=50.0)))


class TestApplyFilter(unittest.TestCase):
    def test_apply_filter(self):
        results = [
            _result("AAPL", score=80.0),
            _result("MSFT", score=40.0),
            _result("GOOGL", score=90.0),
        ]
        out = apply_filter(results, TrendScoreGt(50.0))
        symbols = [r.symbol for r in out]
        self.assertEqual(set(symbols), {"AAPL", "GOOGL"})


if __name__ == "__main__":
    unittest.main()
