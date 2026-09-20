"""
Alert condition evaluators.

Each function takes the alert's ``parameter`` (a string from the alert
config) and a runtime ``value`` (price, signal name, percent change, etc.)
and returns ``True`` if the alert should fire.

The ``evaluate`` function is the public dispatcher that maps a
``condition_type`` string to its evaluator. Unknown condition types are
logged and return False rather than raising — the engine shouldn't crash
on a misconfigured alert.
"""
import json
import logging
from collections.abc import Callable

from .helpers import (
    _compute_avg_volume,
    _compute_highest_high,
    _compute_lowest_low,
)

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
    "signal_profile",
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
        avg_volume = _compute_avg_volume(symbol, lookback=20, timeframe=value.get("timeframe", "1d"))
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


def _eval_signal_profile(parameter: str, value: object) -> bool:
    """Evaluate a configurable trend-signal profile.

    ``parameter`` is JSON containing optional ``direction``, ``min_score``,
    ``min_strength``, ``market_regime`` and ``timeframe`` filters. The engine
    supplies the current trend payload plus the bar timeframe and current
    market regime.
    """
    if not isinstance(value, dict):
        return False
    try:
        profile = json.loads(parameter or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(profile, dict):
        return False

    timeframe = str(profile.get("timeframe") or "").strip().lower()
    if timeframe and timeframe != str(value.get("timeframe") or "").lower():
        return False
    direction = str(profile.get("direction") or "any").strip().lower()
    current_direction = str(value.get("current_direction") or "").lower()
    if direction not in ("", "any") and current_direction != direction:
        return False

    score = value.get("current")
    if not isinstance(score, (int, float)):
        return False
    try:
        min_score = float(profile.get("min_score")) if profile.get("min_score") not in (None, "") else None
        min_strength = float(profile.get("min_strength")) if profile.get("min_strength") not in (None, "") else None
    except (TypeError, ValueError):
        return False
    if min_score is not None and abs(float(score)) < max(0.0, min_score):
        return False
    strength = value.get("strength")
    if min_strength is not None and (not isinstance(strength, (int, float)) or float(strength) < max(0.0, min_strength)):
        return False
    regime = str(profile.get("market_regime") or "any").strip().lower()
    current_regime = str(value.get("current_regime") or "").lower()
    if regime not in ("", "any") and current_regime != regime:
        return False
    return True


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
    "signal_profile": _eval_signal_profile,
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
