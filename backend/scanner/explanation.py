"""Human-readable explanations for scanner results.

The scanner deliberately keeps its indicator calculations numeric.  This
module translates those values into a small, stable payload that clients can
render without re-implementing trading logic or guessing what a score means.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.utils.timezone import NY

_SIGNAL_LABELS = {
    "RSI_OVERSOLD": "RSI oversold",
    "RSI_OVERBOUGHT": "RSI overbought",
    "RSI_OVERSOLD_REVERSAL": "Oversold reversal",
    "MACD_BULLISH": "MACD bullish",
    "MACD_BEARISH": "MACD bearish",
    "MULTI_TIMEFRAME_BULLISH": "Multi-timeframe bullish",
    "MULTI_TIMEFRAME_BEARISH": "Multi-timeframe bearish",
    "VOLUME_SPIKE": "Volume spike",
    "HIGH_VOLUME": "High volume",
    "BREAKOUT": "Breakout",
    "BREAKDOWN": "Breakdown",
    "VOLATILITY_CONTRACTION": "Volatility contraction",
    "VOLATILITY_EXPANSION": "Volatility expansion",
    "RELATIVE_STRENGTH_OUTPERFORMER": "Relative strength leader",
    "RELATIVE_STRENGTH_UNDERPERFORMER": "Relative strength laggard",
    "HEAVY_BUY_PRESSURE": "Heavy buy pressure",
    "HEAVY_SELL_PRESSURE": "Heavy sell pressure",
    "BLOCK_ACTIVITY": "Block activity",
}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value == value else None


def _direction(value: float | None, epsilon: float = 0.0) -> str:
    if value is None:
        return "neutral"
    if value > epsilon:
        return "bullish"
    if value < -epsilon:
        return "bearish"
    return "neutral"


def _label(signal: str) -> str:
    return _SIGNAL_LABELS.get(signal, signal.replace("_", " ").title())


def _driver(
    key: str,
    label: str,
    value: Any,
    direction: str,
    description: str,
    impact: str = "context",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "direction": direction,
        "impact": impact,
        "description": description,
    }


def _safe_age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    try:
        if timestamp.tzinfo is None:
            # Project convention: naive provider timestamps are New York
            # local time, not UTC.
            timestamp = timestamp.replace(tzinfo=NY)
        return max(0.0, (now - timestamp).total_seconds())
    except (TypeError, ValueError):
        return None


def _freshness_status(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "unavailable"
    if age_seconds <= 60:
        return "fresh"
    if age_seconds <= 900:
        return "recent"
    return "stale"


def _timeframe_agreement(trend_signals: dict[str, Any]) -> dict[str, Any]:
    timeframes: list[dict[str, Any]] = []
    bullish = bearish = neutral = 0
    for timeframe, raw_signal in trend_signals.items():
        signal = raw_signal if isinstance(raw_signal, dict) else {}
        raw_direction = str(signal.get("direction") or "neutral").lower()
        if "uptrend" in raw_direction or "bullish" in raw_direction:
            side = "bullish"
            bullish += 1
        elif "downtrend" in raw_direction or "bearish" in raw_direction:
            side = "bearish"
            bearish += 1
        else:
            side = "neutral"
            neutral += 1
        confidence = _number(signal.get("confidence"))
        timeframes.append(
            {
                "timeframe": timeframe,
                "direction": side,
                "raw_direction": raw_direction,
                "confidence": round(confidence, 4) if confidence is not None else None,
            }
        )

    total = len(timeframes)
    dominant_count = max((bullish, bearish, neutral), default=0)
    dominant = (
        "bullish"
        if dominant_count == bullish and bullish
        else ("bearish" if dominant_count == bearish and bearish else "neutral")
    )
    return {
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "total": total,
        "dominant": dominant,
        "alignment_pct": round((dominant_count / total) * 100, 1) if total else 0.0,
        "timeframes": timeframes,
    }


def _changes(result, previous_result) -> dict[str, Any] | None:
    if previous_result is None:
        return None
    current_signals = set(result.signals or [])
    previous_signals = set(previous_result.signals or [])
    current_score = result.calculate_signed_total_score()
    previous_score = previous_result.calculate_signed_total_score()
    score_delta = current_score - previous_score
    return {
        "previous_timestamp": previous_result.timestamp.isoformat(),
        "signals_added": sorted(current_signals - previous_signals),
        "signals_removed": sorted(previous_signals - current_signals),
        "score_delta": round(score_delta, 4),
        "score_direction": _direction(score_delta, epsilon=0.05),
        "changed": bool(current_signals != previous_signals or abs(score_delta) >= 0.05),
    }


def build_signal_explanation(result, previous_result=None) -> dict[str, Any]:
    """Build an explanation payload from a scanner ``ScanResult``.

    Historical performance is intentionally left as ``None`` here.  It is a
    database concern and is attached by the symbol API when requested; this
    keeps batch scans and the live WebSocket path fast and deterministic.
    """
    indicators = result.indicator_values or {}
    signals = list(result.signals or [])
    drivers: list[dict[str, Any]] = []

    rsi = _number(indicators.get("rsi"))
    if rsi is not None:
        rsi_direction = "bullish" if rsi < 30 else "bearish" if rsi > 70 else "neutral"
        rsi_context = (
            "oversold" if rsi < 30 else "overbought" if rsi > 70 else "in the neutral range"
        )
        drivers.append(
            _driver(
                "rsi",
                "RSI",
                round(rsi, 2),
                rsi_direction,
                f"RSI is {rsi_context} at {rsi:.1f}.",
                "bullish" if rsi < 30 else "bearish" if rsi > 70 else "context",
            )
        )

    macd = _number(indicators.get("macd"))
    if macd is not None:
        drivers.append(
            _driver(
                "macd",
                "MACD histogram",
                round(macd, 4),
                _direction(macd, epsilon=0.00001),
                f"MACD histogram is {'above' if macd > 0 else 'below' if macd < 0 else 'at'} zero.",
                "bullish" if macd > 0 else "bearish" if macd < 0 else "context",
            )
        )

    adx = _number(indicators.get("adx"))
    if adx is not None:
        drivers.append(
            _driver(
                "adx",
                "ADX",
                round(adx, 2),
                "neutral",
                f"ADX at {adx:.1f} indicates {'a strong' if adx >= 50 else 'a developing' if adx >= 25 else 'a weak'} trend.",
                "context",
            )
        )

    for period in (20, 50, 200):
        sma = _number(indicators.get(f"sma_{period}"))
        distance = _number(indicators.get(f"price_vs_sma_{period}_pct"))
        if sma is not None and distance is not None:
            side = _direction(distance, epsilon=0.05)
            drivers.append(
                _driver(
                    f"sma_{period}",
                    f"SMA {period}",
                    round(sma, 2),
                    side,
                    f"Price is {abs(distance):.1f}% {'above' if distance >= 0 else 'below'} the {period}-period moving average.",
                    side,
                )
            )

    volume_ratio = _number(indicators.get("volume_ratio"))
    if volume_ratio is not None:
        drivers.append(
            _driver(
                "volume_ratio",
                "Relative volume",
                round(volume_ratio, 2),
                "bullish" if volume_ratio >= 1.5 else "neutral",
                f"Current volume is {volume_ratio:.1f}× the 20-period average.",
                "bullish" if volume_ratio >= 1.5 else "context",
            )
        )

    relative_strength = _number(indicators.get("relative_strength"))
    if relative_strength is not None:
        drivers.append(
            _driver(
                "relative_strength",
                "Relative strength",
                round(relative_strength, 2),
                _direction(relative_strength, epsilon=0.1),
                f"The symbol is {'outperforming' if relative_strength >= 0 else 'underperforming'} its configured benchmark by {abs(relative_strength):.1f}%.",
                "bullish"
                if relative_strength > 0
                else "bearish"
                if relative_strength < 0
                else "context",
            )
        )

    for signal in signals:
        if not any(driver["key"] == f"signal:{signal}" for driver in drivers):
            signal_direction = (
                "bullish"
                if any(
                    word in signal
                    for word in ("BULLISH", "BREAKOUT", "OUTPERFORMER", "BUY", "REVERSAL")
                )
                else (
                    "bearish"
                    if any(
                        word in signal
                        for word in ("BEARISH", "BREAKDOWN", "UNDERPERFORMER", "SELL")
                    )
                    else "neutral"
                )
            )
            drivers.append(
                _driver(
                    f"signal:{signal}",
                    _label(signal),
                    signal,
                    signal_direction,
                    f"{_label(signal)} was triggered by the current scan conditions.",
                    signal_direction,
                )
            )

    agreement = _timeframe_agreement(result.trend_signals or {})
    signed_score = result.calculate_signed_total_score()
    numeric_indicators = sum(
        _number(indicators.get(key)) is not None
        for key in ("rsi", "macd", "adx", "volume_ratio", "volatility_ratio", "relative_strength")
    )
    completeness = numeric_indicators / 6
    alignment = agreement["alignment_pct"] / 100 if agreement["total"] else 0.5
    confidence = max(0.0, min(100.0, abs(signed_score) * 0.45 + alignment * 35 + completeness * 20))

    now = datetime.now(UTC)
    quote_timestamp = getattr(result.quote, "timestamp", None) if result.quote else None
    age_seconds = _safe_age_seconds(quote_timestamp, now)
    freshness = {
        "status": _freshness_status(age_seconds),
        "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "quote_timestamp": quote_timestamp.isoformat() if quote_timestamp else None,
        "scan_timestamp": result.timestamp.isoformat(),
        "provider": getattr(result.quote, "provider", None) if result.quote else None,
    }

    direction = "bullish" if signed_score > 5 else "bearish" if signed_score < -5 else "neutral"
    return {
        "direction": direction,
        "confidence": round(confidence, 1),
        "drivers": drivers,
        "timeframe_agreement": agreement,
        "data_freshness": freshness,
        "changes": _changes(result, previous_result),
        "historical_performance": None,
        "historical_basis": "Completed daily signal snapshots for this symbol; unavailable until signal outcomes exist.",
    }
