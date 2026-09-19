"""
Coverage of the hardcoded NYSE holiday table.

It used to hold 2024-2026 only, but the stored daily history starts 2023-09-18, so
Thanksgiving and Christmas 2023 -- and the 2025-01-09 National Day of Mourning, a special
full closure -- read as trading days. Every backfill's gap check then reported those three
dates as unfillable missing bars, so every job ended ``partial`` (6,796 of 6,816 jobs). From
2027-01-01 it would have treated EVERY holiday as a trading day.

The table is checked against an independent, rule-based generator so a typo (or a wrong
observed-holiday shift) fails here instead of silently misclassifying a session.
"""
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.engines import market_calendar as mc
from backend.engines.market_calendar import USMarketCalendar

_ET = ZoneInfo("America/New_York")
# Full-day closures outside the standing holiday rules.
_SPECIAL_CLOSURES = {date(2025, 1, 9)}          # National Day of Mourning (President Carter)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    w = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * w) // 451
    month, day = divmod(h + w - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date, *, new_year: bool = False) -> date | None:
    """NYSE rule: a Saturday holiday closes the Friday before (except New Year's Day, which
    does not); a Sunday holiday closes the Monday after."""
    if d.weekday() == 5:
        return None if new_year else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _rule_based_holidays(year: int) -> set[date]:
    days = {
        _observed(date(year, 1, 1), new_year=True),
        _nth_weekday(year, 1, 0, 3),                    # MLK Day: 3rd Monday of January
        _nth_weekday(year, 2, 0, 3),                    # Presidents' Day: 3rd Monday of February
        _easter(year) - timedelta(days=2),              # Good Friday
        _last_weekday(year, 5, 0),                      # Memorial Day: last Monday of May
        _observed(date(year, 6, 19)),                   # Juneteenth
        _observed(date(year, 7, 4)),                    # Independence Day
        _nth_weekday(year, 9, 0, 1),                    # Labor Day: 1st Monday of September
        _nth_weekday(year, 11, 3, 4),                   # Thanksgiving: 4th Thursday of November
        _observed(date(year, 12, 25)),                  # Christmas
    }
    return {d for d in days if d is not None}


class TestHolidayTable(unittest.TestCase):
    def test_matches_the_rule_based_calendar_for_every_covered_year(self):
        expected = set(_SPECIAL_CLOSURES)
        for year in mc._HOLIDAY_YEARS:
            expected |= _rule_based_holidays(year)
        self.assertEqual(sorted(mc._NYSE_HOLIDAYS - expected), [], "in the table but not a holiday")
        self.assertEqual(sorted(expected - mc._NYSE_HOLIDAYS), [], "a holiday missing from the table")

    def test_every_entry_is_a_weekday_inside_the_covered_years(self):
        for d in mc._NYSE_HOLIDAYS:
            self.assertLess(d.weekday(), 5, d)
            self.assertIn(d.year, mc._HOLIDAY_YEARS, d)

    def test_the_three_dates_every_backfill_reported_as_gaps_are_closed(self):
        cal = USMarketCalendar()
        for d in (date(2023, 11, 23), date(2023, 12, 25), date(2025, 1, 9)):
            self.assertFalse(cal.is_trading_day(datetime(d.year, d.month, d.day, 12, tzinfo=_ET)), d)

    def test_2027_observed_holidays(self):
        cal = USMarketCalendar()
        for d in (date(2027, 6, 18), date(2027, 7, 5), date(2027, 12, 24), date(2027, 1, 1)):
            self.assertFalse(cal.is_trading_day(datetime(d.year, d.month, d.day, 12, tzinfo=_ET)), d)
        # ...and the ordinary days around them are open.
        for d in (date(2027, 1, 4), date(2027, 6, 17), date(2027, 7, 6), date(2027, 12, 23)):
            self.assertTrue(cal.is_trading_day(datetime(d.year, d.month, d.day, 12, tzinfo=_ET)), d)

    def test_the_gap_detector_no_longer_expects_bars_on_those_days(self):
        from backend.repositories.bar_repository import expected_bar_timestamps

        for start, holiday in ((datetime(2023, 11, 20), date(2023, 11, 23)),
                               (datetime(2023, 12, 20), date(2023, 12, 25)),
                               (datetime(2025, 1, 6), date(2025, 1, 9))):
            got = {t.date() for t in expected_bar_timestamps("AAPL", "1d", start, start + timedelta(days=8))}
            self.assertNotIn(holiday, got)
            self.assertTrue(got, "the window must still contain trading days")


class TestStaleTableWarning(unittest.TestCase):
    def test_warns_once_the_year_is_no_longer_covered(self):
        with self.assertLogs("backend.engines.market_calendar", "WARNING") as cm:
            self.assertTrue(mc._warn_if_holiday_table_is_stale(date(2028, 1, 3)))
        self.assertIn("2028", cm.records[0].getMessage())

    def test_silent_inside_the_covered_years(self):
        for d in (date(2023, 9, 18), date(2026, 9, 19), date(2027, 12, 31)):
            with self.assertNoLogs("backend.engines.market_calendar", "WARNING"):
                self.assertFalse(mc._warn_if_holiday_table_is_stale(d))


if __name__ == "__main__":
    unittest.main()
