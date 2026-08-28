"""
Swing High indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class SwingHighIndicator(BaseIndicator):
    """Swing High indicator - identifies local peaks in price"""

    def __init__(self, lookback_period: int = 2):
        """
        Initialize Swing High indicator

        Args:
            lookback_period: Number of periods to look back and forward to confirm swing
        """
        super().__init__("Swing_High", {"lookback_period": lookback_period})
        self.lookback_period = lookback_period
        self._high_history = []

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Swing High values for the given data"""
        if not data or len(data) < (self.lookback_period * 2 + 1):
            return []

        # Extract high prices
        highs = [float(d['high']) for d in data]

        # Add to history
        self._high_history.extend(highs)

        # Keep history reasonable size
        if len(self._high_history) > len(data) + self.lookback_period * 2:
            self._high_history = self._high_history[-(len(data) + self.lookback_period * 2):]

        # Calculate swing high values
        swing_values = []
        start_index = max(0, len(self._high_history) - len(highs))

        for i in range(len(highs)):
            history_index = start_index + i

            # Need enough data on both sides
            if (history_index < self.lookback_period or
                history_index >= len(self._high_history) - self.lookback_period):
                swing_values.append(None)
                continue

            # Check if current high is higher than lookback_period bars on both sides
            current_high = self._high_history[history_index]
            is_swing_high = True

            # Check left side
            for j in range(history_index - self.lookback_period, history_index):
                if self._high_history[j] >= current_high:
                    is_swing_high = False
                    break

            # Check right side (only if left side passed)
            if is_swing_high:
                for j in range(history_index + 1, history_index + self.lookback_period + 1):
                    if self._high_history[j] >= current_high:
                        is_swing_high = False
                        break

            if is_swing_high:
                swing_values.append(current_high)
            else:
                swing_values.append(None)

        # Store only the valid swing high values (non-None)
        self.values = [v for v in swing_values if v is not None]
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update Swing High with new data point"""
        high_price = float(new_data['high'])

        # Add to history
        self._high_history.append(high_price)

        # Keep only recent data needed for calculation
        # We need lookback_period on both sides, so keep at least lookback_period*2+1
        if len(self._high_history) > self.lookback_period * 2 + 10:
            self._high_history = self._high_history[-(self.lookback_period * 2 + 10):]

        # Need enough data to calculate swing high
        if len(self._high_history) < self.lookback_period * 2 + 1:
            return None

        # Check if the most recent completed bar is a swing high
        # We check the bar that is lookback_period positions ago (so we have lookback_period bars after it)
        check_index = len(self._high_history) - self.lookback_period - 1

        if check_index < self.lookback_period:
            return None

        current_high = self._high_history[check_index]
        is_swing_high = True

        # Check left side
        for j in range(check_index - self.lookback_period, check_index):
            if self._high_history[j] >= current_high:
                is_swing_high = False
                break

        # Check right side
        if is_swing_high:
            for j in range(check_index + 1, check_index + self.lookback_period + 1):
                if j >= len(self._high_history):
                    # We don't have future data yet, so can't confirm
                    return None
                if self._high_history[j] >= current_high:
                    is_swing_high = False
                    break

        if is_swing_high:
            self.values.append(current_high)
            return current_high

        return None
