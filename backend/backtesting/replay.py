"""
Replay helpers that turn a sliding window of historical bars into a
``ScanResult`` suitable for the live signal generator.

Kept as pure functions of ``(bar_window, symbol, timestamp)`` so the
unit tests can exercise it without touching the database.

Phase 19 changes:
  * ``build_indicator_values`` and ``build_scan_result`` accept an
    optional ``ExperimentParameters`` so the Strategy Lab can vary
    indicator periods without changing the live scanner.
  * ``classify_regime`` is a new pure helper that tags each replay
    step with a coarse regime ("risk_on" / "risk_off" / "neutral")
    using only the same bar window the indicators already consume.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from backend.indicators import ADXIndicator, MACDIndicator, RSIIndicator
from backend.models.market_data import Bar
from backend.scanner.scanner import ScanResult

# How many bars of recent volume the replay path uses to decide whether
# a "HIGH_VOLUME" signal should fire. The live scanner uses a 1,000,000
# hardcoded placeholder; for replay we use a relative threshold of
# ``2 × mean(volume[-VOLUME_LOOKBACK:])`` so the rule is meaningful
# against any price level / liquidity regime.
VOLUME_LOOKBACK = 20
HIGH_VOLUME_MULTIPLIER = 2.0

# Minimum bars of warmup needed before RSI(14) and MACD return
# well-defined values. We start iterating at this index.
INDICATOR_WARMUP = 14

# ADX(period) needs ``period * 2`` bars to warm up (it averages the
# DX series over `period` to produce the ADX). Used by the regime-aware
# replay path so the loop's starting index accounts for the most
# demanding indicator.
REGIME_WARMUP = 28  # ADX(14) * 2

# Regime classifier thresholds (mirror MarketRegimeEngine's RISK_ON /
# RISK_OFF / NEUTRAL / TRANSITION / UNKNOWN enum but only emit the
# three well-defined labels from the replay bar window).
ADX_TRENDING_DEFAULT = 25.0
RSI_BULL_THRESHOLD_DEFAULT = 55.0
RSI_BEAR_THRESHOLD_DEFAULT = 45.0


def _bars_to_dicts(bars: Sequence[Bar]) -> list[dict]:
    """Convert Pydantic ``Bar`` objects to the ``{close, high, low}``
    dict shape that ``BaseIndicator.calculate()`` expects."""
    return [{"close": b.close, "high": b.high, "low": b.low} for b in bars]


def build_indicator_values(
    window: Sequence[Bar],
    current_volume: float,
    params: ExperimentParameters | None = None,  # noqa: F821
) -> dict[str, float | int | None]:
    """Compute the indicator values used by ``_generate_signals`` for
    a single replay step.

    The window should include enough bars for the warmup of the most
    demanding indicator (RSI(14) -> 14 bars by default; ADX(14) -> 28
    bars when regime tagging is on). The function returns a dict
    matching the keys ``Scanner._calculate_indicators`` writes so the
    live signal generator can run unchanged.

    When ``params`` is supplied, the RSI / MACD periods come from it
    rather than the hardcoded defaults. The active RSI / MACD
    thresholds are also written into the returned dict as
    ``_rsi_oversold`` / ``_rsi_overbought`` so the replay signal
    generator can override the live scanner's hardcoded 30/70.
    """
    if not window:
        return {
            "rsi": None,
            "macd": None,
            "volume": current_volume,
            "close": None,
        }

    bar_dicts = _bars_to_dicts(window)
    last_close = window[-1].close

    if params is not None:
        rsi_period = params.rsi_period
        macd_fast = params.macd_fast
        macd_slow = params.macd_slow
        macd_signal = params.macd_signal
        rsi_oversold = params.rsi_oversold
        rsi_overbought = params.rsi_overbought
    else:
        rsi_period = 14
        macd_fast = 12
        macd_slow = 26
        macd_signal = 9
        rsi_oversold = 30.0
        rsi_overbought = 70.0

    rsi_series = RSIIndicator(period=rsi_period).calculate(bar_dicts)
    rsi_value = rsi_series[-1] if rsi_series else None

    macd_series = MACDIndicator(
        fast=macd_fast, slow=macd_slow, signal=macd_signal
    ).calculate(bar_dicts)
    macd_value = macd_series[-1] if macd_series else None

    return {
        "rsi": rsi_value,
        "macd": macd_value,
        "volume": current_volume,
        "close": last_close,
        # Replay-signal thresholds. Read by ``replay_generate_signals``
        # in ``engine.py`` to override the live scanner's hardcoded 30/70.
        "_rsi_oversold": rsi_oversold,
        "_rsi_overbought": rsi_overbought,
    }


def build_scan_result(
    symbol: str,
    timestamp: datetime,
    window: Sequence[Bar],
    params: ExperimentParameters | None = None,  # noqa: F821
) -> ScanResult:
    """Build a synthetic ``ScanResult`` for a given bar's timestamp.

    The returned result has ``indicator_values`` populated for the
    indicator-based signals (RSI / MACD / HIGH_VOLUME) and an empty
    ``trend_signals`` dict so the MTF signals are *not* produced
    (the TrendEngine cannot be re-wound from raw bars in v1).

    When ``params`` is supplied, the indicator periods and RSI
    thresholds are propagated into ``indicator_values`` so the replay
    signal generator can honour them.
    """
    current_volume = window[-1].volume if window else 0.0
    result = ScanResult(symbol, timestamp)
    result.indicator_values = build_indicator_values(window, current_volume, params)
    # ``trend_signals`` deliberately left empty: MTF signals are out of
    # scope for v1 because the live TrendEngine is not re-windable from
    # raw bars.
    return result


def relative_volume(current: float, history: Sequence[Bar]) -> float:
    """Compute ``current / mean(volume[-N:])``.

    Returns 0.0 when there's not enough history. Used to decide
    whether a HIGH_VOLUME signal should fire — the live scanner uses
    a fixed 1,000,000 placeholder; the replay path uses a relative
    threshold so the rule is consistent across symbols.
    """
    if not history:
        return 0.0
    lookback = [b.volume for b in history[-VOLUME_LOOKBACK:]]
    if not lookback:
        return 0.0
    mean = sum(lookback) / len(lookback)
    if mean <= 0:
        return 0.0
    return current / mean


def classify_regime(
    bar_window: Sequence[Bar],
    adx_period: int = 14,
    rsi_period: int = 14,
    adx_trending: float = ADX_TRENDING_DEFAULT,
    rsi_bull: float = RSI_BULL_THRESHOLD_DEFAULT,
    rsi_bear: float = RSI_BEAR_THRESHOLD_DEFAULT,
) -> str:
    """Classify the regime at the end of ``bar_window``.

    Returns one of:
      * ``"risk_on"``   — ADX >= ``adx_trending`` AND RSI >= ``rsi_bull``
      * ``"risk_off"``  — ADX >= ``adx_trending`` AND RSI <= ``rsi_bear``
      * ``"neutral"``   — ranging (ADX < ``adx_trending``) or
                          trending but RSI between ``rsi_bear`` and
                          ``rsi_bull``.

    The classifier needs ``max(adx_period * 2 + 1, rsi_period + 1)``
    bars to produce meaningful numbers; shorter windows return
    ``"neutral"`` rather than firing on warmup noise.
    """
    min_bars = max(adx_period * 2 + 1, rsi_period + 1)
    if len(bar_window) < min_bars:
        return "neutral"

    bar_dicts = _bars_to_dicts(bar_window)
    adx_series = ADXIndicator(period=adx_period).calculate(bar_dicts)
    rsi_series = RSIIndicator(period=rsi_period).calculate(bar_dicts)
    adx = adx_series[-1] if adx_series else 0.0
    rsi = rsi_series[-1] if rsi_series else 50.0

    if adx >= adx_trending:
        if rsi >= rsi_bull:
            return "risk_on"
        if rsi <= rsi_bear:
            return "risk_off"
    return "neutral"
