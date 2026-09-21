"""
Tests for bar_repository.expected_bar_timestamps / find_gaps.

Companion to test_bar_repository.py's TestFindDuplicateCalendarBars — that
audit finds too MANY rows for a bucket, this one finds too FEW (zero).
Introduced alongside the RQ-based backfill pipeline's gap-check-and-fill
step (backend/market_data/services/backfill_service.py).
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.repositories.bar_repository import expected_bar_timestamps, find_gaps


class TestExpectedBarTimestamps(unittest.TestCase):
    def test_unsupported_timeframe_raises(self):
        with self.assertRaises(ValueError):
            expected_bar_timestamps("AAPL", "1wk", datetime(2026, 1, 2), datetime(2026, 1, 2))

    def test_1d_one_trading_day(self):
        # Friday 2026-01-02 is a trading day.
        out = expected_bar_timestamps(
            "AAPL", "1d", datetime(2026, 1, 2), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(out, [datetime(2026, 1, 2, 0, 0)])

    def test_1d_skips_weekend(self):
        # Fri 1/2 -> Mon 1/5, skipping Sat/Sun.
        out = expected_bar_timestamps(
            "AAPL", "1d", datetime(2026, 1, 2), datetime(2026, 1, 5, 23, 59)
        )
        self.assertEqual(out, [datetime(2026, 1, 2), datetime(2026, 1, 5)])

    def test_1d_skips_new_years_holiday(self):
        # 2026-01-01 is New Year's Day — not a trading day.
        out = expected_bar_timestamps(
            "AAPL", "1d", datetime(2025, 12, 31), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(
            out,
            [datetime(2025, 12, 31), datetime(2026, 1, 2)],
        )

    def test_1m_full_session_count(self):
        # Regular session is 09:30-16:00 ET = 390 minutes -> 390 1m buckets.
        out = expected_bar_timestamps(
            "AAPL", "1m", datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(len(out), 390)
        self.assertEqual(out[0], datetime(2026, 1, 2, 9, 30))
        self.assertEqual(out[-1], datetime(2026, 1, 2, 15, 59))

    def test_5m_bucket_count(self):
        # 390 minutes / 5 = 78 buckets.
        out = expected_bar_timestamps(
            "AAPL", "5m", datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(len(out), 78)
        self.assertEqual(out[0], datetime(2026, 1, 2, 9, 30))

    def test_1h_bucket_count_and_anchors(self):
        # 09:30-16:00 ET floored to the hour -> 9,10,11,12,13,14,15 = 7 buckets.
        out = expected_bar_timestamps(
            "AAPL", "1h", datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(len(out), 7)
        self.assertEqual(out[0], datetime(2026, 1, 2, 9, 0))
        self.assertEqual(out[-1], datetime(2026, 1, 2, 15, 0))

    def test_4h_bucket_count(self):
        # Session spans the 08:00-11:59 and 12:00-15:59 4h buckets -> 2.
        out = expected_bar_timestamps(
            "AAPL", "4h", datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(out, [datetime(2026, 1, 2, 8, 0), datetime(2026, 1, 2, 12, 0)])

    def test_trims_to_requested_range(self):
        # Range starts mid-session — only buckets from 10:00 on are expected.
        out = expected_bar_timestamps(
            "AAPL", "1h", datetime(2026, 1, 2, 10, 0), datetime(2026, 1, 2, 23, 59)
        )
        self.assertEqual(out[0], datetime(2026, 1, 2, 10, 0))
        self.assertNotIn(datetime(2026, 1, 2, 9, 0), out)


class TestFindGaps(unittest.TestCase):
    def test_no_gaps_when_all_expected_present(self):
        expected = [datetime(2026, 1, 2), datetime(2026, 1, 5)]

        class _FakeQuery:
            def filter(self, *a, **kw):
                return self

            def all(self):
                return [(ts,) for ts in expected]

        db = MagicMock()
        db.query.return_value = _FakeQuery()

        gaps = find_gaps(db, "AAPL", "1d", datetime(2026, 1, 2), datetime(2026, 1, 5))
        self.assertEqual(gaps, [])

    def test_reports_missing_timestamp(self):
        # Only 1/2 present; 1/5 (a trading day) is missing.
        class _FakeQuery:
            def filter(self, *a, **kw):
                return self

            def all(self):
                return [(datetime(2026, 1, 2),)]

        db = MagicMock()
        db.query.return_value = _FakeQuery()

        gaps = find_gaps(db, "AAPL", "1d", datetime(2026, 1, 2), datetime(2026, 1, 5))
        self.assertEqual(gaps, [datetime(2026, 1, 5)])

    def test_empty_range_returns_empty(self):
        db = MagicMock()
        # A weekend-only range has no expected 1d bars — find_gaps should
        # short-circuit before ever querying the DB.
        gaps = find_gaps(db, "AAPL", "1d", datetime(2026, 1, 3), datetime(2026, 1, 4))
        self.assertEqual(gaps, [])
        db.query.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
