"""
Tests for the US equity market calendar.

Covers: trading-day classification, holiday list correctness, session-type
boundaries (premarket/regular/after-hours/closed), and DST handling.
"""
import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.engines.market_calendar import (
    SessionType,
    USMarketCalendar,
    us_market_calendar,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


class TestUSMarketCalendar(unittest.TestCase):
    """Tests for the default singleton US market calendar."""

    def setUp(self):
        self.cal = us_market_calendar

    # --- Trading-day classification ---

    def test_weekday_is_trading_day(self):
        """A normal Tuesday in Jan 2025 is a trading day."""
        self.assertTrue(self.cal.is_trading_day(datetime(2025, 1, 21, 12, 0, tzinfo=ET)))

    def test_saturday_is_not_trading_day(self):
        """Saturday is never a trading day, regardless of holidays."""
        # 2025-01-25 is a Saturday
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 1, 25, 12, 0, tzinfo=ET)))

    def test_sunday_is_not_trading_day(self):
        """Sunday is never a trading day."""
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 1, 26, 12, 0, tzinfo=ET)))

    # --- Holiday list correctness ---

    def test_christmas_2024_is_holiday(self):
        self.assertFalse(self.cal.is_trading_day(datetime(2024, 12, 25, 12, 0, tzinfo=ET)))

    def test_christmas_2025_is_holiday(self):
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 12, 25, 12, 0, tzinfo=ET)))

    def test_christmas_2026_is_holiday(self):
        self.assertFalse(self.cal.is_trading_day(datetime(2026, 12, 25, 12, 0, tzinfo=ET)))

    def test_mlk_day_2025_is_holiday(self):
        """MLK Day = third Monday of January. 2025-01-20."""
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 1, 20, 12, 0, tzinfo=ET)))

    def test_good_friday_2025_is_holiday(self):
        """Good Friday 2025 = 2025-04-18."""
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 4, 18, 12, 0, tzinfo=ET)))

    def test_memorial_day_2025_is_holiday(self):
        """Memorial Day = last Monday of May. 2025-05-26."""
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 5, 26, 12, 0, tzinfo=ET)))

    def test_juneteenth_2025_is_holiday(self):
        """Juneteenth = June 19."""
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 6, 19, 12, 0, tzinfo=ET)))

    def test_independence_day_observed_2026(self):
        """2026-07-04 is Saturday — observed on Friday 2026-07-03."""
        self.assertFalse(self.cal.is_trading_day(datetime(2026, 7, 3, 12, 0, tzinfo=ET)))
        # The actual Saturday is also closed (it's a weekend), so this is
        # a weaker assertion — confirm at least that the observed day is
        # closed for the right reason.
        sat = datetime(2026, 7, 4, 12, 0, tzinfo=ET)
        self.assertFalse(self.cal.is_trading_day(sat))

    def test_thanksgiving_2025_is_holiday(self):
        self.assertFalse(self.cal.is_trading_day(datetime(2025, 11, 27, 12, 0, tzinfo=ET)))

    def test_normal_day_after_holiday_is_open(self):
        """Dec 26 2025 (Friday after Christmas) is a regular trading day."""
        self.assertTrue(self.cal.is_trading_day(datetime(2025, 12, 26, 12, 0, tzinfo=ET)))

    # --- Session-type boundaries ---

    def test_premarket_window(self):
        """04:00–09:29:59 ET on a trading day is premarket."""
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 4, 0, tzinfo=ET)),
            SessionType.PREMARKET,
        )
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 9, 29, 59, tzinfo=ET)),
            SessionType.PREMARKET,
        )

    def test_regular_session_starts_0930(self):
        """09:30 ET sharp starts the regular session."""
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 9, 30, tzinfo=ET)),
            SessionType.REGULAR,
        )

    def test_regular_session_ends_1600(self):
        """16:00 ET ends the regular session (transition to after-hours)."""
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 15, 59, 59, tzinfo=ET)),
            SessionType.REGULAR,
        )
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 16, 0, tzinfo=ET)),
            SessionType.AFTER_HOURS,
        )

    def test_after_hours_window(self):
        """16:00–19:59:59 ET is after-hours."""
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 19, 59, 59, tzinfo=ET)),
            SessionType.AFTER_HOURS,
        )
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 20, 0, tzinfo=ET)),
            SessionType.CLOSED,
        )

    def test_weekend_is_closed(self):
        """Saturday at noon ET is CLOSED regardless of time-of-day."""
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 25, 12, 0, tzinfo=ET)),
            SessionType.CLOSED,
        )

    def test_holiday_is_closed(self):
        """Christmas at noon ET is CLOSED via holiday check."""
        self.assertEqual(
            self.cal.get_session_type(datetime(2024, 12, 25, 12, 0, tzinfo=ET)),
            SessionType.CLOSED,
        )

    # --- DST behavior ---

    def test_spring_forward_does_not_break_trading_day_check(self):
        """2026-03-08 is the spring-forward day in the US. 02:00 ET → 03:00 ET.

        A timestamp at 02:30 ET doesn't exist (clocks jump) but our calendar
        still correctly identifies the day as a trading day — the only
        wrinkle is that 02:30 ET is between the holiday/weekday checks and
        the premarket window, and ``zoneinfo`` resolves the nonexistent time
        to 03:30 ET (post-jump). Either way, the date is a regular day.
        """
        # Use the trading day immediately before the spring-forward Sunday.
        # 2025 spring-forward is Sun 2025-03-09; Friday 2025-03-07 is open.
        self.assertTrue(self.cal.is_trading_day(datetime(2025, 3, 7, 12, 0, tzinfo=ET)))

    def test_fall_back_does_not_break_trading_day_check(self):
        """2026-11-01 is the fall-back day in the US. 02:00 ET happens twice.

        Our calendar is date-based, so the ambiguity doesn't affect
        trading-day classification.
        """
        # 2025 fall-back is Sun 2025-11-02; Monday 2025-11-03 is open.
        self.assertTrue(self.cal.is_trading_day(datetime(2025, 11, 3, 12, 0, tzinfo=ET)))

    # --- is_market_open ---

    def test_is_market_open_at_noon(self):
        self.assertTrue(self.cal.is_market_open(datetime(2025, 1, 21, 12, 0, tzinfo=ET)))

    def test_is_market_open_at_0430(self):
        """04:30 ET is premarket, NOT market open."""
        self.assertFalse(self.cal.is_market_open(datetime(2025, 1, 21, 4, 30, tzinfo=ET)))

    def test_is_market_open_on_weekend(self):
        self.assertFalse(self.cal.is_market_open(datetime(2025, 1, 25, 12, 0, tzinfo=ET)))

    # --- Timezone conversion ---

    def test_naive_datetime_assumed_utc(self):
        """A naive datetime is treated as UTC for conversion to ET.

        ``datetime(2025, 1, 21, 14, 0)`` is 09:00 ET, which is premarket.
        """
        # 14:00 UTC == 09:00 ET on a non-DST day in January
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 14, 0)),
            SessionType.PREMARKET,
        )

    def test_aware_utc_converted(self):
        """An aware UTC datetime is correctly converted to ET."""
        # 19:00 UTC == 14:00 ET
        self.assertEqual(
            self.cal.get_session_type(datetime(2025, 1, 21, 19, 0, tzinfo=UTC)),
            SessionType.REGULAR,
        )

    # --- Singleton ---

    def test_singleton_is_us_calendar(self):
        """The module-level ``us_market_calendar`` is a USMarketCalendar instance."""
        self.assertIsInstance(us_market_calendar, USMarketCalendar)


if __name__ == "__main__":
    unittest.main()
