"""
Relative Strength Index (RSI) indicator
"""
from typing import Any, cast

import numpy as np

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

        # Extract close prices as numpy array
        closes = np.array([float(d['close']) for d in data])

        # Calculate price changes (vectorized)
        changes = np.diff(closes)

        # Separate gains and losses (vectorized)
        gains = np.maximum(changes, 0)
        losses = np.maximum(-changes, 0)

        # Calculate initial average gain and loss (vectorized)
        avg_gain = np.mean(gains[:self.period])
        avg_loss = np.mean(losses[:self.period])

        # Calculate RSI
        rsi_values: list[float | None] = [None] * self.period  # First 'period' values are undefined

        if avg_loss == 0:
            rsi_values.append(100.0)  # Avoid division by zero
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            rsi_values.append(rsi)

        # Calculate remaining RSI values using Wilder's smoothing (vectorized approach)
        # We'll use the same iterative approach but with numpy arrays for efficiency
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
        self.values = cast(list[float], [v for v in rsi_values if v is not None])
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update RSI with new data point using O(1) Wilder's smoothing.

        Phase 20 perf fix: the previous implementation called
        ``calculate()`` on a sliding price window, which is O(period)
        per bar. This version maintains ``avg_gain`` and ``avg_loss`` as
        instance state and updates them in O(1) per bar.
        """
        close_price = float(new_data['close'])

        # ---- Warmup: collect enough data to seed the first RSI ----
        if not hasattr(self, '_price_history'):
            self._price_history = []
            self._prev_close: float | None = None
            self._avg_gain: float | None = None
            self._avg_loss: float | None = None

        if self._prev_close is not None:
            change = close_price - self._prev_close
            gain = max(change, 0.0)
            loss = max(-change, 0.0)

            if self._avg_gain is None:
                # Still accumulating warmup changes to compute first SMA.
                # We need self.period changes to seed the first average.
                self._price_history.append(gain)
                self._price_history.append(loss)
                if len(self._price_history) >= self.period * 2:
                    gains = self._price_history[0::2]
                    losses = self._price_history[1::2]
                    self._avg_gain = sum(gains[:self.period]) / self.period
                    self._avg_loss = sum(losses[:self.period]) / self.period
                    # Compute the first RSI value from those seed averages
                    # so callers see a value the moment we have enough data.
                    if self._avg_loss == 0:
                        rsi_value = 100.0
                    else:
                        rs = self._avg_gain / self._avg_loss
                        rsi_value = 100.0 - (100.0 / (1.0 + rs))
                    self.values.append(rsi_value)
                    # Trim warmup buffer — we no longer need the raw
                    # gain/loss history since state is in _avg_gain/loss.
                    self._price_history = []
                    return rsi_value
            else:
                # ---- Incremental update using Wilder's smoothing (O(1)) ----
                self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
                self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period

                if self._avg_loss == 0:
                    rsi_value = 100.0
                else:
                    rs = self._avg_gain / self._avg_loss
                    rsi_value = 100.0 - (100.0 / (1.0 + rs))

                self.values.append(rsi_value)
                return rsi_value

        self._prev_close = close_price
        return None
