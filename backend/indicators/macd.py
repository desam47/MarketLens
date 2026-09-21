"""
Moving Average Convergence Divergence (MACD) indicator
"""

from typing import Any

import numpy as np

from .base_indicator import BaseIndicator
from .ema import EMAIndicator


class MACDIndicator(BaseIndicator):
    """Moving Average Convergence Divergence indicator"""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__("MACD", {"fast": fast, "slow": slow, "signal": signal})
        self.fast = fast
        self.slow = slow
        self.signal = signal

        # Create underlying EMAs
        self.ema_fast = EMAIndicator(fast)
        self.ema_slow = EMAIndicator(slow)
        self.ema_signal = EMAIndicator(signal)

        # Store MACD line, signal line, and histogram
        self.macd_line: list[float] = []
        self.signal_line: list[float] = []
        self.histogram: list[float] = []

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate MACD for the given data"""
        if not data or len(data) < self.slow:
            return []

        # Extract close prices
        closes = np.array([float(d["close"]) for d in data])

        # Calculate EMAs using the EMAIndicator instances
        ema_fast_values = self.ema_fast.calculate([{"close": c} for c in closes])
        ema_slow_values = self.ema_slow.calculate([{"close": c} for c in closes])

        # Calculate MACD line (fast EMA - slow EMA)
        macd_line = []

        # We need to align the arrays - EMAs start after their respective periods
        start_index = self.slow - 1  # Slow EMA determines when we have enough data

        for i in range(start_index, len(closes)):
            # Get EMA values (adjusting for offset into each EMA's own value list)
            fast_idx = i - (self.slow - self.fast)
            slow_idx = i - start_index

            if fast_idx < len(ema_fast_values) and slow_idx < len(ema_slow_values):
                macd = ema_fast_values[fast_idx] - ema_slow_values[slow_idx]
                macd_line.append(macd)

        # Calculate signal line (EMA of MACD line) using the EMAIndicator instance
        if len(macd_line) >= self.signal:
            signal_values = self.ema_signal.calculate([{"close": val} for val in macd_line])

            # Align MACD and signal lines for histogram calculation
            # The signal line will be shorter by (signal-1) values due to EMA warmup period
            if len(signal_values) > 0:
                # Calculate how many MACD values we need to drop to align with signal line
                macd_offset = len(macd_line) - len(signal_values)
                aligned_macd = macd_line[macd_offset:] if macd_offset > 0 else macd_line
                aligned_signal = signal_values

                if len(aligned_macd) > 0 and len(aligned_signal) > 0:
                    min_len = min(len(aligned_macd), len(aligned_signal))
                    if min_len > 0:
                        aligned_macd = aligned_macd[-min_len:]
                        aligned_signal = aligned_signal[-min_len:]
                        histogram = [
                            macd - sig
                            for macd, sig in zip(aligned_macd, aligned_signal, strict=False)
                        ]
                        self.values = histogram.copy()  # Store histogram as main values
                        return self.values.copy()

        # If we don't have enough data for signal line yet, return empty
        self.values = []
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update MACD with new data point.

        Phase 20 perf fix: the warmup of the signal line used
        ``sum(self.macd_line) / len(self.macd_line)`` which grew
        unboundedly. The canonical MACD signal warmup is the SMA of the
        first ``signal`` MACD values, then the standard EMA for every
        subsequent bar.
        """
        close_price = float(new_data["close"])

        # Update underlying EMAs
        fast_value = self.ema_fast.update({"close": close_price})
        slow_value = self.ema_slow.update({"close": close_price})

        # Calculate MACD line if we have both EMA values
        if fast_value is not None and slow_value is not None:
            macd_value = fast_value - slow_value
            self.macd_line.append(macd_value)

            # Calculate signal line if we have enough MACD values
            if len(self.macd_line) >= self.signal:
                if len(self.signal_line) < self.signal - 1:
                    # ---- Warmup: SMA of the first `signal` MACD values ----
                    # Only the first `signal` bars of macd_line contribute;
                    # afterwards we apply the standard EMA smoothing.
                    signal_value = sum(self.macd_line[-self.signal :]) / self.signal
                else:
                    # EMA calculation
                    signal_value = (macd_value * (2 / (self.signal + 1))) + (
                        self.signal_line[-1] * (1 - (2 / (self.signal + 1)))
                    )

                self.signal_line.append(signal_value)

                # Calculate histogram
                histogram_value = macd_value - signal_value
                self.values.append(histogram_value)
                return histogram_value

        return None
