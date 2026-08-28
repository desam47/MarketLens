"""
Exponential Moving Average (EMA) indicator
"""
from typing import Any

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
        closes = [float(d['close']) for d in data]

        # Calculate SMA for first EMA value
        sma = sum(closes[:self.period]) / self.period
        ema_values = [sma]
        self.prev_ema = sma

        # Calculate EMA for remaining values
        for i in range(self.period, len(closes)):
            ema = (closes[i] * self.multiplier) + (self.prev_ema * (1 - self.multiplier))
            ema_values.append(ema)
            self.prev_ema = ema

        self.values = ema_values
        return ema_values.copy()

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
