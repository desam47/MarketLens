"""
API endpoints for multi-timeframe analysis
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from backend.market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_quotes,
)
from backend.multitimeframe.multi_timeframe_engine import (
    PRESET_NAMES,
    MultiTimeframeEngine,
    MultiTimeframeSnapshot,
    TimeframeTrendSnapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multitimeframe", tags=["multitimeframe"])

# Engines are keyed by (symbol, preset) so the dashboard can switch
# presets without losing warmup on the previous one.
_engines: dict[tuple[str, str], MultiTimeframeEngine] = {}

# Timeframes we ingest bars for. Used to register the confluence engine with
# the live-tick registry under each bar:{tf} key, so it gets notified per-TF.
# Must match what the ingestion service publishes.
_MTF_TIMEFRAMES = ("1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk")


def _seed_mtf_from_bars(symbol: str, engine: MultiTimeframeEngine) -> None:
    """Seed each per-TF trend engine from historical bars with full OHLCV.

    Replays bars oldest→newest directly into each TrendEngine so that the
    TrendEngine's indicators (SuperTrend, BollingerBands, EMA, etc.) have
    proper OHLC data to initialize and compute from.  Without this, the
    seeder only passes close+volume and the indicators silently get None
    for open/high/low — which causes `get_current_trend()` to return None
    for longer timeframes (30m, 1wk) even when bars are persisted.
    """
    from backend.database import SessionLocal
    from backend.models.market_data_sql import BarModel

    db = SessionLocal()
    try:
        for tf in engine.analysis_timeframes:
            trend_eng = engine.trend_engines.get(tf)
            if trend_eng is None:
                continue
            rows = (
                db.query(BarModel)
                .filter(
                    BarModel.symbol == symbol.upper(),
                    BarModel.timeframe == tf.value,
                )
                .order_by(BarModel.timestamp.asc())
                .limit(200)
                .all()
            )
            for bar in rows:
                try:
                    # TrendEngine.update signature is (price, volume, timestamp, provider).
                    # We use the bar's close as the canonical price; the
                    # engine's indicator warmup is intentionally price-only
                    # (see trend_engine._update_indicators which synthesizes
                    # OHLC from the price tick).
                    trend_eng.update(
                        price=float(bar.close or 0.0),
                        volume=int(bar.volume or 0),
                        timestamp=bar.timestamp,
                    )
                except Exception:
                    pass  # Indicator warmup errors during seed are non-fatal
            if rows:
                logger.info(
                    f"Seeded {symbol}/{tf.value} with {len(rows)} historical bars "
                    f"(preset={engine.preset_name})"
                )
    finally:
        db.close()


def get_engine(symbol: str, preset: str = "day_trading") -> MultiTimeframeEngine:
    """Get or create multi-timeframe engine for (symbol, preset).

    Each preset gets its own engine instance so they don't compete for
    the same in-memory trend state — the scalper preset's 1m trend
    engine is independent of the swing preset's 1wk trend engine.
    """
    symbol = symbol.upper()
    key = (symbol, preset)
    if key not in _engines:
        engine = MultiTimeframeEngine(symbol, preset=preset)
        _engines[key] = engine
        # Seed from quotes (tick-level) for fast micro-trend setup.
        quote_count = seed_engine_from_quotes(symbol, engine.update)
        if quote_count > 0:
            logger.info(
                f"Seeded confluence engine for {symbol} ({preset}) "
                f"with {quote_count} historical quotes"
            )
        # Seed each per-TF trend engine from historical bars with full
        # OHLCV data.  We seed the TrendEngine directly (not via
        # ``engine.update``) so the MTF history isn't polluted with
        # rows that were only seeded, not live ticks.
        _seed_mtf_from_bars(symbol, engine)
        # Register for live-tick updates per timeframe (same pattern as trend)
        for tf in _MTF_TIMEFRAMES:
            engine_registry.register(f"bar:{tf}", symbol, engine.update)
    return _engines[key]


@router.get("/presets")
async def list_presets():
    """List available multi-timeframe presets.

    The dashboard uses this to render the preset dropdown. Order is
    the canonical order (shortest horizon first).
    """
    return {"presets": [{"name": name} for name in PRESET_NAMES]}


def _serialize_timeframe_snapshot(snap: TimeframeTrendSnapshot) -> dict:
    """Serialize a TimeframeTrendSnapshot to a JSON-friendly dict."""
    return {
        "symbol": snap.symbol,
        "timeframe": snap.timeframe.value,
        "timestamp": snap.timestamp.isoformat() if snap.timestamp else None,
        "direction": snap.direction.value,
        "score": snap.score,
        "strength": snap.strength,
        "confidence": snap.confidence,
        "data_quality": snap.data_quality,
        "strategy_version": snap.strategy_version,
    }


def _serialize_snapshot(snap: MultiTimeframeSnapshot) -> dict:
    """Serialize a MultiTimeframeSnapshot to a JSON-friendly dict."""
    return {
        "symbol": snap.symbol,
        "timestamp": snap.timestamp.isoformat() if snap.timestamp else None,
        "preset": snap.preset,
        "direction": snap.direction.value,
        "strength": snap.strength,
        "alignment_score": snap.alignment_score,
        "bullish_alignment": snap.bullish_alignment,
        "bearish_alignment": snap.bearish_alignment,
        "conflicting": snap.conflicting,
        "short_term_direction": snap.short_term_direction.value,
        "intermediate_direction": snap.intermediate_direction.value,
        "higher_direction": snap.higher_direction.value,
        "timeframe_snapshots": {
            tf.value: _serialize_timeframe_snapshot(tf_snap)
            for tf, tf_snap in snap.timeframe_snapshots.items()
        },
        "strategy_version": snap.strategy_version,
    }


@router.get("/{symbol}/confluence")
async def get_current_confluence(
    symbol: str,
    preset: str = Query(default="day_trading", description="Trading style preset: scalper, day_trading, swing, or all"),
):
    """Get current multi-timeframe confluence for symbol"""
    try:
        engine = get_engine(symbol.upper(), preset=preset)
        confluence_signal = engine.get_current_confluence()

        if confluence_signal is None:
            return {
                "symbol": symbol.upper(),
                "direction": "neutral",
                "strength": 0.0,
                "alignment_score": 0.0,
                "timeframe_signals": {},
                "timestamp": None,
                # Phase 7: extended fields default to neutral/no-signal.
                "bullish_alignment": 0.0,
                "bearish_alignment": 0.0,
                "conflicting": 0,
                "short_term_direction": "no_signal",
                "intermediate_direction": "no_signal",
                "higher_direction": "no_signal",
                "preset": engine.preset_name,
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
            "timestamp": confluence_signal.timestamp.isoformat() if confluence_signal.timestamp else None,
            # Phase 7: spec-derived alignment + horizon direction fields.
            "bullish_alignment": getattr(confluence_signal, "bullish_alignment", 0.0),
            "bearish_alignment": getattr(confluence_signal, "bearish_alignment", 0.0),
            "conflicting": getattr(confluence_signal, "conflicting", 0),
            "short_term_direction": confluence_signal.short_term_direction.value,
            "intermediate_direction": confluence_signal.intermediate_direction.value,
            "higher_direction": confluence_signal.higher_direction.value,
            "preset": getattr(confluence_signal, "preset", engine.preset_name),
        }
    except Exception as e:
        logger.error(f"Error getting confluence for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/history")
async def get_mtf_history(
    symbol: str,
    limit: int | None = 100,
    preset: str = Query(default="day_trading", description="Trading style preset"),
):
    """Get multi-timeframe history for symbol"""
    try:
        engine = get_engine(symbol.upper(), preset=preset)
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
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/snapshot")
async def get_mtf_snapshot(
    symbol: str,
    preset: str = Query(default="day_trading", description="Trading style preset"),
):
    """Get the current multi-timeframe snapshot for symbol (Phase 7).

    Returns the full ``MultiTimeframeSnapshot`` model: per-timeframe trend
    snapshots, alignment scores, conflicting count, and short / intermediate /
    higher-timeframe directions.
    """
    try:
        engine = get_engine(symbol.upper(), preset=preset)
        snap = engine.get_current_snapshot()
        if snap is None:
            return {
                "symbol": symbol.upper(),
                "snapshot": None,
            }
        return {
            "symbol": symbol.upper(),
            "snapshot": _serialize_snapshot(snap),
        }
    except Exception as e:
        logger.error(f"Error getting MTF snapshot for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/snapshot/history")
async def get_mtf_snapshot_history(
    symbol: str,
    limit: int | None = 100,
    preset: str = Query(default="day_trading", description="Trading style preset"),
):
    """Get multi-timeframe snapshot history for symbol (Phase 7)."""
    try:
        engine = get_engine(symbol.upper(), preset=preset)
        history = engine.get_snapshot_history(limit=limit)
        return {
            "symbol": symbol.upper(),
            "history": [_serialize_snapshot(s) for s in history],
            "count": len(history),
        }
    except Exception as e:
        logger.error(f"Error getting MTF snapshot history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{symbol}/update")
async def update_mtf(
    symbol: str,
    price: float,
    volume: float,
    timestamp: str | None = None,
    preset: str = Query(default="day_trading", description="Trading style preset"),
):
    """Update multi-timeframe engine with new market data"""
    try:
        engine = get_engine(symbol.upper(), preset=preset)

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
        raise HTTPException(status_code=500, detail=str(e)) from e
