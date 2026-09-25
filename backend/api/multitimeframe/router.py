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
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from backend.api.ttl_cache import _confluence_cache, _mtf_history_cache

from ...multitimeframe.multi_timeframe_engine import (
    PRESET_NAMES,
    MultiTimeframeEngine,
    MultiTimeframeSnapshot,
    TimeframeTrendSnapshot,
)
from ..trend.registry import get_engine as get_shared_trend_engine

# All timestamps in this response are emitted in America/New_York so the
# dashboard renders them in EST/EDT without each caller converting.
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    """Return ``value`` as an ISO string in America/New_York.

    Naive datetimes are NY wall time (project storage convention).
    Returns ``None`` for ``None`` so callers don't special-case empty.
    """
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multitimeframe", tags=["multitimeframe"])

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

        # Synthesize an initial confluence signal from the shared engine
        # warmup so the dashboard shows data immediately on first request,
        # not just after a fresh 1m bar arrives.  Without this, presets
        # whose timeframes never receive a live dispatch (e.g. ``all``
        # includes 1d/1wk which only get warmup data) would render empty
        # until the next bar of any registered TF arrives.
        engine._generate_confluence_signal(datetime.now(UTC))

        # MTF engine is now READ-ONLY: it does NOT register for bar updates.
        # The shared TrendEngine(s) are already updated by the ingestion
        # service directly. MTF only aggregates their current signals on demand.
        # This avoids the bug where one bar would update the same shared
        # engine N times (once per TF in the preset).

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
        # Phase 7+: quality metrics
        "data_age_seconds": snap.data_age_seconds,
        "bar_closed": snap.bar_closed,
        "is_warmed_up": snap.is_warmed_up,
        "valid": snap.valid,
        "quality_weight": snap.quality_weight,
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
        "short_term_state": snap.short_term_state.value,
        "intermediate_state": snap.intermediate_state.value,
        "higher_state": snap.higher_state.value,
        "timeframe_snapshots": {
            tf.value: _serialize_timeframe_snapshot(tf_snap)
            for tf, tf_snap in snap.timeframe_snapshots.items()
        },
        "strategy_version": snap.strategy_version,
        # Phase 7+: quality metrics
        "valid_coverage": snap.valid_coverage,
        "quality_weighted_score": snap.quality_weighted_score,
    }


def build_confluence_payload(engine: MultiTimeframeEngine, symbol: str) -> dict:
    """Build the confluence response payload for an already-resolved engine.

    Shared by the HTTP endpoint and the AI Hub ``get_confluence`` tool so
    both read the exact same fields off the exact same engine instance —
    no separate reimplementation to drift out of sync (see the
    get_market_regime_tool/get_market_context_tool incident: duplicated
    ad hoc serialization silently diverged from the real endpoint).
    """
    confluence_signal = engine.get_current_confluence()

    # Composite across every configured timeframe's TrendEngine, all for the
    # same symbol — they should agree on provider, so the first populated
    # one is a representative answer, not a guess among disagreeing sources.
    provider = "MarketLens engine"
    for sub_engine in engine.trend_engines.values():
        for tf in sub_engine.trend_history:
            metadata = sub_engine.get_timeframe_metadata(tf)
            if metadata.get("provider"):
                provider = metadata["provider"]
                break
        if provider != "MarketLens engine":
            break

    if confluence_signal is None:
        return {
            "symbol": symbol.upper(),
            "provider": provider,
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
            "short_term_state": "neutral",
            "intermediate_state": "neutral",
            "higher_state": "neutral",
            "preset": engine.preset_name,
            "valid_coverage": 0.0,
            "quality_weighted_score": 0.0,
        }

    # Reuse the Trend payload builder for every contributing timeframe. This
    # keeps Confluence's source timestamp, validity, warm-up and freshness
    # contract byte-for-byte aligned with the adjacent Trend by Timeframe
    # cards instead of serializing a second, weaker version of that state.
    from backend.api.trend.router import _build_trend_payload

    timeframe_signals = {}
    for tf in confluence_signal.timeframe_signals:
        trend_payload = _build_trend_payload(
            engine.trend_engines[tf],
            symbol.upper(),
            tf.value,
            tf,
        )
        timeframe_signals[tf.value] = {
            "direction": trend_payload["direction"],
            "strength": trend_payload["strength"],
            "confidence": trend_payload["confidence"],
            "score": trend_payload["score"],
            "timestamp": trend_payload["timestamp"],
            "evidence": trend_payload["evidence"],
        }
    return {
        "symbol": confluence_signal.symbol,
        "provider": provider,
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
        "short_term_state": getattr(confluence_signal, "short_term_state", "neutral").value,
        "intermediate_state": getattr(
            confluence_signal, "intermediate_state", "neutral"
        ).value,
        "higher_state": getattr(confluence_signal, "higher_state", "neutral").value,
        "preset": getattr(confluence_signal, "preset", engine.preset_name),
        "valid_coverage": getattr(confluence_signal, "valid_coverage", 0.0),
        "quality_weighted_score": getattr(confluence_signal, "quality_weighted_score", 0.0),
    }


@router.get("/{symbol}/confluence")
async def get_current_confluence(
    symbol: str,
    preset: str = Query(
        default="day_trading",
        description="Trading style preset: scalper, day_trading, swing, or all",
    ),
):
    """Get current multi-timeframe confluence for symbol (30s TTL cache)."""
    # Validate preset - reject unknown presets with 422
    if preset not in PRESET_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid preset: '{preset}'. Valid presets: {', '.join(PRESET_NAMES)}",
        )
    key = f"{symbol.upper()}:{preset}"
    cached = _confluence_cache.get(key)
    if cached is not None:
        return cached
    try:
        engine = get_engine(symbol.upper(), preset=preset)
        payload = build_confluence_payload(engine, symbol)
        _confluence_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting confluence for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/history")
async def get_mtf_history(
    symbol: str,
    limit: int | None = 100,
    preset: str = Query(default="day_trading", description="Trading style preset"),
):
    """Get multi-timeframe history for symbol (60s TTL cache)."""
    key = f"{symbol.upper()}:{preset}:{limit}"
    cached = _mtf_history_cache.get(key)
    if cached is not None:
        return cached
    try:
        engine = get_engine(symbol.upper(), preset=preset)
        history = engine.get_confluence_history(limit=limit)

        payload = {
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
        _mtf_history_cache[key] = payload
        return payload
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
    # Validate preset - reject unknown presets with 422
    if preset not in PRESET_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid preset: '{preset}'. Valid presets: {', '.join(PRESET_NAMES)}",
        )
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
    # Validate preset - reject unknown presets with 422
    if preset not in PRESET_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid preset: '{preset}'. Valid presets: {', '.join(PRESET_NAMES)}",
        )
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

        # MTF engine is now read-only: just generate a fresh confluence signal
        # from the current shared TrendEngine states.
        engine._generate_confluence_signal(ts)

        return {
            "symbol": symbol.upper(),
            "status": "updated",
            "timestamp": _to_dashboard_tz(ts),
        }
    except Exception as e:
        logger.error(f"Error updating MTF for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
