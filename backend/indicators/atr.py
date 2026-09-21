"""
Average True Range (ATR) indicator
"""

from typing import Any, cast

from .base_indicator import BaseIndicator


class ATRIndicator(BaseIndicator):
    """Average True Range indicator"""

    def __init__(self, period: int = 14):
        super().__init__("ATR", {"period": period})
        self.period = period
        self.true_ranges: list[float] = []

    def _calculate_true_range(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate True Range for the given data"""
        if len(data) < 2:
            return []

        true_ranges = []
        for i in range(1, len(data)):
            high = float(data[i]["high"])
            low = float(data[i]["low"])
            prev_close = float(data[i - 1]["close"])

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)

        return true_ranges

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate ATR for the given data"""
        if len(data) < self.period + 1:
            return []

        # Calculate True Range
        true_ranges = self._calculate_true_range(data)

        if len(true_ranges) < self.period:
            return []

        # Calculate ATR as moving average of True Range
        atr_values: list[float | None] = [None] * (
            self.period - 1
        )  # First 'period-1' values are undefined

        for i in range(self.period - 1, len(true_ranges)):
            atr = sum(true_ranges[i - self.period + 1 : i + 1]) / self.period
            atr_values.append(atr)

        # Filter out None values for clean return
        self.values = cast(list[float], [v for v in atr_values if v is not None])
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update ATR with new data point using O(1) Wilder's smoothing.

        Phase 20 perf fix: the previous implementation called
        ``calculate()`` on a sliding price window, which is O(period)
        per bar. This version maintains ``_smoothed_atr`` and the
        previous close as instance state and updates them in O(1) per
        bar.
        """
        high = float(new_data["high"])
        low = float(new_data["low"])
        close = float(new_data["close"])

        # ---- State init ----
        if not hasattr(self, "_smoothed_atr"):
            self._smoothed_atr: float | None = None
            self._prev_close: float | None = None
            self._tr_history: list[float] = []

        # First bar: no prev close, can't compute TR. Stash close.
        if self._prev_close is None:
            self._prev_close = close
            return None

        # Compute True Range for this bar
        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )
        self._prev_close = close

        if self._smoothed_atr is None:
            # ---- Warmup: collect self.period TRs to seed the average ----
            self._tr_history.append(tr)
            if len(self._tr_history) < self.period:
                return None
            # Seed the smoothed ATR with a simple average of the warmup
            # TRs. From this point onward, every new bar applies the
            # Wilder's smoothing formula: atr = (prev * (n-1) + tr) / n
            self._smoothed_atr = sum(self._tr_history) / self.period
            self._tr_history = []
            self.values.append(self._smoothed_atr)
            return self._smoothed_atr

        # ---- Incremental update (O(1)) ----
        self._smoothed_atr = (self._smoothed_atr * (self.period - 1) + tr) / self.period
        self.values.append(self._smoothed_atr)
        return self._smoothed_atr
