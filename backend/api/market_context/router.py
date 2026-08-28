"""
API endpoints for the market-wide context (Phase 8).

Surfaces the aggregate MarketContextSignal that combines SPY/QQQ/IWM/VIX
sub-regimes. Single global engine (no symbol) — there's just one market
context at a time.
"""
import logging

from fastapi import APIRouter, HTTPException

from backend.market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_quotes,
)
from backend.regime.market_context_engine import MarketContextEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-context", tags=["market-context"])

# One global instance (Phase 8 spec §1 — market-wide aggregate)
_engine: MarketContextEngine | None = None


def _seed_sub_engine(symbol: str, context_engine: MarketContextEngine) -> int:
    """Replay a sub-index's stored quotes through its sub-engine.

    Returns the number of quotes replayed. Without seeding, sub-engines
    start cold and the market context stays "unknown" until enough live
    ticks arrive (slow on restart). With ~200 historical quotes, each
    sub-engine's EMA50/ADX14 indicators warm up immediately.
    """
    sub = context_engine.sub_engines.get(symbol.upper())
    if sub is None:
        return 0
    return seed_engine_from_quotes(
        symbol,
        lambda **kw: sub.update(  # ignore high/low/open from quote rows
            price=kw.get("price", 0.0),
            volume=kw.get("volume", 0),
            timestamp=kw.get("timestamp"),
        ),
    )


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
        for sym in _engine._cfg.indices:
            engine_registry.register(
                "quote", sym, lambda p, v, t, _s=sym: _engine.update(
                    price=p, volume=v, timestamp=t, symbol=_s
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
        return signal.to_dict()
    except Exception as e:
        logger.error(f"Error getting market context: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/history")
async def get_context_history(limit: int | None = 100):
    """Get historical market-context signals (most recent first)."""
    try:
        engine = get_engine()
        history = engine.get_history(limit=limit)
        return {
            "history": [s.to_dict() for s in history],
            "count": len(history),
        }
    except Exception as e:
        logger.error(f"Error getting market context history: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
