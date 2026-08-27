"""
Timeframe/candle engine for aggregating market data
"""
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..models.market_data import Bar, DataStatus

logger = logging.getLogger(__name__)

class Timeframe(str, Enum):
    """Supported timeframes"""
    TICK = "tick"
    ONE_MINUTE = "1m"
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
                 open_time: datetime, close_time: datetime):
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
        """Mark candle as closed"""
        self.is_closed = True
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
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.candles: dict[Timeframe, list[Candle]] = {}
        self.current_candles: dict[Timeframe, Candle] = {}
        self.subscribers: list[Any] = []  # For callbacks when candles close

        # Initialize for all timeframes
        for timeframe in Timeframe:
            self.candles[timeframe] = []
            self.current_candles[timeframe] = None
    
    def _get_candle_start_time(self, timestamp: datetime, 
                               timeframe: Timeframe) -> datetime:
        """Calculate the start time for a candle given a timestamp"""
        if timeframe == Timeframe.TICK:
            return timestamp
        
        # Convert to pandas for easy timeframe handling
        if timeframe == Timeframe.ONE_MINUTE:
            return timestamp.replace(second=0, microsecond=0)
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
    
    def update_tick(self, price: float, volume: float, 
                    timestamp: datetime, provider: str = ""):
        """Process a new tick and update all timeframes"""
        # Update each timeframe
        for timeframe in Timeframe:
            if timeframe == Timeframe.TICK:
                # For tick data, each tick is its own candle
                candle = Candle(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=timestamp,
                    close_time=timestamp
                )
                candle.update(price, volume, timestamp, provider)
                candle.close_candle()  # Tick candles are immediately closed
                
                self.candles[timeframe].append(candle)
                # Notify subscribers of new closed candle
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
                    current_candle.close_candle()
                    self.candles[timeframe].append(current_candle)
                    # Notify subscribers of closed candle
                    self._notify_subscribers(current_candle)
                
                # Create new candle
                new_candle = Candle(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=candle_start,
                    close_time=candle_end
                )
                self.current_candles[timeframe] = new_candle
            
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
