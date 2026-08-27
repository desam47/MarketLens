"""
On-Balance Volume (OBV) indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class OBVIndicator(BaseIndicator):
    """On-Balance Volume indicator"""
    
    def __init__(self):
        super().__init__("OBV", {})
        self.obv_values: list[float] = []
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate OBV for the given data"""
        if not data or len(data) < 2:
            return []
        
        # Extract closes and volumes
        closes = [float(d['close']) for d in data]
        volumes = [float(d['volume']) for d in data]
        
        # Calculate OBV
        obv_values = [0.0]  # Start with 0
        
        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:
                # Price up - add volume
                obv_values.append(obv_values[-1] + volumes[i])
            elif closes[i] < closes[i-1]:
                # Price down - subtract volume
                obv_values.append(obv_values[-1] - volumes[i])
            else:
                # Price unchanged - OBV stays the same
                obv_values.append(obv_values[-1])
        
        self.values = obv_values.copy()
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update OBV with new data point"""
        close_price = float(new_data['close'])
        volume = float(new_data['volume'])
        
        # Initialize price and volume history if needed
        if not hasattr(self, '_price_history'):
            self._price_history = []
            self._volume_history = []
        
        self._price_history.append(close_price)
        self._volume_history.append(volume)
        
        # Need at least 2 data points to calculate OBV change
        if len(self._price_history) < 2:
            # First data point - OBV starts at 0
            self.values.append(0.0)
            return 0.0
        
        # Calculate OBV change based on price movement
        prev_close = self._price_history[-2]
        prev_obv = self.values[-1] if self.values else 0.0
        
        if close_price > prev_close:
            # Price up - add volume
            new_obv = prev_obv + volume
        elif close_price < prev_close:
            # Price down - subtract volume
            new_obv = prev_obv - volume
        else:
            # Price unchanged - OBV stays the same
            new_obv = prev_obv
        
        self.values.append(new_obv)
        return new_obv
