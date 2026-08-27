"""
Relative Strength Index (RSI) indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class RSIIndicator(BaseIndicator):
    """Relative Strength Index indicator"""
    
    def __init__(self, period: int = 14):
        super().__init__("RSI", {"period": period})
        self.period = period
        self.gains: list[float] = []
        self.losses: list[float] = []
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate RSI for the given data"""
        if not data or len(data) < self.period + 1:
            return []
        
        # Extract close prices
        closes = [float(d['close']) for d in data]
        
        # Calculate price changes
        changes = []
        for i in range(1, len(closes)):
            changes.append(closes[i] - closes[i-1])
        
        # Separate gains and losses
        gains = [max(change, 0) for change in changes]
        losses = [max(-change, 0) for change in changes]
        
        # Calculate initial average gain and loss
        avg_gain = sum(gains[:self.period]) / self.period
        avg_loss = sum(losses[:self.period]) / self.period
        
        # Calculate RSI
        rsi_values = [None] * self.period  # First 'period' values are undefined
        
        if avg_loss == 0:
            rsi_values.append(100.0)  # Avoid division by zero
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            rsi_values.append(rsi)
        
        # Calculate remaining RSI values using Wilder's smoothing
        for i in range(self.period, len(gains)):
            avg_gain = (avg_gain * (self.period - 1) + gains[i]) / self.period
            avg_loss = (avg_loss * (self.period - 1) + losses[i]) / self.period
            
            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                rsi_values.append(rsi)
        
        # Filter out None values for clean return
        self.values = [v for v in rsi_values if v is not None]
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update RSI with new data point"""
        close_price = float(new_data['close'])
        
        # Initialize price history if needed
        if not hasattr(self, '_price_history'):
            self._price_history = []
        
        self._price_history.append(close_price)
        
        # Keep only what we need for calculation
        # We need at least period + 1 prices to calculate RSI
        if len(self._price_history) < self.period + 1:
            return None
        
        # Keep recent history for efficiency
        if len(self._price_history) > self.period + 10:
            self._price_history = self._price_history[-(self.period + 10):]
        
        # Calculate RSI with current history
        try:
            result = self.calculate([
                {'close': price} for price in self._price_history
            ])
            if result:
                latest_value = result[-1]
                self.values.append(latest_value)
                return latest_value
        except Exception:
            pass
        
        return None
