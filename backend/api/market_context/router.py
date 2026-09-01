"""
API endpoints for the market-wide context (Phase 8).

Surfaces the aggregate MarketContextSignal that combines SPY/QQQ/IWM/VIX
sub-regimes. Single global engine (no symbol) — there's just one market
context at a time.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from ...market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_quotes,
)
from backend.regime.market_context_engine import MarketContextEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-context", tags=["market-context"])

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


# One global instance (Phase 8 spec §1 — market-wide aggregate)
_engine: MarketContextEngine | None = None


def _seed_sub_engine(symbol: str, context_engine: MarketContextEngine) -> int:
    """Replay a sub-index's stored daily bars through its sub-engine.

    Returns the number of bars replayed. Without seeding, sub-engines start
    cold and the market context stays "unknown" until enough live ticks arrive
    (slow on restart). With ~200 historical bars, each sub-engine's
    EMA50/ADX14 indicators warm up immediately.

    Seeds from BarModel (OHLC bars) rather than QuoteModel because some
    indices (QQQ, IWM) have daily bars in the DB but no quote rows.
    """
    from backend.database import SessionLocal
    from backend.models.market_data_sql import BarModel

    sub = context_engine.sub_engines.get(symbol.upper())
    if sub is None:
        return 0

    db = SessionLocal()
    try:
        rows = (
            db.query(BarModel)
            .filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == "1d",
            )
            .order_by(BarModel.timestamp.asc())
            .limit(200)
            .all()
        )
        for bar in rows:
            # BarModel stores naive UTC datetimes (the SQLAlchemy DateTime
            # column has no tzinfo=True). The sub-engine's regime signal
            # timestamp flows into a max() call that compares against
            # datetime.now(timezone.utc) (aware) — comparing naive vs aware
            # raises TypeError. Tag every bar timestamp as UTC before
            # pushing it through the engine.
            ts = bar.timestamp
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            sub.update(
                price=float(bar.close or 0.0),
                volume=int(bar.volume or 0),
                timestamp=ts,
            )
        return len(rows)
    finally:
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
        for sym in _engine._cfg.indices:
            try:
                count = _seed_sub_engine(sym, _engine)
                if count > 0:
                    logger.info(
                        f"Seeded market-context sub-engine for {sym} "
                        f"with {count} historical quotes"
                    )
            except Exception as e:
                logger.warning(f"Failed to seed sub-engine for {sym}: {e}")
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
    """Get the current market-wide context signal."""
    try:
        engine = get_engine()
        signal = engine.get_current_context()
        if signal is None:
            return {
                "regime": "unknown",
                "confidence": 0.0,
                "trend_strength": 0.0,
                "momentum": 0.0,
                "volatility_state": "unknown",
                "sub_regimes": {},
                "contributing_factors": {"reason": "no_data"},
                "timestamp": None,
            }
        d = signal.to_dict()
        d["timestamp"] = _to_dashboard_tz(signal.timestamp)
        return d
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
