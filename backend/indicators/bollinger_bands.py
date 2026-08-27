"""
Bollinger Bands indicator
"""
from typing import Any

from .base_indicator import BaseIndicator
from .sma import SMAIndicator


class BollingerBandsIndicator(BaseIndicator):
    """Bollinger Bands indicator"""
    
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__("Bollinger_Bands", {"period": period, "std_dev": std_dev})
        self.period = period
        self.std_dev = std_dev
        
        # Create underlying SMA for the middle band
        self.sma = SMAIndicator(period)
        
        # Store upper band, middle band (SMA), and lower band
        self.upper_band: list[float] = []
        self.middle_band: list[float] = []  # This is the SMA
        self.lower_band: list[float] = []
        self.bandwidth: list[float] = []  # (Upper - Lower) / Middle
        self.percent_b: list[float] = []  # (Price - Lower) / (Upper - Lower)
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Bollinger Bands for the given data"""
        if not data or len(data) < self.period:
            return []
        
        # Extract close prices
        closes = [float(d['close']) for d in data]
        
        # Calculate SMA (middle band)
        sma_values = self.sma.calculate([{'close': c} for c in closes])
        
        if len(sma_values) < self.period:
            return []
        
        # Calculate standard deviation and bands
        upper_band = []
        middle_band = []
        lower_band = []
        bandwidth = []
        percent_b = []
        
        # We need to align the data - SMA starts after period-1 values
        start_index = self.period - 1
        
        for i in range(start_index, len(closes)):
            # Get the SMA value for this point
            sma_idx = i - start_index
            if sma_idx >= len(sma_values):
                break
                
            middle_value = sma_values[sma_idx]
            
            # Calculate standard deviation for the period
            period_closes = closes[i - self.period + 1:i + 1]
            mean = sum(period_closes) / self.period
            variance = sum((x - mean) ** 2 for x in period_closes) / self.period
            std_dev = variance ** 0.5
            
            # Calculate bands
            upper_value = middle_value + (self.std_dev * std_dev)
            lower_value = middle_value - (self.std_dev * std_dev)
            
            upper_band.append(upper_value)
            middle_band.append(middle_value)
            lower_band.append(lower_value)
            
            # Calculate bandwidth
            if middle_value != 0:
                bw = (upper_value - lower_value) / middle_value
                bandwidth.append(bw)
            else:
                bandwidth.append(0.0)
            
            # Calculate %B
            current_price = closes[i]
            if upper_value != lower_value:
                pb = (current_price - lower_value) / (upper_value - lower_value)
                percent_b.append(pb)
            else:
                percent_b.append(0.0)  # Avoid division by zero
        
        # Store values (using percent_b as the main return value for consistency)
        self.values = percent_b.copy()
        self.upper_band = upper_band
        self.middle_band = middle_band
        self.lower_band = lower_band
        self.bandwidth = bandwidth
        self.percent_b = percent_b
        
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update Bollinger Bands with new data point"""
        close_price = float(new_data['close'])
        high_price = float(new_data['high'])
        low_price = float(new_data['low'])

        # Update underlying SMA
        sma_value = self.sma.update({'close': close_price})

        if sma_value is not None:
            # We need historical data to calculate standard deviation
            if not hasattr(self, '_price_history'):
                self._price_history = []

            self._price_history.append(close_price)

            # Keep only recent data needed for calculation
            if len(self._price_history) > self.period + 10:
                self._price_history = self._price_history[-(self.period + 10):]

            # Calculate Bollinger Bands with current history
            try:
                result = self.calculate([
                    {'close': price, 'high': high_price, 'low': low_price}
                    for price in self._price_history
                ])
                if result:
                    latest_value = result[-1]
                    self.values.append(latest_value)
                    return latest_value
            except Exception:
                pass

        return None
        
    def get_bands(self) -> dict:
        """Get all Bollinger Bands values"""
        return {
            'upper': self.upper_band.copy(),
            'middle': self.middle_band.copy(),
            'lower': self.lower_band.copy(),
            'bandwidth': self.bandwidth.copy(),
            'percent_b': self.percent_b.copy()
        }
