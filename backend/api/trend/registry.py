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

from sqlalchemy import func
from sqlalchemy.orm import aliased

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


def _feed_warmup_bars(engine: TrendEngine, symbol: str, tf: Timeframe, bars) -> int:
    """Feed stored bars into ``engine`` for warmup; return how many it accepted.

    A rejected bar must not abort warmup (one bad row shouldn't leave the engine cold),
    but it used to vanish without a trace. Failures are now counted and reported once
    per (symbol, timeframe).
    """
    seeded = failed = 0
    last_error: Exception | None = None
    for bar in bars:
        try:
            engine.update(
                price=float(bar.close or 0.0),
                volume=int(bar.volume or 0),
                timestamp=bar.timestamp,
                only_timeframe=tf,
            )
            seeded += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            last_error = e
    if failed:
        logger.warning(
            "Trend warmup %s/%s: engine rejected %d of %d bars (last error: %r)",
            symbol, tf.value, failed, len(bars), last_error,
        )
    return seeded


def _seed_from_bar_model(symbol: str, engine: TrendEngine) -> int:
    """Seed a TrendEngine from historical BarModel rows with full OHLCV.

    Uses a single ``IN (...)`` query for all timeframes at once — previously
    made one query per timeframe (10 queries per symbol). Returns the total
    number of bars seeded across all timeframes.

    Caps each timeframe at 200 bars via a ROW_NUMBER() window partitioned
    by timeframe, NOT a flat ``LIMIT 200 * len(timeframes)`` on a single
    ``ORDER BY timestamp ASC`` — 1d/1wk have much deeper history than
    15m/30m/1h, so a flat ascending LIMIT was dominated by old daily/weekly
    rows and could exhaust the cap before reaching any short-timeframe rows,
    leaving those engines unseeded at startup.
    """
    db = SessionLocal()
    try:
        rn = func.row_number().over(
            partition_by=BarModel.timeframe,
            order_by=BarModel.timestamp.desc(),
        ).label("rn")
        subq = (
            db.query(BarModel, rn)
            .filter(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe.in_(_TREND_TIMEFRAMES),
            )
            .subquery()
        )
        bm = aliased(BarModel, subq)
        rows = (
            db.query(bm)
            .filter(subq.c.rn <= 200)
            .order_by(bm.timestamp.asc())
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
            seeded += _feed_warmup_bars(engine, symbol, tf, bars)
            if bars:
                logger.debug(
                    f"Seeded {symbol}/{tf_str} with {len(bars)} bars "
                    "(full OHLCV warmup)"
                )
        return seeded
    finally:
        db.close()


def _create_and_register_engine(symbol: str) -> TrendEngine:
    """Construct a bare (unseeded) TrendEngine, register it in the shared
    registry, and subscribe it to live-tick updates.

    Seeding is the caller's responsibility. Split out so
    ``_batch_seed_engines`` can create-then-seed-from-its-own-already-fetched
    rows instead of going through ``get_engine()``, which used to run its
    own per-symbol ``_seed_from_bar_model`` query and apply those bars
    immediately — doubling every bar applied to the engine's indicators
    when the batch path then applied its own shared-query rows on top
    (found live 2026-09-18; see backend/tests/api/test_trend_registry.py).
    """
    symbol = symbol.upper()
    engine = TrendEngine(symbol)
    _engines[symbol] = engine
    for tf in _TREND_TIMEFRAMES:
        engine_registry.register(f"bar:{tf}", symbol, engine.update)
    return engine


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
        # Single query for all bars across all symbols and timeframes, capped
        # at 200 bars per (symbol, timeframe) via a partitioned ROW_NUMBER()
        # window — see _seed_from_bar_model for why a flat LIMIT on a single
        # ascending order doesn't work here (deep-history timeframes like
        # 1d/1wk would starve shorter ones of their share of the cap).
        rn = func.row_number().over(
            partition_by=(BarModel.symbol, BarModel.timeframe),
            order_by=BarModel.timestamp.desc(),
        ).label("rn")
        subq = (
            db.query(BarModel, rn)
            .filter(
                BarModel.symbol.in_([s.upper() for s in symbols]),
                BarModel.timeframe.in_(_TREND_TIMEFRAMES),
            )
            .subquery()
        )
        bm = aliased(BarModel, subq)
        rows = (
            db.query(bm)
            .filter(subq.c.rn <= 200)
            .order_by(bm.timestamp.asc())
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
            symbol_u = symbol.upper()
            already_existed = symbol_u in _engines
            if already_existed:
                # Already seeded — either by an earlier get_engine() call
                # or an earlier batch. Applying these rows on top would
                # double-count every bar in the engine's indicators.
                results[symbol] = 0
                continue

            engine = _create_and_register_engine(symbol)
            seeded = 0
            tf_buckets = grouped.get(symbol_u, {})
            for tf_str, bars in tf_buckets.items():
                try:
                    tf = Timeframe(tf_str)
                except ValueError:
                    continue
                seeded += _feed_warmup_bars(engine, symbol, tf, bars)

            if seeded == 0:
                # No BarModel rows for this symbol in the batch's shared
                # query (e.g. a freshly-added watchlist symbol with no
                # bars ingested yet) — fall back to quote seeding, same as
                # get_engine()'s own lazy-create path, so the engine isn't
                # left permanently cold now that it's registered here.
                quote_count = seed_engine_from_quotes(symbol, engine.update)
                if quote_count > 0:
                    logger.info(
                        f"Seeded trend engine for {symbol} with {quote_count} quotes "
                        "(BarModel empty, quote fallback)"
                    )

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
        engine = _create_and_register_engine(symbol)

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

    return _engines[symbol]
