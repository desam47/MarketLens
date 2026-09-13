"""
Analysis endpoints: trend transitions, divergences, and price-range
(support/resistance) levels.

These are read-only, symbol-keyed endpoints that run the Phase 9
detection engines against the most recent stored bars. They require no
live-tick state and can be served purely from the historical bar cache.
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from ...analysis.series import bar_dicts_to_arrays as _bar_dicts_to_arrays
from ...analysis.series import load_bars as _load_bars
from ...analysis.series import load_reference_bars as _load_reference_bars
from ...analysis.series import macd_histogram_series as _macd_histogram_series
from ...analysis.series import rsi_series as _rsi_series
from ...divergence import DivergenceEngine
from ...support_resistance import SupportResistanceEngine
from ...transitions import TrendTransitionEngine
from backend.api.ttl_cache import _transitions_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")



def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


# Calendar-anchored reference levels (today / prev day / this week / prev week
# / 52-week high & low) — surfaced separately from the pivot table as a
# "Price History" section. They are computed by the engine from reference_bars
# but intentionally excluded from the S/R pivot panel; here we group them into
# a clean displayed order and only include the ones the engine actually emitted.
_PRICE_HISTORY_LABELS = {
    "today_high": "Today's High",
    "today_low": "Today's Low",
    "prev_day_high": "Prev Day High",
    "prev_day_low": "Prev Day Low",
    "this_week_high": "This Week's High",
    "this_week_low": "This Week's Low",
    "prev_week_high": "Prev Week High",
    "prev_week_low": "Prev Week Low",
    "week_52_high": "52 Week High",
    "week_52_low": "52 Week Low",
}
_PRICE_HISTORY_ORDER = [
    "today_high", "today_low",
    "prev_day_high", "prev_day_low",
    "this_week_high", "this_week_low",
    "prev_week_high", "prev_week_low",
    "week_52_high", "week_52_low",
]


def _extract_price_history(result) -> list[dict]:
    by_type: dict[str, object] = {}
    for lvl in result.levels:
        t = lvl.type.value if hasattr(lvl.type, "value") else str(lvl.type)
        if t in _PRICE_HISTORY_LABELS:
            by_type[t] = lvl
    history: list[dict] = []
    for t in _PRICE_HISTORY_ORDER:
        lvl = by_type.get(t)
        if lvl is None:
            continue
        latest_close = result.latest_close
        dist = (
            ((lvl.price - latest_close) / latest_close) * 100
            if latest_close
            else None
        )
        history.append({
            "label": _PRICE_HISTORY_LABELS[t],
            "type": t,
            "price": lvl.price,
            "strength": lvl.strength,
            "distance_pct": dist,
            "timestamp": (
                lvl.timestamp.isoformat() if getattr(lvl, "timestamp", None) else None
            ),
        })
    return history


@router.get("/{symbol}/transitions")
async def get_transitions(
    symbol: str,
    timeframe: str = "1d",
    window: int = 5,
    min_delta: float = 10.0,
    limit: int = 200,
):
    """Detect trend transitions in the recent trend-score history.

    The trend score series is reconstructed by replaying the stored bars
    through the indicator stack. For a fast read-only response we use
    a simple z-score-of-closes heuristic: compare the current close
    against an N-bar moving average, normalized by recent volatility.
    That's not the same as ``TrendEngine.score`` (which is a weighted
    indicator blend) but it's a sufficient, deterministic input to the
    transition engine for the purposes of surfacing transitions in the
    UI. Callers wanting exact ``TrendEngine.score`` values should use
    ``/api/trend/{symbol}/history/{timeframe}`` directly.
    """
    symbol = symbol.upper()
    cache_key = f"{symbol}:{timeframe}:{window}:{min_delta}:{limit}"
    cached = _transitions_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        bars = await asyncio.to_thread(_load_bars, symbol, timeframe, limit=limit)
        if len(bars) < window + 1:
            payload = {
                "symbol": symbol,
                "timeframe": timeframe,
                "transitions": [],
                "count": 0,
                "latest_score": 0.0,
                "latest_timestamp": None,
            }
            _transitions_cache[cache_key] = payload
            return payload
        arrays = _bar_dicts_to_arrays(bars)
        closes = arrays["closes"]
        timestamps = arrays["timestamps"]
        # Bars come back newest→oldest (desc=True). Both the z-score/SMA
        # heuristic *and* the transition engine are temporal windows that need
        # history *before* each current point, so everything below is computed
        # in chronological (oldest→newest) order. The scan/timestamps are
        # reversed once up front and the resulting transitions reversed back at
        # the end so the payload stays newest→oldest (index [0] = latest bar).
        closes_asc = list(reversed(closes))
        timestamps_asc = list(reversed(timestamps))
        # Score heuristic: signed % delta vs a window-bar SMA, scaled to
        # -100..+100. Smoothed via simple z-score.
        # Phase 3.9.11: O(N) running-sum/sum-of-squares using a deque
        # instead of recomputing the full window slice per bar (was O(N·W)).
        from collections import deque
        sma_window = 20
        win = sma_window + 1  # include the current bar in the window
        scores: list[float] = []
        sma_deque: deque[float] = deque()
        sum_w = 0.0
        sum_sq = 0.0
        for c in closes_asc:
            sma_deque.append(c)
            sum_w += c
            sum_sq += c * c
            if len(sma_deque) > win:
                evicted = sma_deque.popleft()
                sum_w -= evicted
                sum_sq -= evicted * evicted
            if len(sma_deque) < win:
                scores.append(0.0)
                continue
            mean = sum_w / win
            # var = E[x²] − E[x]² ; clamp to avoid sqrt of negative due to FP drift
            var = max(sum_sq / win - mean * mean, 0.0)
            std = var ** 0.5 or 1e-9
            z = (c - mean) / std
            # Clamp to ±2 standard deviations → ±100
            clamped = max(-2.0, min(2.0, z))
            scores.append(clamped * 50.0)

        engine = TrendTransitionEngine(window=window, min_delta=min_delta)
        # detect runs on chronological scores/timestamps so the newest bar can
        # itself be a transition's "current" point, then we reverse to newest→oldest.
        transitions_asc = engine.detect(
            scores,
            timestamps=timestamps_asc,
            symbol=symbol,
            timeframe=timeframe,
        )
        transitions = list(reversed(transitions_asc))
        payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "transitions": [t.to_dict() for t in transitions],
            "count": len(transitions),
            # Bars are returned newest→oldest; the newest bar is the last
            # element of the chronological arrays.
            "latest_score": scores[-1] if scores else 0.0,
            "latest_timestamp": _to_dashboard_tz(timestamps_asc[-1]) if timestamps_asc else None,
        }
        _transitions_cache[cache_key] = payload
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error("transitions error for %s %s: %s", symbol, timeframe, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/price-range")
async def get_price_range(
    symbol: str,
    timeframe: str = "1d",
    limit: int = 500,
    max_levels: int = 20,
):
    """Detect the price-range levels (support/resistance) for ``symbol``
    at ``timeframe``.

    Renamed from ``/support-resistance`` (2026-09-10); the detection is
    unchanged, only the user-facing framing (the Symbol page calls this
    panel "Price Range").
    """
    symbol = symbol.upper()
    try:
        bars = await asyncio.to_thread(_load_bars, symbol, timeframe, limit=limit)
        if len(bars) < 20:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "levels": [],
                "count": 0,
                "latest_close": None,
                "last_index": 0,
            }
        # Calendar-anchored levels (today/prev day/this week/prev week/
        # 52-week) are computed from a separate, cached daily series,
        # independent of the requested timeframe/limit, so they return
        # identical values no matter which chart timeframe is being viewed.
        reference_bars = await asyncio.to_thread(_load_reference_bars, symbol)
        # Engine uses index 0 as "today" / latest and scans toward older bars
        # for prev-period and swing detection, so it works directly with
        # _load_bars's desc=True (newest -> oldest) ordering.
        engine = SupportResistanceEngine(lookback_period=5, lookback_bars=limit)
        result = engine.detect(
            bars, symbol=symbol, timeframe=timeframe, reference_bars=reference_bars
        )
        # Show only the classic pivot table (PP / R1-R3 / S1-S3) — the
        # standard 5-point pivots derived from the prior session's H/L/C.
        # These are timeframe-invariant (computed from the daily reference
        # series) and, unlike the calendar markers or micro-swings, they
        # describe genuine, actionable support/resistance. The engine only
        # emits a level when it sits on the correct side of the latest close,
        # so this is a clean pivot list — ordered R3 -> R1 -> PP -> S1 -> S3.
        PIVOT_TABLE_TYPES = frozenset({
            "pivot_pp",
            "pivot_r1", "pivot_r2", "pivot_r3",
            "pivot_s1", "pivot_s2", "pivot_s3",
        })
        PIVOT_TABLE_ORDER = {
            "pivot_r3": 0, "pivot_r2": 1, "pivot_r1": 2,
            "pivot_pp": 3,
            "pivot_s1": 4, "pivot_s2": 5, "pivot_s3": 6,
        }
        pivot_levels: list = []
        for lvl in result.levels:
            t = lvl.type.value if hasattr(lvl.type, "value") else str(lvl.type)
            if t in PIVOT_TABLE_TYPES:
                pivot_levels.append(lvl)
        pivot_levels.sort(key=lambda l: PIVOT_TABLE_ORDER.get(l.type.value, 99))
        levels = pivot_levels[:max_levels]
        price_history = _extract_price_history(result)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "levels": [level.to_dict() for level in levels],
            "count": len(levels),
            "price_history": price_history,
            "latest_close": result.latest_close,
            "last_index": result.last_index,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("S/R error for %s %s: %s", symbol, timeframe, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/divergences")
async def get_divergences(
    symbol: str,
    timeframe: str = "1d",
    limit: int = 200,
    min_strength: float = 0.0,
):
    """Detect RSI / MACD / volume divergences.

    RSI and MACD values are approximated from the bars (RSI via
    Wilder's smoothing on closes, MACD via the difference of two EMAs)
    so the endpoint can serve from the historical bar cache alone. The
    values match the indicator library closely enough for divergence
    detection, which is what the UI consumes.
    """
    symbol = symbol.upper()
    try:
        bars = await asyncio.to_thread(_load_bars, symbol, timeframe, limit=limit)
        if len(bars) < 30:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "divergences": [],
                "count": 0,
            }
        arrays = _bar_dicts_to_arrays(bars)
        # load_bars returns desc=True (newest→oldest). The DivergenceEngine
        # assumes chronological (oldest→newest) order: pivot `a` is the older
        # bar, pivot `b` the newer, and the divergence is stamped with
        # timestamps[b]. Feeding the raw reversed arrays (as this endpoint
        # used to) inverted every pivot pair and attached the OLDER bar's
        # timestamp. Reverse every input array once up front; the engine sorts
        # its output by pivot_b_index ascending (oldest→newest), so we reverse
        # the result back to newest→oldest to match the transitions endpoint's
        # contract. RSI/MACD are computed on the chronological closes so they
        # stay time-aligned with the pivots.
        closes = list(reversed(arrays["closes"]))
        highs = list(reversed(arrays["highs"]))
        lows = list(reversed(arrays["lows"]))
        volumes = list(reversed(arrays["volumes"]))
        timestamps = list(reversed(arrays["timestamps"]))

        rsi = _rsi_series(closes, period=14)
        macd = _macd_histogram_series(closes, fast=12, slow=26, signal=9)

        engine = DivergenceEngine(pivot_lookback=2, max_pivots_apart=80)
        all_d = engine.detect(
            highs, lows, closes,
            volumes=volumes,
            rsi=rsi,
            macd=macd,
            timestamps=timestamps,
            symbol=symbol,
            timeframe=timeframe,
        )
        # Newest→oldest so index [0] is the most recent divergence.
        all_d = list(reversed(all_d))
        filtered = [d for d in all_d if d.strength >= min_strength]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "divergences": [d.to_dict() for d in filtered],
            "count": len(filtered),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("divergences error for %s %s: %s", symbol, timeframe, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/bars")
async def get_recent_bars(
    symbol: str,
    timeframe: str = "1d",
    limit: int = 60,
    resample_from: str | None = None,
):
    """Return the most recent bars for charting / table views.

    Phase 3.1: higher-timeframe bars (5m, 15m, 30m, 1h, 1d, 1wk) are
    resampled from 1m at read time. The ``source`` field on each bar
    indicates ``"raw"`` (stored directly) or ``"resampled"`` (derived
    from 1m). The ``resample_from`` query parameter is an optional hint
    — set to ``"1m"`` when requesting higher timeframes to document the
    source. It does not change behaviour; it only annotates the response.
    """
    symbol = symbol.upper()
    try:
        bars = await asyncio.to_thread(_load_bars, symbol, timeframe, limit=limit)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "resample_from": resample_from,
            "bars": [
                {
                    "timestamp": _to_dashboard_tz(b.get("timestamp")),
                    "open": b["open"],
                    "high": b["high"],
                    "low": b["low"],
                    "close": b["close"],
                    "volume": b["volume"],
                    "source": b.get("source"),
                    "data_status": b.get("data_status"),
                }
                for b in bars
            ],
            "count": len(bars),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("bars error for %s %s: %s", symbol, timeframe, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


# _load_bars, _bar_dicts_to_arrays, _rsi_series, _macd_histogram_series
# moved to backend/analysis/series.py (imported at the top of this file)
# so backend.ai.context.build_context()'s divergence section can reuse
# them without reaching into this router module's private functions.
