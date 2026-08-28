"""
API endpoints for trend analysis
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.engines.timeframe import Timeframe
from backend.market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_quotes,
)
from backend.trend.trend_engine import TrendEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trend", tags=["trend"])

# In a real implementation, these would be dependency injected or managed as services
_engines: dict[str, TrendEngine] = {}

# Timeframes we ingest bars for. Used to register the trend engine with the
# live-tick registry under each bar:{tf} key. Must match the ingestion service.
_TREND_TIMEFRAMES = ("1m", "2m", "3m", "5m", "15m", "30m", "1h", "1d", "1wk")

def get_engine(symbol: str) -> TrendEngine:
    """Get or create trend engine for symbol, seeding from DB on first access.

    The trend engine maintains all timeframes internally from a single tick
    stream. We register the same update callback for every timeframe we ingest
    so the engine sees one event per bar close per timeframe.
    """
    symbol = symbol.upper()
    if symbol not in _engines:
        engine = TrendEngine(symbol)
        _engines[symbol] = engine
        count = seed_engine_from_quotes(symbol, engine.update)
        if count > 0:
            logger.info(f"Seeded trend engine for {symbol} with {count} historical quotes")
        # Register for live-tick updates. The trend engine buckets ticks into
        # timeframes internally, so it should be notified of every bar event
        # for every timeframe we ingest.
        for tf in _TREND_TIMEFRAMES:
            engine_registry.register(f"bar:{tf}", symbol, engine.update)
    return _engines[symbol]

@router.get("/{symbol}/current/{timeframe}")
async def get_current_trend(symbol: str, timeframe: str):
    """Get current trend for symbol and timeframe"""
    try:
        # Validate timeframe
        try:
            tf = Timeframe(timeframe)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

        engine = get_engine(symbol.upper())
        trend_signal = engine.get_current_trend(tf)

        if trend_signal is None:
            return {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "direction": "unknown",
                "strength": "unknown",
                "confidence": 0.0,
                "timestamp": None
            }

        return {
            "symbol": trend_signal.symbol,
            "timeframe": trend_signal.timeframe.value,
            "direction": trend_signal.direction.value,
            "strength": trend_signal.strength.value,
            "confidence": trend_signal.confidence,
            "timestamp": trend_signal.timestamp.isoformat() if trend_signal.timestamp else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trend for {symbol} {timeframe}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/{symbol}/history/{timeframe}")
async def get_trend_history(symbol: str, timeframe: str, limit: int | None = 100):
    """Get trend history for symbol and timeframe"""
    try:
        # Validate timeframe
        try:
            tf = Timeframe(timeframe)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

        engine = get_engine(symbol.upper())
        history = engine.get_trend_history(tf, limit=limit)

        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "history": [
                {
                    "direction": signal.direction.value,
                    "strength": signal.strength.value,
                    "confidence": signal.confidence,
                    "score": signal.score,
                    "timestamp": signal.timestamp.isoformat() if signal.timestamp else None
                }
                for signal in history
            ],
            "count": len(history)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trend history for {symbol} {timeframe}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/{symbol}/update/{timeframe}")
async def update_trend(symbol: str, timeframe: str, price: float, volume: float,
                      timestamp: str | None = None):
    """Update trend engine with new market data"""
    try:
        # Validate timeframe
        try:
            Timeframe(timeframe)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

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
            "timeframe": timeframe,
            "status": "updated",
            "timestamp": ts.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating trend for {symbol} {timeframe}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
