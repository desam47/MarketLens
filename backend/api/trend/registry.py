"""
Shared TrendEngine registry.

All TrendEngine instances are managed here so that every caller — the
trend API, the multi-timeframe API, and any future consumer — shares the
same warmed-up engine per symbol. This eliminates the signal divergence
that arose when each router created its own TrendEngine with a different
warmup path (quotes vs. bar OHLCV).

Usage:
    from backend.api.trend.registry import get_engine

    engine = get_engine("SPY")          # returns the shared TrendEngine for SPY
    signal = engine.get_current_trend(tf)
"""

from __future__ import annotations

import logging

from backend.database import SessionLocal
from backend.engines.timeframe import Timeframe
from backend.market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_quotes,
)
from backend.models.market_data_sql import BarModel
from backend.trend.trend_engine import TrendEngine

logger = logging.getLogger(__name__)

# Module-level registry: one TrendEngine per symbol, shared across all callers.
_engines: dict[str, TrendEngine] = {}

# Timeframes to register for live-tick ingestion.
# Must match what the ingestion service publishes.
_TREND_TIMEFRAMES = ("1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk")

# Symbols to pre-warm engines for at startup (reads from ingestion defaults).
_WARMUP_SYMBOLS: tuple[str, ...] = ()
try:
    # Import lazily to avoid circular imports at module-load time.
    from backend.market_data.services.ingestion_service import ingestion_service

    # At import time ingestion_service.symbols is empty (not yet started),
    # so we keep _WARMUP_SYMBOLS as () and let warmup_engines() resolve
    # the real symbols at call time instead.
    _WARMUP_SYMBOLS = ()
except Exception:
    _WARMUP_SYMBOLS = ("SPY", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA", "META", "NFLX")


def _seed_from_bar_model(symbol: str, engine: TrendEngine) -> int:
    """Seed a TrendEngine from historical BarModel rows with full OHLCV.

    Unlike ``seed_engine_from_quotes`` (which only provides price+volume),
    this replays the real OHLC bars so SuperTrend/EMA/BB indicators have
    accurate open/high/low data to compute from.  The most impactful
    difference is for shorter timeframes (15m, 30m) where quote-only
    seeding causes the indicators to synthesize fake OHLC from close.

    Returns the number of bars seeded.
    """
    db = SessionLocal()
    try:
        seeded = 0
        for tf_str in _TREND_TIMEFRAMES:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue
            rows = (
                db.query(BarModel)
                .filter(
                    BarModel.symbol == symbol.upper(),
                    BarModel.timeframe == tf_str,
                )
                .order_by(BarModel.timestamp.asc())
                .limit(200)
                .all()
            )
            for bar in rows:
                try:
                    engine.update(
                        price=float(bar.close or 0.0),
                        volume=int(bar.volume or 0),
                        timestamp=bar.timestamp,
                    )
                    seeded += 1
                except Exception:
                    pass  # Warmup errors are non-fatal
            if rows:
                logger.info(
                    f"Seeded {symbol}/{tf_str} with {len(rows)} bars "
                    "(full OHLCV warmup)"
                )
        return seeded
    finally:
        db.close()


def warmup_engines() -> dict[str, int]:
    """Pre-register trend engines for all ingestion symbols at startup.

    Eliminates the cold-start gap where bars arrive before any API request
    has triggered engine registration.  Also seeds each engine from the
    BarModel rows so the indicators have full OHLCV for warmup, not just
    quote price+volume.
    Returns a dict of symbol -> number of bars seeded.

    The symbol list is resolved dynamically from ``ingestion_service.symbols``
    so a watchlist that was empty at import time but populated by the time
    ``warmup_engines()`` is called is still picked up.
    """
    # Resolve symbols at call time — ingestion_service.start() may have
    # populated its list after this module was imported.
    try:
        from backend.market_data.services.ingestion_service import ingestion_service
        symbols = tuple(ingestion_service.symbols) if ingestion_service.symbols else _WARMUP_SYMBOLS
    except Exception:
        symbols = _WARMUP_SYMBOLS

    results = {}
    for symbol in symbols:
        try:
            engine = get_engine(symbol)
            bar_count = _seed_from_bar_model(symbol, engine)
            results[symbol] = bar_count
            logger.info(
                f"Warmed up trend engine for {symbol} "
                f"({bar_count} bars seeded from BarModel)"
            )
        except Exception as e:
            logger.warning(f"Failed to warm up trend engine for {symbol}: {e}")
            results[symbol] = 0
    return results


def get_engine(symbol: str) -> TrendEngine:
    """Get or create a shared TrendEngine for symbol.

    The same TrendEngine instance is returned for all callers.  On first
    access the engine is created, seeded from historical BarModel rows
    (full OHLCV), registered for live-tick updates, and then returned.

    Subsequent callers for the same symbol get the same instance with the
    same warmup history — no divergence.
    """
    symbol = symbol.upper()
    if symbol not in _engines:
        engine = TrendEngine(symbol)
        _engines[symbol] = engine

        # Seed with bar OHLCV for full indicator warmup.
        bar_count = _seed_from_bar_model(symbol, engine)
        if bar_count == 0:
            # No persisted bars yet (fresh DB); fall back to quote seed
            # so the engine is not completely cold.
            quote_count = seed_engine_from_quotes(symbol, engine.update)
            if quote_count > 0:
                logger.info(
                    f"Seeded trend engine for {symbol} with {quote_count} quotes "
                    "(BarModel empty, quote fallback)"
                )
            else:
                logger.warning(
                    f"Trend engine for {symbol} could not seed from BarModel "
                    "(0 bars) or QuoteModel (0 quotes) — engine is cold; "
                    "trend API will return 'unknown' until bars are ingested"
                )
        else:
            logger.info(
                f"Seeded trend engine for {symbol} with {bar_count} bars "
                "(full OHLCV)"
            )

        # Register for live-tick updates per timeframe so the engine
        # stays current as the ingestion service publishes new bars.
        for tf in _TREND_TIMEFRAMES:
            engine_registry.register(f"bar:{tf}", symbol, engine.update)

    return _engines[symbol]
