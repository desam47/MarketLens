"""
Tests for TrendClassification enum and classify_score helper (Phase 6).
"""

import unittest

from backend.trend.trend_engine import (
    TrendClassification,
    TrendStrength,
    classify_score,
    strength_to_float,
)


class TestClassifyScore(unittest.TestCase):
    """Phase 6 spec: map -100..+100 score to the 8-class bucket."""

    def test_strong_bullish_upper(self):
        """score >= 70 is STRONG_BULLISH."""
        self.assertEqual(classify_score(100), TrendClassification.STRONG_BULLISH)
        self.assertEqual(classify_score(85), TrendClassification.STRONG_BULLISH)
        self.assertEqual(classify_score(70), TrendClassification.STRONG_BULLISH)

    def test_bullish(self):
        """30 <= score < 70 is BULLISH."""
        self.assertEqual(classify_score(69), TrendClassification.BULLISH)
        self.assertEqual(classify_score(50), TrendClassification.BULLISH)
        self.assertEqual(classify_score(30), TrendClassification.BULLISH)

    def test_weak_bullish(self):
        """10 <= score < 30 is WEAK_BULLISH."""
        self.assertEqual(classify_score(29), TrendClassification.WEAK_BULLISH)
        self.assertEqual(classify_score(20), TrendClassification.WEAK_BULLISH)
        self.assertEqual(classify_score(10), TrendClassification.WEAK_BULLISH)

    def test_neutral(self):
        """|score| < 10 is NEUTRAL."""
        self.assertEqual(classify_score(9), TrendClassification.NEUTRAL)
        self.assertEqual(classify_score(5), TrendClassification.NEUTRAL)
        self.assertEqual(classify_score(0), TrendClassification.NEUTRAL)
        self.assertEqual(classify_score(-5), TrendClassification.NEUTRAL)
        self.assertEqual(classify_score(-9), TrendClassification.NEUTRAL)

    def test_weak_bearish(self):
        """-30 < score <= -10 is WEAK_BEARISH."""
        self.assertEqual(classify_score(-11), TrendClassification.WEAK_BEARISH)
        self.assertEqual(classify_score(-20), TrendClassification.WEAK_BEARISH)
        self.assertEqual(classify_score(-29), TrendClassification.WEAK_BEARISH)
        self.assertEqual(classify_score(-10), TrendClassification.WEAK_BEARISH)

    def test_bearish(self):
        """-70 < score <= -30 is BEARISH."""
        self.assertEqual(classify_score(-31), TrendClassification.BEARISH)
        self.assertEqual(classify_score(-50), TrendClassification.BEARISH)
        self.assertEqual(classify_score(-69), TrendClassification.BEARISH)
        self.assertEqual(classify_score(-30), TrendClassification.BEARISH)

    def test_strong_bearish_lower(self):
        """score <= -70 is STRONG_BEARISH."""
        self.assertEqual(classify_score(-70), TrendClassification.STRONG_BEARISH)
        self.assertEqual(classify_score(-85), TrendClassification.STRONG_BEARISH)
        self.assertEqual(classify_score(-100), TrendClassification.STRONG_BEARISH)

    def test_no_signal_on_none(self):
        """None (insufficient data) maps to NO_SIGNAL."""
        self.assertEqual(classify_score(None), TrendClassification.NO_SIGNAL)

    def test_no_signal_not_returned_by_score_values(self):
        """classify_score never returns NO_SIGNAL for a numeric score."""
        for score in [-200, -100, -70, -30, -10, -9, 0, 9, 10, 30, 70, 100, 200]:
            result = classify_score(score)
            self.assertNotEqual(
                result,
                TrendClassification.NO_SIGNAL,
                f"score={score} should not map to NO_SIGNAL",
            )

    def test_no_signal_is_value(self):
        """TrendClassification.NO_SIGNAL exists and has the expected string value."""
        self.assertEqual(TrendClassification.NO_SIGNAL.value, "no_signal")


class TestStrengthToFloat(unittest.TestCase):
    """strength_to_float maps the 4-value enum to 0..1 numeric."""

    def test_all_four_levels(self):
        self.assertEqual(strength_to_float(TrendStrength.WEAK), 0.25)
        self.assertEqual(strength_to_float(TrendStrength.MODERATE), 0.5)
        self.assertEqual(strength_to_float(TrendStrength.STRONG), 0.75)
        self.assertEqual(strength_to_float(TrendStrength.VERY_STRONG), 1.0)

    def test_unknown_defaults_to_moderate(self):
        """Unknown strength falls back to 0.5."""
        self.assertEqual(strength_to_float(None), 0.5)


if __name__ == "__main__":
    unittest.main()
