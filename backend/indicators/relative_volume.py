"""
Relative Volume indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class RelativeVolumeIndicator(BaseIndicator):
    """Relative Volume indicator"""
    
    def __init__(self, period: int = 20):
        super().__init__("Relative_Volume", {"period": period})
        self.period = period
        self.volume_sma: list[float] = []  # Store SMA values for comparison
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Relative Volume for the given data"""
        if not data or len(data) < self.period + 1:
            return []
        
        # Extract volumes
        volumes = [float(d['volume']) for d in data]
        
        # Calculate Volume SMA first
        volume_sma_values = [None] * (self.period - 1)  # First 'period-1' values are undefined
        
        for i in range(self.period - 1, len(volumes)):
            sma = sum(volumes[i - self.period + 1:i + 1]) / self.period
            volume_sma_values.append(sma)
        
        # Calculate Relative Volume: Current Volume / Volume SMA
        rv_values = [None] * len(volumes)  # Initialize with None
        
        for i in range(len(volumes)):
            if i >= self.period - 1 and volume_sma_values[i] is not None and volume_sma_values[i] != 0:
                rv_values[i] = volumes[i] / volume_sma_values[i]
        
        # Filter out None values for clean return
        self.values = [v for v in rv_values if v is not None]
        self.volume_sma = [v for v in volume_sma_values if v is not None]  # Store for potential use
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update Relative Volume with new data point"""
        volume = float(new_data['volume'])
        
        # Add to history
        if not hasattr(self, '_volume_history'):
            self._volume_history = []
        self._volume_history.append(volume)
        
        # Keep only recent data needed for calculation
        if len(self._volume_history) > self.period + 10:
            self._volume_history = self._volume_history[-(self.period + 10):]
        
        # Calculate Relative Volume with current history
        try:
            result = self.calculate([
                {'volume': vol} for vol in self._volume_history
            ])
            if result:
                latest_value = result[-1]
                self.values.append(latest_value)
                return latest_value
        except Exception:
            pass
        
        return None
