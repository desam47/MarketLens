"""
US equity (NYSE/NASDAQ) market calendar.

Single source of truth for trading-day and session classification logic. Uses
stdlib ``zoneinfo`` for DST handling — no third-party calendar packages.

The holiday list is hardcoded for 2024–2026 (the project's current date range);
extend ``_NYSE_HOLIDAYS`` when the project needs further years.

Why hardcoded instead of ``exchange_calendars`` or ``pandas_market_calendars``:
those are not installed in this project and would add a heavy dependency for a
list of 30 dates. The exchange rules themselves (weekend + holiday) are simple
and fully covered here.
"""
import logging
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# US/Eastern is the canonical equity session timezone. zoneinfo handles
# DST transitions (spring-forward 02:00 → 03:00 in March, fall-back
# 02:00 ← 01:00 in November) automatically.
EASTERN = ZoneInfo("America/New_York")


class SessionType(StrEnum):
    """Equity session classification for a given moment in US/Eastern time."""

    PREMARKET = "premarket"      # 04:00–09:30 ET
    REGULAR = "regular"          # 09:30–16:00 ET
    AFTER_HOURS = "after_hours"  # 16:00–20:00 ET
    CLOSED = "closed"            # weekend, holiday, or outside 04:00–20:00 ET


# Hardcoded NYSE holidays for 2024, 2025, and 2026.
# Sources: NYSE official trading-hours calendar for each year.
# Includes observed-on-Monday / observed-on-Friday rules for weekend holidays.
_NYSE_HOLIDAYS: frozenset[date] = frozenset({
    # 2024
    date(2024, 1, 1),    # New Year's Day (Mon)
    date(2024, 1, 15),   # Martin Luther King Jr. Day
    date(2024, 2, 19),   # Presidents' Day
    date(2024, 3, 29),   # Good Friday
    date(2024, 5, 27),   # Memorial Day
    date(2024, 6, 19),   # Juneteenth
    date(2024, 7, 4),    # Independence Day
    date(2024, 9, 2),    # Labor Day
    date(2024, 11, 28),  # Thanksgiving
    date(2024, 12, 25),  # Christmas
    # 2025
    date(2025, 1, 1),    # New Year's Day
    date(2025, 1, 20),   # MLK Day
    date(2025, 2, 17),   # Presidents' Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7, 4),    # Independence Day
    date(2025, 9, 1),    # Labor Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed — 7/4 is Saturday)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})


# Session boundaries in US/Eastern local time. These are intentionally plain
# ``time`` objects — ``datetime.combine`` + ``EASTERN`` does the timezone
# attachment. We use ``time`` instead of hardcoded UTC offsets so DST is
# handled implicitly.
_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_AFTER_HOURS_CLOSE = time(20, 0)


class USMarketCalendar:
    """US equity market calendar (NYSE / NASDAQ)."""

    def to_et(self, dt: datetime) -> datetime:
        """Convert a datetime to US/Eastern. Naive datetimes are assumed UTC.

        Zone-aware datetimes are converted via ``astimezone``; naive datetimes
        are localized as UTC first (the codebase's convention is to store
        timestamps in UTC at rest).
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(EASTERN)

    def is_trading_day(self, dt: datetime) -> bool:
        """Return True if ``dt`` falls on a weekday that is not a NYSE holiday.

        A datetime at midnight on a weekend or holiday is NOT a trading day,
        even though ``is_market_open()`` would also return False for a
        different reason. The two checks answer different questions.
        """
        et = self.to_et(dt)
        if et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        return et.date() not in _NYSE_HOLIDAYS

    def is_market_open(self, dt: datetime) -> bool:
        """Return True if the regular session is open at ``dt`` (US/Eastern)."""
        return self.get_session_type(dt) == SessionType.REGULAR

    def get_session_type(self, dt: datetime) -> SessionType:
        """Classify ``dt`` into one of the four session types.

        Logic, in order:
        1. If the date is a weekend or holiday → CLOSED.
        2. Otherwise map the time-of-day to PREMARKET / REGULAR / AFTER_HOURS.
        3. Outside 04:00–20:00 ET on a weekday → CLOSED (overnight).
        """
        et = self.to_et(dt)

        # Step 1: weekend or holiday → CLOSED.
        if et.weekday() >= 5 or et.date() in _NYSE_HOLIDAYS:
            return SessionType.CLOSED

        # Step 2: time-of-day mapping. Comparing ``time`` objects is independent
        # of date and timezone — both sides are plain local times.
        t = et.time()
        if t < _PREMARKET_OPEN:
            return SessionType.CLOSED
        if t < _REGULAR_OPEN:
            return SessionType.PREMARKET
        if t < _REGULAR_CLOSE:
            return SessionType.REGULAR
        if t < _AFTER_HOURS_CLOSE:
            return SessionType.AFTER_HOURS
        return SessionType.CLOSED


# Module-level singleton for the default US equity calendar.
# Engines default to this so the test seam (``calendar=...`` constructor arg)
# is purely an override path.
us_market_calendar = USMarketCalendar()
