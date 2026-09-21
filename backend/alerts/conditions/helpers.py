"""
DB-backed helper computations for alert condition evaluators and payload builders.

These functions open their own short-lived SQLAlchemy sessions, run small
bounded queries, and return primitive values. They're called
inline from the evaluator/payload functions when the alert engine hasn't
pre-computed a value.
"""

import logging

from backend.database import SessionLocal

logger = logging.getLogger(__name__)


def _previous_bars(symbol: str, timeframe: str, lookback: int) -> list:
    """The ``lookback`` closed bars BEFORE the current one, oldest first.

    Fetches ``lookback + 1`` bars and drops the newest, exactly as the payload builders
    in ``payloads.py`` do — the single correct definition of "the last N bars, excluding
    the current one".
    """
    return _get_recent_bars(symbol, timeframe, lookback + 1)[:-1]


def _compute_avg_volume(symbol: str, lookback: int = 20, timeframe: str = "1d") -> float | None:
    """Return average volume over the last ``lookback`` bars (excluding current).

    Fixed: this used ``func.avg(volume)`` with ``ORDER BY ... LIMIT``, but an aggregate
    yields ONE row, so the ORDER BY/LIMIT never restricted what it averaged — it was the
    mean volume of EVERY stored bar for the symbol, across all timeframes mixed together
    (1m volumes with daily volumes). It also had no timeframe filter.
    """
    past = _previous_bars(symbol, timeframe, lookback)
    if not past:
        return None
    avg = sum(float(b.volume or 0) for b in past) / len(past)
    return avg if avg else None


def _compute_highest_high(symbol: str, timeframe: str, lookback: int) -> float | None:
    """Return the highest high over the last ``lookback`` closed bars (excluding current).

    Fixed for the same reason as ``_compute_avg_volume``: ``func.max(high)`` with
    ``ORDER BY ... LIMIT`` returned the highest high in the ENTIRE stored history, so a
    breakout alert evaluated through this path would only fire on a multi-year high.
    """
    past = _previous_bars(symbol, timeframe, lookback)
    return max(float(b.high) for b in past) if past else None


def _compute_lowest_low(symbol: str, timeframe: str, lookback: int) -> float | None:
    """Return the lowest low over the last ``lookback`` closed bars (excluding current)."""
    past = _previous_bars(symbol, timeframe, lookback)
    return min(float(b.low) for b in past) if past else None


def _get_recent_bars(symbol: str, timeframe: str, limit: int) -> list:
    """Fetch the most recent ``limit`` closed bars for a symbol/timeframe."""
    try:
        from backend.models import BarModel

        db = SessionLocal()
        try:
            bars = (
                db.query(BarModel)
                .filter(
                    BarModel.symbol == symbol.upper(),
                    BarModel.timeframe == timeframe,
                )
                .order_by(BarModel.timestamp.desc())
                .limit(limit)
                .all()
            )
            return list(reversed(bars))  # oldest first for easier delta computation
        finally:
            db.close()
    except Exception:
        # Still degrades to "no data" (an alert simply doesn't fire), but no longer
        # silently: a failing query used to look identical to "no bars yet".
        logger.warning("alert helper could not load %s/%s bars", symbol, timeframe, exc_info=True)
        return []


def _compute_trend_score_from_bars(bars: list, lookback: int = 14) -> tuple[float, float, str, str]:
    """Compute a simple trend score and direction from OHLC bars.

    Uses a smoothed close approach: if current close > SMA(close, lookback)
    the score is positive (up to +100); if below, negative (down to -100).

    Returns (current_score, previous_score, current_direction, previous_direction).
    Directions: "bullish", "bearish", "neutral".
    """
    if len(bars) < lookback + 1:
        return 0.0, 0.0, "neutral", "neutral"

    closes = [float(b.close) for b in bars]
    sma = sum(closes[-lookback:]) / lookback

    current_close = closes[-1]
    previous_close = closes[-2] if len(closes) > 1 else closes[-1]

    # Score: 0 = SMA, ±100 = extreme deviation
    max_dev = max(abs(current_close - sma) / (sma + 1e-10), 0.05) * 100
    current_score = min(max_dev, 100.0) if current_close > sma else -min(max_dev, 100.0)

    prev_max_dev = max(abs(previous_close - sma) / (sma + 1e-10), 0.05) * 100
    previous_score = min(prev_max_dev, 100.0) if previous_close > sma else -min(prev_max_dev, 100.0)

    def _dir(score):
        if score > 10:
            return "bullish"
        elif score < -10:
            return "bearish"
        return "neutral"

    return current_score, previous_score, _dir(current_score), _dir(previous_score)
