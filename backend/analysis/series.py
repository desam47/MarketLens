"""
Bar-loading and indicator-series helpers.

Extracted verbatim (logic unchanged) from
``backend.api.analysis.router``, which originally defined these as
private (``_``-prefixed) module functions used only by its own
``/transitions``, ``/divergences``, and ``/bars`` endpoints. Moved
here, under public names, so other non-router code — most notably
``backend.ai.context.build_context()``'s divergence section — can
reuse the exact same bar-to-array and RSI/MACD-series logic instead
of importing a router module's private functions (or worse,
duplicating it).

``backend/api/analysis/router.py`` still owns the endpoints; it now
imports these functions from here rather than defining them locally.
"""

from __future__ import annotations

from datetime import datetime

from cachetools import TTLCache

from backend.database import SessionLocal
from backend.repositories import bar_repository


def load_bars(
    symbol: str,
    timeframe: str,
    limit: int = 500,
    before: datetime | None = None,
) -> list[dict]:
    """Load bars from DB and reshape for engine consumption.

    Returns dicts with ``open/high/low/close/volume/timestamp`` keys
    that the Phase 9 engines accept. ``source`` is propagated for
    Phase 3.1 so the API can distinguish ``"raw"`` from ``"resampled"``
    bars. ``data_status`` is propagated too — today's 1d bar is written
    live from market open onward (ingestion_service._resample_1d_live_and_
    upsert), marked DataStatus.INCOMPLETE, and finalized to HISTORICAL at
    16:02 ET once the authoritative provider-sourced close is written —
    callers can use this to distinguish a live/in-progress bar from a
    settled one instead of the bar being hidden until close.
    """
    db = SessionLocal()
    try:
        # Use desc=True so the most recent bars come first — charts and
        # tables need the latest data, not the oldest.
        bars = bar_repository.get_bars(
            db, symbol, timeframe, limit=limit, to_ts=before, desc=True,
        )
    finally:
        db.close()

    out: list[dict] = []
    for b in bars:
        out.append(
            {
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "timestamp": b.timestamp,
                "source": b.source,
                "session": getattr(b, "session", None) or "regular",
                "data_status": b.data_status.value
                if hasattr(b.data_status, "value")
                else b.data_status,
            }
        )
    return out


# Calendar-anchored S/R levels (today/prev-day/this-week/prev-week/52-week
# high & low) are computed from a dedicated daily series independent of
# whatever timeframe/limit the caller wants — see
# SupportResistanceEngine.detect()'s `reference_bars` param. That series
# can be up to 5000 daily bars, and both `/api/analysis/{symbol}/price-range`
# and the AI context builder (backend/ai/context.py) ask for the identical
# series per symbol, so it's cached here rather than re-queried from the DB
# on every request.
_REFERENCE_BARS_LIMIT = 5000
_reference_bars_cache: TTLCache[str, list[dict]] = TTLCache(maxsize=200, ttl=60)


def load_reference_bars(symbol: str) -> list[dict]:
    """Load (and cache, 60s TTL) the daily reference-bar series for `symbol`."""
    symbol = symbol.upper()
    cached = _reference_bars_cache.get(symbol)
    if cached is not None:
        return cached
    bars = load_bars(symbol, "1d", limit=_REFERENCE_BARS_LIMIT)
    _reference_bars_cache[symbol] = bars
    return bars


def bar_dicts_to_arrays(bars: list[dict]) -> dict:
    """Convert a list of bar dicts to parallel arrays for the engines."""
    if not bars:
        return {
            "opens": [],
            "highs": [],
            "lows": [],
            "closes": [],
            "volumes": [],
            "timestamps": [],
        }
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    timestamps: list = []
    for b in bars:
        opens.append(b["open"])
        highs.append(b["high"])
        lows.append(b["low"])
        closes.append(b["close"])
        volumes.append(b["volume"])
        timestamps.append(b["timestamp"])
    return {
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
        "timestamps": timestamps,
    }


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
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
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
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


def macd_histogram_series(
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
            seed = sum(macd_line[slow - 1 : slow - 1 + signal]) / signal
            signal_line[first_sig_idx] = seed
            for i in range(first_sig_idx + 1, n):
                signal_line[i] = macd_line[i] * k + signal_line[i - 1] * (1 - k)
    return [macd_line[i] - signal_line[i] for i in range(n)]
