"""
Tests for ``backend.backtesting.experiment_runner`` helpers.

``_split_slices`` and ``_aggregate_metrics`` are pure functions with no
I/O and can be tested directly. Full integration (running an experiment
end-to-end) is covered by the API integration tests.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.backtesting.experiment_runner import (
    ExperimentConfig,
    _aggregate_metrics,
    _split_slices,
)
from backend.backtesting.parameters import ExperimentParameters


class TestSplitSlices(unittest.TestCase):
    """Verify the 3-way IS/Val/OOS date split is proportional."""

    def test_default_20_20_split(self):
        start = datetime(2025, 1, 1)
        end   = datetime(2025, 12, 31)
        slices = _split_slices(start, end, n_splits=3, val_pct=0.20, oos_pct=0.20)
        names = [s[0] for s in slices]
        self.assertEqual(names, ["in_sample", "validation", "out_of_sample"])

    def test_slices_are_contiguous(self):
        start = datetime(2025, 1, 1)
        end   = datetime(2025, 6, 1)
        slices = _split_slices(start, end, n_splits=3, val_pct=0.20, oos_pct=0.20)
        is_end   = slices[0][2]
        val_end  = slices[1][2]
        oos_end  = slices[2][2]
        self.assertEqual(is_end,   slices[1][1])   # IS.end == Val.start
        self.assertEqual(val_end,   slices[2][1])   # Val.end == OOS.start
        self.assertEqual(oos_end - start, (end - start) / 3)  # OOS.end = window boundary

    def test_empty_range_returns_empty_list(self):
        result = _split_slices(datetime(2025, 1, 1), datetime(2025, 1, 1), 3, 0.2, 0.2)
        self.assertEqual(result, [])

    def test_val_pct_zero_no_validation_slice(self):
        # When val_pct=0, the validation slice is 0-length but still present.
        start = datetime(2025, 1, 1)
        end   = datetime(2025, 12, 31)
        slices = _split_slices(start, end, n_splits=3, val_pct=0.0, oos_pct=0.2)
        # val_end == is_end (zero-length slice)
        self.assertEqual(slices[0][0], "in_sample")
        self.assertEqual(slices[1][0], "validation")
        self.assertEqual(slices[1][1], slices[0][2])   # val_start == is_end


class TestAggregateMetrics(unittest.TestCase):
    """Verify per-slice metric aggregation across multiple runs."""

    def _mock_run(self, win_rate, avg_return, sharpe, total_signals):
        run = MagicMock()
        run.win_rate_1d    = win_rate
        run.avg_return_1d  = avg_return
        run.avg_return_5d  = None
        run.avg_return_20d = None
        run.median_return_1d = None
        run.sharpe_ratio   = sharpe
        run.profit_factor  = None
        run.max_drawdown   = None
        run.signal_frequency = None
        run.total_signals  = total_signals
        return run

    def test_mean_win_rate_across_runs(self):
        runs = [
            self._mock_run(0.50, 0.5, 1.0, 20),
            self._mock_run(0.60, 0.6, 1.2, 30),
            self._mock_run(0.70, 0.7, 1.4, 10),
        ]
        result = _aggregate_metrics(runs)
        self.assertAlmostEqual(result["win_rate_1d"], 0.60)
        self.assertAlmostEqual(result["avg_return_1d"], 0.60)

    def test_total_signals_summed(self):
        runs = [
            self._mock_run(0.50, 0.5, 1.0, 20),
            self._mock_run(0.60, 0.6, 1.2, 30),
        ]
        result = _aggregate_metrics(runs)
        self.assertEqual(result["total_signals"], 50)

    def test_missing_metric_returns_none(self):
        runs = [
            self._mock_run(0.50, None, 1.0, 20),
            self._mock_run(0.60, None, 1.2, 30),
        ]
        result = _aggregate_metrics(runs)
        self.assertIsNone(result["avg_return_1d"])

    def test_none_run_skipped(self):
        runs = [
            self._mock_run(0.50, 0.5, 1.0, 20),
            None,
            self._mock_run(0.60, 0.6, 1.2, 30),
        ]
        result = _aggregate_metrics(runs)
        self.assertAlmostEqual(result["win_rate_1d"], 0.55)


class TestExperimentConfigValidation(unittest.TestCase):
    """``ExperimentConfig`` raises on bad inputs."""

    def test_val_pct_plus_oos_pct_must_be_less_than_1(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(
                name="test",
                symbols=["AAPL"],
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 6, 1),
                signals=["RSI_OVERSOLD"],
                parameters=ExperimentParameters(),
                val_pct=0.6,
                oos_pct=0.5,
            )

    def test_negative_pct_rejected(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(
                name="test",
                symbols=["AAPL"],
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 6, 1),
                signals=["RSI_OVERSOLD"],
                parameters=ExperimentParameters(),
                val_pct=-0.1,
                oos_pct=0.2,
            )

    def test_n_splits_must_be_at_least_2(self):
        with self.assertRaises(ValueError):
            ExperimentConfig(
                name="test",
                symbols=["AAPL"],
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 6, 1),
                signals=["RSI_OVERSOLD"],
                parameters=ExperimentParameters(),
                n_splits=1,
            )


if __name__ == "__main__":
    unittest.main()
