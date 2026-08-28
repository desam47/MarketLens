"""
Condition evaluators for alerts.

Each function takes the alert's ``parameter`` and a runtime ``value``
(price, signal name, percent change, etc.) and returns ``True`` if the
alert should fire. ``evaluate`` is the public dispatcher that maps a
``condition_type`` string to its evaluator.

For conditions that need multi-bar or multi-timeframe data (trend, volume,
divergence), the evaluator opens its own DB session and computes the
required values inline. These queries are lightweight (LIMIT 50-100) so
the overhead is negligible.

Adding a new condition:
  1. Add a ``_eval_<condition>`` function below.
  2. Add an entry to ``VALID_CONDITION_TYPES``.
  3. Add an entry to ``_EVALUATORS``.
  4. If the condition needs a price/bar callback, register it in
     ``AlertsEngine`` and handle it in ``_on_quote``.
"""
import logging
from collections.abc import Callable

from sqlalchemy import func

from backend.database import SessionLocal

logger = logging.getLogger(__name__)

# Public registry of supported condition types. A request with an
# unknown condition_type is rejected at the API boundary.
VALID_CONDITION_TYPES: tuple[str, ...] = (
    # Original 4
    "signal_equals",
    "price_above",
    "price_below",
    "pct_change_above",
    # Phase 11 additions
    "trend_crosses_above_70",
    "trend_crosses_below_70",
    "trend_direction_changes",
    "trend_strengthens",
    "trend_weakens",
    "full_timeframe_alignment",
    "timeframe_conflict",
    "volume_expansion",
    "divergence",
    "breakout",
    "breakdown",
    "market_regime_change",
)


# --- Signal / price conditions (original) --------------------------------

def _eval_signal_equals(parameter: str, value: object) -> bool:
    """Fires when the scanner's signal list contains ``parameter``.

    ``value`` is expected to be an iterable of signal strings (from
    ScanResult.signals). Parameter is a single signal name.
    """
    if not isinstance(value, (list, tuple, set)):
        return False
    return parameter in value


def _eval_price_above(parameter: str, value: object) -> bool:
    """Fires when the latest price is greater than ``parameter`` (as float)."""
    try:
        threshold = float(parameter)
    except (TypeError, ValueError):
        return False
    if not isinstance(value, (int, float)):
        return False
    return float(value) > threshold


def _eval_price_below(parameter: str, value: object) -> bool:
    """Fires when the latest price is less than ``parameter`` (as float)."""
    try:
        threshold = float(parameter)
    except (TypeError, ValueError):
        return False
    if not isinstance(value, (int, float)):
        return False
    return float(value) < threshold


def _eval_pct_change_above(parameter: str, value: object) -> bool:
    """Fires when the 1-day percent change is greater than ``parameter``.

    ``value`` is a float percentage (e.g. ``5.0`` for +5%).
    """
    try:
        threshold = float(parameter)
    except (TypeError, ValueError):
        return False
    if not isinstance(value, (int, float)):
        return False
    return float(value) > threshold


# --- Trend conditions -----------------------------------------------------

def _eval_trend_crosses_above_70(parameter: str, value: object) -> bool:
    """Fires when trend score crosses above 70 (from below or from 70).

    ``value`` is a dict with ``current`` (float score) and ``previous``
    (float score) keys, computed from the most recent bar snapshot.

    The parameter is unused (threshold is always 70).
    """
    if not isinstance(value, dict):
        return False
    current = value.get("current")
    previous = value.get("previous")
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return False
    return float(current) > 70 and float(previous) <= 70


def _eval_trend_crosses_below_70(parameter: str, value: object) -> bool:
    """Fires when trend score crosses below 70 (from above or from 70).

    ``value`` is a dict with ``current`` and ``previous`` keys.
    The parameter is unused (threshold is always 70).
    """
    if not isinstance(value, dict):
        return False
    current = value.get("current")
    previous = value.get("previous")
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return False
    return float(current) < 70 and float(previous) >= 70


def _eval_trend_direction_changes(parameter: str, value: object) -> bool:
    """Fires when the trend direction flips (bullish ↔ bearish ↔ neutral).

    ``value`` is a dict with ``current_direction`` and ``previous_direction``
    strings: "bullish", "bearish", or "neutral".
    """
    if not isinstance(value, dict):
        return False
    current = str(value.get("current_direction", "")).lower()
    previous = str(value.get("previous_direction", "")).lower()
    if current == previous:
        return False
    # Ignore transitions from/to "unknown"
    if current == "unknown" or previous == "unknown":
        return False
    return True


def _eval_trend_strengthens(parameter: str, value: object) -> bool:
    """Fires when trend score moves further into positive territory.

    ``value`` is a dict with ``current`` and ``previous`` (float scores).
    The ``parameter`` is the minimum delta required (default "5").
    Fires when current > previous + threshold and current > 0.
    """
    if not isinstance(value, dict):
        return False
    current = value.get("current")
    previous = value.get("previous")
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return False
    try:
        threshold = float(parameter) if parameter else 5.0
    except (TypeError, ValueError):
        threshold = 5.0
    return float(current) > float(previous) + threshold and float(current) > 0


def _eval_trend_weakens(parameter: str, value: object) -> bool:
    """Fires when trend score moves toward neutral from a strong position.

    ``value`` is a dict with ``current`` and ``previous`` (float scores).
    The ``parameter`` is the minimum delta required (default "5").
    Fires when previous > 20 and current < previous - threshold.
    """
    if not isinstance(value, dict):
        return False
    current = value.get("current")
    previous = value.get("previous")
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return False
    try:
        threshold = float(parameter) if parameter else 5.0
    except (TypeError, ValueError):
        threshold = 5.0
    return float(current) < float(previous) - threshold and float(previous) > 20


# --- Multi-timeframe conditions -------------------------------------------

def _eval_full_timeframe_alignment(parameter: str, value: object) -> bool:
    """Fires when all timeframes agree on the same trend direction.

    ``value`` is a list of direction strings: "bullish", "bearish", "neutral".
    Fires when all non-unknown entries are identical.
    """
    if not isinstance(value, (list, tuple)):
        return False
    known = [str(d).lower() for d in value if str(d).lower() not in ("unknown", "")]
    if len(known) < 2:
        return False
    return len(set(known)) == 1


def _eval_timeframe_conflict(parameter: str, value: object) -> bool:
    """Fires when timeframes disagree on trend direction.

    ``value`` is a list of direction strings: "bullish", "bearish", "neutral".
    Fires when there are at least 2 distinct known directions.
    """
    if not isinstance(value, (list, tuple)):
        return False
    known = [str(d).lower() for d in value if str(d).lower() not in ("unknown", "")]
    if len(known) < 2:
        return False
    return len(set(known)) > 1


# --- Volume / price-pattern conditions ------------------------------------

def _eval_volume_expansion(parameter: str, value: object) -> bool:
    """Fires when current volume is N× the average volume over a lookback.

    ``value`` is a dict with ``current_volume`` (int/float) and optionally
    ``avg_volume`` (float, pre-computed). If ``avg_volume`` is not present,
    computes it inline from the last 20 bars.
    The ``parameter`` is the minimum multiplier (default "2.0").
    """
    if not isinstance(value, dict):
        return False
    current_volume = value.get("current_volume")
    if not isinstance(current_volume, (int, float)) or float(current_volume) <= 0:
        return False
    try:
        multiplier = float(parameter) if parameter else 2.0
    except (TypeError, ValueError):
        multiplier = 2.0

    avg_volume = value.get("avg_volume")
    if avg_volume is None or not isinstance(avg_volume, (int, float)) or float(avg_volume) <= 0:
        # Compute avg inline from recent bars
        symbol = value.get("symbol")
        if not symbol:
            return False
        avg_volume = _compute_avg_volume(symbol, lookback=20)
        if avg_volume is None or avg_volume <= 0:
            return False

    return float(current_volume) >= float(avg_volume) * multiplier


def _eval_divergence(parameter: str, value: object) -> bool:
    """Fires on negative or positive divergence between price and RSI-like momentum.

    ``value`` is a dict with ``price_change_pct`` and ``rsi_like`` keys.
    ``parameter`` is "negative" or "positive" (default "negative").
    Negative divergence: price rising (+), RSI falling (-)
    Positive divergence: price falling (-), RSI rising (+)
    """
    if not isinstance(value, dict):
        return False
    price_change = value.get("price_change_pct")
    rsi_like = value.get("rsi_like")
    if not isinstance(price_change, (int, float)) or not isinstance(rsi_like, (int, float)):
        return False
    direction = str(parameter).lower() if parameter else "negative"
    if direction == "negative":
        return float(price_change) > 0 and float(rsi_like) < 50
    elif direction == "positive":
        return float(price_change) < 0 and float(rsi_like) > 50
    return False


def _eval_breakout(parameter: str, value: object) -> bool:
    """Fires when price exceeds the highest high over a lookback window.

    ``value`` is a dict with ``current_price`` and optionally
    ``highest_high`` (pre-computed). If not present, computes inline.
    The ``parameter`` is the lookback bars (default "20").
    """
    if not isinstance(value, dict):
        return False
    current_price = value.get("current_price")
    if not isinstance(current_price, (int, float)) or float(current_price) <= 0:
        return False
    highest = value.get("highest_high")
    if highest is None or not isinstance(highest, (int, float)):
        symbol = value.get("symbol")
        timeframe = value.get("timeframe", "1d")
        if not symbol:
            return False
        try:
            lookback = int(parameter) if parameter else 20
        except (TypeError, ValueError):
            lookback = 20
        highest = _compute_highest_high(symbol, timeframe, lookback)
        if highest is None:
            return False
    return float(current_price) > float(highest)


def _eval_breakdown(parameter: str, value: object) -> bool:
    """Fires when price falls below the lowest low over a lookback window.

    ``value`` is a dict with ``current_price`` and optionally
    ``lowest_low`` (pre-computed). If not present, computes inline.
    The ``parameter`` is the lookback bars (default "20").
    """
    if not isinstance(value, dict):
        return False
    current_price = value.get("current_price")
    if not isinstance(current_price, (int, float)) or float(current_price) <= 0:
        return False
    lowest = value.get("lowest_low")
    if lowest is None or not isinstance(lowest, (int, float)):
        symbol = value.get("symbol")
        timeframe = value.get("timeframe", "1d")
        if not symbol:
            return False
        try:
            lookback = int(parameter) if parameter else 20
        except (TypeError, ValueError):
            lookback = 20
        lowest = _compute_lowest_low(symbol, timeframe, lookback)
        if lowest is None:
            return False
    return float(current_price) < float(lowest)


def _eval_market_regime_change(parameter: str, value: object) -> bool:
    """Fires when the market-wide regime changes.

    ``value`` is a dict with ``current_regime`` and ``previous_regime`` strings
    (RISK_ON, RISK_OFF, NEUTRAL, TRANSITION, UNKNOWN).

    ``parameter`` is unused (fires on any change) unless it's a specific regime
    name to filter to.
    """
    if not isinstance(value, dict):
        return False
    current = str(value.get("current_regime") or "").lower()
    previous = str(value.get("previous_regime") or "").lower()
    if not current or current == "unknown":
        return False
    if not previous or previous == "unknown":
        return False  # no previous known regime, not a real change
    if current == previous:
        return False
    # If parameter is a specific regime name, only fire on that one
    if parameter and parameter.strip():
        param = parameter.strip().lower()
        if current != param:
            return False
    return True


# --- Helper: DB queries for inline computation ---------------------------

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


# --- Dispatcher ----------------------------------------------------------

_EVALUATORS: dict[str, Callable[[str, object], bool]] = {
    "signal_equals": _eval_signal_equals,
    "price_above": _eval_price_above,
    "price_below": _eval_price_below,
    "pct_change_above": _eval_pct_change_above,
    "trend_crosses_above_70": _eval_trend_crosses_above_70,
    "trend_crosses_below_70": _eval_trend_crosses_below_70,
    "trend_direction_changes": _eval_trend_direction_changes,
    "trend_strengthens": _eval_trend_strengthens,
    "trend_weakens": _eval_trend_weakens,
    "full_timeframe_alignment": _eval_full_timeframe_alignment,
    "timeframe_conflict": _eval_timeframe_conflict,
    "volume_expansion": _eval_volume_expansion,
    "divergence": _eval_divergence,
    "breakout": _eval_breakout,
    "breakdown": _eval_breakdown,
    "market_regime_change": _eval_market_regime_change,
}


def evaluate(condition_type: str, parameter: str, value: object) -> bool:
    """Return True if the alert with this condition should fire.

    Unknown condition types are logged and return False rather than
    raising — the engine shouldn't crash on a misconfigured alert.
    """
    evaluator = _EVALUATORS.get(condition_type)
    if evaluator is None:
        logger.warning(f"Unknown alert condition_type: {condition_type!r}")
        return False
    try:
        return evaluator(parameter, value)
    except Exception as e:
        # Defensive: a bug in any one condition must not break the engine.
        logger.warning(f"Condition eval failed for {condition_type}({parameter!r}, {value!r}): {e}")
        return False


# --- Value builders (called from engine to construct condition payloads) ---

def build_trend_payload(symbol: str, timeframe: str = "1d", lookback: int = 14) -> dict:
    """Build the value dict for trend conditions from recent bars."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 2)
    if len(bars) < lookback + 1:
        return {"current": 0.0, "previous": 0.0,
                "current_direction": "neutral", "previous_direction": "neutral"}
    current, previous, curr_dir, prev_dir = _compute_trend_score_from_bars(bars, lookback)
    return {
        "current": current,
        "previous": previous,
        "current_direction": curr_dir,
        "previous_direction": prev_dir,
    }


def build_alignment_payload(symbol: str) -> dict:
    """Build the value dict for timeframe alignment/conflict conditions.

    Checks 1m, 5m, 15m, 30m, 1h, 1d, 1wk for trend agreement.
    """
    timeframes = ["1m", "5m", "15m", "30m", "1h", "1d", "1wk"]
    directions = []
    for tf in timeframes:
        bars = _get_recent_bars(symbol, tf, 15)
        if len(bars) < 5:
            directions.append("unknown")
            continue
        _, _, curr_dir, _ = _compute_trend_score_from_bars(bars)
        directions.append(curr_dir)
    return {"directions": directions, "symbol": symbol}


def build_volume_payload(symbol: str, timeframe: str = "1d", lookback: int = 20) -> dict:
    """Build the value dict for volume_expansion."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if not bars:
        return {"current_volume": 0, "avg_volume": 0.0, "symbol": symbol}
    current_volume = float(bars[-1].volume or 0)
    past_bars = bars[:-1]
    if not past_bars:
        return {"current_volume": current_volume, "avg_volume": 0.0, "symbol": symbol}
    avg_volume = sum(float(b.volume or 0) for b in past_bars) / len(past_bars)
    return {
        "current_volume": current_volume,
        "avg_volume": avg_volume,
        "symbol": symbol,
    }


def build_breakout_payload(symbol: str, timeframe: str = "1d", lookback: int = 20) -> dict:
    """Build the value dict for breakout."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if not bars:
        return {"current_price": 0.0, "highest_high": 0.0, "symbol": symbol}
    current_price = float(bars[-1].close)
    past_bars = bars[:-1]
    if not past_bars:
        return {"current_price": current_price, "highest_high": current_price, "symbol": symbol}
    highest = max(float(b.high) for b in past_bars)
    return {"current_price": current_price, "highest_high": highest, "symbol": symbol}


def build_breakdown_payload(symbol: str, timeframe: str = "1d", lookback: int = 20) -> dict:
    """Build the value dict for breakdown."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if not bars:
        return {"current_price": 0.0, "lowest_low": 0.0, "symbol": symbol}
    current_price = float(bars[-1].close)
    past_bars = bars[:-1]
    if not past_bars:
        return {"current_price": current_price, "lowest_low": current_price, "symbol": symbol}
    lowest = min(float(b.low) for b in past_bars)
    return {"current_price": current_price, "lowest_low": lowest, "symbol": symbol}


def build_divergence_payload(symbol: str, timeframe: str = "1d", lookback: int = 14) -> dict:
    """Build the value dict for divergence.

    Computes a simple RSI-like momentum from bars and compares with price change.
    """
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if len(bars) < lookback + 1:
        return {"price_change_pct": 0.0, "rsi_like": 50.0}
    closes = [float(b.close) for b in bars]
    # Price change: compare first to last bar in the window
    price_change_pct = (closes[-1] - closes[0]) / (closes[0] + 1e-10) * 100.0
    # RSI-like: compute gain/loss average ratio
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    avg_gain = sum(gains) / lookback if gains else 0
    avg_loss = sum(losses) / lookback if losses else 1e-10
    rs = avg_gain / avg_loss
    rsi_like = 100.0 - (100.0 / (1.0 + rs))
    return {"price_change_pct": price_change_pct, "rsi_like": rsi_like}


def build_regime_change_payload(symbol: str = "^MKT") -> dict:
    """Build the value dict for market_regime_change from MarketContextEngine history.

    Reuses the process-wide MarketContextEngine singleton which already maintains
    a history of MarketContextSignal objects. Returns current and previous regime
    values; if the engine hasn't computed two signals yet, returns unknown for
    both so the evaluator rejects it.
    """
    try:
        from backend.regime.market_context_engine import market_context_engine
    except Exception:
        return {"current_regime": "unknown", "previous_regime": "unknown", "symbol": symbol}
    history = market_context_engine.get_history(limit=2)
    if len(history) < 2:
        return {"current_regime": "unknown", "previous_regime": "unknown", "symbol": symbol}
    return {
        "current_regime": history[-1].regime,
        "previous_regime": history[-2].regime,
        "symbol": symbol,
    }
