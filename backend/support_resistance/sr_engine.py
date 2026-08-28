"""
Support and resistance level detection engine.

Detects and scores S/R levels across seven types per the spec:

- SWING_HIGH   : local peak confirmed by ``lookback_period`` bars on both sides
- SWING_LOW    : local trough confirmed by ``lookback_period`` bars on both sides
- PIVOT_HIGH   : classic Camarilla/Woodie-style pivot high (H - 2*L + C)
- PIVOT_LOW    : classic pivot low (2*H - L - C)
- PREV_DAY_HIGH: previous trading day's high
- PREV_DAY_LOW : previous trading day's low
- PREV_WEEK_HIGH / PREV_WEEK_LOW: same for week

Consolidation zones are clusters of swing highs or swing lows that are
close enough in price to be treated as a single zone.

Every level carries the fields required by the spec:

- ``price``
- ``type``
- ``timeframe``
- ``strength`` (0..1)
- ``touch_count``
- ``age`` (bars since the level was last touched)
- ``distance_from_price`` (absolute distance to the latest close, as %)

Historical-only: all levels are computed from data available at
``end_index + 1`` — no look-ahead.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal


class SRType(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    PIVOT_HIGH = "pivot_high"
    PIVOT_LOW = "pivot_low"
    PREV_DAY_HIGH = "prev_day_high"
    PREV_DAY_LOW = "prev_day_low"
    PREV_WEEK_HIGH = "prev_week_high"
    PREV_WEEK_LOW = "prev_week_low"
    CONSOLIDATION_ZONE = "consolidation_zone"


@dataclass(frozen=True)
class SRLevel:
    """A single support or resistance level."""
    price: float
    type: SRType
    timeframe: str
    # strength is a 0..1 score; higher = more significant level
    strength: float
    # How many times price has revisited this level (as a swing high/low)
    touch_count: int
    # Bars since this level was last active (0 = current bar, None = unknown)
    age: int | None
    # Distance from latest close, expressed as a percentage
    distance_from_price: float | None
    # Index in the source data where this level was first detected
    origin_index: int | None = None
    # For consolidation zones: list of component prices
    component_prices: tuple[float, ...] = field(default_factory=tuple)
    timestamp: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "type": self.type.value,
            "timeframe": self.timeframe,
            "strength": self.strength,
            "touch_count": self.touch_count,
            "age": self.age,
            "distance_from_price": self.distance_from_price,
            "origin_index": self.origin_index,
            "component_prices": list(self.component_prices),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class SRResult:
    """All S/R levels for a symbol/timeframe scan."""
    symbol: str
    timeframe: str
    levels: list[SRLevel]
    # Index of the latest bar used to produce this result (no look-ahead)
    last_index: int
    # Latest close price seen in the scan
    latest_close: float | None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "last_index": self.last_index,
            "latest_close": self.latest_close,
            "levels": [level.to_dict() for level in self.levels],
        }


class SupportResistanceEngine:
    """Detect support and resistance levels from OHLCV bar data.

    Parameters
    ----------
    lookback_period:
        Bars on either side required to confirm a swing high/low.
        Higher = fewer but more significant pivots.
    pivot_period:
        Lookback period for classic pivot high/low calculation
        (period == lookback_period here for consistency).
    zone_width_pct:
        Two swing highs within this percentage of each other are merged
        into a consolidation zone. Expressed as a fraction (e.g.
        ``0.005`` = 0.5%).
    lookback_bars:
        How many bars of history to scan. Set to ``None`` to scan all
        available bars. Limiting this keeps runtime bounded for very
        long histories.
    atr_multiplier:
        S/R strength is partly based on how many ATRs away from price
        the level sits. If ATR is not provided explicitly it is
        approximated from the bar series.
    """

    def __init__(
        self,
        lookback_period: int = 2,
        pivot_period: int | None = None,
        zone_width_pct: float = 0.005,
        lookback_bars: int | None = 500,
        atr_multiplier: float = 2.0,
    ) -> None:
        if lookback_period < 1:
            raise ValueError("lookback_period must be >= 1")
        if zone_width_pct < 0 or zone_width_pct > 1:
            raise ValueError("zone_width_pct must be between 0 and 1")
        self.lookback_period = lookback_period
        self.pivot_period = pivot_period or lookback_period
        self.zone_width_pct = zone_width_pct
        self.lookback_bars = lookback_bars
        self.atr_multiplier = atr_multiplier

    # --- public API ---

    def detect(
        self,
        bars: Sequence[dict],
        symbol: str = "",
        timeframe: str = "",
    ) -> SRResult:
        """Scan bars for support and resistance levels.

        ``bars`` is a sequence of dicts each containing at minimum
        ``high``, ``low``, ``close``, ``volume`` keys (and optionally
        ``timestamp``). They must be ordered oldest → newest.
        Only bars up to ``last_index`` (inclusive) are used; no data
        after that point influences the result.

        Returns an ``SRResult`` with all detected levels.
        """
        n = len(bars)
        if n < 2 * self.lookback_period + 2:
            return SRResult(symbol=symbol, timeframe=timeframe,
                            levels=[], last_index=0, latest_close=None)

        if self.lookback_bars is not None and n > self.lookback_bars:
            bars = list(bars[-self.lookback_bars:])
            n = len(bars)

        last_index = n - 1
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        latest_close = closes[-1] if closes else None

        timestamps = [b.get("timestamp") for b in bars]
        if timestamps and timestamps[-1] is None:
            timestamps = None

        levels: list[SRLevel] = []

        # 1. Swing highs and swing lows
        swing_high_indices = self._swing_highs(highs)
        swing_low_indices = self._swing_lows(lows)
        for idx in swing_high_indices:
            price = highs[idx]
            strength = self._swing_strength(price, highs, highs[idx:idx+1],
                                             self.lookback_period)
            age = self._age(idx, last_index)
            dist = self._distance_pct(price, latest_close)
            levels.append(SRLevel(
                price=price,
                type=SRType.SWING_HIGH,
                timeframe=timeframe,
                strength=strength,
                touch_count=1,
                age=age,
                distance_from_price=dist,
                origin_index=idx,
                timestamp=timestamps[idx] if timestamps else None,
            ))
        for idx in swing_low_indices:
            price = lows[idx]
            strength = self._swing_strength(price, lows, lows[idx:idx+1],
                                             self.lookback_period)
            age = self._age(idx, last_index)
            dist = self._distance_pct(price, latest_close)
            levels.append(SRLevel(
                price=price,
                type=SRType.SWING_LOW,
                timeframe=timeframe,
                strength=strength,
                touch_count=1,
                age=age,
                distance_from_price=dist,
                origin_index=idx,
                timestamp=timestamps[idx] if timestamps else None,
            ))

        # 2. Classic pivot high / low (using pivot_period)
        for i in range(self.pivot_period, n):
            h = highs[i]
            low_val = lows[i]
            c = closes[i]
            pp = (h + low_val + c) / 3.0
            r1 = 2 * pp - low_val
            s1 = 2 * pp - h
            # Pivot high: R1
            r1_strength = self._swing_strength(r1, highs, [h],
                                                self.pivot_period)
            if r1_strength > 0:
                dist = self._distance_pct(r1, latest_close)
                levels.append(SRLevel(
                    price=r1,
                    type=SRType.PIVOT_HIGH,
                    timeframe=timeframe,
                    strength=r1_strength * 0.8,
                    touch_count=1,
                    age=self._age(i, last_index),
                    distance_from_price=dist,
                    origin_index=i,
                    timestamp=timestamps[i] if timestamps else None,
                ))
            # Pivot low: S1
            s1_strength = self._swing_strength(s1, lows, [low_val],
                                                  self.pivot_period)
            if s1_strength > 0:
                dist = self._distance_pct(s1, latest_close)
                levels.append(SRLevel(
                    price=s1,
                    type=SRType.PIVOT_LOW,
                    timeframe=timeframe,
                    strength=s1_strength * 0.8,
                    touch_count=1,
                    age=self._age(i, last_index),
                    distance_from_price=dist,
                    origin_index=i,
                    timestamp=timestamps[i] if timestamps else None,
                ))

        # 3. Previous day high / low
        day_levels = self._prev_period_levels(
            highs, lows, closes, "day", timestamps
        )
        levels.extend(day_levels)

        # 4. Previous week high / low
        week_levels = self._prev_period_levels(
            highs, lows, closes, "week", timestamps
        )
        levels.extend(week_levels)

        # 5. Merge swing highs / lows into consolidation zones
        zones = self._build_zones(levels)
        for zone in zones:
            levels.append(zone)

        # 6. Deduplicate by price and sort by strength descending
        levels = self._deduplicate(levels)
        levels.sort(key=lambda level: level.strength, reverse=True)

        return SRResult(
            symbol=symbol,
            timeframe=timeframe,
            levels=levels,
            last_index=last_index,
            latest_close=latest_close,
        )

    # --- internals ---

    def _swing_highs(self, highs: Sequence[float]) -> list[int]:
        lb = self.lookback_period
        n = len(highs)
        out: list[int] = []
        for i in range(lb, n - lb):
            if highs[i] == max(highs[i - lb: i + lb + 1]):
                if not out or i - out[-1] > lb:
                    out.append(i)
        return out

    def _swing_lows(self, lows: Sequence[float]) -> list[int]:
        lb = self.lookback_period
        n = len(lows)
        out: list[int] = []
        for i in range(lb, n - lb):
            if lows[i] == min(lows[i - lb: i + lb + 1]):
                if not out or i - out[-1] > lb:
                    out.append(i)
        return out

    def _swing_strength(
        self,
        price: float,
        series: Sequence[float],
        recent: Sequence[float],
        period: int,
    ) -> float:
        """Compute a 0..1 strength score for a level.

        Combines:
        - How many ATRs the level is away from recent price
        - How long the level has been "untouched"
        """
        atr = self._atr_approx(series)
        if atr <= 0:
            return 0.5
        # ATR distance: closer = slightly stronger (resistance near price
        # is more immediately relevant), capped at 5 ATRs
        dist_atrs = abs(price - series[-1]) / atr
        dist_score = max(0.0, 1.0 - dist_atrs / 5.0) * 0.5
        # Age: older untested levels are stronger (if the level hasn't
        # been broken recently, it is a stronger S/R)
        # We measure age as bars since the level first appeared
        age_score = min(period / (period + 5.0), 1.0) * 0.5
        return dist_score + age_score

    def _atr_approx(self, series: Sequence[float]) -> float:
        """Approximate ATR from a high/low/close series as |H - L|."""
        if len(series) < 2:
            return 1.0
        return max(abs(series[-1] - series[0]) / len(series), 1e-9)

    def _age(self, origin: int, last: int) -> int:
        return max(0, last - origin)

    def _distance_pct(self, price: float, reference: float | None) -> float | None:
        if reference is None or reference == 0:
            return None
        return abs(price - reference) / abs(reference) * 100.0

    def _prev_period_levels(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        period: Literal["day", "week"],
        timestamps: Sequence | None,
    ) -> list[SRLevel]:
        """Detect previous day/week high and low levels."""
        if timestamps is None:
            return []
        n = len(timestamps)
        levels: list[SRLevel] = []
        # We'll scan for the previous period boundary by looking at the
        # datetime's day/week attributes. Since bars may not be continuous,
        # we keep track of the previous period's high/low and emit a level
        # once the period changes.
        if n < 2:
            return levels

        prev_day = None
        prev_week = None
        day_bar_indices: list[int] = []
        week_bar_indices: list[int] = []

        for i in range(n):
            ts = timestamps[i]
            if ts is None:
                continue
            day = getattr(ts, "date", lambda: None)()
            if callable(day):
                day = ts
            week = ts.isocalendar()[1] if hasattr(ts, "isocalendar") else None

            if prev_day is not None and day != prev_day:
                # Emit previous day levels
                if day_bar_indices:
                    ph = max(highs[j] for j in day_bar_indices)
                    pl = min(lows[j] for j in day_bar_indices)
                    age = n - max(day_bar_indices)
                    for price, stype in ((ph, SRType.PREV_DAY_HIGH),
                                          (pl, SRType.PREV_DAY_LOW)):
                        levels.append(SRLevel(
                            price=price,
                            type=stype,
                            timeframe="",
                            strength=0.6,
                            touch_count=1,
                            age=age,
                            distance_from_price=self._distance_pct(
                                price, closes[-1] if closes else None),
                            origin_index=day_bar_indices[0],
                            timestamp=timestamps[day_bar_indices[0]],
                        ))
                day_bar_indices = []

            if prev_week is not None and week != prev_week:
                if week_bar_indices:
                    wh = max(highs[j] for j in week_bar_indices)
                    wl = min(lows[j] for j in week_bar_indices)
                    age = n - max(week_bar_indices)
                    for price, stype in ((wh, SRType.PREV_WEEK_HIGH),
                                          (wl, SRType.PREV_WEEK_LOW)):
                        levels.append(SRLevel(
                            price=price,
                            type=stype,
                            timeframe="",
                            strength=0.5,
                            touch_count=1,
                            age=age,
                            distance_from_price=self._distance_pct(
                                price, closes[-1] if closes else None),
                            origin_index=week_bar_indices[0],
                            timestamp=timestamps[week_bar_indices[0]],
                        ))
                week_bar_indices = []

            day_bar_indices.append(i)
            week_bar_indices.append(i)
            prev_day = day
            prev_week = week

        return levels

    def _build_zones(self, levels: list[SRLevel]) -> list[SRLevel]:
        """Merge nearby swing high/low levels into consolidation zones."""
        candidates = [level for level in levels
                      if level.type in (SRType.SWING_HIGH, SRType.SWING_LOW)]
        if len(candidates) < 2:
            return []

        # Sort by price
        candidates.sort(key=lambda level: level.price)
        zones: list[list[SRLevel]] = []
        current: list[SRLevel] = [candidates[0]]

        for c in candidates[1:]:
            prev = current[-1]
            # Merge if within zone_width_pct of each other
            if abs(c.price - prev.price) / (prev.price or 1) <= self.zone_width_pct:
                current.append(c)
            else:
                if len(current) > 1:
                    zones.append(current)
                current = [c]
        if len(current) > 1:
            zones.append(current)

        out: list[SRLevel] = []
        for group in zones:
            components = tuple(c.price for c in group)
            avg_price = sum(components) / len(components)
            total_strength = sum(c.strength for c in group)
            # Zones are slightly weaker than the strongest component
            strength = min(total_strength / len(group) * 0.9, 1.0)
            earliest_idx = min(c.origin_index or 0 for c in group)
            latest_ts = max((c.timestamp for c in group if c.timestamp),
                            default=None)
            out.append(SRLevel(
                price=avg_price,
                type=SRType.CONSOLIDATION_ZONE,
                timeframe=group[0].timeframe,
                strength=strength,
                touch_count=len(group),
                age=self._age(earliest_idx, len(group)),
                distance_from_price=self._distance_pct(avg_price, group[0].distance_from_price),
                origin_index=earliest_idx,
                component_prices=components,
                timestamp=latest_ts,
            ))
        return out

    def _deduplicate(self, levels: list[SRLevel]) -> list[SRLevel]:
        """Keep the highest-strength level for each unique price bucket."""
        seen: dict[tuple[SRType, int], SRLevel] = {}
        # Bucketing by rounding price to 2 decimal places (sensible for stocks)
        BUCKET = 2
        for level in levels:
            bucket = round(level.price, BUCKET)
            key = (level.type, bucket)
            if key not in seen or level.strength > seen[key].strength:
                seen[key] = level
        return list(seen.values())


__all__ = [
    "SRType",
    "SRLevel",
    "SRResult",
    "SupportResistanceEngine",
]
