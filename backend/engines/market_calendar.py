"""
US equity (NYSE/NASDAQ) market calendar.

Single source of truth for trading-day and session classification logic. Uses
stdlib ``zoneinfo`` for DST handling — no third-party calendar packages.

The holiday list is hardcoded for 2023–2027 (see ``_HOLIDAY_YEARS``); extend
``_NYSE_HOLIDAYS`` when the project needs further years. Outside those years EVERY holiday reads
as a trading day, so a warning is logged at import once the current year is no longer covered.

Why hardcoded instead of ``exchange_calendars`` or ``pandas_market_calendars``:
those are not installed in this project and would add a heavy dependency for a
list of 30 dates. The exchange rules themselves (weekend + holiday) are simple
and fully covered here.
"""

import logging
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# US/Eastern is the canonical equity session timezone. zoneinfo handles
# DST transitions (spring-forward 02:00 → 03:00 in March, fall-back
# 02:00 ← 01:00 in November) automatically.
EASTERN = ZoneInfo("America/New_York")


class SessionType(StrEnum):
    """Equity session classification for a given moment in US/Eastern time."""

    PREMARKET = "premarket"  # 04:00–09:30 ET
    REGULAR = "regular"  # 09:30–16:00 ET
    AFTER_HOURS = "after_hours"  # 16:00–20:00 ET
    CLOSED = "closed"  # weekend, holiday, or outside 04:00–20:00 ET


# Hardcoded NYSE holidays for 2023 through 2027.
# Sources: NYSE official trading-hours calendar for each year.
# Includes observed-on-Monday / observed-on-Friday rules for weekend holidays. 2023 covers
# the stored daily history (which starts 2023-09-18); before it was added, Thanksgiving and
# Christmas 2023 read as unfillable "missing bars" on every backfill's gap check.
_NYSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2023
        date(2023, 1, 2),  # New Year's Day (observed — 1/1 is Sunday)
        date(2023, 1, 16),  # Martin Luther King Jr. Day
        date(2023, 2, 20),  # Presidents' Day
        date(2023, 4, 7),  # Good Friday
        date(2023, 5, 29),  # Memorial Day
        date(2023, 6, 19),  # Juneteenth
        date(2023, 7, 4),  # Independence Day
        date(2023, 9, 4),  # Labor Day
        date(2023, 11, 23),  # Thanksgiving
        date(2023, 12, 25),  # Christmas
        # 2024
        date(2024, 1, 1),  # New Year's Day (Mon)
        date(2024, 1, 15),  # Martin Luther King Jr. Day
        date(2024, 2, 19),  # Presidents' Day
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 27),  # Memorial Day
        date(2024, 6, 19),  # Juneteenth
        date(2024, 7, 4),  # Independence Day
        date(2024, 9, 2),  # Labor Day
        date(2024, 11, 28),  # Thanksgiving
        date(2024, 12, 25),  # Christmas
        # 2025
        date(2025, 1, 1),  # New Year's Day
        date(2025, 1, 9),  # National Day of Mourning (President Carter) — special full closure
        date(2025, 1, 20),  # MLK Day
        date(2025, 2, 17),  # Presidents' Day
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial Day
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),  # Independence Day
        date(2025, 9, 1),  # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (observed — 7/4 is Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
        # 2027
        date(2027, 1, 1),  # New Year's Day
        date(2027, 1, 18),  # MLK Day
        date(2027, 2, 15),  # Presidents' Day
        date(2027, 3, 26),  # Good Friday
        date(2027, 5, 31),  # Memorial Day
        date(2027, 6, 18),  # Juneteenth (observed — 6/19 is Saturday)
        date(2027, 7, 5),  # Independence Day (observed — 7/4 is Sunday)
        date(2027, 9, 6),  # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas (observed — 12/25 is Saturday)
    }
)

# Years the table above is complete for.
_HOLIDAY_YEARS: range = range(2023, 2028)


def _warn_if_holiday_table_is_stale(today: date | None = None) -> bool:
    """Log (and return True) when ``today`` is past the last year ``_NYSE_HOLIDAYS`` covers.

    Past that year every holiday is silently treated as a trading day: sessions are
    misclassified and every gap check reports the holiday as a missing bar.
    """
    today = today or date.today()
    if today.year in _HOLIDAY_YEARS or today.year < _HOLIDAY_YEARS.start:
        return False
    logger.warning(
        "NYSE holiday table covers %d-%d but it is %d: holidays are being treated as trading "
        "days. Extend _NYSE_HOLIDAYS in backend/engines/market_calendar.py.",
        _HOLIDAY_YEARS.start,
        _HOLIDAY_YEARS.stop - 1,
        today.year,
    )
    return True


_warn_if_holiday_table_is_stale()


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

    def regular_session_bounds(self, dt: datetime) -> tuple[datetime, datetime]:
        """Return the current/next regular-session open and close in ET.

        This is shared market state and avoids querying a provider once per
        symbol just to determine the exchange schedule. If ``dt`` is during
        regular hours, today's bounds are returned; otherwise the next
        trading day's bounds are returned.
        """
        et = self.to_et(dt)
        for offset in range(8):
            session_date = et.date() + timedelta(days=offset)
            probe = datetime.combine(session_date, time(12, 0), tzinfo=EASTERN)
            if not self.is_trading_day(probe):
                continue
            opening = datetime.combine(session_date, _REGULAR_OPEN, tzinfo=EASTERN)
            closing = datetime.combine(session_date, _REGULAR_CLOSE, tzinfo=EASTERN)
            if offset > 0 or et < opening or et <= closing:
                return opening, closing
        raise RuntimeError("Unable to find a future US regular session")


# Module-level singleton for the default US equity calendar.
# Engines default to this so the test seam (``calendar=...`` constructor arg)
# is purely an override path.
us_market_calendar = USMarketCalendar()


def classify_bar_session(ts: datetime) -> str:
    """Classify a bar timestamp into 'premarket' / 'regular' / 'after_hours'
    for storage in ``BarModel.session`` / ``Bar.session``.

    ``ts`` follows this codebase's bar-timestamp convention: naive datetime
    = NY local (see backend.utils.timezone / any provider's
    ``_epoch_ms_to_ny``). It must NOT be handed to ``USMarketCalendar``
    as-is — ``to_et()`` treats naive input as UTC — so NY tzinfo is
    stamped first, making ``to_et()``'s astimezone() a correct no-op
    instead of silently shifting the clock by several hours.

    Single source of truth for this classification — call it at the data
    layer (``bar_repository.upsert_bars``), not per-provider. Found live
    2026-09-09: WebullProvider correctly self-reports session on bars it
    returns, but Alpaca (used as a 1m gap-fill provider) returns genuine
    premarket ticks on its own IEX feed without being asked and without
    tagging them — its Bar objects fell back to the Bar model's
    'regular' default, letting a mistagged bar leak into resampled
    5m/15m/30m timeframes despite ingestion_service._resample_and_upsert's
    session='regular' filter (the filter was correct; the input session
    value it trusted wasn't). Trusting N providers to each independently
    self-report a fact that's cheaply derivable from the timestamp itself
    is inherently fragile — computing it once, centrally, from ground
    truth is not.

    ``SessionType.CLOSED`` shouldn't occur for a real trade bar, but
    falls back to 'regular' defensively rather than raising or inventing
    a fourth stored value.
    """
    ts_aware = ts.replace(tzinfo=EASTERN) if ts.tzinfo is None else ts
    session_type = us_market_calendar.get_session_type(ts_aware)
    if session_type == SessionType.PREMARKET:
        return "premarket"
    if session_type == SessionType.AFTER_HOURS:
        return "after_hours"
    return "regular"
