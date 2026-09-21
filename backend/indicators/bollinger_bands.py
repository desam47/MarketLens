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
        closes = [float(d["close"]) for d in data]

        # Calculate the middle band (SMA) directly off the closes — do NOT
        # delegate to ``self.sma.calculate()`` because that method mutates
        # ``self.sma._price_history`` and returns only the *new* SMA values
        # produced by this call, not a full-length series aligned to the
        # input. Computing the SMA inline keeps the bands aligned with the
        # input data on every fresh call.
        middle_band: list[float] = []
        for i in range(self.period - 1, len(closes)):
            window = closes[i - self.period + 1 : i + 1]
            middle_band.append(sum(window) / self.period)

        # Calculate standard deviation and bands
        upper_band: list[float] = []
        lower_band: list[float] = []
        bandwidth: list[float] = []
        percent_b: list[float] = []

        for i, middle_value in enumerate(middle_band):
            # Map back to the closes index.
            closes_idx = i + self.period - 1
            period_closes = closes[closes_idx - self.period + 1 : closes_idx + 1]
            mean = sum(period_closes) / self.period
            variance = sum((x - mean) ** 2 for x in period_closes) / self.period
            std_dev = variance**0.5

            # Calculate bands
            upper_value = middle_value + (self.std_dev * std_dev)
            lower_value = middle_value - (self.std_dev * std_dev)

            upper_band.append(upper_value)
            lower_band.append(lower_value)

            # Calculate bandwidth
            if middle_value != 0:
                bw = (upper_value - lower_value) / middle_value
                bandwidth.append(bw)
            else:
                bandwidth.append(0.0)

            # Calculate %B
            current_price = closes[closes_idx]
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
        """Update Bollinger Bands with new data point using O(1) running mean/std.

        Phase 20 perf fix: the previous implementation called
        ``calculate()`` over a rolling price window on every bar, which
        is O(period) per bar. This version maintains a Welford-style
        running mean / M2 of the last ``period`` closes, so the band
        values are computed in O(1) per bar.

        Algorithm:
          - Keep a deque of length ≤ ``period`` and the running mean
            and M2 (sum of squared deviations from the current mean).
          - On a new close, if the deque is full, the oldest value is
            evicted; mean and M2 are updated via the standard
            Welford subtract-and-add steps.
          - Once warm, ``variance = M2 / period`` and ``std_dev = sqrt(variance)``.
        """
        close_price = float(new_data["close"])

        # ---- State init ----
        if not hasattr(self, "_window"):
            from collections import deque

            self._window: deque[float] = deque()
            self._sum: float = 0.0  # sum of values in window
            self._sum_sq: float = 0.0  # sum of squares in window

        # ---- Add the new value ----
        self._window.append(close_price)
        self._sum += close_price
        self._sum_sq += close_price * close_price

        # ---- Maintain window size ----
        if len(self._window) > self.period:
            oldest = self._window.popleft()
            self._sum -= oldest
            self._sum_sq -= oldest * oldest

        if len(self._window) < self.period:
            # Still warming up
            return None

        # ---- O(1) stats over the window ----
        n = self.period
        mean = self._sum / n
        # Population variance (matches the offline calculate() path which
        # uses ``sum((x - mean) ** 2) / period`` rather than sample variance).
        # Numerically: var = (sum_sq / n) - mean^2. We use a guard against
        # tiny negative values from float drift before the sqrt.
        variance = (self._sum_sq / n) - (mean * mean)
        if variance < 0.0:
            variance = 0.0
        std_dev = variance**0.5

        upper = mean + self.std_dev * std_dev
        lower = mean - self.std_dev * std_dev
        bandwidth = (upper - lower) / mean if mean != 0.0 else 0.0
        percent_b = (close_price - lower) / (upper - lower) if upper != lower else 0.0

        # ---- Append to the historical series ----
        self.middle_band.append(mean)
        self.upper_band.append(upper)
        self.lower_band.append(lower)
        self.bandwidth.append(bandwidth)
        self.percent_b.append(percent_b)
        self.values.append(percent_b)

        return percent_b

    def get_bands(self) -> dict:
        """Get all Bollinger Bands values"""
        return {
            "upper": self.upper_band.copy(),
            "middle": self.middle_band.copy(),
            "lower": self.lower_band.copy(),
            "bandwidth": self.bandwidth.copy(),
            "percent_b": self.percent_b.copy(),
        }
