"""
API endpoints for strategy selection
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

# Reuse the seeded, live-updated engines from the other routers so the strategy
# selector operates on the same state the rest of the API sees. This prevents the
# strategy endpoint from returning stale/wrong signals due to its own un-seeded
# engine copies.
# Importing here is safe: regime/trend/multitimeframe routers import from
# market_data, never from each other — no import cycle.
from ..multitimeframe.router import get_engine as get_mtf_engine
from ..regime.router import get_engine as get_regime_engine
from ..trend.router import get_engine as get_trend_engine
from ...engines.timeframe import Timeframe
from ...strategy.strategy_selector import StrategySelector
from backend.api.ttl_cache import _strategy_cache, _strategy_history_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


# Only the selector itself needs per-symbol state (selection history).
_selectors: dict[str, StrategySelector] = {}

def get_strategy_selector(symbol: str) -> StrategySelector:
    """Get or create strategy selector for symbol"""
    if symbol not in _selectors:
        _selectors[symbol] = StrategySelector(symbol)
    return _selectors[symbol]

@router.get("/{symbol}/current")
async def get_current_strategy(symbol: str):
    """Get current recommended strategy for symbol (30s TTL cache)."""
    key = symbol.upper()
    cached = _strategy_cache.get(key)
    if cached is not None:
        return cached
    try:
        # Get current signals from all engines
        regime_engine = get_regime_engine(symbol.upper())
        trend_engine = get_trend_engine(symbol.upper())
        mtf_engine = get_mtf_engine(symbol.upper())

        regime_signal = regime_engine.get_current_regime()
        trend_signal = trend_engine.get_current_trend(Timeframe.ONE_HOUR)  # Default to 1h
        confluence_signal = mtf_engine.get_current_confluence()

        # Select strategy
        selector = get_strategy_selector(symbol.upper())
        strategy_signal = selector.select_strategy(
            regime_signal=regime_signal,
            trend_signal=trend_signal,
            confluence_signal=confluence_signal
        )

        payload = {
            "symbol": strategy_signal.symbol,
            "strategy_type": strategy_signal.strategy_type.value,
            "confidence": strategy_signal.confidence,
            "timeframe": strategy_signal.timeframe,
            "parameters": strategy_signal.parameters,
            "regime_signal": {
                "regime": strategy_signal.regime_signal.regime.value if strategy_signal.regime_signal else None,
                "confidence": strategy_signal.regime_signal.confidence if strategy_signal.regime_signal else None,
                "strength": strategy_signal.regime_signal.strength if strategy_signal.regime_signal else None
            } if strategy_signal.regime_signal else None,
            "trend_signal": {
                "direction": strategy_signal.trend_signal.direction.value if strategy_signal.trend_signal else None,
                "strength": strategy_signal.trend_signal.strength.value if strategy_signal.trend_signal else None,
                "confidence": strategy_signal.trend_signal.confidence if strategy_signal.trend_signal else None
            } if strategy_signal.trend_signal else None,
            "confluence_signal": {
                "direction": strategy_signal.confluence_signal.direction.value if strategy_signal.confluence_signal else None,
                "strength": strategy_signal.confluence_signal.strength if strategy_signal.confluence_signal else None,
                "alignment_score": strategy_signal.confluence_signal.alignment_score if strategy_signal.confluence_signal else None
            } if strategy_signal.confluence_signal else None,
            "timestamp": _to_dashboard_tz(strategy_signal.timestamp)
        }
        _strategy_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting strategy for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/{symbol}/history")
async def get_strategy_history(symbol: str, limit: int | None = 100):
    """Get strategy selection history for symbol (60s TTL cache)."""
    key = f"{symbol.upper()}:{limit}"
    cached = _strategy_history_cache.get(key)
    if cached is not None:
        return cached
    try:
        selector = get_strategy_selector(symbol.upper())
        history = selector.get_selection_history(limit=limit)

        payload = {
            "symbol": symbol.upper(),
            "history": [
                {
                    "strategy_type": signal.strategy_type.value,
                    "confidence": signal.confidence,
                    "timeframe": signal.timeframe,
                    "timestamp": _to_dashboard_tz(signal.timestamp)
                }
                for signal in history
            ],
            "count": len(history)
        }
        _strategy_history_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting strategy history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/{symbol}/select")
async def select_strategy_manual(symbol: str,
                               regime: str | None = None,
                               regime_confidence: float | None = None,
                               regime_strength: float | None = None,
                               trend_direction: str | None = None,
                               trend_strength: str | None = None,
                               trend_confidence: float | None = None,
                               confluence_direction: str | None = None,
                               confluence_strength: float | None = None,
                               confluence_alignment: float | None = None):
    """Manually select strategy with provided signals (for testing)"""
    try:
        from backend.engines.timeframe import Timeframe
        from backend.multitimeframe.multi_timeframe_engine import (
            ConfluenceDirection,
            ConfluenceSignal,
        )
        from backend.regime.market_regime_engine import MarketRegime, RegimeSignal
        from backend.trend.trend_engine import (
            TrendDirection,
            TrendSignal,
            TrendStrength,
        )

        # Build regime signal if provided
        regime_signal = None
        if regime is not None:
            regime_signal = RegimeSignal(
                symbol=symbol.upper(),
                regime=MarketRegime(regime),
                confidence=regime_confidence or 0.0,
                strength=regime_strength or 0.0,
                supporting_factors={},
                timestamp=datetime.now()
            )

        # Build trend signal if provided
        trend_signal = None
        if trend_direction is not None:
            trend_signal = TrendSignal(
                symbol=symbol.upper(),
                timeframe=Timeframe.ONE_HOUR,
                direction=TrendDirection(trend_direction),
                strength=TrendStrength(trend_strength) if trend_strength else TrendStrength.MODERATE,
                confidence=trend_confidence or 0.0,
                timestamp=datetime.now()
            )

        # Build confluence signal if provided
        confluence_signal = None
        if confluence_direction is not None:
            confluence_signal = ConfluenceSignal(
                symbol=symbol.upper(),
                direction=ConfluenceDirection(confluence_direction),
                strength=confluence_strength or 0.0,
                alignment_score=confluence_alignment or 0.0,
                timeframe_signals={},
                timestamp=datetime.now()
            )

        # Select strategy
        selector = get_strategy_selector(symbol.upper())
        strategy_signal = selector.select_strategy(
            regime_signal=regime_signal,
            trend_signal=trend_signal,
            confluence_signal=confluence_signal
        )

        return {
            "symbol": strategy_signal.symbol,
            "strategy_type": strategy_signal.strategy_type.value,
            "confidence": strategy_signal.confidence,
            "timeframe": strategy_signal.timeframe,
            "parameters": strategy_signal.parameters,
            "timestamp": _to_dashboard_tz(strategy_signal.timestamp)
        }
    except Exception as e:
        logger.error(f"Error selecting strategy manually for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
