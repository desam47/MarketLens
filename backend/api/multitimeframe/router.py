"""
API endpoints for multi-timeframe analysis

TrendEngine instances are sourced from the shared registry
(``backend.api.trend.registry``) so the per-timeframe signals in the
confluence response match the signals from ``/api/trend/{symbol}/current/{tf}``.
Both endpoints read the same warmed-up TrendEngine state, eliminating the
signal divergence that arose from independent warmup paths.

The MTF engine still owns its own confluence weight and alignment
computations (per-preset), but delegates its per-TF trend data to the
shared engines.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from ...multitimeframe.multi_timeframe_engine import (
    PRESET_NAMES,
    MultiTimeframeEngine,
    MultiTimeframeSnapshot,
    TimeframeTrendSnapshot,
)

from ...market_data.services.engine_seeder import engine_registry
from ..trend.registry import get_engine as get_shared_trend_engine

# All timestamps in this response are emitted in America/New_York so the
# dashboard renders them in EST/EDT without each caller converting. Mirror
# of the helper in backend.api.main (kept inline here to avoid a circular
# import between the multitimeframe router and main).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    """Return ``value`` as an ISO string in America/New_York.

    Naive datetimes are treated as UTC (matches what the trend engines
    produce internally). Returns ``None`` for ``None`` so callers don't
    have to special-case the empty state.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(_DASHBOARD_TZ).isoformat()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multitimeframe", tags=["multitimeframe"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(_DASHBOARD_TZ).isoformat()

# Engines are keyed by (symbol, preset) so the dashboard can switch
# presets without losing warmup on the previous one.
_engines: dict[tuple[str, str], MultiTimeframeEngine] = {}


def get_engine(symbol: str, preset: str = "day_trading") -> MultiTimeframeEngine:
    """Get or create a multi-timeframe engine for (symbol, preset).

    The MTF engine delegates per-timeframe trend data to the shared
    TrendEngine instances from ``backend.api.trend.registry``.  This means
    ``get_current_confluence().timeframe_signals["15m"]`` returns the same
    direction/strength/confidence as ``GET /api/trend/SPY/current/15m``.
    """
    symbol = symbol.upper()
    key = (symbol, preset)
    if key not in _engines:
        engine = MultiTimeframeEngine(symbol, preset=preset)
        _engines[key] = engine

        # Inject the shared, pre-warmed TrendEngine for each active timeframe.
        # The shared engines are already seeded from BarModel OHLCV and
        # registered for live-tick updates by ``get_shared_trend_engine``.
        for tf in engine.analysis_timeframes:
            shared = get_shared_trend_engine(symbol)
            # The shared engine is keyed by symbol only (one engine per symbol),
            # so all MTF presets share the same underlying per-TF TrendEngine.
            # This is intentional: the trend signal for 15m should be identical
            # regardless of which preset is selected.
            engine.trend_engines[tf] = shared

        # Register this MTF engine with the engine registry so it receives
        # bar dispatches from the ingestion service.  When ingestion calls
        # ``engine_registry.dispatch_bar(symbol, timeframe, ...)``, the MTF
        # engine's ``update()`` is invoked, which calls
        # ``_generate_confluence_signal()`` to populate confluence_history.
        # Without this, the MTF engine's confluence endpoint always returned
        # empty signals because ``update()`` was never called.
        for tf in engine.analysis_timeframes:
            engine_registry.register(f"bar:{tf.value}", symbol, engine.update)

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
        "timestamp": _to_dashboard_tz(snap.timestamp),
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
        "timestamp": _to_dashboard_tz(snap.timestamp),
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
                "timestamp": _to_dashboard_tz(signal.timestamp),
            }

        return {
            "symbol": confluence_signal.symbol,
            "direction": confluence_signal.direction.value,
            "strength": confluence_signal.strength,
            "alignment_score": confluence_signal.alignment_score,
            "timeframe_signals": timeframe_signals,
            "timestamp": _to_dashboard_tz(confluence_signal.timestamp),
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
                    "timestamp": _to_dashboard_tz(signal.timestamp),
                }
                for signal in history
            ],
            "count": len(history),
        }
    except Exception as e:
        logger.error(f"Error getting MTF history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/snapshot")
async def get_mtf_snapshot(
    symbol: str,
    preset: str = Query(default="day_trading", description="Trading style preset"),
):
    """Get the current multi-timeframe snapshot for symbol.

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
    """Get multi-timeframe snapshot history for symbol."""
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
    """Update multi-timeframe engine with new market data.

    Delegates to the shared TrendEngine instances (which are already registered
    for live-tick updates), so no additional registration is needed.
    """
    try:
        engine = get_engine(symbol.upper(), preset=preset)

        # Parse timestamp if provided
        ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()

        # The MTF engine's update() delegates to each per-TF TrendEngine.
        # Those TrendEngines are shared with the trend API, so this single
        # call updates both the trend endpoint and the confluence endpoint.
        engine.update(
            price=price,
            volume=volume,
            timestamp=ts,
        )

        return {
            "symbol": symbol.upper(),
            "status": "updated",
            "timestamp": _to_dashboard_tz(ts),
        }
    except Exception as e:
        logger.error(f"Error updating MTF for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
