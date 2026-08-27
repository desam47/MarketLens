"""
Condition evaluators for alerts.

Each function takes the alert's ``parameter`` and a runtime ``value``
(price, signal name, percent change, etc.) and returns ``True`` if the
alert should fire. ``evaluate`` is the public dispatcher that maps a
``condition_type`` string to its evaluator.

The signature is uniform — ``value`` is always a float for price/pct
conditions and a string for signal conditions — so adding a new
condition is a single new branch here plus an entry in
``VALID_CONDITION_TYPES``.
"""
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Public registry of supported condition types. A request with an
# unknown condition_type is rejected at the API boundary.
VALID_CONDITION_TYPES: tuple[str, ...] = (
    "signal_equals",
    "price_above",
    "price_below",
    "pct_change_above",
)


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


# Dispatch table. Add new conditions by adding a function above and
# an entry here. The order in the tuple doesn't matter.
_EVALUATORS: dict[str, Callable[[str, object], bool]] = {
    "signal_equals": _eval_signal_equals,
    "price_above": _eval_price_above,
    "price_below": _eval_price_below,
    "pct_change_above": _eval_pct_change_above,
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
