"""
Tests for backend/analysis/series.py — bar-loading and indicator-series
helpers extracted from backend/api/analysis/router.py so non-router
code (backend.ai.context.build_context()) can reuse them.

These mirror the coverage backend/tests/api/test_analysis_router.py
already has for the same logic via HTTP, but exercise the functions
directly now that they're independently importable.
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from backend.analysis.series import (
    bar_dicts_to_arrays,
    load_bars,
    macd_histogram_series,
    rsi_series,
)


class TestLoadBars(unittest.TestCase):

    @patch("backend.analysis.series.bar_repository")
    def test_reshapes_bar_rows_to_dicts(self, mock_repo):
        mock_repo.get_bars.return_value = [
            MagicMock(
                open=100.0, high=101.0, low=99.0, close=100.5,
                volume=1_000_000, timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                source="raw", data_status=MagicMock(value="historical"),
            )
        ]
        bars = load_bars("AAPL", "1d", limit=10)
        self.assertEqual(len(bars), 1)
        b = bars[0]
        self.assertEqual(b["open"], 100.0)
        self.assertEqual(b["close"], 100.5)
        self.assertEqual(b["source"], "raw")
        self.assertEqual(b["data_status"], "historical")

    @patch("backend.analysis.series.bar_repository")
    def test_data_status_without_value_attr_passed_through(self, mock_repo):
        """A plain string data_status (no .value) must not crash the
        hasattr(...) guard."""
        mock_repo.get_bars.return_value = [
            MagicMock(
                open=1, high=1, low=1, close=1, volume=1,
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                source="raw", data_status="historical",
            )
        ]
        bars = load_bars("AAPL", "1d")
        self.assertEqual(bars[0]["data_status"], "historical")

    @patch("backend.analysis.series.bar_repository")
    def test_closes_db_session_even_on_error(self, mock_repo):
        mock_repo.get_bars.side_effect = RuntimeError("boom")
        with patch("backend.analysis.series.SessionLocal") as mock_session_cls:
            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db
            with self.assertRaises(RuntimeError):
                load_bars("AAPL", "1d")
            mock_db.close.assert_called_once()


class TestBarDictsToArrays(unittest.TestCase):

    def test_empty_input_returns_empty_arrays(self):
        arrays = bar_dicts_to_arrays([])
        self.assertEqual(arrays["closes"], [])
        self.assertEqual(arrays["timestamps"], [])

    def test_converts_to_parallel_arrays(self):
        bars = [
            {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100, "timestamp": "t1"},
            {"open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 200, "timestamp": "t2"},
        ]
        arrays = bar_dicts_to_arrays(bars)
        self.assertEqual(arrays["closes"], [1.5, 2.5])
        self.assertEqual(arrays["volumes"], [100, 200])
        self.assertEqual(arrays["timestamps"], ["t1", "t2"])


class TestRsiSeries(unittest.TestCase):

    def test_short_series_stays_neutral(self):
        closes = [100.0] * 5
        out = rsi_series(closes, period=14)
        self.assertEqual(out, [50.0] * 5)

    def test_sustained_uptrend_pushes_rsi_high(self):
        closes = [100 + i for i in range(30)]
        out = rsi_series(closes, period=14)
        self.assertGreater(out[-1], 70.0)

    def test_sustained_downtrend_pushes_rsi_low(self):
        closes = [100 - i for i in range(30)]
        out = rsi_series(closes, period=14)
        self.assertLess(out[-1], 30.0)


class TestMacdHistogramSeries(unittest.TestCase):

    def test_short_series_returns_zeros(self):
        closes = [100.0] * 10
        out = macd_histogram_series(closes, fast=12, slow=26, signal=9)
        self.assertEqual(out, [0.0] * 10)

    def test_returns_one_value_per_bar(self):
        closes = [100 + i * 0.1 for i in range(60)]
        out = macd_histogram_series(closes, fast=12, slow=26, signal=9)
        self.assertEqual(len(out), 60)

    def test_sustained_uptrend_yields_positive_histogram(self):
        closes = [100 + i * 0.5 for i in range(60)]
        out = macd_histogram_series(closes, fast=12, slow=26, signal=9)
        self.assertGreater(out[-1], 0.0)


if __name__ == "__main__":
    unittest.main()
