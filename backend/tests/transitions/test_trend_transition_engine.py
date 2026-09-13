"""Tests for TrendTransitionEngine."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.transitions.trend_transition_engine import (
    TransitionDirection,
    TransitionType,
    TrendTransitionEngine,
)


class TestTrendTransitionEngine(unittest.TestCase):

    def test_initialization_defaults(self):
        engine = TrendTransitionEngine()
        self.assertEqual(engine.window, 5)
        self.assertEqual(engine.min_delta, 10.0)
        self.assertEqual(engine.reversal_threshold, 0.0)

    def test_initialization_custom(self):
        engine = TrendTransitionEngine(window=3, min_delta=5.0, reversal_threshold=10.0)
        self.assertEqual(engine.window, 3)
        self.assertEqual(engine.min_delta, 5.0)
        self.assertEqual(engine.reversal_threshold, 10.0)

    def test_window_validation(self):
        with self.assertRaises(ValueError):
            TrendTransitionEngine(window=0)
        with self.assertRaises(ValueError):
            TrendTransitionEngine(min_delta=-1.0)

    def test_short_series_returns_empty(self):
        engine = TrendTransitionEngine()
        # Series too short for window
        scores = [10.0, 20.0]
        result = engine.detect(scores)
        self.assertEqual(result, [])

    def test_bullish_acceleration(self):
        """Score rises significantly in positive territory."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        # +35 → +68 is a bullish acceleration (delta = +33, both positive)
        scores = [35.0, 68.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, TransitionType.BULLISH_ACCELERATION)
        self.assertEqual(result[0].direction, TransitionDirection.BULLISH)
        self.assertAlmostEqual(result[0].delta, 33.0)
        self.assertAlmostEqual(result[0].previous_score, 35.0)
        self.assertAlmostEqual(result[0].current_score, 68.0)

    def test_bullish_weakening(self):
        """Score falls but stays positive."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        # +84 → +61 is a bullish weakening (delta = -23, both positive)
        scores = [84.0, 61.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, TransitionType.BULLISH_WEAKENING)
        self.assertEqual(result[0].direction, TransitionDirection.BULLISH)
        self.assertAlmostEqual(result[0].delta, -23.0)

    def test_bearish_acceleration(self):
        """Score falls significantly in negative territory."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        # -30 → -65 is bearish acceleration (delta = -35, both negative)
        scores = [-30.0, -65.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, TransitionType.BEARISH_ACCELERATION)
        self.assertEqual(result[0].direction, TransitionDirection.BEARISH)
        self.assertAlmostEqual(result[0].delta, -35.0)

    def test_bearish_weakening(self):
        """Score rises toward zero but stays negative."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        # -65 → -20 is bearish weakening (delta = +45, both negative)
        scores = [-65.0, -20.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, TransitionType.BEARISH_WEAKENING)
        self.assertEqual(result[0].direction, TransitionDirection.BEARISH)
        self.assertAlmostEqual(result[0].delta, 45.0)

    def test_bullish_reversal(self):
        """Score crosses from negative to positive."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        scores = [-20.0, 25.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, TransitionType.BULLISH_REVERSAL)
        self.assertEqual(result[0].direction, TransitionDirection.BULLISH)
        self.assertAlmostEqual(result[0].delta, 45.0)

    def test_bearish_reversal(self):
        """Score crosses from positive to negative."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        scores = [30.0, -15.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, TransitionType.BEARISH_REVERSAL)
        self.assertEqual(result[0].direction, TransitionDirection.BEARISH)

    def test_bearish_reversal_with_threshold(self):
        """Score crosses zero but reversal_threshold requires more magnitude."""
        engine = TrendTransitionEngine(window=1, min_delta=5.0, reversal_threshold=10.0)
        # -5 → +8: crosses zero but neither side exceeds threshold
        scores = [-5.0, 8.0]
        result = engine.detect(scores)
        self.assertEqual(result, [])

    def test_zero_is_neutral_not_reversal(self):
        """Exactly-zero is neutral: touching zero from a standstill is not a
        reversal (#5). prev=0 -> curr>0 and prev=0 -> curr<0 must both be
        filtered, since nothing was actually reversed."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        self.assertEqual(engine.detect([0.0, 30.0]), [])
        self.assertEqual(engine.detect([0.0, -30.0]), [])

    def test_zero_to_positive_is_not_acceleration(self):
        """prev=0 -> curr>0 is not a BULLISH_ACCELERATION either: there is no
        prior positive momentum to accelerate (both must be strictly > 0)."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        result = engine.detect([0.0, 80.0])
        self.assertEqual(result, [])

    def test_below_min_delta_filtered(self):
        """Score delta smaller than min_delta is ignored."""
        engine = TrendTransitionEngine(window=1, min_delta=50.0)
        # +35 → +68: delta=33 < min_delta=50 → filtered
        scores = [35.0, 68.0]
        result = engine.detect(scores)
        self.assertEqual(result, [])

    def test_window_2(self):
        """Window of 2 uses the point 2 positions back."""
        engine = TrendTransitionEngine(window=2, min_delta=10.0)
        # Index 0, 1, 2: transition from index 0 to index 2
        scores = [20.0, 25.0, 80.0]
        result = engine.detect(scores)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].previous_score, 20.0)
        self.assertAlmostEqual(result[0].current_score, 80.0)
        self.assertEqual(result[0].delta, 60.0)
        self.assertEqual(result[0].type, TransitionType.BULLISH_ACCELERATION)

    def test_timestamps_attached(self):
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        ts = [datetime(2024, 1, i) for i in range(1, 6)]
        scores = [35.0, 68.0, 70.0, 72.0, 74.0]
        result = engine.detect(scores, timestamps=ts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].timestamp, datetime(2024, 1, 2))

    def test_latest_returns_last(self):
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        scores = [10.0, 20.0, 40.0, 75.0]
        result = engine.latest(scores)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.current_score, 75.0)
        self.assertEqual(result.type, TransitionType.BULLISH_ACCELERATION)

    def test_latest_uses_newest_timestamp(self):
        """When scores are newest-first, latest() must resolve by timestamp
        (newest transition), NOT by array position. This guards against the
        prior off-by-one bug where latest() returned the oldest transition
        because the series is inverted before reaching the engine."""
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        # Newest-first scores with matching newest-first timestamps. The
        # newest *timestamp* belongs to index 1 (the first point that has a
        # full window of history behind it), so the newest transition is NOT
        # at the end of the array.
        ts_new, ts_mid, ts_old = (
            datetime(2024, 1, 4),
            datetime(2024, 1, 3),
            datetime(2024, 1, 2),
        )
        scores = [75.0, 40.0, 20.0, 10.0]
        timestamps = [ts_new, ts_mid, ts_old, datetime(2024, 1, 1)]
        result = engine.latest(scores, timestamps=timestamps)
        self.assertIsNotNone(result)
        # The newest transition is the one at index 1 (prev=75 -> curr=40),
        # timestamp ts_mid. The old code returned the last element (index 3,
        # timestamp 2024-01-01) -- the oldest, not the newest.
        self.assertEqual(result.timestamp, ts_mid)
        self.assertAlmostEqual(result.current_score, 40.0)

    def test_latest_empty(self):
        engine = TrendTransitionEngine(window=10)
        result = engine.latest([10.0, 20.0])
        self.assertIsNone(result)

    def test_symbol_and_timeframe_stamped(self):
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        result = engine.detect([35.0, 68.0], symbol="AAPL", timeframe="1d")
        self.assertEqual(result[0].symbol, "AAPL")
        self.assertEqual(result[0].timeframe, "1d")

    def test_to_dict(self):
        engine = TrendTransitionEngine(window=1, min_delta=10.0)
        result = engine.detect([35.0, 68.0])
        d = result[0].to_dict()
        self.assertIn("type", d)
        self.assertIn("delta", d)
        self.assertEqual(d["type"], "bullish_acceleration")

    def test_mixed_sign_no_cross(self):
        """Score crosses zero but delta below min_delta is filtered."""
        engine = TrendTransitionEngine(window=1, min_delta=50.0)
        scores = [20.0, -10.0]
        result = engine.detect(scores)
        # delta=30 < min_delta=50; below threshold → filtered
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
