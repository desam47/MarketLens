"""
Swing Low indicator
"""

from typing import Any, cast

from .base_indicator import BaseIndicator


class SwingLowIndicator(BaseIndicator):
    """Swing Low indicator - identifies local troughs in price"""

    def __init__(self, lookback_period: int = 2):
        """
        Initialize Swing Low indicator

        Args:
            lookback_period: Number of periods to look back and forward to confirm swing
        """
        super().__init__("Swing_Low", {"lookback_period": lookback_period})
        self.lookback_period = lookback_period
        self._low_history = []

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Swing Low values for the given data"""
        if not data or len(data) < (self.lookback_period * 2 + 1):
            return []

        # Extract low prices
        lows = [float(d["low"]) for d in data]

        # Add to history
        self._low_history.extend(lows)

        # Keep history reasonable size
        if len(self._low_history) > len(data) + self.lookback_period * 2:
            self._low_history = self._low_history[-(len(data) + self.lookback_period * 2) :]

        # Calculate swing low values
        swing_values: list[float | None] = []
        start_index = max(0, len(self._low_history) - len(lows))

        for i in range(len(lows)):
            history_index = start_index + i

            # Need enough data on both sides
            if (
                history_index < self.lookback_period
                or history_index >= len(self._low_history) - self.lookback_period
            ):
                swing_values.append(None)
                continue

            # Check if current low is lower than lookback_period bars on both sides
            current_low = self._low_history[history_index]
            is_swing_low = True

            # Check left side
            for j in range(history_index - self.lookback_period, history_index):
                if self._low_history[j] <= current_low:
                    is_swing_low = False
                    break

            # Check right side (only if left side passed)
            if is_swing_low:
                for j in range(history_index + 1, history_index + self.lookback_period + 1):
                    if self._low_history[j] <= current_low:
                        is_swing_low = False
                        break

            if is_swing_low:
                swing_values.append(current_low)
            else:
                swing_values.append(None)

        # Store only the valid swing low values (non-None)
        self.values = cast(list[float], [v for v in swing_values if v is not None])
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update Swing Low with new data point"""
        low_price = float(new_data["low"])

        # Add to history
        self._low_history.append(low_price)

        # Keep only recent data needed for calculation
        # We need lookback_period on both sides, so keep at least lookback_period*2+1
        if len(self._low_history) > self.lookback_period * 2 + 10:
            self._low_history = self._low_history[-(self.lookback_period * 2 + 10) :]

        # Need enough data to calculate swing low
        if len(self._low_history) < self.lookback_period * 2 + 1:
            return None

        # Check if the most recent completed bar is a swing low
        # We check the bar that is lookback_period positions ago (so we have lookback_period bars after it)
        check_index = len(self._low_history) - self.lookback_period - 1

        if check_index < self.lookback_period:
            return None

        current_low = self._low_history[check_index]
        is_swing_low = True

        # Check left side
        for j in range(check_index - self.lookback_period, check_index):
            if self._low_history[j] <= current_low:
                is_swing_low = False
                break

        # Check right side
        if is_swing_low:
            for j in range(check_index + 1, check_index + self.lookback_period + 1):
                if j >= len(self._low_history):
                    # We don't have future data yet, so can't confirm
                    return None
                if self._low_history[j] <= current_low:
                    is_swing_low = False
                    break

        if is_swing_low:
            self.values.append(current_low)
            return current_low

        return None
