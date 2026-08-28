"""
Timeframe/candle engine for aggregating market data
"""
import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from ..models.market_data import Bar, DataStatus
from .market_calendar import SessionType, USMarketCalendar, us_market_calendar

logger = logging.getLogger(__name__)

class Timeframe(StrEnum):
    """Supported timeframes"""
    TICK = "tick"
    ONE_MINUTE = "1m"
    TWO_MINUTE = "2m"
    THREE_MINUTE = "3m"
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"
    THIRTY_MINUTE = "30m"
    ONE_HOUR = "1h"
    TWO_HOUR = "2h"
    FOUR_HOUR = "4h"
    ONE_DAY = "1d"
    ONE_WEEK = "1wk"
    ONE_MONTH = "1mo"

class Candle:
    """Represents a single OHLCV candle"""

    def __init__(self, symbol: str, timeframe: Timeframe,
                 open_time: datetime, close_time: datetime,
                 session_type: SessionType | None = None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.open_time = open_time
        self.close_time = close_time

        # OHLCV data
        self.open: float | None = None
        self.high: float | None = None
        self.low: float | None = None
        self.close: float | None = None
        self.volume: float = 0.0

        # Metadata
        self.is_closed = False
        self.data_status = DataStatus.LIVE
        self.provider: str | None = None
        self.tick_count = 0
        # Phase 4: session type at the time this candle was opened.
        self.session_type = session_type

    def update(self, price: float, volume: float,
               timestamp: datetime, provider: str = ""):
        """Update candle with new tick data"""
        if self.open is None:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)

        self.close = price
        self.volume += volume
        self.tick_count += 1
        self.provider = provider

        # Update data status if needed
        if self.data_status == DataStatus.LIVE:
            # Check if we're receiving delayed data
            # This would be determined by the data provider in practice
            pass

    def close_candle(self):
        """Mark candle as closed.

        Preserves any data-quality status (GAP / INCOMPLETE / DUPLICATE) that
        was set on the candle by the engine before close — those flags are
        more informative than the default ``HISTORICAL``.
        """
        self.is_closed = True
        if self.data_status == DataStatus.LIVE:
            self.data_status = DataStatus.HISTORICAL

    def to_bar(self) -> Bar:
        """Convert candle to Bar model"""
        if not self.is_closed:
            raise ValueError("Cannot convert open candle to Bar")

        return Bar(
            symbol=self.symbol,
            timestamp=self.close_time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=int(self.volume),
            timeframe=self.timeframe.value,
            provider=self.provider or "unknown",
            data_status=self.data_status
        )

    def __repr__(self):
        return (f"Candle({self.symbol} {self.timeframe.value} "
                f"O:{self.open} H:{self.high} L:{self.low} C:{self.close} "
                f"V:{self.volume} {'closed' if self.is_closed else 'open'})")

class TimeframeEngine:
    """Engine for aggregating market data into multiple timeframes"""

    def __init__(self, symbol: str, calendar: USMarketCalendar | None = None):
        self.symbol = symbol
        self.calendar = calendar or us_market_calendar
        self.candles: dict[Timeframe, list[Candle]] = {}
        self.current_candles: dict[Timeframe, Candle] = {}
        self.subscribers: list[Any] = []  # For callbacks when candles close

        # Phase 4: gap + duplicate + session tracking
        # For each non-TICK timeframe, the open_time of the most recently seen
        # candle. ``None`` means "no candle yet for this timeframe".
        self._last_candle_open: dict[Timeframe, datetime | None] = {}
        # Candle-start timestamps seen for a given timeframe. Used to detect
        # duplicate ticks within the same candle window (same (tf, candle_start)
        # but different tick timestamps — not a dup).
        self._seen_candle_starts: dict[Timeframe, set[datetime]] = {}
        # All tick timestamps ever accepted. A tick with a previously-seen
        # timestamp is a duplicate — counted once per tick regardless of how
        # many timeframes it would update.
        self._seen_timestamps: set[datetime] = set()
        # Counters exposed for tests and ops visibility.
        self.duplicate_count = 0
        self.gap_count = 0
        self.incomplete_count = 0

        # Initialize for all timeframes
        for timeframe in Timeframe:
            self.candles[timeframe] = []
            self.current_candles[timeframe] = None
            self._last_candle_open[timeframe] = None
            self._seen_candle_starts[timeframe] = set()

    def _get_candle_start_time(self, timestamp: datetime,
                               timeframe: Timeframe) -> datetime:
        """Calculate the start time for a candle given a timestamp"""
        if timeframe == Timeframe.TICK:
            return timestamp

        # Convert to pandas for easy timeframe handling
        if timeframe == Timeframe.ONE_MINUTE:
            return timestamp.replace(second=0, microsecond=0)
        elif timeframe == Timeframe.TWO_MINUTE:
            minute = timestamp.minute - (timestamp.minute % 2)
            return timestamp.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == Timeframe.THREE_MINUTE:
            minute = timestamp.minute - (timestamp.minute % 3)
            return timestamp.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == Timeframe.FIVE_MINUTE:
            minute = timestamp.minute - (timestamp.minute % 5)
            return timestamp.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == Timeframe.FIFTEEN_MINUTE:
            minute = timestamp.minute - (timestamp.minute % 15)
            return timestamp.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == Timeframe.THIRTY_MINUTE:
            minute = timestamp.minute - (timestamp.minute % 30)
            return timestamp.replace(minute=minute, second=0, microsecond=0)
        elif timeframe == Timeframe.ONE_HOUR:
            return timestamp.replace(minute=0, second=0, microsecond=0)
        elif timeframe == Timeframe.TWO_HOUR:
            hour = timestamp.hour - (timestamp.hour % 2)
            return timestamp.replace(hour=hour, minute=0, second=0, microsecond=0)
        elif timeframe == Timeframe.FOUR_HOUR:
            hour = timestamp.hour - (timestamp.hour % 4)
            return timestamp.replace(hour=hour, minute=0, second=0, microsecond=0)
        elif timeframe == Timeframe.ONE_DAY:
            return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        elif timeframe == Timeframe.ONE_WEEK:
            # Start of week (Monday)
            days_since_monday = timestamp.weekday()
            start_date = timestamp - timedelta(days=days_since_monday)
            return start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        elif timeframe == Timeframe.ONE_MONTH:
            return timestamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            # Default to 1 minute
            return timestamp.replace(second=0, microsecond=0)

    def _get_candle_end_time(self, start_time: datetime,
                             timeframe: Timeframe) -> datetime:
        """Calculate the end time for a candle given its start time"""
        if timeframe == Timeframe.TICK:
            return start_time

        if timeframe == Timeframe.ONE_MINUTE:
            return start_time + timedelta(minutes=1)
        elif timeframe == Timeframe.TWO_MINUTE:
            return start_time + timedelta(minutes=2)
        elif timeframe == Timeframe.THREE_MINUTE:
            return start_time + timedelta(minutes=3)
        elif timeframe == Timeframe.FIVE_MINUTE:
            return start_time + timedelta(minutes=5)
        elif timeframe == Timeframe.FIFTEEN_MINUTE:
            return start_time + timedelta(minutes=15)
        elif timeframe == Timeframe.THIRTY_MINUTE:
            return start_time + timedelta(minutes=30)
        elif timeframe == Timeframe.ONE_HOUR:
            return start_time + timedelta(hours=1)
        elif timeframe == Timeframe.TWO_HOUR:
            return start_time + timedelta(hours=2)
        elif timeframe == Timeframe.FOUR_HOUR:
            return start_time + timedelta(hours=4)
        elif timeframe == Timeframe.ONE_DAY:
            return start_time + timedelta(days=1)
        elif timeframe == Timeframe.ONE_WEEK:
            return start_time + timedelta(weeks=1)
        elif timeframe == Timeframe.ONE_MONTH:
            # Approximate month as 30 days
            return start_time + timedelta(days=30)
        else:
            return start_time + timedelta(minutes=1)

    def _timeframe_delta(self, timeframe: Timeframe) -> timedelta:
        """Return the nominal duration of a single bar for ``timeframe``."""
        deltas = {
            Timeframe.ONE_MINUTE: timedelta(minutes=1),
            Timeframe.TWO_MINUTE: timedelta(minutes=2),
            Timeframe.THREE_MINUTE: timedelta(minutes=3),
            Timeframe.FIVE_MINUTE: timedelta(minutes=5),
            Timeframe.FIFTEEN_MINUTE: timedelta(minutes=15),
            Timeframe.THIRTY_MINUTE: timedelta(minutes=30),
            Timeframe.ONE_HOUR: timedelta(hours=1),
            Timeframe.TWO_HOUR: timedelta(hours=2),
            Timeframe.FOUR_HOUR: timedelta(hours=4),
            Timeframe.ONE_DAY: timedelta(days=1),
            Timeframe.ONE_WEEK: timedelta(weeks=1),
            Timeframe.ONE_MONTH: timedelta(days=30),
        }
        return deltas.get(timeframe, timedelta(minutes=1))

    def is_market_open(self, dt: datetime) -> bool:
        """Return True if the regular session is open at ``dt`` (delegates to calendar)."""
        return self.calendar.is_market_open(dt)

    def get_session_type(self, dt: datetime) -> SessionType:
        """Classify ``dt`` into a session type using the configured calendar."""
        return self.calendar.get_session_type(dt)

    def to_et(self, dt: datetime) -> datetime:
        """Convert a timestamp to US/Eastern for session/holiday checks."""
        return self.calendar.to_et(dt)

    def update_tick(self, price: float, volume: float,
                    timestamp: datetime, provider: str = ""):
        """Process a new tick and update all timeframes.

        Phase 4 additions: duplicate detection (same exact tick timestamp seen
        twice — counted once regardless of how many timeframes it would cross)
        and gap detection (when a new candle starts more than 1.5x the expected
        period after the prior candle, the prior one is marked DataStatus.GAP).
        Session type is stamped onto each new candle at open time.
        """
        # --- Phase 4: duplicate-tick detection (fires once per tick) ---
        # A tick with a previously-seen timestamp is a duplicate. We allow the
        # same tick timestamp to fan out across multiple timeframes (it crosses
        # multiple candle boundaries), so the dedupe key is the timestamp alone.
        if timestamp in self._seen_timestamps:
            self.duplicate_count += 1
            logger.debug(
                "Duplicate tick for %s at %s — skipping",
                self.symbol, timestamp,
            )
            return
        self._seen_timestamps.add(timestamp)

        # Resolve the session type once for this tick (used by non-TICK candles).
        tick_session = self.calendar.get_session_type(timestamp)

        # Update each timeframe
        for timeframe in Timeframe:
            if timeframe == Timeframe.TICK:
                # TICK: each tick is its own candle; duplicate detection is
                # not meaningful at the tick level (every tick has a unique
                # timestamp by definition) so we skip the dedupe path.
                candle = Candle(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=timestamp,
                    close_time=timestamp,
                    session_type=tick_session,
                )
                candle.update(price, volume, timestamp, provider)
                candle.close_candle()  # Tick candles are immediately closed

                self.candles[timeframe].append(candle)
                self._notify_subscribers(candle)
                continue

            # Get the start time for this timeframe
            candle_start = self._get_candle_start_time(timestamp, timeframe)
            candle_end = self._get_candle_end_time(candle_start, timeframe)

            # Check if we need a new candle
            current_candle = self.current_candles[timeframe]
            if current_candle is None or current_candle.open_time != candle_start:
                # Close the previous candle if it exists and is open
                if current_candle is not None and not current_candle.is_closed:
                    # --- Phase 4: gap detection ---
                    # If the previous candle's close_time is more than 1.5x
                    # the timeframe period before this candle's open, mark
                    # the previous one as a GAP. We use 1.5x to absorb
                    # one-tick jitter at boundary crossings.
                    expected_step = self._timeframe_delta(timeframe) * 1.5
                    actual_step = candle_start - current_candle.open_time
                    if actual_step > expected_step:
                        current_candle.data_status = DataStatus.GAP
                        self.gap_count += 1
                        logger.debug(
                            "Gap detected in %s %s: %s between candles",
                            self.symbol, timeframe.value, actual_step,
                        )

                    # --- Phase 4: incomplete detection ---
                    # A single-tick candle is a strong signal of an
                    # incomplete bar (e.g. the only tick landed in the last
                    # second before boundary). Threshold of 1 is conservative.
                    if current_candle.tick_count <= 1:
                        current_candle.data_status = DataStatus.INCOMPLETE
                        self.incomplete_count += 1

                    current_candle.close_candle()
                    self.candles[timeframe].append(current_candle)
                    self._notify_subscribers(current_candle)

                # Create new candle
                new_candle = Candle(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=candle_start,
                    close_time=candle_end,
                    session_type=tick_session,
                )
                self.current_candles[timeframe] = new_candle
                self._last_candle_open[timeframe] = candle_start

            # Update the current candle
            if self.current_candles[timeframe] is not None:
                self.current_candles[timeframe].update(
                    price, volume, timestamp, provider
                )

    def get_current_candle(self, timeframe: Timeframe) -> Candle | None:
        """Get the current (open) candle for a timeframe"""
        return self.current_candles.get(timeframe)

    def get_closed_candles(self, timeframe: Timeframe,
                           limit: int | None = None) -> list[Candle]:
        """Get closed candles for a timeframe"""
        candles = self.candles.get(timeframe, [])
        if limit is not None:
            return candles[-limit:] if len(candles) > limit else candles
        return candles.copy()

    def get_latest_closed_candle(self, timeframe: Timeframe) -> Candle | None:
        """Get the most recent closed candle for a timeframe"""
        candles = self.get_closed_candles(timeframe)
        return candles[-1] if candles else None

    def get_candles_as_bars(self, timeframe: Timeframe,
                            limit: int | None = None) -> list[Bar]:
        """Get candles as Bar models"""
        candles = self.get_closed_candles(timeframe, limit)
        bars = []
        for candle in candles:
            if candle.is_closed:
                try:
                    bars.append(candle.to_bar())
                except ValueError:
                    # Skip candles that couldn't be converted
                    pass
        return bars

    def subscribe(self, callback: Any):
        """Subscribe to candle close events"""
        self.subscribers.append(callback)

    def unsubscribe(self, callback: Any):
        """Unsubscribe from candle close events"""
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def _notify_subscribers(self, candle: Candle):
        """Notify all subscribers of a closed candle"""
        for subscriber in self.subscribers:
            try:
                subscriber(candle)
            except Exception as e:
                logger.error(f"Error notifying subscriber: {e}")

    def reset(self):
        """Reset the engine to initial state"""
        for timeframe in Timeframe:
            if timeframe != Timeframe.TICK:
                self.candles[timeframe].clear()
                self.current_candles[timeframe] = None
        self.subscribers.clear()

class MultiSymbolTimeframeEngine:
    """Timeframe engine that handles multiple symbols"""

    def __init__(self):
        self.engines: dict[str, TimeframeEngine] = {}

    def get_engine(self, symbol: str) -> TimeframeEngine:
        """Get or create timeframe engine for a symbol"""
        if symbol not in self.engines:
            self.engines[symbol] = TimeframeEngine(symbol)
        return self.engines[symbol]

    def update_tick(self, symbol: str, price: float, volume: float,
                    timestamp: datetime, provider: str = ""):
        """Update tick for a specific symbol"""
        engine = self.get_engine(symbol)
        engine.update_tick(price, volume, timestamp, provider)

    def get_engine_for_symbol(self, symbol: str) -> TimeframeEngine | None:
        """Get timeframe engine for a symbol"""
        return self.engines.get(symbol)

    def reset_all(self):
        """Reset all engines"""
        for engine in self.engines.values():
            engine.reset()

# Global instance for easy access
multi_symbol_timeframe_engine = MultiSymbolTimeframeEngine()
