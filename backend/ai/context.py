"""
Phase 16 — Structured context builder for the AI analysis prompt.

The context is a JSON-serialisable dict that captures everything the
quantitative engine already knows about a symbol. Per the spec:

> AI must NEVER directly calculate raw indicators if the application
> already has the calculation. Instead send structured context.

The builder reads from the existing engine singletons and bundles
the data the prompt template will turn into a natural-language
question. If any of the required components are unavailable (e.g.
a cold-start symbol with no bars), ``build_context()`` raises
``InsufficientDataError`` so the caller can return an
``UncertaintyResponse`` rather than asking the AI to hallucinate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.scanner.scanner import market_scanner


class InsufficientDataError(RuntimeError):
    """Raised when the quant engine doesn't have enough data to ask
    the AI a question. The caller should return an uncertainty
    response, never an AI answer."""


@dataclass
class AnalysisContext:
    """All structured inputs to the AI prompt.

    Field names map directly to the JSON keys the prompt template
    expects (see :mod:`backend.ai.prompt`).
    """

    symbol: str
    timeframe: str
    price: float | None
    timestamp: str | None
    data_status: str  # "live" | "stale" | "unknown"
    timeframe_scores: dict[str, Any] = field(default_factory=dict)
    trend_state: dict[str, Any] = field(default_factory=dict)
    market_structure: dict[str, Any] = field(default_factory=dict)
    market_regime: dict[str, Any] = field(default_factory=dict)
    relative_strength: dict[str, Any] = field(default_factory=dict)
    sector_alignment: dict[str, Any] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    momentum: dict[str, Any] = field(default_factory=dict)
    support_resistance: dict[str, Any] = field(default_factory=dict)
    trend_transition: dict[str, Any] = field(default_factory=dict)
    historical_signal_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price": self.price,
            "timestamp": self.timestamp,
            "data_status": self.data_status,
            "timeframe_scores": self.timeframe_scores,
            "trend_state": self.trend_state,
            "market_structure": self.market_structure,
            "market_regime": self.market_regime,
            "relative_strength": self.relative_strength,
            "sector_alignment": self.sector_alignment,
            "volume": self.volume,
            "momentum": self.momentum,
            "support_resistance": self.support_resistance,
            "trend_transition": self.trend_transition,
            "historical_signal_stats": self.historical_signal_stats,
        }


def _safe_call(fn, *args, default=None, **kwargs):
    """Call a quant engine function and return its result, swallowing
    any exception. Used so that a single broken sub-engine doesn't
    take the whole context down — the AI just sees a partial picture."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return default


def _regime_value(enum_or_str) -> str:
    """Coerce a regime enum or string to a plain string."""
    if hasattr(enum_or_str, "value"):
        return enum_or_str.value
    return str(enum_or_str)


def _sig_to_dict(sig) -> dict[str, Any]:
    """Serialize a signal dataclass to a dict, stripping private fields."""
    if sig is None:
        return {}
    result = {}
    for k, v in vars(sig).items():
        if k.startswith("_"):
            continue
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        elif hasattr(v, "value"):  # enum
            result[k] = v.value
        else:
            result[k] = v
    return result


def build_context(symbol: str, timeframe: str = "1d") -> AnalysisContext:
    """Gather a structured context dict for ``symbol``.

    ``timeframe`` is the primary analysis window. Cross-timeframe
    scores come from the scanner's MTF result; everything else is
    taken from the most recent engine state.

    Raises ``InsufficientDataError`` when there's no quote and no
    trend signal — the caller should return an uncertainty response.
    """
    sym = symbol.upper()
    tf = timeframe.upper()

    # --- 1. Single-symbol scan (complete snapshot including MTF scores) ---
    try:
        scan = market_scanner.scan_symbol(sym)
    except Exception as e:
        raise InsufficientDataError(f"scan failed for {sym}: {e}") from e

    if scan is None:
        raise InsufficientDataError(f"no scan result for {sym}")

    quote = scan.quote
    # quote is a Pydantic model (or None)
    if quote is not None:
        price = quote.price
        ts = quote.timestamp
    else:
        price = None
        ts = scan.timestamp

    if price is None:
        raise InsufficientDataError(f"no price available for {sym}")

    # --- 2. MTF trend signals ---
    mtf = scan.trend_signals or {}
    timeframe_scores: dict[str, Any] = {}
    for tf_key, tsig in mtf.items():
        # trend_signals is a dict-of-dicts in the scanner API
        if hasattr(tsig, "direction"):
            timeframe_scores[tf_key] = {
                "direction": _regime_value(tsig.direction),
                "strength": _regime_value(tsig.strength),
                "confidence": round(float(tsig.confidence), 2),
            }
        elif isinstance(tsig, dict):
            timeframe_scores[tf_key] = {
                "direction": _regime_value(tsig.get("direction", "unknown")),
                "strength": _regime_value(tsig.get("strength", "unknown")),
                "confidence": round(float(tsig.get("confidence", 0)), 2),
            }

    # Primary signal for the requested timeframe
    primary_sig = mtf.get(tf)
    if primary_sig is None:
        primary_sig = next(iter(mtf.values()), None)

    trend_state: dict[str, Any] = {}
    if primary_sig is not None:
        if hasattr(primary_sig, "direction"):
            trend_state = {
                "direction": _regime_value(primary_sig.direction),
                "strength": _regime_value(primary_sig.strength),
                "confidence": round(float(primary_sig.confidence), 2),
            }
        elif isinstance(primary_sig, dict):
            trend_state = {
                "direction": _regime_value(primary_sig.get("direction", "unknown")),
                "strength": _regime_value(primary_sig.get("strength", "unknown")),
                "confidence": round(float(primary_sig.get("confidence", 0)), 2),
            }

    # --- 3. Market regime (global) ---
    market_regime: dict[str, Any] = {}
    try:
        from backend.regime.market_regime_engine import MarketRegimeEngine

        engine = MarketRegimeEngine(sym)
        regime_sig = engine.get_current_regime()
        if regime_sig is not None:
            market_regime = _sig_to_dict(regime_sig)
    except Exception:  # noqa: BLE001
        pass

    # --- 4. Relative strength (per-symbol, one per benchmark) ---
    rs_list: list[dict[str, Any]] = []
    try:
        from backend.regime.relative_strength_engine import RelativeStrengthEngine

        rs_engine = RelativeStrengthEngine(sym)
        signals = rs_engine.get_signals()
        rs_list = [_sig_to_dict(s) for s in signals]
    except Exception:  # noqa: BLE001
        pass

    # Pick the primary benchmark (SPY) for the display
    primary_rs: dict[str, Any] = {}
    for rs in rs_list:
        if rs.get("benchmark") in ("SPY", "QQQ"):
            primary_rs = rs
            break
    if not primary_rs and rs_list:
        primary_rs = rs_list[0]

    # --- 5. Sector alignment ---
    sector_alignment: dict[str, Any] = {}
    try:
        from backend.regime.sector_engine import SectorEngine

        sector_engine = SectorEngine(sym)
        sector_sig = sector_engine.get_current_signal()
        if sector_sig is not None:
            sector_alignment = _sig_to_dict(sector_sig)
    except Exception:  # noqa: BLE001
        pass

    # --- 6. Volume / momentum from indicator values ---
    ind = scan.indicator_values or {}
    volume: dict[str, Any] = {
        "rvol": ind.get("rvol"),
        "volume_state": ind.get("volume_state"),
    }
    momentum: dict[str, Any] = {
        "rsi": ind.get("rsi"),
        "macd_hist": ind.get("macd_hist"),
    }

    # --- 7. Support / resistance ---
    sr: dict[str, Any] = {}
    try:
        from backend.support_resistance.support_resistance_engine import (
            support_resistance_engine,
        )

        levels = support_resistance_engine.detect_levels(sym, timeframe)
        sr = {
            "supports": [
                {"price": round(level.price, 2), "strength": round(float(level.strength), 2)}
                for level in (levels.supports or [])[:3]
            ],
            "resistances": [
                {"price": round(level.price, 2), "strength": round(float(level.strength), 2)}
                for level in (levels.resistances or [])[:3]
            ],
        }
    except Exception:  # noqa: BLE001
        pass

    # --- 8. Most recent trend transition ---
    transition: dict[str, Any] = {}
    try:
        from backend.transitions.trend_transition_engine import (
            trend_transition_engine,
        )

        history = trend_transition_engine.get_history(
            symbol=sym, timeframe=timeframe, limit=1
        )
        if history:
            t = history[-1]
            transition = {
                "type": _regime_value(t.type),
                "from_score": round(float(t.previous_score), 2),
                "to_score": round(float(t.current_score), 2),
                "delta": round(float(t.delta), 2),
                "direction": _regime_value(t.direction),
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            }
    except Exception:  # noqa: BLE001
        pass

    # --- 9. Historical signal statistics (Phase 13) ---
    signal_stats: dict[str, Any] = {}
    try:
        from backend.services.signal_recorder import signal_recorder

        stats = signal_recorder.get_stats(symbol=sym, timeframe=timeframe)
        if stats:
            signal_stats = {
                "total_signals": stats.get("total", 0),
                "avg_return_5b": stats.get("avg_return_5b"),
                "avg_return_10b": stats.get("avg_return_10b"),
                "win_rate": stats.get("win_rate"),
            }
    except Exception:  # noqa: BLE001
        pass

    return AnalysisContext(
        symbol=sym,
        timeframe=timeframe,
        price=price,
        timestamp=str(ts) if ts else None,
        data_status="live" if quote else "stale",
        timeframe_scores=timeframe_scores,
        trend_state=trend_state,
        market_structure={
            "total_score": round(scan.calculate_total_score(), 2),
            "dimension_scores": {k: round(v, 1) for k, v in (scan.scores or {}).items()},
            "signals": list(scan.signals or []),
        },
        market_regime=market_regime,
        relative_strength=primary_rs,
        sector_alignment=sector_alignment,
        volume=volume,
        momentum=momentum,
        support_resistance=sr,
        trend_transition=transition,
        historical_signal_stats=signal_stats,
    )
