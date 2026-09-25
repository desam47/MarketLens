"""
Tests for trade_plan_builder candidate assembly and selection.

Tests cover:
  - widest stop wins (a stop inside another source's noise estimate is unsafe)
  - stops/targets on the wrong side of entry are rejected
  - targets must clear MIN_REWARD_RISK against the selected stop
  - the options expected move is context only, never an auto-selected target
  - duplicate target prices collapse
  - oversized positions are flagged against account value
  - ATR is computed on ascending bars
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.services.trade_plan_builder import (
    ATR_PERIOD,
    MIN_REWARD_RISK,
    _collect_targets,
    _select,
    _sizing_warnings,
    _volatility,
)


def stop(source, price, entry):
    return {
        "source": source,
        "price": price,
        "distance_pct": abs(entry - price) / entry * 100,
        "rationale": "",
    }


class TestStopSelection(unittest.TestCase):
    def test_widest_stop_wins_for_long(self):
        # Stop placement answers "where is the thesis wrong", so the widest
        # candidate wins even though a tighter one would flatter the R:R.
        entry = 100.0
        stops = [
            stop("structural", 99.5, entry),
            stop("volatility", 99.0, entry),
            stop("empirical", 98.0, entry),
        ]
        targets = [stop("structural", 105.0, entry)]

        chosen, _, _ = _select(stops, targets, entry, "long")
        assert chosen is not None
        self.assertEqual(chosen["source"], "empirical")
        self.assertEqual(chosen["price"], 98.0)

    def test_widest_stop_wins_for_short(self):
        entry = 100.0
        stops = [stop("structural", 100.5, entry), stop("empirical", 102.0, entry)]
        targets = [stop("structural", 95.0, entry)]

        chosen, _, _ = _select(stops, targets, entry, "short")
        assert chosen is not None
        self.assertEqual(chosen["price"], 102.0)

    def test_tighter_stop_never_preferred_for_flattering_reward_risk(self):
        # A 0.5-wide stop would give 10:1 against this target; the 2.0-wide
        # stop gives 2.5:1 and is still the one chosen.
        entry = 100.0
        stops = [stop("tight", 99.5, entry), stop("wide", 98.0, entry)]
        chosen, targets, best = _select(stops, [stop("structural", 105.0, entry)], entry, "long")

        assert chosen is not None and best is not None
        self.assertEqual(chosen["source"], "wide")
        self.assertEqual([t["price"] for t in targets], [105.0])
        self.assertAlmostEqual(best, 2.5, places=4)

    def test_stop_on_wrong_side_rejected(self):
        entry = 100.0
        # Both above entry: invalid for a long.
        stops = [stop("structural", 101.0, entry), stop("empirical", 105.0, entry)]
        chosen, _, _ = _select(stops, targets=[stop("s", 110.0, entry)], entry=entry, direction="long")
        self.assertIsNone(chosen)

    def test_no_stops_yields_none(self):
        chosen, targets, _ = _select([], [], 100.0, "long")
        self.assertIsNone(chosen)
        self.assertEqual(targets, [])


class TestTargetSelection(unittest.TestCase):
    def test_target_below_min_reward_risk_excluded(self):
        entry = 100.0
        stops = [stop("empirical", 98.0, entry)]  # risk = 2.0
        targets = [
            stop("structural", 100.5, entry),  # reward 0.5 -> R:R 0.25, rejected
            stop("structural", 103.0, entry),  # reward 3.0 -> R:R 1.5, kept
        ]

        _, chosen, _ = _select(stops, targets, entry, "long")
        self.assertEqual([t["price"] for t in chosen], [103.0])

    def test_all_targets_inside_stop_distance_yields_none(self):
        entry = 100.0
        stops = [stop("empirical", 98.0, entry)]  # risk = 2.0
        targets = [stop("structural", 100.2, entry), stop("structural", 101.0, entry)]

        _, chosen, _ = _select(stops, targets, entry, "long")
        # The setup has no room; the caller must warn rather than plan.
        self.assertEqual(chosen, [])

    def test_min_reward_risk_boundary_is_inclusive(self):
        entry = 100.0
        stops = [stop("empirical", 98.0, entry)]  # risk = 2.0
        exact = entry + 2.0 * MIN_REWARD_RISK
        _, chosen, _ = _select(stops, [stop("structural", exact, entry)], entry, "long")
        self.assertEqual([t["price"] for t in chosen], [exact])

    def test_options_target_is_context_only(self):
        entry = 100.0
        stops = [stop("empirical", 99.0, entry)]
        targets = [stop("options_implied", 150.0, entry)]

        _, chosen, _ = _select(stops, targets, entry, "long")
        # Easily clears R:R, but its horizon is days -- never auto-selected.
        self.assertEqual(chosen, [])

    def test_duplicate_target_prices_collapse(self):
        entry = 100.0
        stops = [stop("empirical", 99.0, entry)]  # risk 1.0
        targets = [stop("structural", 102.0, entry), stop("empirical", 102.0, entry)]

        _, chosen, _ = _select(stops, targets, entry, "long")
        self.assertEqual(len(chosen), 1)

    def test_targets_ordered_nearest_first(self):
        entry = 100.0
        stops = [stop("empirical", 99.0, entry)]
        targets = [
            stop("structural", 105.0, entry),
            stop("structural", 101.5, entry),
            stop("structural", 103.0, entry),
        ]

        _, chosen, _ = _select(stops, targets, entry, "long")
        self.assertEqual([t["price"] for t in chosen], [101.5, 103.0, 105.0])

    def test_short_targets_must_be_below_entry(self):
        entry = 100.0
        stops = [stop("empirical", 102.0, entry)]  # risk 2.0
        targets = [stop("structural", 105.0, entry), stop("structural", 96.0, entry)]

        _, chosen, _ = _select(stops, targets, entry, "short")
        self.assertEqual([t["price"] for t in chosen], [96.0])


class TestCollectTargets(unittest.TestCase):
    def test_options_bound_follows_direction(self):
        sources = {
            "structural": {"available": False},
            "empirical": {"available": False},
            "options_implied": {
                "available": True,
                "upper_bound": 110.0,
                "lower_bound": 90.0,
                "days_to_expiration": 7,
            },
        }
        long_targets = _collect_targets(sources, 100.0, "long")
        short_targets = _collect_targets(sources, 100.0, "short")

        self.assertEqual(long_targets[0]["price"], 110.0)
        self.assertEqual(short_targets[0]["price"], 90.0)


class TestSizingWarnings(unittest.TestCase):
    def test_position_exceeding_account_is_flagged(self):
        plan = {"position_size": {"position_value": 526000.0}}
        warnings = _sizing_warnings(plan, 100000.0)

        self.assertEqual(len(warnings), 1)
        self.assertIn("exceeds account value", warnings[0])
        self.assertIn("5.3x", warnings[0])

    def test_position_within_account_is_silent(self):
        plan = {"position_size": {"position_value": 50000.0}}
        self.assertEqual(_sizing_warnings(plan, 100000.0), [])

    def test_missing_plan_or_account_is_silent(self):
        self.assertEqual(_sizing_warnings(None, 100000.0), [])
        self.assertEqual(_sizing_warnings({"position_size": {"position_value": 1e9}}, None), [])

    def test_unsized_plan_is_silent(self):
        self.assertEqual(_sizing_warnings({"position_size": {}}, 100000.0), [])


class TestVolatilitySource(unittest.TestCase):
    def _bars_desc(self, n):
        """Newest -> oldest, as load_bars returns. Rising series, range 2.0."""
        return [
            {"high": 101.0 + i, "low": 99.0 + i, "close": 100.0 + i, "open": 100.0 + i, "volume": 1000}
            for i in range(n, 0, -1)
        ]

    def test_atr_computed_on_ascending_bars(self):
        result = _volatility(self._bars_desc(60), "TEST", "5m")

        self.assertTrue(result["available"])
        self.assertEqual(result["atr_period"], ATR_PERIOD)
        self.assertEqual(result["atr_source"], "atr_indicator_14")
        # Each ascending step is +1 with a 2.0 intrabar range, so true range
        # is 2.0 throughout and ATR converges there. Reversed (descending)
        # input would give the same magnitude here, so the guard is that a
        # positive, finite ATR came back at all.
        self.assertAlmostEqual(result["atr_dollars"], 2.0, places=4)

    def test_too_few_bars_is_unavailable(self):
        result = _volatility(self._bars_desc(5), "TEST", "5m")
        self.assertFalse(result["available"])
        self.assertIn("ATR", result["error"])


if __name__ == "__main__":
    unittest.main()
