"""
Replay helpers that turn a sliding window of historical bars into a
``ScanResult`` suitable for the live signal generator.

Kept as pure functions of ``(bar_window, symbol, timestamp)`` so the
unit tests can exercise it without touching the database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from backend.indicators import MACDIndicator, RSIIndicator
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


def _bars_to_dicts(bars: Sequence[Bar]) -> list[dict]:
    """Convert Pydantic ``Bar`` objects to the ``{close, high, low}``
    dict shape that ``BaseIndicator.calculate()`` expects."""
    return [{"close": b.close, "high": b.high, "low": b.low} for b in bars]


def build_indicator_values(
    window: Sequence[Bar],
    current_volume: float,
) -> dict[str, float | int | None]:
    """Compute the indicator values used by ``_generate_signals`` for
    a single replay step.

    The window should include enough bars for the warmup of the most
    demanding indicator (RSI(14) -> 14 bars). The function returns a
    dict matching the keys ``Scanner._calculate_indicators`` writes so
    the live signal generator can run unchanged.
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

    rsi_series = RSIIndicator(period=14).calculate(bar_dicts)
    rsi_value = rsi_series[-1] if rsi_series else None

    macd_series = MACDIndicator().calculate(bar_dicts)
    macd_value = macd_series[-1] if macd_series else None

    return {
        "rsi": rsi_value,
        "macd": macd_value,
        "volume": current_volume,
        "close": last_close,
    }


def build_scan_result(
    symbol: str,
    timestamp: datetime,
    window: Sequence[Bar],
) -> ScanResult:
    """Build a synthetic ``ScanResult`` for a given bar's timestamp.

    The returned result has ``indicator_values`` populated for the
    indicator-based signals (RSI / MACD / HIGH_VOLUME) and an empty
    ``trend_signals`` dict so the MTF signals are *not* produced
    (the TrendEngine cannot be re-wound from raw bars in v1).
    """
    current_volume = window[-1].volume if window else 0.0
    result = ScanResult(symbol, timestamp)
    result.indicator_values = build_indicator_values(window, current_volume)
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
