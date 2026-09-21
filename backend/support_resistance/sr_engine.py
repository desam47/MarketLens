"""
Support and resistance level detection engine.

Detects and scores S/R levels across seven types per the spec:

- SWING_HIGH   : local peak confirmed by ``lookback_period`` bars on both sides
- SWING_LOW    : local trough confirmed by ``lookback_period`` bars on both sides
- PIVOT_PP/R1/R2/R3 : classic pivot table (standard 5-point method) —
  P = (H+L+C)/3, R1 = 2P-L, R2 = P+(H-L), R3 = H+2(P-L); S mirrors.
- PIVOT_S1/S2/S3    : classic pivot table support levels (S1 = 2P-H,
  S2 = P-(H-L), S3 = L-2(H-P)).
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
- ``touch_count`` (how many times price revisited this level)
- ``age`` (bars since the level was last touched)
- ``origin_index`` (bar index where the level was first detected)

The signed percentage distance from the latest close is NOT stored on the
level — the frontend computes it directly from ``price`` and the latest
close (it is a display concern, and a backend value would drift from the
live price the UI shows). Keeping it off the contract avoids the penny-stock
``26397%`` nonsense that a stale/synthetic ``latest_close`` produced.

Historical-only: all levels are computed from data available at
``end_index + 1`` — no look-ahead.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal


class SRType(StrEnum):
    TODAY_HIGH = "today_high"
    TODAY_LOW = "today_low"
    PREV_DAY_HIGH = "prev_day_high"
    PREV_DAY_LOW = "prev_day_low"
    THIS_WEEK_HIGH = "this_week_high"
    THIS_WEEK_LOW = "this_week_low"
    PREV_WEEK_HIGH = "prev_week_high"
    PREV_WEEK_LOW = "prev_week_low"
    WEEK_52_HIGH = "week_52_high"
    WEEK_52_LOW = "week_52_low"
    PIVOT_PP = "pivot_pp"
    PIVOT_R1 = "pivot_r1"
    PIVOT_R2 = "pivot_r2"
    PIVOT_R3 = "pivot_r3"
    PIVOT_S1 = "pivot_s1"
    PIVOT_S2 = "pivot_s2"
    PIVOT_S3 = "pivot_s3"
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
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
        reference_bars: Sequence[dict] | None = None,
    ) -> SRResult:
        """Scan bars for support and resistance levels.

        ``bars`` is a sequence of dicts each containing at minimum
        ``high``, ``low``, ``close``, ``volume`` keys (and optionally
        ``timestamp``). They must be ordered newest → oldest (index 0 =
        the current/latest bar) — matches ``load_bars``'s ``desc=True``.
        Only bars up to ``last_index`` (inclusive) are used; no data
        after that point influences the result.

        ``reference_bars`` (optional) is a separate, typically coarser
        (e.g. daily) bar series used ONLY to compute the calendar-anchored
        levels — TODAY_HIGH/LOW, THIS_WEEK_HIGH/LOW, PREV_DAY_HIGH/LOW,
        PREV_WEEK_HIGH/LOW, WEEK_52_HIGH/LOW. Those six pairs describe
        calendar facts about the symbol (what did it do today / this
        week / over the trailing year), not facts about whichever chart
        timeframe the caller happens to be viewing — deriving them from
        ``bars`` instead made them drift between timeframes (e.g. "today's
        high" on the 1h chart differing from the 1m chart, or "52-week
        high" only reaching back as far as that timeframe's own bar limit).
        Swing highs/lows, pivots, and consolidation zones remain
        genuinely timeframe-specific and are still computed from
        ``bars``. When omitted, falls back to using ``bars`` itself for
        the calendar levels too (single-series behavior, e.g. for tests).

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
        volumes = [b.get("volume", 0) or 0 for b in bars]
        # Bars arrive newest→oldest (desc=True), so closes[0] is the latest close.
        latest_close = closes[0] if closes else None

        timestamps = [b.get("timestamp") for b in bars]
        if timestamps and timestamps[-1] is None:
            timestamps = None

        # Calendar-anchored levels (steps 1-5 below) are computed from this
        # series, not `bars`/`highs`/`lows`/`timestamps` — see docstring.
        ref = bars if not reference_bars else reference_bars
        ref_n = len(ref)
        ref_highs = [b["high"] for b in ref]
        ref_lows = [b["low"] for b in ref]
        ref_closes = [b["close"] for b in ref]
        ref_volumes = [b.get("volume", 0) or 0 for b in ref]
        ref_timestamps = [b.get("timestamp") for b in ref]
        if ref_timestamps and ref_timestamps[-1] is None:
            ref_timestamps = None

        # True ATR for the two source series. Swing/pivot levels are scored
        # against the ATR of the series they actually come from (bars vs ref),
        # so distance relevance is consistent for each level type.
        atr_bars = self._true_atr(highs, lows, closes)
        atr_ref = self._true_atr(ref_highs, ref_lows, ref_closes)

        levels: list[SRLevel] = []

        # 1. Today's high / low — aggregate every bar matching ref[0]'s
        #    calendar date (ref arrives newest→oldest). For daily bars this
        #    is just ref[0]'s own OHLC; for intraday reference bars using
        #    ref[0] alone was wrong — a single 1-minute candle's high/low is
        #    nowhere near the full session's actual range. Computed from
        #    `ref`, not `bars`, so this is identical across all chart
        #    timeframes for a given symbol at a given moment.
        today_indices: list[int] = []
        if ref_n > 0:
            today_indices = [0]
            today_date = None
            if ref_timestamps and ref_timestamps[0] is not None:
                today_date = getattr(ref_timestamps[0], "date", lambda: None)()
            if today_date is not None:
                for i in range(1, ref_n):
                    ts = ref_timestamps[i]
                    bar_date = getattr(ts, "date", lambda: None)() if ts is not None else None
                    if bar_date != today_date:
                        break
                    today_indices.append(i)

            today_high = max(ref_highs[i] for i in today_indices)
            today_low = min(ref_lows[i] for i in today_indices)
            for price, stype in ((today_high, SRType.TODAY_HIGH),
                                  (today_low, SRType.TODAY_LOW)):
                levels.append(SRLevel(
                    price=price,
                    type=stype,
                    timeframe=timeframe,
                    strength=0.8,
                    touch_count=1,
                    age=0,
                    origin_index=0,
                    timestamp=ref_timestamps[0] if ref_timestamps else None,
                ))

        # 2. This week's running high / low — scan from newest to oldest
        #     until we hit a week boundary. Computed from `ref` (see step 1).
        #     NOTE: uses ISO calendar weeks (Mon–Sun); a Sunday session can
        #     fall in the prior ISO week, so "this week" may occasionally
        #     start Monday rather than the market's trading week. Acceptable
        #     for a reference level, but documented as a known boundary case.
        if ref_timestamps:
            this_week = getattr(ref_timestamps[0], "isocalendar", lambda: (None, None, None))()
            if callable(this_week[0]):
                this_week = None
            else:
                this_week = this_week[:2]  # (year, week_number)
        else:
            this_week = None

        this_week_bar_count = 0
        if this_week:
            week_high = ref_highs[0]
            week_low = ref_lows[0]
            last_in_week_idx = 0
            for i in range(ref_n):
                ts = ref_timestamps[i]
                wk = getattr(ts, "isocalendar", lambda: (None, None, None))()
                if callable(wk[0]):
                    break
                if wk[:2] != this_week:
                    break
                week_high = max(week_high, ref_highs[i])
                week_low = min(week_low, ref_lows[i])
                last_in_week_idx = i
                this_week_bar_count = i + 1
            # Always emit this week's H/L — they may match today's H/L
            # (which is fine; the engine dedup pass will keep the strongest).
            # age/origin_index/timestamp point at the *oldest bar still in
            # this week* (last_in_week_idx), not the oldest bar in the whole
            # ref array — otherwise this drifts arbitrarily stale on any
            # fetch window wider than a week.
            levels.append(SRLevel(
                price=week_high, type=SRType.THIS_WEEK_HIGH,
                timeframe=timeframe, strength=0.75, touch_count=1,
                age=self._age(last_in_week_idx),
                origin_index=last_in_week_idx,
                timestamp=ref_timestamps[last_in_week_idx] if ref_timestamps else None,
            ))
            levels.append(SRLevel(
                price=week_low, type=SRType.THIS_WEEK_LOW,
                timeframe=timeframe, strength=0.75, touch_count=1,
                age=self._age(last_in_week_idx),
                origin_index=last_in_week_idx,
                timestamp=ref_timestamps[last_in_week_idx] if ref_timestamps else None,
            ))

        # 3. Previous day high / low — skip the entire *today* group (not just
        #     ref[0]) so the first detected day change is yesterday→day
        #     before. Keep only the most recent one of each type. Computed
        #     from `ref` (see step 1).
        day_skip = len(today_indices) if ref_n > 0 else 0
        if ref_n > day_skip:
            day_levels = self._prev_period_levels(
                ref_highs[day_skip:], ref_lows[day_skip:], "day",
                ref_timestamps[day_skip:] if ref_timestamps else None,
                index_offset=day_skip, latest_close=latest_close,
            )
            day_levels = self._keep_most_recent_of_each_type(day_levels)
            levels.extend(day_levels)

        # 4. Previous week high / low — skip the entire *this week* group
        #     (not just ref[0]) so the first detected week change is
        #     prev_week→week before. Keep only the most recent one of each
        #     type. Computed from `ref` (see step 1).
        week_skip = this_week_bar_count or day_skip
        if ref_n > week_skip:
            week_levels = self._prev_period_levels(
                ref_highs[week_skip:], ref_lows[week_skip:], "week",
                ref_timestamps[week_skip:] if ref_timestamps else None,
                index_offset=week_skip, latest_close=latest_close,
            )
            week_levels = self._keep_most_recent_of_each_type(week_levels)
            levels.extend(week_levels)

        # 5. 52-week high / low — highest high and lowest low over the
        #     trailing 52 weeks (364 days) of the reference dataset, not the
        #     entire history (see step 1). Falls back to the full reference
        #     series when timestamps aren't available to bound the window.
        _WEEK_52_DAYS = 364
        window_highs, window_lows, window_indices = ref_highs, ref_lows, list(range(ref_n))
        if ref_timestamps and ref_timestamps[0] is not None:
            try:
                cutoff = ref_timestamps[0] - timedelta(days=_WEEK_52_DAYS)
            except TypeError:
                cutoff = None
            if cutoff is not None:
                window_highs, window_lows, window_indices = [], [], []
                for i, ts in enumerate(ref_timestamps):
                    if ts is not None and ts >= cutoff:
                        window_highs.append(ref_highs[i])
                        window_lows.append(ref_lows[i])
                        window_indices.append(i)

        if window_highs:
            w52_high_price = max(window_highs)
            w52_low_price = min(window_lows)
            w52_high_idx = window_indices[window_highs.index(w52_high_price)]
            w52_low_idx = window_indices[window_lows.index(w52_low_price)]
            for price, idx, stype in (
                (w52_high_price, w52_high_idx, SRType.WEEK_52_HIGH),
                (w52_low_price, w52_low_idx, SRType.WEEK_52_LOW),
            ):
                levels.append(SRLevel(
                    price=price, type=stype, timeframe=timeframe,
                    strength=0.9, touch_count=1,
                    age=self._age(idx),
                        origin_index=idx,
                    timestamp=ref_timestamps[idx] if ref_timestamps else None,
                ))

        # 6. Swing highs / lows — local peaks / troughs confirmed by lookback_period bars
        # Filter out noise: only keep swings with strength >= 0.1
        SWING_STRENGTH_FLOOR = 0.1
        swing_high_indices = self._swing_highs(highs)
        swing_low_indices = self._swing_lows(lows)
        for idx in swing_high_indices:
            price = highs[idx]
            age = self._age(idx)
            touch_count = self._count_touches(price, highs)
            volume_ratio = self._volume_ratio(idx, volumes)
            strength = self._swing_strength(
                price, latest_close, age, touch_count, volume_ratio, atr_bars,
            )
            if strength < SWING_STRENGTH_FLOOR:
                continue
            levels.append(SRLevel(
                price=price, type=SRType.SWING_HIGH, timeframe=timeframe,
                strength=strength, touch_count=touch_count,
                age=age,
                origin_index=idx,
                timestamp=timestamps[idx] if timestamps else None,
            ))
        for idx in swing_low_indices:
            price = lows[idx]
            age = self._age(idx)
            touch_count = self._count_touches(price, lows)
            volume_ratio = self._volume_ratio(idx, volumes)
            strength = self._swing_strength(
                price, latest_close, age, touch_count, volume_ratio, atr_bars,
            )
            if strength < SWING_STRENGTH_FLOOR:
                continue
            levels.append(SRLevel(
                price=price, type=SRType.SWING_LOW, timeframe=timeframe,
                strength=strength, touch_count=touch_count,
                age=age,
                origin_index=idx,
                timestamp=timestamps[idx] if timestamps else None,
            ))

        # 7. Classic pivot table (PP / R1-R3 / S1-S3) — the standard
        #    5-point pivot set, derived from the PRIOR completed session's
        #    aggregated H/L/C (the conventional pivot-point convention: one
        #    pivot set per session, not one per bar). Computed from `ref` —
        #    the same calendar-anchored series as prev_day/prev_week — so
        #    pivots are timeframe-invariant too. `_prev_day_ohlc`'s
        #    day-grouping works whether `ref` is genuinely daily (production)
        #    or intraday (the reference_bars=None fallback, e.g. in tests).
        #    `day_skip` (computed in step 3) already excludes *today's* group.
        #
        #    Resistance levels (R1-R3) are only emitted when they sit ABOVE
        #    the latest close; support levels (PP and S1-S3) only when at or
        #    below it. That keeps the table free of levels the price has
        #    already blown through (e.g. when close is near the highs, the R1
        #    that's now below price is dropped), so the panel shows what's
        #    actually ahead of the market.
        prev_session = self._prev_day_ohlc(
            ref_highs[day_skip:], ref_lows[day_skip:], ref_closes[day_skip:],
            ref_timestamps[day_skip:] if ref_timestamps else None,
            index_offset=day_skip,
        )
        if prev_session is not None and latest_close is not None:
            sh, sl, sc, s_origin = prev_session
            pp = (sh + sl + sc) / 3.0
            r1 = 2 * pp - sl
            r2 = pp + (sh - sl)
            r3 = sh + 2 * (pp - sl)
            s1 = 2 * pp - sh
            s2 = pp - (sh - sl)
            s3 = sl - 2 * (sh - pp)
            aget = self._age(s_origin)
            pivot_specs = [
                # (type, price, is_resistance)
                (SRType.PIVOT_R3, r3, True),
                (SRType.PIVOT_R2, r2, True),
                (SRType.PIVOT_R1, r1, True),
                (SRType.PIVOT_PP, pp, False),
                (SRType.PIVOT_S1, s1, False),
                (SRType.PIVOT_S2, s2, False),
                (SRType.PIVOT_S3, s3, False),
            ]
            # Emit the full 5-point pivot table (R3..S3) unconditionally —
            # a classic pivot table always shows all seven levels regardless
            # of where the latest close sits. Side filtering previously hid
            # R-levels on up days and S-levels on down days, which made the
            # panel look incomplete. The frontend still colors them
            # resistances (red) vs supports (green) and the AI context
            # consumer re-buckets by price <= latest_close, so nothing else
            # depends on the old asymmetry.
            for stype, price, is_resistance in pivot_specs:
                touch_count = self._count_touches(
                    price, ref_highs if is_resistance else ref_lows
                )
                volume_ratio = self._volume_ratio(s_origin, ref_volumes)
                strength = self._swing_strength(
                    price, latest_close, aget, touch_count, volume_ratio, atr_ref,
                )
                levels.append(SRLevel(
                    price=price, type=stype, timeframe=timeframe,
                    strength=strength, touch_count=touch_count,
                    age=aget,
                    origin_index=s_origin,
                    timestamp=ref_timestamps[s_origin] if ref_timestamps else None,
                ))

        # 8. Merge levels into consolidation zones
        zones = self._build_zones(levels, latest_close)
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
        latest_price: float | None,
        age: int,
        touch_count: int = 1,
        volume_ratio: float = 1.0,
        atr: float | None = None,
    ) -> float:
        """Compute a 0..1 strength score for a level.

        Weighted blend of four independently-normalized components:
        - ``dist_score``   (40%): how close the level is to the current
          price, in ATRs — closer = more immediately relevant.
        - ``age_score``    (30%): how long the level has existed, in bars
          — older untested levels are more significant.
        - ``touch_score``  (15%): how many times price has revisited this
          level (see ``_count_touches``) — more touches = more respected.
        - ``volume_score`` (15%): relative volume at the level's origin
          bar (see ``_volume_ratio``) — a level formed on high volume is
          more likely to matter than one formed on a quiet bar.

        ``latest_price`` must be the actual current/latest price. ``age``
        must be the level's own bar age (e.g. via ``self._age(idx)``), not
        a constant configured lookback — a constant would make every level
        in a category score identically regardless of how old it is.
        ``atr`` is the *true* Average True Range of the source series
        (see ``_true_atr``), passed in by the caller so it reflects the
        same bars the level came from. Falls back to 0.5 when ATR can't be
        computed.
        """
        if atr is None or atr <= 0 or latest_price is None:
            return 0.5
        dist_atrs = abs(price - latest_price) / atr
        dist_score = max(0.0, 1.0 - dist_atrs / 5.0)
        age_score = min(age / 50.0, 1.0)
        touch_score = min(max(touch_count - 1, 0) / 4.0, 1.0)
        volume_score = min(max(volume_ratio, 0.0) / 3.0, 1.0)
        return (
            dist_score * 0.40
            + age_score * 0.30
            + touch_score * 0.15
            + volume_score * 0.15
        )

    def _true_atr(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
    ) -> float:
        """Average True Range over the (newest→oldest) OHLC series.

        True range per bar = max(H − L, |H − prev_close|, |L − prev_close|),
        averaged across the series. This replaces the old ``_atr_approx``
        which used (total price range ÷ bar count) — that metric conflated
        a steady multi-week trend with high volatility, inflating the
        denominator and silently driving every level's distance score
        toward zero (it was 40% of the strength blend).
        """
        n = len(highs)
        if n < 2 or len(lows) < 2 or len(closes) < 2:
            return 1.0
        trs: list[float] = []
        for i in range(1, n):
            h, low, c_prev = highs[i], lows[i], closes[i - 1]
            trs.append(max(h - low, abs(h - c_prev), abs(low - c_prev)))
        atr = sum(trs) / len(trs)
        return atr if atr > 0 else 1.0

    def _count_touches(self, price: float, series: Sequence[float]) -> int:
        """Count bars in ``series`` within ``zone_width_pct`` of ``price``.

        Gives swings/pivots a real touch count instead of the previous
        hardcoded ``1`` — a level price touches by chance once (its own
        origin bar) at minimum, so this is always >= 1 for a price that
        actually occurs in ``series``.
        """
        if not series:
            return 1
        tolerance = abs(price) * self.zone_width_pct
        return sum(1 for v in series if abs(v - price) <= tolerance) or 1

    def _volume_ratio(self, idx: int, volumes: Sequence[float]) -> float:
        """Volume at ``idx`` relative to the series' average volume.

        Returns 1.0 (neutral) when volumes are missing/empty/all-zero or
        ``idx`` is out of range, rather than raising or dividing by zero.
        """
        if not volumes or idx < 0 or idx >= len(volumes):
            return 1.0
        avg = sum(volumes) / len(volumes)
        if avg <= 0:
            return 1.0
        return volumes[idx] / avg

    def _age(self, origin_index: int | None) -> int:
        """Bars since this level's origin bar.

        Bars arrive newest→oldest (index 0 = current bar), so a level's
        age in bars is simply its own index — no "last bar" reference
        needed. (Previously computed as ``last_index - origin_index``,
        which assumed the opposite, oldest→newest ordering and produced
        inverted ages: a level from minutes ago showed a huge age while
        one from hours ago showed a tiny one.)

        ``origin_index`` is ``int | None``; treat ``None`` as "age unknown"
        and fall back to 0 rather than crashing on ``max(0, None)``.
        """
        if origin_index is None:
            return 0
        return max(0, origin_index)

    def _prev_period_levels(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        period: Literal["day", "week"],
        timestamps: Sequence | None,
        index_offset: int = 0,
        latest_close: float | None = None,
    ) -> list[SRLevel]:
        """Detect previous day OR previous week high and low levels.

        Only emits levels for the requested ``period`` ('day' or 'week'),
        not both. This lets callers request day and week levels independently.

        ``highs``/``lows``/``timestamps`` are a slice of the full bars array
        (the caller has already trimmed off the current day/week's own
        bars); ``index_offset`` is that slice's starting position in the
        full array, so origin_index/age reflect true bar distance rather
        than a position within the slice. ``latest_close`` is the true
        latest close (not the slice's own [0], which is already stale by
        definition since the slice excludes the most recent period).
        """
        if timestamps is None:
            return []
        n = len(timestamps)
        levels: list[SRLevel] = []
        if n < 2:
            return levels

        is_day = period == "day"
        prev_period: Any = None
        bar_indices: list[int] = []
        hi_type: type[SRType]
        lo_type: type[SRType]
        if is_day:
            hi_type, lo_type = SRType.PREV_DAY_HIGH, SRType.PREV_DAY_LOW
        else:
            hi_type, lo_type = SRType.PREV_WEEK_HIGH, SRType.PREV_WEEK_LOW

        for i in range(n):
            ts = timestamps[i]
            if ts is None:
                continue
            if is_day:
                cur = getattr(ts, "date", lambda: None)()
                if callable(cur):
                    cur = ts
            else:
                cur = ts.isocalendar()[1] if hasattr(ts, "isocalendar") else None

            if prev_period is not None and cur != prev_period:
                if bar_indices:
                    ph = max(highs[j] for j in bar_indices)
                    pl = min(lows[j]  for j in bar_indices)
                    # bar_indices is ascending within this slice; since bars
                    # run newest→oldest, the smallest index is this group's
                    # most recent bar — that's the level's true origin.
                    origin_idx = bar_indices[0] + index_offset
                    age = self._age(origin_idx)
                    for price, stype in ((ph, hi_type), (pl, lo_type)):
                        levels.append(SRLevel(
                            price=price,
                            type=stype,
                            timeframe="",
                            strength=0.6 if is_day else 0.5,
                            touch_count=1,
                            age=age,
                            origin_index=origin_idx,
                            timestamp=timestamps[bar_indices[0]],
                        ))
                bar_indices = []

            bar_indices.append(i)
            prev_period = cur

        return levels

    def _prev_day_ohlc(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        timestamps: Sequence | None,
        index_offset: int = 0,
    ) -> tuple[float, float, float, int] | None:
        """Aggregate H/L/C for the most recent complete calendar day.

        ``highs``/``lows``/``closes``/``timestamps`` are a slice with the
        current/"today" group already trimmed off by the caller (mirrors
        ``_prev_period_levels``'s day-grouping so it works whether the
        slice is genuinely daily bars or intraday bars grouped by date()).
        Returns ``(high, low, close, origin_index)`` for that day, where
        ``close`` is the most recent (smallest-index) bar's close — bars
        run newest→oldest, so that's the session's actual closing price —
        or ``None`` if there's no complete day in the slice.
        """
        if not timestamps:
            return None
        bar_indices: list[int] = []
        day = None
        for i, ts in enumerate(timestamps):
            if ts is None:
                continue
            cur = getattr(ts, "date", lambda: None)()
            if cur is None:
                continue
            if day is not None and cur != day:
                break
            bar_indices.append(i)
            day = cur
        if not bar_indices:
            return None
        high = max(highs[j] for j in bar_indices)
        low = min(lows[j] for j in bar_indices)
        close = closes[bar_indices[0]]
        origin_index = bar_indices[0] + index_offset
        return high, low, close, origin_index

    def _build_zones(
        self, levels: list[SRLevel], latest_close: float | None = None
    ) -> list[SRLevel]:
        """Merge nearby resistance (high) and support (low) levels into zones."""
        candidates = [level for level in levels
                      if level.type in (
                          SRType.TODAY_HIGH, SRType.PREV_DAY_HIGH,
                          SRType.THIS_WEEK_HIGH, SRType.PREV_WEEK_HIGH,
                          SRType.WEEK_52_HIGH, SRType.WEEK_52_LOW,
                          SRType.TODAY_LOW, SRType.PREV_DAY_LOW,
                          SRType.THIS_WEEK_LOW, SRType.PREV_WEEK_LOW,
                          SRType.SWING_HIGH, SRType.SWING_LOW,
                      )]
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
            # Components can originate from two different bar arrays now
            # (calendar-anchored levels from `reference_bars`, technical
            # levels — swings — from `bars`), so their origin_index values
            # are not comparable across the group. Each component's own
            # `.age` is already computed correctly against its own source
            # array; pick the youngest component directly instead of
            # recomputing via min(origin_index), which would incorrectly
            # mix the two index spaces.
            youngest = min(group, key=lambda c: c.age if c.age is not None else 0)
            latest_ts = max((c.timestamp for c in group if c.timestamp),
                            default=None)
            out.append(SRLevel(
                price=avg_price,
                type=SRType.CONSOLIDATION_ZONE,
                timeframe=group[0].timeframe,
                strength=strength,
                touch_count=len(group),
                age=youngest.age,
                origin_index=youngest.origin_index,
                component_prices=components,
                timestamp=latest_ts,
            ))
        return out

    def _deduplicate(self, levels: list[SRLevel]) -> list[SRLevel]:
        """Keep the highest-strength level for each (type, exact price) pair.

        Using exact price (rounded to 4dp) prevents distinct levels that are
        close but meaningful (e.g. today's high vs prev_day_high on penny stocks)
        from being incorrectly merged.

        Each prev_* type (prev_day_high, prev_day_low, prev_week_high, prev_week_low)
        is a distinct temporal context — they are kept separately even at the same price.
        """
        seen: dict[tuple[SRType, float], SRLevel] = {}
        for level in levels:
            key = (level.type, round(level.price, 4))
            if key not in seen or level.strength > seen[key].strength:
                seen[key] = level
        return list(seen.values())

    def _keep_most_recent_of_each_type(
        self, levels: list[SRLevel]
    ) -> list[SRLevel]:
        """Keep only the most recent level per SRType.

        Bars are ordered newest→oldest, so lower origin_index = more recent.
        We keep the level with the smallest origin_index per type.
        """
        by_type: dict[SRType, SRLevel] = {}
        for level in levels:
            if level.type not in by_type:
                by_type[level.type] = level
            elif self._is_more_recent(level, by_type[level.type]):
                by_type[level.type] = level
        return list(by_type.values())

    @staticmethod
    def _is_more_recent(
        candidate: SRLevel, incumbent: SRLevel
    ) -> bool:
        """Return ``True`` if ``candidate`` is more recent than ``incumbent``.

        Bars are ordered newest→oldest, so a *smaller* ``origin_index`` means
        more recent. ``origin_index`` is ``int | None`` on ``SRLevel``; a ``None``
        value can't be compared with ``<`` (it raises ``TypeError``), so we treat
        ``None`` as "unknown age / oldest" — i.e. never more recent than a known
        index. This keeps the comparison crash-free for levels built with the
        default ``origin_index=None`` (e.g. JSON round-trips or external callers).
        """
        c_idx = candidate.origin_index
        i_idx = incumbent.origin_index
        if c_idx is None:
            return False
        if i_idx is None:
            return True
        return c_idx < i_idx


__all__ = [
    "SRType",
    "SRLevel",
    "SRResult",
    "SupportResistanceEngine",
]
