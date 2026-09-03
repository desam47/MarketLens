"""
Analysis endpoints: trend transitions, divergences, and S/R levels.

These are read-only, symbol-keyed endpoints that run the Phase 9
detection engines against the most recent stored bars. They require no
live-tick state and can be served purely from the historical bar cache.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from ...database import SessionLocal
from ...divergence import DivergenceEngine
from ...repositories import bar_repository
from ...support_resistance import SupportResistanceEngine
from ...transitions import TrendTransitionEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


def _load_bars(symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
    """Load bars from DB and reshape for engine consumption.

    Returns dicts with ``open/high/low/close/volume/timestamp`` keys
    that the Phase 9 engines accept. ``source`` is propagated for
    Phase 3.1 so the API can distinguish ``"raw"`` from ``"resampled"``
    bars.
    """
    db = SessionLocal()
    try:
        bars = bar_repository.get_bars(db, symbol, timeframe, limit=limit)
    finally:
        db.close()
    out: list[dict] = []
    for b in bars:
        out.append({
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "timestamp": b.timestamp,
            "source": b.source,
        })
    return out


def _bar_dicts_to_arrays(bars: list[dict]) -> dict:
    """Convert a list of bar dicts to parallel arrays for the engines."""
    if not bars:
        return {
            "opens": [], "highs": [], "lows": [], "closes": [],
            "volumes": [], "timestamps": [],
        }
    return {
        "opens": [b["open"] for b in bars],
        "highs": [b["high"] for b in bars],
        "lows": [b["low"] for b in bars],
        "closes": [b["close"] for b in bars],
        "volumes": [b["volume"] for b in bars],
        "timestamps": [b["timestamp"] for b in bars],
    }


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
    try:
        bars = _load_bars(symbol, timeframe, limit=limit)
        if len(bars) < window + 1:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "transitions": [],
                "count": 0,
                "latest_score": 0.0,
                "latest_timestamp": None,
            }
        arrays = _bar_dicts_to_arrays(bars)
        closes = arrays["closes"]
        # Score heuristic: signed % delta vs a window-bar SMA, scaled to
        # -100..+100. Smoothed via simple z-score.
        sma_window = 20
        scores: list[float] = []
        for i in range(len(closes)):
            if i < sma_window:
                scores.append(0.0)
                continue
            window_closes = closes[i - sma_window: i + 1]
            mean = sum(window_closes) / len(window_closes)
            std = max(
                (sum((c - mean) ** 2 for c in window_closes)
                 / len(window_closes)) ** 0.5,
                1e-9,
            )
            z = (closes[i] - mean) / std
            # Clamp to ±2 standard deviations → ±100
            clamped = max(-2.0, min(2.0, z))
            scores.append(clamped * 50.0)

        engine = TrendTransitionEngine(window=window, min_delta=min_delta)
        transitions = engine.detect(
            scores,
            timestamps=arrays["timestamps"],
            symbol=symbol,
            timeframe=timeframe,
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "transitions": [t.to_dict() for t in transitions],
            "count": len(transitions),
            "latest_score": scores[-1],
            "latest_timestamp": _to_dashboard_tz(arrays["timestamps"][-1]) if arrays["timestamps"] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("transitions error for %s %s: %s", symbol, timeframe, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/support-resistance")
async def get_support_resistance(
    symbol: str,
    timeframe: str = "1d",
    limit: int = 500,
    max_levels: int = 12,
):
    """Detect support and resistance levels for ``symbol`` at ``timeframe``."""
    symbol = symbol.upper()
    try:
        bars = _load_bars(symbol, timeframe, limit=limit)
        if len(bars) < 20:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "levels": [],
                "count": 0,
                "latest_close": None,
                "last_index": 0,
            }
        engine = SupportResistanceEngine(lookback_period=2, lookback_bars=limit)
        result = engine.detect(bars, symbol=symbol, timeframe=timeframe)
        levels = result.levels[:max_levels]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "levels": [level.to_dict() for level in levels],
            "count": len(levels),
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
        bars = _load_bars(symbol, timeframe, limit=limit)
        if len(bars) < 30:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "divergences": [],
                "count": 0,
            }
        arrays = _bar_dicts_to_arrays(bars)
        closes = arrays["closes"]
        highs = arrays["highs"]
        lows = arrays["lows"]
        volumes = arrays["volumes"]
        timestamps = arrays["timestamps"]

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
        bars = _load_bars(symbol, timeframe, limit=limit)
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


# --- indicator helpers (used by the divergence endpoint) ---

def _rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Wilder's RSI on a close series. Returns one value per bar;
    early bars are filled with 50.0 (neutral) so the divergence engine
    can still index them."""
    n = len(closes)
    out = [50.0] * n
    if n < period + 1:
        return out
    gains = [0.0]
    losses = [0.0]
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _macd_histogram_series(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[float]:
    """MACD histogram (MACD line - signal line) on a close series.

    Uses simple EMA (not Wilder). The exact value differs from the
    indicator library's MACD, but the divergence engine only cares
    about *relative* changes between pivot pairs, so a small
    systematic bias is acceptable.
    """
    n = len(closes)
    if n < slow + signal:
        return [0.0] * n

    def ema(series: list[float], period: int) -> list[float]:
        k = 2.0 / (period + 1.0)
        out = [series[0]]
        for v in series[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(n)]
    # Signal line EMA over the last `signal` of macd_line, aligned.
    signal_line = [0.0] * n
    if n >= slow + signal:
        # Initialize the first signal at macd_line[slow-1] (simple mean
        # of the prior `signal` values).
        first_sig_idx = slow - 1 + signal - 1
        if first_sig_idx < n:
            k = 2.0 / (signal + 1.0)
            seed = sum(macd_line[slow - 1: slow - 1 + signal]) / signal
            signal_line[first_sig_idx] = seed
            for i in range(first_sig_idx + 1, n):
                signal_line[i] = (
                    macd_line[i] * k + signal_line[i - 1] * (1 - k)
                )
    return [macd_line[i] - signal_line[i] for i in range(n)]
