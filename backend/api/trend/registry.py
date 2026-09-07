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

    Uses a single ``IN (...)`` query for all timeframes at once — previously
    made one query per timeframe (10 queries per symbol). Returns the total
    number of bars seeded across all timeframes.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(BarModel)
            .filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe.in_(_TREND_TIMEFRAMES),
            )
            .order_by(BarModel.timestamp.asc())
            .limit(200 * len(_TREND_TIMEFRAMES))
            .all()
        )
        seeded = 0
        by_tf: dict[str, list] = {}
        for bar in rows:
            by_tf.setdefault(bar.timeframe, []).append(bar)

        for tf_str, bars in by_tf.items():
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue
            for bar in bars:
                try:
                    engine.update(
                        price=float(bar.close or 0.0),
                        volume=int(bar.volume or 0),
                        timestamp=bar.timestamp,
                        only_timeframe=tf,
                    )
                    seeded += 1
                except Exception:
                    pass  # Warmup errors are non-fatal
            if bars:
                logger.debug(
                    f"Seeded {symbol}/{tf_str} with {len(bars)} bars "
                    "(full OHLCV warmup)"
                )
        return seeded
    finally:
        db.close()


def _batch_seed_engines(symbols: tuple[str, ...]) -> dict[str, int]:
    """Seed multiple trend engines from a single ``IN (...)`` query.

    Phase 3.9.3: previously each engine was seeded with its own per-symbol
    query (and before that, 10 per-timeframe queries × N symbols). This
    helper collapses the entire startup warmup into one round-trip: one
    query for all (symbol, timeframe) rows, then Python dispatches into
    each engine's per-timeframe state.
    """
    if not symbols:
        return {}

    db = SessionLocal()
    try:
        # Single query for all bars across all symbols and timeframes.
        rows = (
            db.query(BarModel)
            .filter(
                BarModel.symbol.in_([s.upper() for s in symbols]),
                BarModel.timeframe.in_(_TREND_TIMEFRAMES),
            )
            .order_by(BarModel.timestamp.asc())
            .limit(200 * len(_TREND_TIMEFRAMES) * len(symbols))
            .all()
        )
    finally:
        db.close()

    # Group rows by symbol+timeframe so each engine gets its own slices.
    grouped: dict[str, dict[str, list[BarModel]]] = {}
    for bar in rows:
        grouped.setdefault(bar.symbol, {}).setdefault(bar.timeframe, []).append(bar)

    results: dict[str, int] = {}
    for symbol in symbols:
        try:
            engine = get_engine(symbol)
            seeded = 0
            tf_buckets = grouped.get(symbol.upper(), {})
            for tf_str, bars in tf_buckets.items():
                try:
                    tf = Timeframe(tf_str)
                except ValueError:
                    continue
                for bar in bars:
                    try:
                        engine.update(
                            price=float(bar.close or 0.0),
                            volume=int(bar.volume or 0),
                            timestamp=bar.timestamp,
                            only_timeframe=tf,
                        )
                        seeded += 1
                    except Exception:
                        pass  # Warmup errors are non-fatal
            results[symbol] = seeded
            if seeded:
                logger.info(
                    f"Bulk-seeded trend engine for {symbol} "
                    f"({seeded} bars from shared BarModel query)"
                )
        except Exception as e:
            logger.warning(f"Failed to bulk-seed trend engine for {symbol}: {e}")
            results[symbol] = 0
    return results


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

    # Phase 3.9.3: single batched query instead of N per-symbol queries.
    # Engines are still created lazily via get_engine() so the per-symbol
    # seed loop only adds bars, not extra DB hits.
    results = _batch_seed_engines(symbols)
    for sym, count in results.items():
        if count:
            logger.info(
                f"Warmed up trend engine for {sym} "
                f"({count} bars seeded from BarModel)"
            )
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
