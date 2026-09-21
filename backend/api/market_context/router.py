"""
API endpoints for the market-wide context (Phase 8).

Surfaces the aggregate MarketContextSignal that combines SPY/QQQ/IWM/VIX
sub-regimes. Single global engine (no symbol) — there's just one market
context at a time.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from backend.api.ttl_cache import _context_cache
from backend.database import SessionLocal
from backend.models.market_data_sql import BarModel
from backend.regime.market_context_engine import MarketContextEngine
from backend.utils.timezone import ensure_aware_ny

from ...market_data.services.engine_seeder import engine_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-context", tags=["market-context"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


# One global instance (Phase 8 spec §1 — market-wide aggregate)
_engine: MarketContextEngine | None = None


def _seed_sub_engine(
    symbol: str,
    context_engine: MarketContextEngine,
    db: SessionLocal | None = None,
) -> int:
    """Replay a sub-index's stored daily bars through its sub-engine.

    Returns the number of bars replayed. Without seeding, sub-engines start
    cold and the market context stays "unknown" until enough live ticks arrive
    (slow on restart). With ~200 historical bars, each sub-engine's
    EMA50/ADX14 indicators warm up immediately.

    Seeds from BarModel (OHLC bars) rather than QuoteModel because some
    indices (QQQ, IWM) have daily bars in the DB but no quote rows.

    Phase 3.9.10: accepts an optional shared ``db`` session so the caller
    can batch all 4 sub-engine seeds into a single SessionLocal open.
    """
    sub = context_engine.sub_engines.get(symbol.upper())
    if sub is None:
        return 0

    own_session = db is None
    if own_session:
        from backend.database import SessionLocal as _SessionLocal
        db = _SessionLocal()
    try:
        rows = (
            db.query(BarModel)
            .filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == "1d",
            )
            .order_by(BarModel.timestamp.desc())
            .limit(200)
            .all()
        )
        # Query above orders newest-first to get the most RECENT 200 bars
        # (found live 2026-09-11: ascending + limit(200) was grabbing the
        # OLDEST 200 of e.g. SPY's 753 stored days — seeding every restart
        # with bars from over a year ago instead of a live-relevant
        # window). Replay into the regime engine oldest-to-newest so its
        # EMA/ADX indicators build up in the correct chronological order.
        for bar in reversed(rows):
            # BarModel stores naive **America/New_York** datetimes (the
            # SQLAlchemy DateTime column has no tzinfo=True). Engines compare
            # against aware values, so stamp the zone before pushing through.
            # This previously tagged them UTC, shifting every bar 4-5h.
            ts = ensure_aware_ny(bar.timestamp)
            sub.update(
                price=float(bar.close or 0.0),
                volume=int(bar.volume or 0),
                timestamp=ts,
            )
        return len(rows)
    finally:
        if own_session:
            db.close()


def get_engine() -> MarketContextEngine:
    """Lazy-init the singleton + register for live ticks on the 4 indices."""
    global _engine
    if _engine is None:
        _engine = MarketContextEngine()
        # Seed each sub-engine with historical quotes so we return real
        # signals immediately after a restart, not just after enough new
        # ticks arrive. (Live ticks continue flowing via the registry
        # registration below — the seed only warms up the indicators.)
        # Phase 3.9.10: one shared SessionLocal for all 4 sub-engines.
        db = SessionLocal()
        try:
            for sym in _engine._cfg.indices:
                try:
                    count = _seed_sub_engine(sym, _engine, db=db)
                    if count > 0:
                        logger.info(
                            f"Seeded market-context sub-engine for {sym} "
                            f"with {count} historical quotes"
                        )
                except Exception as e:
                    logger.warning(f"Failed to seed sub-engine for {sym}: {e}")
        finally:
            db.close()
        # Register each sub-engine with the live-tick path so it
        # automatically receives any incoming quotes for SPY/QQQ/IWM/VIX.
        # The lambda uses **kwargs because ``dispatch_quote`` invokes callbacks
        # as ``cb(price=..., volume=..., timestamp=..., high=..., low=...,
        # open_price=...)`` — fixed kwargs, not positional.
        for sym in _engine._cfg.indices:
            engine_registry.register(
                "quote", sym, lambda price, volume, timestamp, _s=sym, **_:
                    _engine.update(
                        price=price, volume=volume, timestamp=timestamp, symbol=_s
                    )
            )
    return _engine


@router.get("/current")
async def get_current_context():
    """Get the current market-wide context signal (10s TTL cache).

    This is the most-frequently-polled endpoint from the dashboard header.
    """
    cached = _context_cache.get("market_context")
    if cached is not None:
        return cached
    try:
        engine = get_engine()
        signal = engine.get_current_context()
        if signal is None:
            payload = {
                "regime": "unknown",
                "confidence": 0.0,
                "trend_strength": 0.0,
                "momentum": 0.0,
                "volatility_state": "unknown",
                "sub_regimes": {},
                "contributing_factors": {"reason": "no_data"},
                "timestamp": None,
            }
        else:
            payload = signal.to_dict()
            payload["timestamp"] = _to_dashboard_tz(signal.timestamp)
        _context_cache["market_context"] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting market context: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/history")
async def get_context_history(limit: int | None = 100):
    """Get historical market-context signals (most recent first)."""
    try:
        engine = get_engine()
        history = engine.get_history(limit=limit)
        out = []
        for s in history:
            d = s.to_dict()
            d["timestamp"] = _to_dashboard_tz(s.timestamp)
            out.append(d)
        return {
            "history": out,
            "count": len(history),
        }
    except Exception as e:
        logger.error(f"Error getting market context history: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
