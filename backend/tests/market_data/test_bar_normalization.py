"""
Tests for _normalize_1h_bar / _normalize_1d_bar in ingestion_service.py.

_normalize_1d_bar floors a provider's daily bar onto the canonical 00:00
anchor, so different providers' conventions collide on the same (symbol,
timeframe, timestamp) unique key instead of silently creating a second row
for the same day. Coverage here would have caught the 2026-09-09 bug where
webull (00:00) and yahoo_finance (09:30) 1d bars never collided — 63% of all
stored 1d rows ended up duplicated, with close prices differing by up to
2.4% between the two rows for the same trading day.

_normalize_1h_bar keeps only bars that start on the hour (MD-01).
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.market_data.services.ingestion_service import (
    _normalize_1d_bar,
    _normalize_1h_bar,
)
from backend.models.market_data import Bar, DataStatus


def _bar(ts: datetime, timeframe: str = "1h") -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe=timeframe,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        timestamp=ts,
        provider="test",
        data_status=DataStatus.HISTORICAL,
    )


class TestNormalize1hBar(unittest.TestCase):
    """MD-01: stored 1h bars cover a clock hour. A provider bar that starts on the
    half hour (Webull, Yahoo: 09:30-10:30) straddles two clock hours, so it is
    skipped rather than floored onto the earlier one."""

    def test_clean_hour_boundary_passes_through_unchanged(self):
        """Alpaca-style :00 bars are returned as-is (same object)."""
        bar = _bar(datetime(2025, 1, 2, 14, 0, 0))
        result = _normalize_1h_bar(bar, "AlpacaProvider")
        self.assertIs(result, bar)
        self.assertEqual(result.timestamp, datetime(2025, 1, 2, 14, 0, 0))

    def test_kept_bar_gets_the_session_of_its_clock_hour(self):
        """upsert_bars leaves 1h sessions to the writer; the 09:00 hour spans the open."""
        cases = {9: "mixed", 10: "regular", 8: "premarket", 16: "after_hours"}
        for hour, session in cases.items():
            with self.subTest(hour=hour):
                bar = _normalize_1h_bar(_bar(datetime(2025, 1, 2, hour, 0, 0)), "AlpacaProvider")
                self.assertEqual(bar.session, session)

    def test_half_hour_bar_is_skipped_not_floored(self):
        """Flooring stored the 09:30-10:30 bar as 09:00, 30 minutes early."""
        for provider in ("WebullProvider", "YFinanceProvider"):
            with self.subTest(provider=provider):
                bar = _bar(datetime(2025, 1, 2, 9, 30, 0))
                self.assertIsNone(_normalize_1h_bar(bar, provider))

    def test_unexpected_offset_is_skipped(self):
        """A bar that doesn't start on the hour is dropped, not
        silently mis-bucketed onto the wrong hour."""
        bar = _bar(datetime(2025, 1, 2, 14, 17, 0))
        result = _normalize_1h_bar(bar, "SomeOtherProvider")
        self.assertIsNone(result)


class TestNormalize1dBar(unittest.TestCase):
    def test_clean_midnight_passes_through_unchanged(self):
        """Webull/Alpaca-style 00:00 bars are returned as-is (same object)."""
        bar = _bar(datetime(2025, 1, 2, 0, 0, 0), timeframe="1d")
        result = _normalize_1d_bar(bar, "WebullProvider")
        self.assertIs(result, bar)
        self.assertEqual(result.timestamp, datetime(2025, 1, 2, 0, 0, 0))

    def test_rth_open_offset_floors_to_midnight(self):
        """yahoo_finance-style 09:30 bars are floored to 00:00 of the same
        calendar day (not a UTC-conversion floor that could shift dates)."""
        bar = _bar(datetime(2025, 1, 2, 9, 30, 0), timeframe="1d")
        result = _normalize_1d_bar(bar, "yahoo_finance")
        self.assertIsNotNone(result)
        self.assertEqual(result.timestamp, datetime(2025, 1, 2, 0, 0, 0))
        self.assertEqual(result.close, bar.close)
        self.assertEqual(result.volume, bar.volume)

    def test_webull_and_yahoo_finance_bars_collide_on_same_key(self):
        """Regression test for the 2026-09-09 bug: webull's 00:00 bar and
        yahoo_finance's 09:30 bar for the same trading day must normalize
        to the identical timestamp so they upsert onto one row."""
        webull = _normalize_1d_bar(_bar(datetime(2025, 1, 2, 0, 0, 0), "1d"), "WebullProvider")
        yahoo = _normalize_1d_bar(_bar(datetime(2025, 1, 2, 9, 30, 0), "1d"), "yahoo_finance")
        self.assertEqual(webull.timestamp, yahoo.timestamp)

    def test_offset_does_not_shift_across_a_date_boundary(self):
        """Flooring hour/minute/second must not change the calendar date —
        a naive UTC-based floor could shift 09:30 ET back a day depending
        on the offset; this must stay on the same date."""
        bar = _bar(datetime(2025, 1, 2, 9, 30, 0), timeframe="1d")
        result = _normalize_1d_bar(bar, "yahoo_finance")
        self.assertEqual(result.timestamp.date(), datetime(2025, 1, 2).date())

    def test_unexpected_offset_is_skipped(self):
        """A bar that matches neither known convention is dropped, not
        silently mis-bucketed onto the wrong day (e.g. Alpaca's 13:30 ET
        free-tier noise bar)."""
        bar = _bar(datetime(2025, 1, 2, 13, 30, 0), timeframe="1d")
        result = _normalize_1d_bar(bar, "AlpacaProvider")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
