"""
SuperTrend indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class SuperTrendIndicator(BaseIndicator):
    """SuperTrend indicator"""
    
    def __init__(self, atr_period: int = 10, multiplier: float = 3.0):
        super().__init__("SuperTrend", {"atr_period": atr_period, "multiplier": multiplier})
        self.atr_period = atr_period
        self.multiplier = multiplier
        # We'll need to calculate ATR first
        self.atr_values: list[float] = []
        self.prev_supertrend: float | None = None
        self.prev_direction: bool | None = None  # True for uptrend, False for downtrend
    
    def _calculate_atr(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Average True Range"""
        if len(data) < 2:
            return []
        
        true_ranges = []
        for i in range(1, len(data)):
            high = float(data[i]['high'])
            low = float(data[i]['low'])
            prev_close = float(data[i-1]['close'])
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)
        
        # Calculate ATR (simple moving average of true ranges)
        atr_values = []
        for i in range(len(true_ranges)):
            if i < self.atr_period - 1:
                atr_values.append(None)
            else:
                atr = sum(true_ranges[i - self.atr_period + 1:i + 1]) / self.atr_period
                atr_values.append(atr)
        
        # Filter out None values
        return [v for v in atr_values if v is not None]
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate SuperTrend for the given data"""
        if len(data) < self.atr_period + 1:
            return []
        
        # Calculate ATR first
        atr_values = self._calculate_atr(data)
        if len(atr_values) < self.atr_period:
            return []
        
        # Extract prices
        highs = [float(d['high']) for d in data]
        lows = [float(d['low']) for d in data]
        closes = [float(d['close']) for d in data]
        
        supertrend_values = []
        
        # Calculate basic upper and lower bands
        for i in range(len(data)):
            if i < self.atr_period:
                # Not enough data for ATR calculation yet
                supertrend_values.append(None)
                continue
            
            # Current ATR value (aligned with data index)
            atr_idx = i - self.atr_period
            if atr_idx < 0 or atr_idx >= len(atr_values):
                supertrend_values.append(None)
                continue
                
            atr = atr_values[atr_idx]
            
            # Basic bands
            basic_ub = (highs[i] + lows[i]) / 2 + self.multiplier * atr
            basic_lb = (highs[i] + lows[i]) / 2 - self.multiplier * atr
            
            # Final bands
            if i == self.atr_period:
                # First calculation - initialize
                final_ub = basic_ub
                final_lb = basic_lb
                # Determine initial trend
                if closes[i] > basic_ub:
                    self.prev_direction = True  # Uptrend
                    self.prev_supertrend = final_lb
                elif closes[i] < basic_lb:
                    self.prev_direction = False  # Downtrend
                    self.prev_supertrend = final_ub
                else:
                    # Default to uptrend if close is between bands
                    self.prev_direction = True
                    self.prev_supertrend = final_lb
                
                supertrend_values.append(self.prev_supertrend)
            else:
                # Calculate final bands
                if basic_ub < self.prev_supertrend or self.prev_direction == False:
                    final_ub = basic_ub
                else:
                    final_ub = self.prev_supertrend
                    
                if basic_lb > self.prev_supertrend or self.prev_direction == True:
                    final_lb = basic_lb
                else:
                    final_lb = self.prev_supertrend
                
                # Determine trend
                if self.prev_direction == True:  # Was in uptrend
                    if closes[i] <= final_lb:
                        # Trend change to downtrend
                        self.prev_direction = False
                        self.prev_supertrend = final_ub
                    else:
                        # Stay in uptrend
                        self.prev_supertrend = final_lb
                else:  # Was in downtrend
                    if closes[i] >= final_ub:
                        # Trend change to uptrend
                        self.prev_direction = True
                        self.prev_supertrend = final_lb
                    else:
                        # Stay in downtrend
                        self.prev_supertrend = final_ub
                
                supertrend_values.append(self.prev_supertrend)
        
        # Filter out None values and store
        self.values = [v for v in supertrend_values if v is not None]
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update SuperTrend with new data point"""
        # For simplicity in streaming update, we'll recalculate with recent data
        # In a production system, you'd want to optimize this
        if not hasattr(self, '_price_history'):
            self._price_history = []
        
        # Add new data to history
        self._price_history.append({
            'high': float(new_data['high']),
            'low': float(new_data['low']),
            'close': float(new_data['close'])
        })
        
        # Keep only recent data needed for calculation
        # We need at least atr_period + 1 data points
        min_required = self.atr_period + 1
        if len(self._price_history) < min_required:
            return None
        
        # Keep only the last 2 * atr_period points for efficiency
        if len(self._price_history) > 2 * self.atr_period:
            self._price_history = self._price_history[-2 * self.atr_period:]
        
        # Recalculate SuperTrend with recent data
        try:
            result = self.calculate(self._price_history)
            if result:
                latest_value = result[-1]
                self.values.append(latest_value)
                return latest_value
        except Exception:
            pass
        
        return None
