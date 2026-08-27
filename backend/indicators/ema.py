from typing import Any

"""
Exponential Moving Average (EMA) indicator
"""

from .base_indicator import BaseIndicator


class EMAIndicator(BaseIndicator):
    """Exponential Moving Average indicator"""
    
    def __init__(self, period: int):
        super().__init__("EMA", {"period": period})
        self.period = period
        self.multiplier = 2 / (period + 1)
        self.prev_ema: float | None = None
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate EMA for the given data"""
        if not data or len(data) < self.period:
            return []
        
        # Extract close prices
        closes = [float(d['close']) for d in data]
        
        # Calculate SMA for first EMA value
        sma = sum(closes[:self.period]) / self.period
        ema_values = [sma]
        self.prev_ema = sma
        
        # Calculate EMA for remaining values
        for i in range(self.period, len(closes)):
            ema = (closes[i] * self.multiplier) + (self.prev_ema * (1 - self.multiplier))
            ema_values.append(ema)
            self.prev_ema = ema
        
        self.values = ema_values
        return ema_values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update EMA with new data point"""
        close_price = float(new_data['close'])
        
        if self.prev_ema is None:
            # First value - need to calculate SMA from enough data points
            # For simplicity, we'll use the close price as the first EMA
            self.prev_ema = close_price
            self.values.append(self.prev_ema)
        else:
            # Standard EMA calculation
            ema = (close_price * self.multiplier) + (self.prev_ema * (1 - self.multiplier))
            self.prev_ema = ema
            self.values.append(ema)
        
        return self.prev_ema
