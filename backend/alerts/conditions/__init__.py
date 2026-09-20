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

The module is split into submodules for readability:
  - ``evaluators`` — the 18 ``_eval_*`` functions and the ``evaluate`` dispatcher
  - ``payloads``   — the 7 ``build_*_payload`` functions used by the engine
  - ``helpers``    — small DB-backed computations shared by both

This ``__init__`` re-exports the public API so existing call sites
(``from backend.alerts.conditions import ...``) keep working unchanged.

Adding a new condition:
  1. Add a ``_eval_<condition>`` function in ``evaluators.py``.
  2. Add an entry to ``VALID_CONDITION_TYPES`` in ``evaluators.py``.
  3. Add an entry to ``_EVALUATORS`` in ``evaluators.py``.
  4. If the condition needs a price/bar callback, register it in
     ``AlertsEngine`` and handle it in ``_on_quote``.
"""
from .evaluators import (
    _EVALUATORS,
    VALID_CONDITION_TYPES,
    _eval_breakdown,
    _eval_breakout,
    _eval_divergence,
    _eval_full_timeframe_alignment,
    _eval_market_regime_change,
    _eval_pct_change_above,
    _eval_price_above,
    _eval_price_below,
    _eval_signal_equals,
    _eval_signal_profile,
    _eval_timeframe_conflict,
    _eval_trend_crosses_above_70,
    _eval_trend_crosses_below_70,
    _eval_trend_direction_changes,
    _eval_trend_strengthens,
    _eval_trend_weakens,
    _eval_volume_expansion,
    evaluate,
)
from .helpers import (
    _compute_avg_volume,
    _compute_highest_high,
    _compute_lowest_low,
    _compute_trend_score_from_bars,
    _get_recent_bars,
)
from .payloads import (
    build_alignment_payload,
    build_breakdown_payload,
    build_breakout_payload,
    build_divergence_payload,
    build_regime_change_payload,
    build_trend_payload,
    build_volume_payload,
)

__all__ = [
    # Evaluators
    "VALID_CONDITION_TYPES",
    "evaluate",
    "_EVALUATORS",
    # Individual evaluator functions (public for direct testing)
    "_eval_signal_equals",
    "_eval_price_above",
    "_eval_price_below",
    "_eval_pct_change_above",
    "_eval_trend_crosses_above_70",
    "_eval_trend_crosses_below_70",
    "_eval_trend_direction_changes",
    "_eval_trend_strengthens",
    "_eval_trend_weakens",
    "_eval_full_timeframe_alignment",
    "_eval_timeframe_conflict",
    "_eval_volume_expansion",
    "_eval_divergence",
    "_eval_breakout",
    "_eval_breakdown",
    "_eval_market_regime_change",
    "_eval_signal_profile",
    # Payload builders
    "build_trend_payload",
    "build_alignment_payload",
    "build_volume_payload",
    "build_breakout_payload",
    "build_breakdown_payload",
    "build_divergence_payload",
    "build_regime_change_payload",
    # Helpers
    "_compute_avg_volume",
    "_compute_highest_high",
    "_compute_lowest_low",
    "_get_recent_bars",
    "_compute_trend_score_from_bars",
]
