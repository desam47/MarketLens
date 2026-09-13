"""
Volume Simple Moving Average indicator
"""
from typing import Any, cast

from .base_indicator import BaseIndicator


class VolumeSMAIndicator(BaseIndicator):
    """Volume Simple Moving Average indicator"""

    def __init__(self, period: int = 20):
        super().__init__("Volume_SMA", {"period": period})
        self.period = period

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Volume SMA for the given data"""
        if not data or len(data) < self.period:
            return []

        # Extract volumes
        volumes = [float(d['volume']) for d in data]

        # Calculate SMA
        sma_values: list[float | None] = [None] * (self.period - 1)  # First 'period-1' values are undefined

        for i in range(self.period - 1, len(volumes)):
            sma = sum(volumes[i - self.period + 1:i + 1]) / self.period
            sma_values.append(sma)

        # Filter out None values for clean return
        self.values = cast(list[float], [v for v in sma_values if v is not None])
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update Volume SMA with new data point"""
        volume = float(new_data['volume'])

        # Add to history
        if not hasattr(self, '_volume_history'):
            self._volume_history = []
        self._volume_history.append(volume)

        # Keep only the last 'period' volumes for efficiency
        if len(self._volume_history) > self.period:
            self._volume_history = self._volume_history[-self.period:]

        # Calculate SMA if we have enough data
        if len(self._volume_history) >= self.period:
            sma = sum(self._volume_history) / self.period
            self.values.append(sma)
            return sma

        return None
