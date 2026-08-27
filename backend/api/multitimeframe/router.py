"""
API endpoints for multi-timeframe analysis
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_quotes,
)
from backend.multitimeframe.multi_timeframe_engine import MultiTimeframeEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multitimeframe", tags=["multitimeframe"])

# In a real implementation, these would be dependency injected or managed as services
_engines: dict[str, MultiTimeframeEngine] = {}

# Timeframes we ingest bars for. Used to register the confluence engine with
# the live-tick registry under each bar:{tf} key, so it gets notified per-TF.
_MTF_TIMEFRAMES = ("1m", "5m", "15m", "1h", "1d")

def get_engine(symbol: str) -> MultiTimeframeEngine:
    """Get or create multi-timeframe engine for symbol, seeding from DB on first access."""
    symbol = symbol.upper()
    if symbol not in _engines:
        engine = MultiTimeframeEngine(symbol)
        _engines[symbol] = engine
        count = seed_engine_from_quotes(symbol, engine.update)
        if count > 0:
            logger.info(f"Seeded confluence engine for {symbol} with {count} historical quotes")
        # Register for live-tick updates per timeframe (same pattern as trend)
        for tf in _MTF_TIMEFRAMES:
            engine_registry.register(f"bar:{tf}", symbol, engine.update)
    return _engines[symbol]

@router.get("/{symbol}/confluence")
async def get_current_confluence(symbol: str):
    """Get current multi-timeframe confluence for symbol"""
    try:
        engine = get_engine(symbol.upper())
        confluence_signal = engine.get_current_confluence()

        if confluence_signal is None:
            return {
                "symbol": symbol.upper(),
                "direction": "neutral",
                "strength": 0.0,
                "alignment_score": 0.0,
                "timeframe_signals": {},
                "timestamp": None
            }

        # Convert timeframe signals to serializable format
        timeframe_signals = {}
        for tf, signal in confluence_signal.timeframe_signals.items():
            timeframe_signals[tf.value] = {
                "direction": signal.direction.value,
                "strength": signal.strength.value,
                "confidence": signal.confidence,
                "timestamp": signal.timestamp.isoformat() if signal.timestamp else None
            }

        return {
            "symbol": confluence_signal.symbol,
            "direction": confluence_signal.direction.value,
            "strength": confluence_signal.strength,
            "alignment_score": confluence_signal.alignment_score,
            "timeframe_signals": timeframe_signals,
            "timestamp": confluence_signal.timestamp.isoformat() if confluence_signal.timestamp else None
        }
    except Exception as e:
        logger.error(f"Error getting confluence for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{symbol}/history")
async def get_mtf_history(symbol: str, limit: int | None = 100):
    """Get multi-timeframe history for symbol"""
    try:
        engine = get_engine(symbol.upper())
        history = engine.get_confluence_history(limit=limit)

        return {
            "symbol": symbol.upper(),
            "history": [
                {
                    "direction": signal.direction.value,
                    "strength": signal.strength,
                    "alignment_score": signal.alignment_score,
                    "timestamp": signal.timestamp.isoformat() if signal.timestamp else None
                }
                for signal in history
            ],
            "count": len(history)
        }
    except Exception as e:
        logger.error(f"Error getting MTF history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{symbol}/update")
async def update_mtf(symbol: str, price: float, volume: float,
                    timestamp: str | None = None):
    """Update multi-timeframe engine with new market data"""
    try:
        engine = get_engine(symbol.upper())

        # Parse timestamp if provided
        ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()

        engine.update(
            price=price,
            volume=volume,
            timestamp=ts
        )

        return {
            "symbol": symbol.upper(),
            "status": "updated",
            "timestamp": ts.isoformat()
        }
    except Exception as e:
        logger.error(f"Error updating MTF for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))