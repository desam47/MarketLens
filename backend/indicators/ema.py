"""
Exponential Moving Average (EMA) indicator
"""
from typing import Any

import numpy as np

from .base_indicator import BaseIndicator


class EMAIndicator(BaseIndicator):
    """Exponential Moving Average indicator"""

    def __init__(self, period: int):
        super().__init__("EMA", {"period": period})
        self.period = period
        self.multiplier = 2 / (period + 1)
        self.prev_ema: float | None = None

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate EMA for the given data"""
        if not data or len(data) < self.period:
            return []

        # Extract close prices
        closes = np.array([float(d['close']) for d in data])
        period = self.period
        multiplier = 2 / (period + 1)
        alpha = multiplier
        beta = 1 - multiplier

        # Calculate SMA for first EMA value
        sma = closes[:period].mean()

        # Create array for convolution: c[i] = alpha * closes[i] for i >= period, else 0
        c = np.zeros_like(closes)
        c[period:] = alpha * closes[period:]

        # Create beta sequence: [beta^0, beta^1, ..., beta^{n-1}]
        beta_seq = beta ** np.arange(len(closes))

        # Compute s = convolve(c, beta_seq, mode='full') and take first len(closes) elements
        s = np.convolve(c, beta_seq, mode='full')[:len(closes)]

        # Compute term = sma * beta^{i - (period-1)} for each i
        exponents = np.arange(len(closes)) - (period - 1)
        term = sma * (beta ** exponents)

        # Full EMA array (including values before period-1, which we will discard)
        ema_full = term + s

        # Extract the valid EMA values (from index period-1 to end)
        ema_result = ema_full[period-1:]
        ema_list = ema_result.tolist()

        # Update state for update() method
        self.values = ema_list
        if ema_list:
            self.prev_ema = ema_list[-1]
        else:
            self.prev_ema = None

        return ema_list.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update EMA with new data point.

        Phase 20 perf fix: the warmup used to seed the first EMA with
        the close price itself, which produces a misleadingly high
        initial value for uptrends and low for downtrends. The
        canonical EMA warmup is a simple average of the first
        ``period`` closes, then apply the standard smoothing formula
        for every subsequent bar.
        """
        close_price = float(new_data['close'])

        if not hasattr(self, "_warmup_buffer"):
            self._warmup_buffer: list[float] = []

        if self.prev_ema is None:
            # ---- Warmup: accumulate until we have `period` closes ----
            self._warmup_buffer.append(close_price)
            if len(self._warmup_buffer) < self.period:
                return None
            # Seed EMA with the SMA of the first `period` closes.
            seed = sum(self._warmup_buffer) / self.period
            self.prev_ema = seed
            self.values.append(seed)
            self._warmup_buffer = []
            return seed

        # Standard EMA calculation
        ema = (close_price * self.multiplier) + (self.prev_ema * (1 - self.multiplier))
        self.prev_ema = ema
        self.values.append(ema)
        return ema
