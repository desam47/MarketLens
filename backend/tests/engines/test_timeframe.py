"""
Tests for timeframe/candle engine
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.engines.timeframe import (
    Timeframe,
    TimeframeEngine,
    multi_symbol_timeframe_engine,
)


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
        timestamp = datetime.now()
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

if __name__ == '__main__':
    unittest.main()
