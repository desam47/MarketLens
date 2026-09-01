"""
Average Directional Index (ADX) indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class ADXIndicator(BaseIndicator):
    """Average Directional Index indicator"""

    def __init__(self, period: int = 14):
        super().__init__("ADX", {"period": period})
        self.period = period
        self.dm_plus: list[float] = []
        self.dm_minus: list[float] = []
        self.tr: list[float] = []  # True Range

    def _calculate_dm_and_tr(self, data: list[dict[str, Any]]) -> tuple:
        """Calculate Directional Movement and True Range"""
        if len(data) < 2:
            return [], [], []

        dm_plus = []
        dm_minus = []
        tr = []

        for i in range(1, len(data)):
            high_curr = float(data[i]['high'])
            low_curr = float(data[i]['low'])
            high_prev = float(data[i-1]['high'])
            low_prev = float(data[i-1]['low'])
            close_prev = float(data[i-1]['close'])

            # Calculate Directional Movement
            up_move = high_curr - high_prev
            down_move = low_prev - low_curr

            if up_move > down_move and up_move > 0:
                dm_plus_val = up_move
            else:
                dm_plus_val = 0

            if down_move > up_move and down_move > 0:
                dm_minus_val = down_move
            else:
                dm_minus_val = 0

            dm_plus.append(dm_plus_val)
            dm_minus.append(dm_minus_val)

            # Calculate True Range
            tr1 = high_curr - low_curr
            tr2 = abs(high_curr - close_prev)
            tr3 = abs(low_curr - close_prev)
            tr_val = max(tr1, tr2, tr3)
            tr.append(tr_val)

        return dm_plus, dm_minus, tr

    def _wilder_smoothing(self, values: list[float], period: int) -> list[float]:
        """Apply Wilder's smoothing (similar to EMA but with 1/period)"""
        if len(values) < period:
            return [None] * len(values)

        smoothed = [None] * (period - 1)  # First 'period-1' values are undefined
        # First smoothed value is simple average
        first_avg = sum(values[:period]) / period
        smoothed.append(first_avg)

        # Subsequent values using Wilder's smoothing
        for i in range(period, len(values)):
            smoothed_val = (smoothed[i-1] * (period - 1) + values[i]) / period
            smoothed.append(smoothed_val)

        return smoothed

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate ADX for the given data"""
        if len(data) < self.period + 1:
            return []

        # Calculate DM and TR
        dm_plus, dm_minus, tr = self._calculate_dm_and_tr(data)

        if len(tr) < self.period:
            return []

        # Smooth DM and TR using Wilder's smoothing
        smoothed_dm_plus = self._wilder_smoothing(dm_plus, self.period)
        smoothed_dm_minus = self._wilder_smoothing(dm_minus, self.period)
        smoothed_tr = self._wilder_smoothing(tr, self.period)

        # Calculate DI+ and DI-
        di_plus = []
        di_minus = []
        dx = []

        for i in range(len(smoothed_tr)):
            # The first (period - 1) entries of Wilder-smoothed arrays
            # are None by construction; skip them to avoid division by None.
            if (
                smoothed_tr[i] is None
                or smoothed_dm_plus[i] is None
                or smoothed_dm_minus[i] is None
            ):
                di_plus_val = None
                di_minus_val = None
            elif smoothed_tr[i] == 0:
                di_plus_val = 0
                di_minus_val = 0
            else:
                di_plus_val = (smoothed_dm_plus[i] / smoothed_tr[i]) * 100
                di_minus_val = (smoothed_dm_minus[i] / smoothed_tr[i]) * 100

            di_plus.append(di_plus_val)
            di_minus.append(di_minus_val)

            # Calculate DX
            if di_plus_val is None or di_minus_val is None:
                dx_val = None
            elif di_plus_val + di_minus_val == 0:
                dx_val = 0
            else:
                dx_val = (
                    abs(di_plus_val - di_minus_val)
                    / (di_plus_val + di_minus_val)
                    * 100
                )
            dx.append(dx_val)

        # The first (period - 1) entries of dx are None by construction
        # (the underlying DI series hasn't been computed yet). Filter
        # them out before smoothing so _wilder_smoothing can operate on
        # numeric values only.
        dx_numeric = [v for v in dx if v is not None]

        # Smooth DX to get ADX
        adx = self._wilder_smoothing(dx_numeric, self.period)

        # Filter out None values and store
        self.values = [v for v in adx if v is not None]
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update ADX with new data point using O(1) Wilder's smoothing.

        Phase 20 perf fix: the previous implementation called
        ``calculate()`` on a sliding price window, which is O(period²)
        per bar (period for DM/TR + period for DX). This version
        maintains smoothed ``+DM`` / ``-DM`` / ``TR`` plus a rolling
        buffer of the last ``period`` DX values, all updated in O(1)
        per bar.

        Two-phase warmup:
          1. Accumulate ``period`` raw DM/TR values, then seed the
             smoothed +DM/-DM/TR averages.
          2. From that point, every new bar updates those smoothed
             values and appends a DX value to the rolling window. Once
             the window is full, we also stream-update the ADX.
        """
        high = float(new_data['high'])
        low = float(new_data['low'])
        close = float(new_data['close'])

        # ---- State init ----
        if not hasattr(self, '_smoothed_dm_plus'):
            self._smoothed_dm_plus: float | None = None
            self._smoothed_dm_minus: float | None = None
            self._smoothed_tr: float | None = None
            self._prev_high: float | None = None
            self._prev_low: float | None = None
            self._prev_close: float | None = None
            # Warmup buffers
            self._dm_plus_history: list[float] = []
            self._dm_minus_history: list[float] = []
            self._tr_history: list[float] = []
            # Rolling DX window for streaming ADX update
            self._dx_window: list[float] = []
            self._smoothed_adx: float | None = None

        # First bar: no previous data, stash for next call
        if self._prev_close is None:
            self._prev_high = high
            self._prev_low = low
            self._prev_close = close
            return None

        # ---- Compute +DM, -DM, TR for this bar ----
        up_move = high - self._prev_high
        down_move = self._prev_low - low

        if up_move > down_move and up_move > 0:
            dm_plus = up_move
        else:
            dm_plus = 0.0
        if down_move > up_move and down_move > 0:
            dm_minus = down_move
        else:
            dm_minus = 0.0

        tr1 = high - low
        tr2 = abs(high - self._prev_close)
        tr3 = abs(low - self._prev_close)
        tr = max(tr1, tr2, tr3)

        # ---- Update previous-bar snapshots for next iteration ----
        self._prev_high = high
        self._prev_low = low
        self._prev_close = close

        # ---- Phase 1: collect warmup values ----
        if self._smoothed_tr is None:
            self._dm_plus_history.append(dm_plus)
            self._dm_minus_history.append(dm_minus)
            self._tr_history.append(tr)
            if len(self._tr_history) < self.period:
                return None
            # Seed the smoothed averages from the warmup values
            self._smoothed_dm_plus = sum(self._dm_plus_history) / self.period
            self._smoothed_dm_minus = sum(self._dm_minus_history) / self.period
            self._smoothed_tr = sum(self._tr_history) / self.period
            self._dm_plus_history = []
            self._dm_minus_history = []
            self._tr_history = []
            # Fall through to compute the first DX for this bar.

        # ---- Phase 2: stream-update smoothed averages (O(1)) ----
        self._smoothed_dm_plus = (
            (self._smoothed_dm_plus * (self.period - 1) + dm_plus) / self.period
        )
        self._smoothed_dm_minus = (
            (self._smoothed_dm_minus * (self.period - 1) + dm_minus) / self.period
        )
        self._smoothed_tr = (
            (self._smoothed_tr * (self.period - 1) + tr) / self.period
        )

        # ---- Compute DI+/DI- and DX for this bar ----
        if self._smoothed_tr == 0:
            di_plus = di_minus = 0.0
        else:
            di_plus = (self._smoothed_dm_plus / self._smoothed_tr) * 100.0
            di_minus = (self._smoothed_dm_minus / self._smoothed_tr) * 100.0

        # Phase 6.1: surface DI+/DI- as instance attributes so
        # TrendEngine._calculate_trend can read them for directional scoring.
        self._di_plus = di_plus
        self._di_minus = di_minus

        di_sum = di_plus + di_minus
        if di_sum == 0:
            dx = 0.0
        else:
            dx = abs(di_plus - di_minus) / di_sum * 100.0

        # ---- Update the rolling DX window and stream-update ADX ----
        if self._smoothed_adx is None:
            # Accumulate DX values until we have a full period
            self._dx_window.append(dx)
            if len(self._dx_window) < self.period:
                return None
            self._smoothed_adx = sum(self._dx_window) / self.period
            self._dx_window = []
            self.values.append(self._smoothed_adx)
            return self._smoothed_adx

        self._smoothed_adx = (
            (self._smoothed_adx * (self.period - 1) + dx) / self.period
        )
        self.values.append(self._smoothed_adx)
        return self._smoothed_adx
