"""
DB-backed helper computations for alert condition evaluators and payload builders.

These functions open their own short-lived SQLAlchemy sessions, run small
bounded queries (LIMIT 50-100), and return primitive values. They're called
inline from the evaluator/payload functions when the alert engine hasn't
pre-computed a value.
"""
from sqlalchemy import func

from backend.database import SessionLocal


def _compute_avg_volume(symbol: str, lookback: int = 20) -> float | None:
    """Return average volume over the last ``lookback`` bars (excluding current)."""
    try:
        from backend.models import BarModel
        db = SessionLocal()
        try:
            avg = db.query(func.avg(BarModel.volume)).filter(
                BarModel.symbol == symbol.upper(),
            ).order_by(
                BarModel.timestamp.desc()
            ).limit(lookback + 1).scalar()
            return float(avg) if avg else None
        finally:
            db.close()
    except Exception:
        return None


def _compute_highest_high(symbol: str, timeframe: str, lookback: int) -> float | None:
    """Return the highest high over the last ``lookback`` closed bars."""
    try:
        from backend.models import BarModel
        db = SessionLocal()
        try:
            result = db.query(func.max(BarModel.high)).filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == timeframe,
            ).order_by(
                BarModel.timestamp.desc()
            ).limit(lookback + 1).scalar()
            return float(result) if result else None
        finally:
            db.close()
    except Exception:
        return None


def _compute_lowest_low(symbol: str, timeframe: str, lookback: int) -> float | None:
    """Return the lowest low over the last ``lookback`` closed bars."""
    try:
        from backend.models import BarModel
        db = SessionLocal()
        try:
            result = db.query(func.min(BarModel.low)).filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == timeframe,
            ).order_by(
                BarModel.timestamp.desc()
            ).limit(lookback + 1).scalar()
            return float(result) if result else None
        finally:
            db.close()
    except Exception:
        return None


def _get_recent_bars(symbol: str, timeframe: str, limit: int) -> list:
    """Fetch the most recent ``limit`` closed bars for a symbol/timeframe."""
    try:
        from backend.models import BarModel
        db = SessionLocal()
        try:
            bars = db.query(BarModel).filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == timeframe,
            ).order_by(BarModel.timestamp.desc()).limit(limit).all()
            return list(reversed(bars))  # oldest first for easier delta computation
        finally:
            db.close()
    except Exception:
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
