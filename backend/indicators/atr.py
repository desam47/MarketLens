"""
Average True Range (ATR) indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class ATRIndicator(BaseIndicator):
    """Average True Range indicator"""
    
    def __init__(self, period: int = 14):
        super().__init__("ATR", {"period": period})
        self.period = period
        self.true_ranges: list[float] = []
    
    def _calculate_true_range(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate True Range for the given data"""
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
        
        return true_ranges
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate ATR for the given data"""
        if len(data) < self.period + 1:
            return []
        
        # Calculate True Range
        true_ranges = self._calculate_true_range(data)
        
        if len(true_ranges) < self.period:
            return []
        
        # Calculate ATR as moving average of True Range
        atr_values = [None] * (self.period - 1)  # First 'period-1' values are undefined
        
        for i in range(self.period - 1, len(true_ranges)):
            atr = sum(true_ranges[i - self.period + 1:i + 1]) / self.period
            atr_values.append(atr)
        
        # Filter out None values for clean return
        self.values = [v for v in atr_values if v is not None]
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update ATR with new data point"""
        # For simplicity in streaming update, we'll recalculate with recent data
        # In a production system, you'd want to optimize this with proper state maintenance
        if not hasattr(self, '_price_history'):
            self._price_history = []

        # Add new data to history
        self._price_history.append({
            'high': float(new_data['high']),
            'low': float(new_data['low']),
            'close': float(new_data['close'])
        })

        # Keep only recent data needed for calculation
        # We need at least period + 1 data points for ATR calculation
        min_required = self.period + 1
        if len(self._price_history) < min_required:
            return None

        # Keep only the last 2 * period points for efficiency
        if len(self._price_history) > 2 * self.period:
            self._price_history = self._price_history[-2 * self.period:]

        # Recalculate ATR with recent data
        try:
            result = self.calculate(self._price_history)
            if result:
                latest_value = result[-1]
                self.values.append(latest_value)
                return latest_value
        except Exception:
            pass

        return None
