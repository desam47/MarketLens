"""
Tests for ``backend.backtesting.overfit``.

Validates ``compute_overfit_report`` against known cases:
healthy strategy, suspicious IS results, OOS degradation, and sign flips.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.backtesting.overfit import (
    OverfitReport,
    compute_overfit_report,
)


def _metrics(
    win_rate: float | None = None,
    avg_return_1d: float | None = None,
    sharpe: float | None = None,
    total_signals: int | None = None,
    **kwargs,
) -> dict:
    return {
        "win_rate_1d": win_rate,
        "avg_return_1d": avg_return_1d,
        "avg_return_5d": kwargs.get("avg_return_5d"),
        "avg_return_20d": kwargs.get("avg_return_20d"),
        "median_return_1d": kwargs.get("median_return_1d"),
        "sharpe_ratio": sharpe,
        "profit_factor": kwargs.get("profit_factor"),
        "max_drawdown": kwargs.get("max_drawdown"),
        "signal_frequency": kwargs.get("signal_frequency"),
        "total_signals": total_signals,
    }


class TestOverfitReportHealthy(unittest.TestCase):
    """A normal strategy: IS and OOS performance are close."""

    def test_healthy_strategy_score_near_zero(self):
        report = compute_overfit_report(
            _metrics(win_rate=0.55, sharpe=1.1, total_signals=100),
            _metrics(win_rate=0.53, sharpe=1.0, total_signals=95),
            _metrics(win_rate=0.52, sharpe=0.95, total_signals=90),
        )
        self.assertLess(report.score, 0.5)
        self.assertTrue(report.is_stable)
        self.assertEqual(len(report.warnings), 0)


class TestOverfitReportHighWinRateIS(unittest.TestCase):
    """IS win rate above 70% is a red flag even if OOS is reasonable."""

    def test_high_is_win_rate_adds_score(self):
        report = compute_overfit_report(
            _metrics(win_rate=0.80, sharpe=1.5, total_signals=80),
            _metrics(win_rate=0.52, sharpe=1.0, total_signals=75),
            _metrics(win_rate=0.50, sharpe=0.9, total_signals=70),
        )
        # IS win_rate 80% > threshold 70% → +0.3
        self.assertGreaterEqual(report.score, 0.3)


class TestOverfitReportSharpeDrop(unittest.TestCase):
    """Sharpe ratio collapse from IS to OOS signals overfitting."""

    def test_high_sharpe_is_with_drop_to_oos(self):
        report = compute_overfit_report(
            _metrics(win_rate=0.60, sharpe=2.5, total_signals=100),
            _metrics(win_rate=0.55, sharpe=1.5, total_signals=95),
            _metrics(win_rate=0.50, sharpe=0.3, total_signals=90),
        )
        # IS Sharpe > 2.0: +0.3
        # IS→OOS Sharpe ratio > 2.0: +0.4
        self.assertGreaterEqual(report.score, 0.7)
        self.assertFalse(report.is_stable)


class TestOverfitReportSignFlip(unittest.TestCase):
    """Return flips from positive to negative across splits = overfit."""

    def test_return_sign_flip_is_to_oos(self):
        report = compute_overfit_report(
            _metrics(win_rate=0.60, avg_return_1d=1.5, sharpe=1.2, total_signals=80),
            _metrics(win_rate=0.55, avg_return_1d=1.0, sharpe=1.0, total_signals=75),
            _metrics(win_rate=0.45, avg_return_1d=-0.8, sharpe=-0.5, total_signals=70),
        )
        # Return sign flip IS→OOS: +0.5
        self.assertGreaterEqual(report.score, 0.5)


class TestOverfitReportLowSignalCount(unittest.TestCase):
    """Few signals → high variance → instability flagged."""

    def test_low_signal_count_warns(self):
        report = compute_overfit_report(
            _metrics(win_rate=0.70, sharpe=1.8, total_signals=5),
            _metrics(win_rate=0.50, sharpe=0.9, total_signals=4),
            _metrics(win_rate=0.45, sharpe=0.7, total_signals=3),
        )
        # Each slice: < 10 signals → +0.1 each
        self.assertGreaterEqual(report.score, 0.3)
        self.assertTrue(
            any("signals" in w.lower() for w in report.warnings),
            f"Expected low-signal warning, got: {report.warnings}",
        )


class TestOverfitReportMissingMetrics(unittest.TestCase):
    """Partial metrics (None values) should not crash."""

    def test_none_win_rate_does_not_crash(self):
        report = compute_overfit_report(
            _metrics(win_rate=None, sharpe=1.0, total_signals=50),
            _metrics(win_rate=None, sharpe=0.9, total_signals=45),
            _metrics(win_rate=None, sharpe=0.8, total_signals=40),
        )
        self.assertIsInstance(report, OverfitReport)
        self.assertIsInstance(report.score, (int, float))


class TestOverfitReportOosMetricsAdded(unittest.TestCase):
    """compute_overfit_report returns oos_sharpe_ratio and oos_win_rate_gap."""

    def test_oos_metrics_populated(self):
        report = compute_overfit_report(
            _metrics(win_rate=0.60, sharpe=1.5, total_signals=100),
            _metrics(win_rate=0.55, sharpe=1.2, total_signals=95),
            _metrics(win_rate=0.52, sharpe=1.0, total_signals=90),
        )
        self.assertEqual(report.oos_sharpe_ratio, 1.5)
        self.assertAlmostEqual(report.oos_win_rate_gap, 0.08, places=6)


if __name__ == "__main__":
    unittest.main()
