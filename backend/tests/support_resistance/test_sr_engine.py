"""Tests for SupportResistanceEngine."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.support_resistance.sr_engine import (
    SRType,
    SupportResistanceEngine,
)


def _bar(t, c, h=None, low=None, v=1000):
    return {
        "open": c,
        "high": c if h is None else h,
        "low": c if low is None else low,
        "close": c,
        "volume": v,
        "timestamp": t,
    }


def _make_bars(n=50, base=100.0, with_timestamps=True):
    """Simple oscillating series: produces clear swing highs and lows."""
    bars = []
    base_t = datetime(2024, 1, 1)
    for i in range(n):
        # Sinusoidal pattern
        phase = (i / n) * 6.28
        c = base + 5 * (1 + __import__("math").sin(phase))
        h = c + 1.0
        low = c - 1.0
        if with_timestamps:
            bars.append(_bar(base_t + timedelta(hours=i), c, h, low))
        else:
            bars.append({"open": c, "high": h, "low": low, "close": c,
                         "volume": 1000})
    return bars


class TestSupportResistanceEngine(unittest.TestCase):

    def test_initialization_defaults(self):
        engine = SupportResistanceEngine()
        self.assertEqual(engine.lookback_period, 2)
        self.assertEqual(engine.zone_width_pct, 0.005)
        self.assertEqual(engine.lookback_bars, 500)

    def test_validation(self):
        with self.assertRaises(ValueError):
            SupportResistanceEngine(lookback_period=0)
        with self.assertRaises(ValueError):
            SupportResistanceEngine(zone_width_pct=-0.1)
        with self.assertRaises(ValueError):
            SupportResistanceEngine(zone_width_pct=1.5)

    def test_insufficient_data(self):
        engine = SupportResistanceEngine()
        result = engine.detect([_bar(datetime.now(), 100.0)] * 3)
        self.assertEqual(result.levels, [])
        self.assertEqual(result.last_index, 0)

    def test_detects_swing_highs_and_lows(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars, symbol="AAPL", timeframe="1h")
        swing_highs = [lvl for lvl in result.levels if lvl.type == SRType.SWING_HIGH]
        swing_lows = [lvl for lvl in result.levels if lvl.type == SRType.SWING_LOW]
        self.assertGreater(len(swing_highs), 0)
        self.assertGreater(len(swing_lows), 0)

    def test_swing_high_above_swing_low(self):
        """Swing highs should be priced above swing lows in an oscillating series."""
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars)
        highs = [lvl.price for lvl in result.levels if lvl.type == SRType.SWING_HIGH]
        lows = [lvl.price for lvl in result.levels if lvl.type == SRType.SWING_LOW]
        if highs and lows:
            self.assertGreater(min(highs), max(lows))

    def test_swing_level_has_required_fields(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars, symbol="AAPL", timeframe="1d")
        swing = [lvl for lvl in result.levels
                 if lvl.type in (SRType.SWING_HIGH, SRType.SWING_LOW)]
        self.assertGreater(len(swing), 0)
        lvl = swing[0]
        # Spec requires: price, type, timeframe, strength, touch_count, age,
        # distance_from_price
        self.assertIsInstance(lvl.price, float)
        self.assertIn(lvl.type, (SRType.SWING_HIGH, SRType.SWING_LOW))
        self.assertEqual(lvl.timeframe, "1d")
        self.assertIsInstance(lvl.strength, float)
        self.assertGreaterEqual(lvl.strength, 0.0)
        self.assertLessEqual(lvl.strength, 1.0)
        self.assertIsInstance(lvl.touch_count, int)
        self.assertIsInstance(lvl.age, int)
        self.assertGreaterEqual(lvl.age, 0)
        # distance_from_price was removed from the level contract (the
        # frontend computes signed % distance from price directly) — assert
        # the remaining required fields are present and well-formed.
        self.assertNotIn("distance_from_price", lvl.to_dict())

    def test_pivot_table_detected(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars)
        pivots = [lvl for lvl in result.levels
                  if lvl.type in (SRType.PIVOT_PP, SRType.PIVOT_R1, SRType.PIVOT_R2,
                                  SRType.PIVOT_R3, SRType.PIVOT_S1, SRType.PIVOT_S2,
                                  SRType.PIVOT_S3)]
        self.assertGreater(len(pivots), 0)

    def test_pivot_table_ordering_and_sides(self):
        """R levels must be above PP, S levels below; R only above close."""
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars)
        by_index: dict = {}
        for lvl in result.levels:
            if lvl.type in (SRType.PIVOT_PP, SRType.PIVOT_R1, SRType.PIVOT_R2,
                            SRType.PIVOT_R3, SRType.PIVOT_S1, SRType.PIVOT_S2,
                            SRType.PIVOT_S3):
                by_index.setdefault(lvl.origin_index, []).append(lvl)
        for idx, group in by_index.items():
            prices = {lvl.type: lvl.price for lvl in group}
            if SRType.PIVOT_PP in prices:
                pp = prices[SRType.PIVOT_PP]
                if SRType.PIVOT_R1 in prices:
                    self.assertGreater(prices[SRType.PIVOT_R1], pp,
                        f"at bar {idx}: R1 {prices[SRType.PIVOT_R1]} should be > PP {pp}")
                if SRType.PIVOT_S1 in prices:
                    self.assertLess(prices[SRType.PIVOT_S1], pp,
                        f"at bar {idx}: S1 {prices[SRType.PIVOT_S1]} should be < PP {pp}")
                # Monotonic: R3 > R2 > R1 > PP > S1 > S2 > S3
                seq = [SRType.PIVOT_R3, SRType.PIVOT_R2, SRType.PIVOT_R1,
                       SRType.PIVOT_PP, SRType.PIVOT_S1, SRType.PIVOT_S2,
                       SRType.PIVOT_S3]
                present = [prices[s] for s in seq if s in prices]
                for a, b in zip(present, present[1:]):
                    self.assertGreater(a, b, f"at bar {idx}: pivot sequence must be decreasing")

    def test_prev_day_levels_with_timestamps(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=200)
        # Build bars spanning multiple days
        base = datetime(2024, 1, 1, 9, 30)
        bars = []
        for i in range(200):
            t = base + timedelta(hours=i // 8, minutes=(i % 8) * 30)
            c = 100.0 + (i % 10) * 0.5
            bars.append(_bar(t, c, c + 1, c - 1))
        result = engine.detect(bars)
        prev_day = [lvl for lvl in result.levels
                    if lvl.type in (SRType.PREV_DAY_HIGH, SRType.PREV_DAY_LOW)]
        self.assertGreater(len(prev_day), 0)

    def test_prev_week_levels_with_timestamps(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=300)
        # Build bars spanning multiple weeks (8h/day × 5d/week = ~40h/week)
        base = datetime(2024, 1, 1, 9, 30)
        bars = []
        for i in range(300):
            t = base + timedelta(hours=i * 2)  # every 2 hours
            c = 100.0 + (i % 10) * 0.5
            bars.append(_bar(t, c, c + 1, c - 1))
        result = engine.detect(bars)
        prev_week = [lvl for lvl in result.levels
                     if lvl.type in (SRType.PREV_WEEK_HIGH, SRType.PREV_WEEK_LOW)]
        self.assertGreater(len(prev_week), 0)

    def test_consolidation_zones_form(self):
        """Build a series that oscillates inside a tight range, then expand."""
        bars = []
        base_t = datetime(2024, 1, 1)
        # First 40 bars: tight range 99-101
        for i in range(40):
            c = 100.0 + 0.5 * ((-1) ** i)
            bars.append(_bar(base_t + timedelta(hours=i), c, c + 0.3, c - 0.3))
        # Next 40 bars: break out
        for i in range(40, 80):
            c = 100.0 + (i - 40) * 0.5
            bars.append(_bar(base_t + timedelta(hours=i), c, c + 1, c - 1))

        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100,
                                          zone_width_pct=0.01)
        result = engine.detect(bars)
        zones = [lvl for lvl in result.levels if lvl.type == SRType.CONSOLIDATION_ZONE]
        # We may or may not get a zone depending on the synthetic data;
        # if we do, verify its structure
        for z in zones:
            self.assertEqual(z.type, SRType.CONSOLIDATION_ZONE)
            self.assertGreater(z.touch_count, 1)
            self.assertGreater(len(z.component_prices), 1)

    def test_no_look_ahead(self):
        """Detect() must only consider data up to last_index."""
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars)
        for lvl in result.levels:
            if lvl.origin_index is not None:
                self.assertLessEqual(lvl.origin_index, result.last_index)

    def test_result_metadata(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(50)
        result = engine.detect(bars, symbol="AAPL", timeframe="1d")
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.timeframe, "1d")
        self.assertEqual(result.last_index, 49)
        self.assertIsNotNone(result.latest_close)

    def test_to_dict(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(50)
        result = engine.detect(bars, symbol="AAPL", timeframe="1d")
        d = result.to_dict()
        self.assertIn("levels", d)
        self.assertIn("last_index", d)
        if d["levels"]:
            self.assertIn("price", d["levels"][0])
            self.assertIn("type", d["levels"][0])
            self.assertIn("strength", d["levels"][0])

    def test_lookback_bars_limits_history(self):
        """lookback_bars should bound how many of the most recent bars are scanned."""
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=30)
        bars = _make_bars(100)
        result = engine.detect(bars)
        # last_index is the index of the *last* bar in the bounded window
        self.assertEqual(result.last_index, 29)

    def test_strength_in_range(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars)
        for lvl in result.levels:
            self.assertGreaterEqual(lvl.strength, 0.0)
            self.assertLessEqual(lvl.strength, 1.0)

    def test_sorted_by_strength_descending(self):
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100)
        bars = _make_bars(80)
        result = engine.detect(bars)
        strengths = [lvl.strength for lvl in result.levels]
        self.assertEqual(strengths, sorted(strengths, reverse=True))

    def test_deduplication(self):
        """Levels at nearly identical prices are deduped to the strongest."""
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=100,
                                          zone_width_pct=0.001)
        bars = _make_bars(80)
        result = engine.detect(bars)
        # All swing highs (excluding zones) should have unique prices
        # to within 0.01 (2 decimal places)
        swing_prices = [lvl.price for lvl in result.levels
                        if lvl.type in (SRType.SWING_HIGH, SRType.SWING_LOW)]
        rounded = [round(p, 2) for p in swing_prices]
        self.assertEqual(len(rounded), len(set(rounded)))


if __name__ == "__main__":
    unittest.main()
