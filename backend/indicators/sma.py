from typing import Any

"""
Simple Moving Average (SMA) indicator
"""
from .base_indicator import BaseIndicator


class SMAIndicator(BaseIndicator):
    """Simple Moving Average indicator"""

    def __init__(self, period: int):
        super().__init__("SMA", {"period": period})
        self.period = period
        self._price_history = []

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate SMA for the given data"""
        if not data:
            return []

        # Extract close prices
        closes = [float(d['close']) for d in data]

        # Add to price history
        self._price_history.extend(closes)

        # Keep history reasonable size
        if len(self._price_history) > self.period * 2:
            self._price_history = self._price_history[-self.period * 2:]

        # Calculate SMA values for the new data points
        sma_values = []
        start_index = max(0, len(self._price_history) - len(closes))

        for i in range(len(closes)):
            history_index = start_index + i
            if history_index < self.period - 1:
                # Not enough data for SMA yet
                sma_values.append(None)
            else:
                # Calculate SMA of last 'period' prices
                start_idx = history_index - self.period + 1
                end_idx = history_index + 1
                sma = sum(self._price_history[start_idx:end_idx]) / self.period
                sma_values.append(sma)

        # Store only the valid SMA values
        self.values = [v for v in sma_values if v is not None]
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update SMA with new data point"""
        close_price = float(new_data['close'])

        # Add to price history
        self._price_history.append(close_price)

        # Keep history reasonable size
        if len(self._price_history) > self.period * 2:
            self._price_history = self._price_history[-self.period * 2:]

        # Calculate SMA if we have enough data
        if len(self._price_history) >= self.period:
            sma = sum(self._price_history[-self.period:]) / self.period
            self.values.append(sma)
            return sma

        return None