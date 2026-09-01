"""
Tests for timeframe/candle engine
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.engines.market_calendar import SessionType, USMarketCalendar
from backend.engines.timeframe import (
    MultiSymbolTimeframeEngine,
    Timeframe,
    TimeframeEngine,
)
from backend.models.market_data import DataStatus

ET = ZoneInfo("America/New_York")


class TestTimeframeEngine(unittest.TestCase):

    def setUp(self):
        self.symbol = "AAPL"
        self.engine = TimeframeEngine(self.symbol)

    def test_engine_initialization(self):
        """Test that engine initializes correctly"""
        self.assertEqual(self.engine.symbol, self.symbol)
        self.assertIsInstance(self.engine.candles, dict)
        self.assertIsInstance(self.engine.current_candles, dict)

        # Check that all timeframes are initialized (except TICK)
        for timeframe in Timeframe:
            if timeframe != Timeframe.TICK:
                self.assertIn(timeframe, self.engine.candles)
                self.assertIn(timeframe, self.engine.current_candles)
                self.assertEqual(self.engine.candles[timeframe], [])
                self.assertIsNone(self.engine.current_candles[timeframe])

    def test_tick_update_creates_candle(self):
        """Test that processing a tick creates a candle"""
        timestamp = datetime.now()
        price = 150.0
        volume = 1000

        # Update with a tick
        self.engine.update_tick(price, volume, timestamp)

        # Check that we have a candle for TICK timeframe
        tick_candles = self.engine.get_closed_candles(Timeframe.TICK)
        self.assertEqual(len(tick_candles), 1)

        candle = tick_candles[0]
        self.assertEqual(candle.symbol, self.symbol)
        self.assertEqual(candle.timeframe, Timeframe.TICK)
        self.assertEqual(candle.open, price)
        self.assertEqual(candle.high, price)
        self.assertEqual(candle.low, price)
        self.assertEqual(candle.close, price)
        self.assertEqual(candle.volume, volume)
        self.assertTrue(candle.is_closed)

    def test_multiple_ticks_same_timeframe(self):
        """Test multiple ticks in the same timeframe period"""
        base_time = datetime.now().replace(second=0, microsecond=0)

        # Add multiple ticks within the same minute
        ticks = [
            (150.0, 100, base_time),
            (151.0, 200, base_time + timedelta(seconds=10)),
            (149.0, 150, base_time + timedelta(seconds=20)),
            (152.0, 300, base_time + timedelta(seconds=45))
        ]

        for price, volume, timestamp in ticks:
            self.engine.update_tick(price, volume, timestamp)

        # Check 1-minute candle
        minute_candles = self.engine.get_closed_candles(Timeframe.ONE_MINUTE)
        # Should have one closed candle (the minute is not complete yet, so actually 0)
        # Actually, since we're using current time, and the minute isn't complete,
        # we should have 0 closed candles and 1 open candle
        self.assertEqual(len(minute_candles), 0)  # No closed candles yet

        # But we should have a current candle
        current_candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertIsNotNone(current_candle)
        self.assertEqual(current_candle.open, 150.0)
        self.assertEqual(current_candle.high, 152.0)  # Highest price
        self.assertEqual(current_candle.low, 149.0)   # Lowest price
        self.assertEqual(current_candle.close, 152.0) # Last price
        self.assertEqual(current_candle.volume, 750)  # Total volume

    def test_minute_boundary_crossing(self):
        """Test that candles close properly when crossing minute boundaries"""
        base_time = datetime.now().replace(second=0, microsecond=0)

        # Add ticks in first minute
        self.engine.update_tick(150.0, 100, base_time)
        self.engine.update_tick(151.0, 200, base_time + timedelta(seconds=30))

        # Add ticks in second minute (crossing boundary)
        self.engine.update_tick(152.0, 150, base_time + timedelta(minutes=1, seconds=10))
        self.engine.update_tick(153.0, 300, base_time + timedelta(minutes=1, seconds=20))

        # Check 1-minute candles
        minute_candles = self.engine.get_closed_candles(Timeframe.ONE_MINUTE)
        self.assertEqual(len(minute_candles), 1)  # First minute should be closed

        first_minute = minute_candles[0]
        self.assertEqual(first_minute.open, 150.0)
        self.assertEqual(first_minute.high, 151.0)
        self.assertEqual(first_minute.low, 150.0)
        self.assertEqual(first_minute.close, 151.0)
        self.assertEqual(first_minute.volume, 300)
        self.assertTrue(first_minute.is_closed)

        # Check current candle (second minute)
        current_candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertIsNotNone(current_candle)
        self.assertEqual(current_candle.open, 152.0)
        self.assertEqual(current_candle.high, 153.0)
        self.assertEqual(current_candle.low, 152.0)
        self.assertEqual(current_candle.close, 153.0)
        self.assertEqual(current_candle.volume, 450)
        self.assertFalse(current_candle.is_closed)  # Still open

    def test_candle_to_bar_conversion(self):
        """Test converting a closed candle to Bar model"""
        # Use timezone-aware UTC so the bar.timestamp comparison works
        # regardless of the _ensure_aware() normalization in update_tick.
        timestamp = datetime.now(timezone.utc)
        price = 150.0
        volume = 1000

        # Create and close a tick candle
        self.engine.update_tick(price, volume, timestamp)

        # Get the candle and convert to bar
        tick_candles = self.engine.get_closed_candles(Timeframe.TICK)
        candle = tick_candles[0]

        bar = candle.to_bar()

        self.assertEqual(bar.symbol, self.symbol)
        self.assertEqual(bar.timestamp, timestamp)
        self.assertEqual(bar.open, price)
        self.assertEqual(bar.high, price)
        self.assertEqual(bar.low, price)
        self.assertEqual(bar.close, price)
        self.assertEqual(bar.volume, int(volume))
        self.assertEqual(bar.timeframe, Timeframe.TICK.value)
        self.assertEqual(bar.data_status, "HISTORICAL")  # Should be HISTORICAL when closed

    def test_multi_symbol_engine(self):
        """Test the multi-symbol timeframe engine"""
        # Use a fresh instance — the module-level singleton is shared across
        # tests, so prior tests may have left AAPL/GOOGL engines behind with
        # extra ticks that break the closed_candles count assertion below.
        multi_symbol_timeframe_engine = MultiSymbolTimeframeEngine()
        # Update tick for first symbol
        multi_symbol_timeframe_engine.update_tick("AAPL", 150.0, 100, datetime.now())

        # Update tick for second symbol
        multi_symbol_timeframe_engine.update_tick("GOOGL", 2800.0, 50, datetime.now())

        # Check that both symbols have engines
        aapl_engine = multi_symbol_timeframe_engine.get_engine_for_symbol("AAPL")
        googl_engine = multi_symbol_timeframe_engine.get_engine_for_symbol("GOOGL")

        self.assertIsNotNone(aapl_engine)
        self.assertIsNotNone(googl_engine)
        self.assertEqual(aapl_engine.symbol, "AAPL")
        self.assertEqual(googl_engine.symbol, "GOOGL")

        # Check that each has data
        aapl_ticks = aapl_engine.get_closed_candles(Timeframe.TICK)
        googl_ticks = googl_engine.get_closed_candles(Timeframe.TICK)

        self.assertEqual(len(aapl_ticks), 1)
        self.assertEqual(len(googl_ticks), 1)
        self.assertEqual(aapl_ticks[0].symbol, "AAPL")
        self.assertEqual(googl_ticks[0].symbol, "GOOGL")

class TestCandleAggregation(unittest.TestCase):
    """Aggregation correctness for the spec's required pairs.

    For each pair we feed a continuous stream of 1-minute ticks and verify
    the higher-timeframe candle is OHLCV-correct (H=max, L=min, O=first,
    C=last, V=sum) and that the candle count matches expectations.
    """

    def setUp(self):
        self.engine = TimeframeEngine("AAPL")
        # 09:30 ET on 2025-01-21 (Tuesday) — well inside regular session.
        self.base = datetime(2025, 1, 21, 9, 30, tzinfo=ET)

    def _feed_minute_bars(self, count: int, prices: list[float], volume: int = 100):
        """Feed ``count`` 1-minute bars where each bar has one tick.

        ``prices`` provides the close price of each minute; the OHLCV of the
        resulting 1m candle is the same price for O/H/L/C.
        """
        for i in range(count):
            ts = self.base + timedelta(minutes=i, seconds=15)
            self.engine.update_tick(prices[i], volume, ts, provider="test")

    def test_one_minute_to_five_minute_aggregation(self):
        """6 contiguous 1m ticks → 5 land in the 09:30-09:35 5m bar; the 6th closes it."""
        # 5m boundary starts at 09:30. Ticks at 09:30, 09:31, 09:32, 09:33, 09:34
        # all land in the 09:30-09:35 5m bar; the 6th tick (09:35) closes it.
        self._feed_minute_bars(6, [100.0, 101.0, 99.0, 102.0, 103.0, 104.0])
        five_min = self.engine.get_closed_candles(Timeframe.FIVE_MINUTE)
        self.assertEqual(len(five_min), 1)
        candle = five_min[0]
        # OHLCV from the 5 ticks that landed in 09:30-09:35 (the 6th tick is in
        # a new 5m window and doesn't affect the closed candle).
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.high, 103.0)
        self.assertEqual(candle.low, 99.0)
        self.assertEqual(candle.close, 103.0)  # last tick INSIDE the 5m window
        self.assertEqual(candle.volume, 500)    # 5 * 100

    def test_five_minute_to_fifteen_minute_aggregation(self):
        """16 contiguous 1m ticks → 15 land in 09:30-09:45 15m bar; the 16th closes it."""
        # 15m window 09:30-09:45 contains 15 1m ticks; the 16th (at 09:45) closes it.
        for i in range(16):
            ts = self.base + timedelta(minutes=i, seconds=15)
            self.engine.update_tick(100.0 + i, 50, ts, provider="test")
        fifteen_min = self.engine.get_closed_candles(Timeframe.FIFTEEN_MINUTE)
        self.assertEqual(len(fifteen_min), 1)
        candle = fifteen_min[0]
        # All 15 prices 100..114 → O=100, H=114, L=100, C=114
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.high, 114.0)
        self.assertEqual(candle.low, 100.0)
        self.assertEqual(candle.close, 114.0)
        self.assertEqual(candle.volume, 15 * 50)

    def test_fifteen_minute_to_one_hour_aggregation(self):
        """31 contiguous 1m ticks → 30 land in 09:00-10:00 1h bar; the 31st closes it.

        Base time is 09:30, so the 1h window 09:00-10:00 contains ticks at
        09:30, 09:31, ..., 09:59 (30 ticks). The 31st tick at 10:00 lands in
        the 10:00-11:00 window and closes the 1h candle.
        """
        for i in range(31):
            ts = self.base + timedelta(minutes=i, seconds=15)
            self.engine.update_tick(50.0 + (i % 10), 10, ts, provider="test")
        one_hour = self.engine.get_closed_candles(Timeframe.ONE_HOUR)
        self.assertEqual(len(one_hour), 1)
        candle = one_hour[0]
        # First tick (09:30) has price 50; the last tick INSIDE the 1h window
        # is at 09:59 (i=29) with price 50 + 29%10 = 59. The 31st tick (i=30)
        # is at 10:00 and lands in a new 1h window.
        self.assertEqual(candle.open, 50.0)
        self.assertEqual(candle.high, 59.0)
        self.assertEqual(candle.low, 50.0)
        self.assertEqual(candle.close, 59.0)
        self.assertEqual(candle.volume, 30 * 10)

    def test_one_minute_to_fifteen_minute_direct(self):
        """16 contiguous 1m ticks → 15 land in 09:30-09:45 15m bar; the 16th closes it."""
        for i in range(16):
            ts = self.base + timedelta(minutes=i, seconds=15)
            self.engine.update_tick(200.0 + i, 100, ts, provider="test")
        fifteen_min = self.engine.get_closed_candles(Timeframe.FIFTEEN_MINUTE)
        self.assertEqual(len(fifteen_min), 1)
        candle = fifteen_min[0]
        self.assertEqual(candle.open, 200.0)
        self.assertEqual(candle.high, 214.0)
        self.assertEqual(candle.low, 200.0)
        self.assertEqual(candle.close, 214.0)
        self.assertEqual(candle.volume, 15 * 100)

    def test_candle_alignment_to_boundary(self):
        """5m candle open_time should align to the 5-minute boundary."""
        # Feed 6 ticks: the 6th (at 09:35) closes the 09:30-09:35 5m candle.
        self._feed_minute_bars(6, [100.0] * 6)
        five_min = self.engine.get_closed_candles(Timeframe.FIVE_MINUTE)
        self.assertEqual(len(five_min), 1)
        candle = five_min[0]
        # 5m boundary: minutes 0..4 land in the 09:30-09:35 window.
        expected = datetime(2025, 1, 21, 9, 30, tzinfo=ET)
        self.assertEqual(candle.open_time, expected)


class TestMissingCandles(unittest.TestCase):
    """When a bar is missing (gap > 1.5x the period), the prior bar is flagged GAP."""

    def setUp(self):
        self.engine = TimeframeEngine("AAPL")
        self.base = datetime(2025, 1, 21, 9, 30, tzinfo=ET)

    def test_one_minute_gap_detected(self):
        """Skip 2 minutes between ticks — a 1m gap of 120s is > 1.5 × 60s = 90s.

        Feed 2 ticks in the 09:30 candle (so it's NOT also INCOMPLETE) then
        jump to 09:32 — the 09:30 candle closes with GAP.
        """
        # Ticks 1+2 at 09:30:15 and 09:30:45 (both in 09:30 candle)
        self.engine.update_tick(100.0, 10, self.base + timedelta(seconds=15))
        self.engine.update_tick(101.0, 10, self.base + timedelta(seconds=45))
        # Tick 3 at 09:32:15 — opens new 1m candle at 09:32. The 09:30 candle
        # closes with GAP because the gap from 09:30 to 09:32 is 120s > 90s.
        self.engine.update_tick(102.0, 10, self.base + timedelta(minutes=2, seconds=15))
        one_min = self.engine.get_closed_candles(Timeframe.ONE_MINUTE)
        self.assertEqual(len(one_min), 1)
        self.assertEqual(one_min[0].data_status, DataStatus.GAP)
        self.assertEqual(self.engine.gap_count, 1)

    def test_no_gap_for_contiguous_minutes(self):
        """Ticks at 09:30:15, 09:30:45, 09:31:15 — no gap, no GAP flag.

        After 3 ticks (2 in 09:30, 1 in 09:31), 09:30 is closed (no gap, the
        1m step from 09:30 → 09:31 is 60s which is ≤ 1.5 × 60s = 90s threshold).
        """
        self.engine.update_tick(100.0, 10, self.base + timedelta(seconds=15))
        self.engine.update_tick(101.0, 10, self.base + timedelta(seconds=45))
        self.engine.update_tick(
            102.0, 10, self.base + timedelta(minutes=1, seconds=15)
        )
        one_min = self.engine.get_closed_candles(Timeframe.ONE_MINUTE)
        # 09:30 candle is closed (no gap, contiguous); 09:31 is current.
        self.assertEqual(len(one_min), 1)
        self.assertEqual(one_min[0].data_status, DataStatus.HISTORICAL)
        self.assertEqual(self.engine.gap_count, 0)


class TestDuplicateCandles(unittest.TestCase):
    """A tick with the same (timeframe, candle_open, timestamp) is a duplicate."""

    def setUp(self):
        self.engine = TimeframeEngine("AAPL")
        self.base = datetime(2025, 1, 21, 9, 30, 15, tzinfo=ET)

    def test_duplicate_tick_is_skipped(self):
        """Feeding the same tick twice increments duplicate_count and keeps volume correct."""
        self.engine.update_tick(100.0, 10, self.base)
        # 1st tick lands in the 09:30 1m candle; volume=10.
        candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertEqual(candle.volume, 10)

        self.engine.update_tick(100.0, 10, self.base)
        # 2nd tick is a duplicate for the 1m candle — skipped, volume still 10.
        candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertEqual(candle.volume, 10)
        self.assertEqual(self.engine.duplicate_count, 1)

    def test_distinct_ticks_at_same_minute_arent_duplicates(self):
        """Two ticks within the same 1m candle at different timestamps are NOT duplicates."""
        self.engine.update_tick(100.0, 10, self.base)
        self.engine.update_tick(101.0, 10, self.base + timedelta(seconds=15))
        candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertEqual(candle.volume, 20)
        self.assertEqual(self.engine.duplicate_count, 0)


class TestIncompleteCandles(unittest.TestCase):
    """A candle with only 1 tick is flagged INCOMPLETE when it closes."""

    def setUp(self):
        self.engine = TimeframeEngine("AAPL")
        self.base = datetime(2025, 1, 21, 9, 30, 15, tzinfo=ET)

    def test_single_tick_candle_marked_incomplete(self):
        """A 1m candle that received exactly 1 tick is INCOMPLETE on close."""
        # 1 tick at 09:30:15 (in 09:30 candle). Then jump to 09:31:15 to close it.
        self.engine.update_tick(100.0, 10, self.base)
        self.engine.update_tick(101.0, 10, self.base + timedelta(minutes=1))
        one_min = self.engine.get_closed_candles(Timeframe.ONE_MINUTE)
        self.assertEqual(len(one_min), 1)
        self.assertEqual(one_min[0].data_status, DataStatus.INCOMPLETE)
        self.assertEqual(self.engine.incomplete_count, 1)

    def test_multi_tick_candle_not_incomplete(self):
        """A 1m candle with 2+ ticks is NOT incomplete when it closes."""
        self.engine.update_tick(100.0, 10, self.base)
        self.engine.update_tick(101.0, 10, self.base + timedelta(seconds=15))
        self.engine.update_tick(102.0, 10, self.base + timedelta(minutes=1))
        one_min = self.engine.get_closed_candles(Timeframe.ONE_MINUTE)
        self.assertEqual(len(one_min), 1)
        # Multi-tick candle closes cleanly → HISTORICAL (not INCOMPLETE, not GAP).
        self.assertEqual(one_min[0].data_status, DataStatus.HISTORICAL)
        self.assertEqual(self.engine.incomplete_count, 0)


class TestSessionBoundaries(unittest.TestCase):
    """SessionType is correctly classified at the various US/Eastern boundaries."""

    def setUp(self):
        self.engine = TimeframeEngine("AAPL")
        # 2025-01-21 is a regular Tuesday (no holidays in the way).
        self.trading_day = datetime(2025, 1, 21, tzinfo=ET)

    def test_premarket_before_open(self):
        self.assertEqual(
            self.engine.get_session_type(self.trading_day.replace(hour=9, minute=29)),
            SessionType.PREMARKET,
        )

    def test_regular_at_open(self):
        self.assertEqual(
            self.engine.get_session_type(self.trading_day.replace(hour=9, minute=30)),
            SessionType.REGULAR,
        )

    def test_regular_just_before_close(self):
        self.assertEqual(
            self.engine.get_session_type(self.trading_day.replace(hour=15, minute=59, second=59)),
            SessionType.REGULAR,
        )

    def test_after_hours_at_close(self):
        """16:00 sharp transitions to after-hours."""
        self.assertEqual(
            self.engine.get_session_type(self.trading_day.replace(hour=16, minute=0)),
            SessionType.AFTER_HOURS,
        )

    def test_after_hours_late(self):
        self.assertEqual(
            self.engine.get_session_type(self.trading_day.replace(hour=19, minute=59, second=59)),
            SessionType.AFTER_HOURS,
        )

    def test_closed_at_20_00(self):
        """20:00 ET is the end of after-hours; closed from 20:00 to 04:00."""
        self.assertEqual(
            self.engine.get_session_type(self.trading_day.replace(hour=20, minute=0)),
            SessionType.CLOSED,
        )

    def test_saturday_is_closed(self):
        """2025-01-25 is a Saturday."""
        saturday = datetime(2025, 1, 25, 12, 0, tzinfo=ET)
        self.assertEqual(self.engine.get_session_type(saturday), SessionType.CLOSED)

    def test_candle_session_type_at_open(self):
        """Candles opened in regular session carry SessionType.REGULAR."""
        ts = self.trading_day.replace(hour=10, minute=0, second=15)
        self.engine.update_tick(100.0, 10, ts)
        candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertEqual(candle.session_type, SessionType.REGULAR)

    def test_candle_session_type_in_premarket(self):
        """Candles opened in premarket carry SessionType.PREMARKET."""
        ts = self.trading_day.replace(hour=8, minute=30, second=15)
        self.engine.update_tick(100.0, 10, ts)
        candle = self.engine.get_current_candle(Timeframe.ONE_MINUTE)
        self.assertEqual(candle.session_type, SessionType.PREMARKET)


class TestTimezoneConversion(unittest.TestCase):
    """zoneinfo-based timezone conversion across DST boundaries."""

    def setUp(self):
        self.engine = TimeframeEngine("AAPL")

    def test_summer_et_matches_local(self):
        """In July (EDT, UTC-4), noon ET is 16:00 UTC."""
        # Feed a tick at 12:00 ET and verify the engine's calendar sees it
        # as 12:00 ET (regular session).
        ts = datetime(2025, 7, 21, 12, 0, tzinfo=ET)
        self.engine.update_tick(100.0, 10, ts)
        et_converted = self.engine.to_et(ts)
        self.assertEqual(et_converted.hour, 12)
        self.assertEqual(et_converted.minute, 0)

    def test_winter_et_matches_local(self):
        """In January (EST, UTC-5), noon ET is 17:00 UTC."""
        ts = datetime(2025, 1, 21, 12, 0, tzinfo=ET)
        et_converted = self.engine.to_et(ts)
        self.assertEqual(et_converted.hour, 12)

    def test_spring_forward_day_still_works(self):
        """The trading day just before a DST spring-forward is open."""
        # 2025-03-09 is spring-forward Sunday; Friday 2025-03-07 is open.
        ts = datetime(2025, 3, 7, 12, 0, tzinfo=ET)
        self.assertTrue(self.engine.is_market_open(ts))

    def test_fall_back_day_still_works(self):
        """The trading day just after a DST fall-back is open."""
        # 2025-11-02 is fall-back Sunday; Monday 2025-11-03 is open.
        ts = datetime(2025, 11, 3, 12, 0, tzinfo=ET)
        self.assertTrue(self.engine.is_market_open(ts))

    def test_to_et_accepts_naive_utc(self):
        """Naive datetimes are treated as UTC (the project-wide convention)."""
        # 19:00 UTC == 14:00 ET (winter). Naive datetime.
        naive = datetime(2025, 1, 21, 19, 0)
        et = self.engine.to_et(naive)
        self.assertEqual(et.hour, 14)
        self.assertEqual(et.tzinfo, ET)


class TestCustomCalendarOverride(unittest.TestCase):
    """The TimeframeEngine constructor accepts a custom calendar for testing."""

    def test_engine_uses_default_calendar(self):
        """No calendar arg → uses the global us_market_calendar."""
        from backend.engines.market_calendar import us_market_calendar
        e = TimeframeEngine("AAPL")
        self.assertIs(e.calendar, us_market_calendar)

    def test_engine_uses_custom_calendar(self):
        """A custom calendar instance overrides the default."""
        custom = USMarketCalendar()
        e = TimeframeEngine("AAPL", calendar=custom)
        self.assertIs(e.calendar, custom)


if __name__ == '__main__':
    unittest.main()
