"""
Tests for excursion_distribution.

Tests cover:
  - bullish rows read straight through; bearish rows swap mae/mfe and negate
  - adverse excursion reported as a positive magnitude, favorable kept signed
  - below min_sample every statistic is withheld (None), not weakened
  - percentile values against a known array
  - neutral/incomplete rows excluded from the distribution
  - confidence tiers and the outlier-tail note
"""

import os
import sys
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.services.excursion_stats import (
    DEFAULT_MIN_SAMPLE,
    HIGH_CONFIDENCE_SAMPLE,
    excursion_distribution,
)


# Mirrors the columns fetch_excursion_rows selects; directional_outcome reads
# these by getattr, so a plain dataclass stands in for a SQLAlchemy Row.
@dataclass
class Row:
    trend_state: str | None
    mae: float | None
    mfe: float | None
    return_5b: float | None = 0.0
    return_10b: float | None = 0.0
    return_20b: float | None = 0.0


def bullish(mae, mfe, r10=0.0):
    return Row(trend_state="bullish", mae=mae, mfe=mfe, return_10b=r10)


def bearish(mae, mfe, r10=0.0):
    return Row(trend_state="bearish", mae=mae, mfe=mfe, return_10b=r10)


class TestSampleGating(unittest.TestCase):
    def test_below_min_sample_withholds_every_statistic(self):
        rows = [bullish(-1.0, 2.0)] * 10
        stats = excursion_distribution(rows, min_sample=DEFAULT_MIN_SAMPLE)

        self.assertEqual(stats["sample_size"], 10)
        self.assertFalse(stats["sufficient"])
        self.assertEqual(stats["confidence"], "insufficient")
        # A half-trusted stop is worse than an admitted gap.
        self.assertIsNone(stats["adverse_excursion_pct"])
        self.assertIsNone(stats["favorable_excursion_pct"])
        self.assertIsNone(stats["win_rate"])
        self.assertIsNone(stats["avg_return_10b"])
        self.assertIsNone(stats["median_return_10b"])
        self.assertTrue(stats["notes"])

    def test_empty_rows_are_insufficient_not_an_error(self):
        stats = excursion_distribution([])
        self.assertEqual(stats["sample_size"], 0)
        self.assertFalse(stats["sufficient"])
        self.assertIsNone(stats["adverse_excursion_pct"])

    def test_confidence_tiers(self):
        low = excursion_distribution([bullish(-1.0, 2.0)] * DEFAULT_MIN_SAMPLE, min_sample=DEFAULT_MIN_SAMPLE)
        self.assertEqual(low["confidence"], "moderate")

        high = excursion_distribution([bullish(-1.0, 2.0)] * HIGH_CONFIDENCE_SAMPLE)
        self.assertEqual(high["confidence"], "high")


class TestDirectionHandling(unittest.TestCase):
    def test_bullish_adverse_is_magnitude_of_mae(self):
        stats = excursion_distribution([bullish(-1.5, 3.0)] * 100, min_sample=100)

        # mae is stored negative; the stop distance is its magnitude.
        self.assertEqual(stats["adverse_excursion_pct"]["p50"], 1.5)
        self.assertEqual(stats["favorable_excursion_pct"]["p50"], 3.0)

    def test_bearish_swaps_and_negates(self):
        # A bearish call earns when price falls: its adverse excursion is the
        # raw high-side mfe, and its favorable excursion is the raw mae.
        stats = excursion_distribution([bearish(-4.0, 1.0)] * 100, min_sample=100)

        self.assertEqual(stats["adverse_excursion_pct"]["p50"], 1.0)
        self.assertEqual(stats["favorable_excursion_pct"]["p50"], 4.0)

    def test_bearish_win_rate_uses_negated_return(self):
        # Price fell 2%, which a short called correctly.
        stats = excursion_distribution([bearish(-1.0, 1.0, r10=-2.0)] * 100, min_sample=100)

        self.assertEqual(stats["win_rate"], 1.0)
        self.assertEqual(stats["avg_return_10b"], 2.0)

    def test_neutral_and_incomplete_rows_excluded(self):
        rows = (
            [bullish(-1.0, 2.0)] * 100
            + [Row(trend_state="neutral", mae=-99.0, mfe=99.0)] * 50
            + [Row(trend_state=None, mae=-99.0, mfe=99.0)] * 50
            + [Row(trend_state="bullish", mae=None, mfe=2.0)] * 50
        )
        stats = excursion_distribution(rows, min_sample=100)

        self.assertEqual(stats["sample_size"], 100)
        self.assertEqual(stats["adverse_excursion_pct"]["p50"], 1.0)


class TestPercentileValues(unittest.TestCase):
    def test_percentiles_match_known_array(self):
        # Adverse magnitudes 1..100 -> linear-interpolated quantiles.
        rows = [bullish(-float(i), float(i)) for i in range(1, 101)]
        stats = excursion_distribution(rows, min_sample=100)

        adverse = stats["adverse_excursion_pct"]
        self.assertAlmostEqual(adverse["p25"], 25.75, places=2)
        self.assertAlmostEqual(adverse["p50"], 50.5, places=2)
        self.assertAlmostEqual(adverse["p75"], 75.25, places=2)
        self.assertAlmostEqual(adverse["p90"], 90.1, places=2)

    def test_win_rate_counts_only_positive_returns(self):
        rows = [bullish(-1.0, 1.0, r10=1.0)] * 60 + [bullish(-1.0, 1.0, r10=-1.0)] * 40
        stats = excursion_distribution(rows, min_sample=100)
        self.assertEqual(stats["win_rate"], 0.6)

    def test_median_is_robust_to_a_split_artifact_outlier(self):
        rows = [bullish(-1.0, 1.0)] * 99 + [bullish(-99.7, 21845.0)]
        stats = excursion_distribution(rows, min_sample=100)

        # The mean would be wrecked; the median holds.
        self.assertEqual(stats["adverse_excursion_pct"]["p50"], 1.0)
        self.assertEqual(stats["favorable_excursion_pct"]["p50"], 1.0)


class TestOutlierNote(unittest.TestCase):
    def test_fat_tail_is_flagged(self):
        rows = [bullish(-1.0, 1.0)] * 50 + [bullish(-80.0, 9000.0)] * 50
        stats = excursion_distribution(rows, min_sample=100)

        self.assertTrue(any("artifact" in note for note in stats["notes"]))

    def test_ordinary_tail_is_not_flagged(self):
        rows = [bullish(-1.0, 2.0)] * 100
        stats = excursion_distribution(rows, min_sample=100)

        self.assertEqual(stats["notes"], [])


if __name__ == "__main__":
    unittest.main()
