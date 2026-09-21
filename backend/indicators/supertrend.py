"""
SuperTrend indicator
"""

from typing import Any

from .atr import ATRIndicator
from .base_indicator import BaseIndicator


class SuperTrendIndicator(BaseIndicator):
    """SuperTrend indicator

    Uses Wilder's smoothing for ATR (consistent across calculate() and update())
    and the Goessman final-band adjustment: in an uptrend the final lower band
    can only rise and in a downtrend the final upper band can only fall, each
    referenced against its own previous final value — not a shared prev value.

    An optional ``confirmation`` parameter requires the close to stay beyond the
    active band for ``confirmation`` additional consecutive bars after the
    initial breach before a trend flip is accepted. This dramatically reduces
    whipsaw flips in choppy/range-bound markets. ``confirmation=0`` (default)
    preserves the original immediate-flip behaviour (flips on the first
    breaching bar).

    ``band_distance_atr`` exposes how many ATRs of cushion price has over the
    active band (0 right after a flip, growing as the trend extends) so
    callers can weight this signal by conviction instead of treating a
    day-old flip the same as a month-old, well-confirmed trend.
    """

    def __init__(self, atr_period: int = 10, multiplier: float = 3.0, confirmation: int = 0):
        super().__init__(
            "SuperTrend",
            {"atr_period": atr_period, "multiplier": multiplier, "confirmation": confirmation},
        )
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.confirmation = confirmation
        self.prev_supertrend: float | None = None
        self.prev_direction: bool | None = None  # True for uptrend, False for downtrend
        # True until the first full bar is available.
        self.is_uptrend: bool | None = None
        # How many ATRs of cushion price has over the active band — 0 right
        # after a flip (close just barely broke the band), growing as the
        # trend extends. Lets callers weight this signal by conviction
        # instead of treating every flip as equally strong.
        self.band_distance_atr: float | None = None
        # Phase 20 perf: the online path uses an ATRIndicator that maintains
        # its own Wilder-smoothed ATR in O(1) per bar.
        self._atr_indicator = ATRIndicator(period=atr_period)
        # Confirmation counter (live + offline)
        self._confirm_count = 0
        # Separate final-band trackers for the Goessman adjustment
        self._prev_final_ub: float | None = None
        self._prev_final_lb: float | None = None

    def _calculate_atr(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate Average True Range using Wilder's smoothing.

        Phase 20 consistency fix: previously this returned a simple moving
        average of true ranges, which disagreed with the online ``update()``
        path (Wilder's smoothing). Using Wilder's smoothing everywhere ensures
        ``calculate()`` and ``update()`` produce equivalent ATR values.
        """
        if len(data) < 2:
            return []

        true_ranges: list[float] = []
        for i in range(1, len(data)):
            high = float(data[i]["high"])
            low = float(data[i]["low"])
            prev_close = float(data[i - 1]["close"])
            true_range = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(true_range)

        # Wilder smoothing: first value is SMA, then exponential:
        #   atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
        atr_values: list[float | None] = []
        for i in range(len(true_ranges)):
            if i < self.atr_period - 1:
                atr_values.append(None)
            elif i == self.atr_period - 1:
                # Seed with simple average of first `period` TRs
                atr = sum(true_ranges[i - self.atr_period + 1 : i + 1]) / self.atr_period
                atr_values.append(atr)
            else:
                prev_atr = atr_values[-1]
                assert prev_atr is not None
                atr = (prev_atr * (self.atr_period - 1) + true_ranges[i]) / self.atr_period
                atr_values.append(atr)

        # Filter out None values
        return [v for v in atr_values if v is not None]

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate SuperTrend for the given data.

        Mirrors the online ``update()`` path: Wilder ATR, Goessman final-band
        adjustment with separate upper/lower trackers, and optional confirmation
        before trend flips. Trend is seeded from data (``closes[period] >
        upperBand``) rather than forced bullish.
        """
        if len(data) < self.atr_period + 1:
            return []

        # Calculate ATR first (Wilder's smoothing — consistent with update())
        atr_values = self._calculate_atr(data)
        if len(atr_values) < 1:
            return []

        # Extract prices
        highs = [float(d["high"]) for d in data]
        lows = [float(d["low"]) for d in data]
        closes = [float(d["close"]) for d in data]

        # Reset confirmation state for a fresh calculate()
        self._confirm_count = 0

        supertrend_values: list[float | None] = []
        # Reset band trackers for a fresh calculate()
        self._prev_final_ub = None
        self._prev_final_lb = None

        # Calculate basic upper and lower bands
        for i in range(len(data)):
            if i < self.atr_period:
                # Not enough data for ATR calculation yet
                supertrend_values.append(None)
                continue

            # Current ATR value (aligned with data index)
            atr_idx = i - self.atr_period
            if atr_idx >= len(atr_values):
                supertrend_values.append(None)
                continue

            atr = atr_values[atr_idx]

            # Basic bands
            hl2 = (highs[i] + lows[i]) / 2
            basic_ub = hl2 + self.multiplier * atr
            basic_lb = hl2 - self.multiplier * atr

            if i == self.atr_period:
                # First calculation — seed trend from data.
                self._prev_final_ub = basic_ub
                self._prev_final_lb = basic_lb
                if closes[i] > basic_ub:
                    self.prev_direction = True  # Uptrend
                    self.prev_supertrend = basic_lb
                elif closes[i] < basic_lb:
                    self.prev_direction = False  # Downtrend
                    self.prev_supertrend = basic_ub
                else:
                    # Default to uptrend if close is between bands
                    self.prev_direction = True
                    self.prev_supertrend = basic_lb

                self.band_distance_atr = (
                    (
                        (closes[i] - self.prev_supertrend) / atr
                        if self.prev_direction
                        else (self.prev_supertrend - closes[i]) / atr
                    )
                    if atr > 0
                    else 0.0
                )

                supertrend_values.append(self.prev_supertrend)
            else:
                # ---- Goessman band adjustment (track both bands) ----
                # _prev_final_ub/_prev_final_lb are always set by now: the
                # i == self.atr_period branch above seeds both on the first
                # iteration of this loop.
                if self.prev_direction:  # Was in uptrend
                    final_ub = basic_ub
                    assert self._prev_final_lb is not None
                    final_lb = max(basic_lb, self._prev_final_lb)
                else:  # Was in downtrend
                    assert self._prev_final_ub is not None
                    final_ub = min(basic_ub, self._prev_final_ub)
                    final_lb = basic_lb

                # ---- Trend determination with optional confirmation ----
                # Strict inequalities: a close exactly on the band (e.g.
                # zero-ATR/flat data) must not trigger a flip, or flat
                # series oscillate direction every single bar.
                if self.prev_direction:  # Was in uptrend
                    if closes[i] < final_lb:
                        # Potential flip to downtrend
                        self._confirm_count += 1
                        if self._confirm_count > self.confirmation:
                            self.prev_direction = False
                            self.prev_supertrend = final_ub
                            self._confirm_count = 0
                        else:
                            self.prev_supertrend = final_lb
                    else:
                        self._confirm_count = 0
                        self.prev_supertrend = final_lb
                else:  # Was in downtrend
                    if closes[i] > final_ub:
                        # Potential flip to uptrend
                        self._confirm_count += 1
                        if self._confirm_count > self.confirmation:
                            self.prev_direction = True
                            self.prev_supertrend = final_lb
                            self._confirm_count = 0
                        else:
                            self.prev_supertrend = final_ub
                    else:
                        self._confirm_count = 0
                        self.prev_supertrend = final_ub

                # Persist final bands for next iteration
                self._prev_final_ub = final_ub
                self._prev_final_lb = final_lb

                self.band_distance_atr = (
                    (
                        (closes[i] - self.prev_supertrend) / atr
                        if self.prev_direction
                        else (self.prev_supertrend - closes[i]) / atr
                    )
                    if atr > 0
                    else 0.0
                )

                supertrend_values.append(self.prev_supertrend)

        # Filter out None values and store
        self.values = [v for v in supertrend_values if v is not None]
        # Set is_uptrend from the final direction so both calculate() and
        # update() agree on the trend signal after warm-up.
        self.is_uptrend = self.prev_direction
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update SuperTrend with new data point using O(1) ATR.

        Phase 20 perf fix: the previous implementation called ``calculate()``
        over a rolling buffer on every bar, which recomputes the ATR from
        scratch (O(period) per bar). This version composes an
        ``ATRIndicator`` that uses Wilder's smoothing — ATR is produced in
        O(1) per bar, and the SuperTrend bands are computed from that in
        O(1) as well.

        The SuperTrend state machine (which tracks the trend direction across
        bars) is inherently stateful and must remain; only the ATR
        computation is now O(1).
        """
        high = float(new_data["high"])
        low = float(new_data["low"])
        close = float(new_data["close"])

        # ---- O(1) ATR from the composed indicator ----
        atr = self._atr_indicator.update(
            {
                "high": high,
                "low": low,
                "close": close,
            }
        )
        if atr is None:
            # ATRIndicator still warming up — not enough TRs for a smoothed ATR yet.
            return None

        # ---- SuperTrend bands (O(1) from here) ----
        hl2 = (high + low) / 2.0
        basic_ub = hl2 + self.multiplier * atr
        basic_lb = hl2 - self.multiplier * atr

        # First valid bar: seed direction from where close sits relative to
        # the bands. Matches calculate()'s three-way seed exactly (defaults
        # to uptrend when close falls between the bands) — previously this
        # only checked `close > basic_ub`, treating "between the bands" as
        # downtrend and diverging from calculate() on the seed bar.
        if self.prev_supertrend is None:
            self._prev_final_ub = basic_ub
            self._prev_final_lb = basic_lb
            self.prev_direction = not (close < basic_lb)
            self.prev_supertrend = basic_lb if self.prev_direction else basic_ub
            self.band_distance_atr = (
                (
                    (close - self.prev_supertrend) / atr
                    if self.prev_direction
                    else (self.prev_supertrend - close) / atr
                )
                if atr > 0
                else 0.0
            )
            self.values.append(self.prev_supertrend)
            self.is_uptrend = self.prev_direction
            return self.prev_supertrend

        # ---- Goessman band adjustment (match calculate()) ----
        # _prev_final_ub/_prev_final_lb are always set by now: the
        # `self.prev_supertrend is None` branch above seeds both on the
        # first valid bar.
        if self.prev_direction:  # Was in uptrend
            final_ub = basic_ub
            assert self._prev_final_lb is not None
            final_lb = max(basic_lb, self._prev_final_lb)
            if close < final_lb:
                # Potential flip to downtrend
                self._confirm_count += 1
                if self._confirm_count > self.confirmation:
                    self.prev_direction = False
                    self.prev_supertrend = final_ub
                    self._confirm_count = 0
                else:
                    self.prev_supertrend = final_lb
            else:
                self._confirm_count = 0
                self.prev_supertrend = final_lb
        else:  # Was in downtrend
            final_lb = basic_lb
            assert self._prev_final_ub is not None
            final_ub = min(basic_ub, self._prev_final_ub)
            if close > final_ub:
                # Potential flip to uptrend
                self._confirm_count += 1
                if self._confirm_count > self.confirmation:
                    self.prev_direction = True
                    self.prev_supertrend = final_lb
                    self._confirm_count = 0
                else:
                    self.prev_supertrend = final_ub
            else:
                self._confirm_count = 0
                self.prev_supertrend = final_ub

        # Persist final bands for next iteration
        self._prev_final_ub = final_ub
        self._prev_final_lb = final_lb

        self.band_distance_atr = (
            (
                (close - self.prev_supertrend) / atr
                if self.prev_direction
                else (self.prev_supertrend - close) / atr
            )
            if atr > 0
            else 0.0
        )

        self.values.append(self.prev_supertrend)
        self.is_uptrend = self.prev_direction
        return self.prev_supertrend

    def reset(self):
        """Reset all internal state, including band trackers and ATR."""
        super().reset()
        self.prev_supertrend = None
        self.prev_direction = None
        self.is_uptrend = None
        self.band_distance_atr = None
        self._confirm_count = 0
        self._prev_final_ub = None
        self._prev_final_lb = None
        self._atr_indicator = ATRIndicator(period=self.atr_period)
