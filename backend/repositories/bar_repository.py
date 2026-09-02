"""
Bar repository for persisting and retrieving historical OHLCV bars.
"""
import collections.abc
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from backend.models.market_data import Bar, DataStatus
from backend.models.market_data_sql import BarModel
from backend.observability import record_bar, record_bars
from backend.utils.resampler import resample_ohlcv, ResampleError

logger = logging.getLogger(__name__)

# Phase 3.1: map each target timeframe to its 1m multiplier.
# Used to compute how many 1m bars to fetch when producing N output bars.
# Each value is a conservative upper bound on the 1m data per output bar,
# used for ``fetch_limit = limit * multiplier``.
#
# The _WIDENING_HOURS table below is used for the lower-bound widening
# (from_ts - timedelta) and is proportional to the calendar period, which
# is distinct from the trading-minute estimate used for fetch_limit.
_TF_MULTIPLIER: dict[str, int] = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 480,   # 8h × 60 min (RTH + extended hours for liquid names)
    "1wk": 2400,  # 5 trading days × 480 min (8h with extended hours)
}

# Hours to widen the from_ts lower bound so the leading resampled bucket
# is complete. Proportional to the calendar period of each timeframe.
_WIDENING_HOURS: dict[str, int] = {
    "5m": 0,
    "15m": 0,
    "30m": 1,
    "1h": 1,
    "4h": 4,
    "1d": 24,   # calendar day
    "1wk": 168,  # ISO week = 7 days
}


def _bar_to_model(bar: Bar) -> BarModel:
    """Convert a Pydantic Bar to a BarModel row."""
    return BarModel(
        symbol=bar.symbol.upper(),
        timeframe=bar.timeframe,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        timestamp=bar.timestamp,
        provider=bar.provider,
        data_status=bar.data_status.value if isinstance(bar.data_status, DataStatus) else str(bar.data_status),
    )


def upsert_bars(db: Session, bars: list[Bar]) -> int:
    """Bulk-insert or update a list of bars.

    Uses ``insert(...).on_conflict_do_update`` when the uniqueness
    constraint is in place; falls back to per-row merge when it isn't.
    Returns the number of rows written.
    """
    if not bars:
        return 0

    # Count the bars we're about to write for the metrics endpoint.
    # We call record_bar after the commit so the counter reflects rows
    # actually written, not attempted.
    incoming_count = len(bars)

    # Detect whether the unique constraint exists by checking sqlite_master.
    # The constraint is added via the model's unique=True flag.
    has_unique = _has_unique_constraint(db, "bars",
        ("symbol", "timeframe", "timestamp"))

    written = 0
    if has_unique:
        # Fast bulk upsert via ON CONFLICT DO UPDATE.
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(BarModel).values([
            {
                "symbol": b.symbol.upper(),
                "timeframe": b.timeframe,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "timestamp": b.timestamp,
                "provider": b.provider,
                "data_status": (
                    b.data_status.value
                    if isinstance(b.data_status, DataStatus)
                    else str(b.data_status)
                ),
                "source": "raw",  # Phase 3.1: ingestion always writes raw 1m.
            }
            for b in bars
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "timestamp"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "provider": stmt.excluded.provider,
                "data_status": stmt.excluded.data_status,
            },
        )
        result = db.execute(stmt)
        db.commit()
        written = result.rowcount
    else:
        # Per-row merge fallback (SQLAlchemy 2.0 ORM style).
        for bar in bars:
            existing = db.query(BarModel).filter(
                and_(
                    BarModel.symbol == bar.symbol.upper(),
                    BarModel.timeframe == bar.timeframe,
                    BarModel.timestamp == bar.timestamp,
                )
            ).first()
            if existing:
                existing.open = bar.open
                existing.high = bar.high
                existing.low = bar.low
                existing.close = bar.close
                existing.volume = bar.volume
                existing.provider = bar.provider
                existing.data_status = (
                    bar.data_status.value
                    if isinstance(bar.data_status, DataStatus)
                    else str(bar.data_status)
                )
            else:
                db.add(_bar_to_model(bar))
            written += 1
        db.commit()

    # Record per-bar counters for the perf endpoint. The bar_repository
    # is the single point of write for all bars, so this is the right
    # place to count. We record after commit so a rolled-back transaction
    # doesn't inflate the counter.
    record_bars(incoming_count)

    return written


def get_bars(
    db: Session,
    symbol: str,
    timeframe: str,
    limit: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    fallback_provider: collections.abc.Callable[[str, str], list[Bar]] | None = None,
) -> list[Bar]:
    """Return bars for (symbol, timeframe), ordered oldest → newest.

    Phase 3.1: the storage layer only holds 1m bars. When the caller
    requests a higher timeframe (5m, 15m, 30m, 1h, 1d, 1wk) this function
    transparently fetches 1m rows from the DB and resamples them in-memory
    using ``resample_ohlcv``.

    For ``timeframe == "1m"`` the query hits the index directly and
    returns raw rows.

    For higher timeframes, the function fetches enough 1m bars to satisfy
    ``limit`` (with a 1m-multiplier safety margin), resamples, and slices
    the result to ``limit``.

    ``from_ts`` / ``to_ts`` are optional time-range filters. For the 1m
    fast path they translate into a ``WHERE timestamp >= from_ts AND
    timestamp <= to_ts`` clause. For higher timeframes, the same
    filters are applied to the underlying 1m fetch *before* resampling,
    so the result respects the requested window. The fetcher widens the
    1m range by the resample multiplier on the from side so a window
    that starts mid-bucket still produces a complete leading bar
    (otherwise the open/close of the first bar would be clipped).

    Phase 3.1.8: if the DB returns fewer bars than required to honour
    ``limit`` *after* resampling, and ``fallback_provider`` is supplied
    (a ``Callable[[symbol, timeframe], list[Bar]]``), the function
    falls back to fetching the target timeframe directly from the
    provider. The fallback only triggers when 1m coverage is
    insufficient — most cold-cache and short-window requests are served
    purely from the DB. Use this to backfill missing data transparently
    for the 1d/1wk timeframes that yfinance serves without 1m backing.

    The ``(symbol, timeframe, timestamp)`` unique index on the table provides
    deduplication — no two rows share the same triple. (Pre-existing rows
    inserted before the index was added are a data-quality concern for
    migration, not for query-time deduplication; removing the now-redundant
    .distinct() call improves query performance.)

    Phase 20 perf: slow queries (≥ 100ms) are logged with their EXPLAIN
    plan so expensive query patterns surface during development.
    """
    import time

    # Determine multiplier and per-path fetch parameters. For 1m the
    # multiplier is 1 (no widening, no resampling); for higher TFs the
    # multiplier controls how much 1m data to over-fetch so resampling
    # produces a complete leading bucket.
    is_resampled = timeframe != "1m"
    multiplier = _TF_MULTIPLIER.get(timeframe) if is_resampled else None
    if is_resampled and multiplier is None:
        # Unsupported target (e.g. 2m, 4h) — return empty so callers don't
        # silently get wrong data.
        logger.warning(f"get_bars: unsupported timeframe {timeframe!r}; returning []")
        return []

    # Bound the upper edge when the caller gave us a ``from_ts`` but no
    # ``to_ts`` and no ``limit``. Otherwise the query walks the entire
    # history from the widened lower bound to the latest bar — for a
    # multi-year backtest or a user-supplied start date, that's
    # potentially millions of rows. Cap the upper bound at ``now()`` so
    # the read is symmetric and bounded.
    fetch_limit = (limit * multiplier) if (limit and is_resampled) else limit
    effective_to_ts = to_ts
    if (
        is_resampled
        and from_ts is not None
        and effective_to_ts is None
        and not fetch_limit
    ):
        effective_to_ts = datetime.now(timezone.utc)
        logger.debug(
            f"get_bars: capping to_ts to now() for {symbol} {timeframe} "
            f"(from_ts={from_ts}, no to_ts or limit)"
        )

    # Widen the lower bound so the leading resampled bucket contains a
    # complete set of 1m contributing bars (otherwise open = first 1m
    # in window, not first 1m of the bucket). Use the WIDENING_HOURS
    # table for calendar-proportional widening (e.g. 1wk → 168h = 1 week)
    # instead of the minute-based multiplier which is only for fetch_limit.
    # On the 1m fast path the original ``from_ts`` is used directly.
    widen_hours = _WIDENING_HOURS.get(timeframe, 0) if is_resampled else 0
    effective_from_ts = (
        from_ts - timedelta(hours=widen_hours)
        if (is_resampled and from_ts is not None)
        else from_ts
    )

    rows, elapsed_ms = _fetch_1m_bars(
        db,
        symbol=symbol,
        from_ts=effective_from_ts,
        to_ts=effective_to_ts,
        limit=fetch_limit,
    )

    _log_slow_query(
        db,
        "get_bars",
        symbol=symbol.upper(),
        timeframe=timeframe,
        limit=limit,
        from_ts=from_ts,
        to_ts=to_ts,
        fetch_limit=fetch_limit,
        elapsed_ms=elapsed_ms,
    )

    if not is_resampled:
        return [_model_to_bar(row) for row in rows]

    # Higher TF path: resample 1m rows into the requested timeframe.
    bars_1m = [_model_to_bar(row) for row in rows]
    try:
        resampled = resample_ohlcv(bars_1m, timeframe)
    except ResampleError as e:
        logger.warning(f"get_bars: resample failed for {symbol}/{timeframe}: {e}")
        return []

    # Apply the from_ts filter on the resampled output (the leading bar
    # may have been included by the multiplier widening above).
    if from_ts is not None:
        resampled = [b for b in resampled if b.timestamp >= from_ts]

    # Phase 3.1.8: if we have fewer resampled bars than requested and
    # a fallback provider is supplied, try fetching the target timeframe
    # directly from the provider. This handles the cold-cache / recent-backfill
    # case where the DB has no 1m rows for the requested window.
    if limit is not None and len(resampled) < limit and fallback_provider is not None:
        try:
            provider_bars = fallback_provider(symbol.upper(), timeframe)
            if provider_bars:
                # Filter provider bars to the requested window.
                if from_ts is not None:
                    provider_bars = [b for b in provider_bars if b.timestamp >= from_ts]
                if to_ts is not None:
                    provider_bars = [b for b in provider_bars if b.timestamp <= to_ts]
                if provider_bars:
                    # Phase 3.1.8: mark these as source="raw" since they
                    # came from the provider (not resampled from 1m).
                    for b in provider_bars:
                        b.source = "raw"
                    logger.debug(
                        f"get_bars: hybrid fallback returned {len(provider_bars)} "
                        f"{timeframe} bars for {symbol} (DB had {len(resampled)})"
                    )
                    return provider_bars[-limit:] if limit else provider_bars
        except Exception as e:
            logger.warning(
                f"get_bars: hybrid fallback failed for {symbol}/{timeframe}: {e}"
            )
            # Fall through: return whatever we got from resampling.

    if limit is not None:
        return resampled[-limit:]
    return resampled


def _fetch_1m_bars(
    db: Session,
    symbol: str,
    from_ts: datetime | None,
    to_ts: datetime | None,
    limit: int | None,
) -> tuple[list[BarModel], float]:
    """Fetch 1m rows for ``symbol`` with the given time window and limit.

    Used by both the 1m fast path and the resample path: the only
    difference between the two is the limit and the widened ``from_ts``
    passed in, which is handled by the caller. Returns the rows and the
    elapsed wall-clock time in milliseconds.
    """
    import time

    query = (
        db.query(BarModel)
        .filter(
            and_(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == "1m",
            )
        )
        .order_by(BarModel.timestamp.asc())
    )
    if from_ts is not None:
        query = query.filter(BarModel.timestamp >= from_ts)
    if to_ts is not None:
        query = query.filter(BarModel.timestamp <= to_ts)
    if limit:
        query = query.limit(limit)

    t0 = time.perf_counter()
    rows: list[BarModel] = query.all()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return rows, elapsed_ms


def _model_to_bar(row: BarModel) -> Bar:
    """Convert a BarModel row back to a Pydantic Bar."""
    return Bar(
        symbol=row.symbol,
        timeframe=row.timeframe,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        timestamp=row.timestamp,
        provider=row.provider,
        data_status=DataStatus(row.data_status),
        source=row.source,
    )


# Cache of (table, columns) -> bool. The SQLite schema only changes via
# Alembic migrations which restart the server, so the result is constant
# for the lifetime of the process. We compute it on first call and reuse
# the cached value on every subsequent upsert — previously this query
# ran on every ``upsert_bars`` call (every 1m bar write from ingestion).
_UNIQUE_CONSTRAINT_CACHE: dict[tuple[str, tuple[str, ...]], bool] = {}


def _has_unique_constraint(db: Session, table: str, columns: tuple[str, ...]) -> bool:
    """Return True when the named table has a unique constraint on the given columns.

    The result is cached for the lifetime of the process — see
    ``_UNIQUE_CONSTRAINT_CACHE`` above. Pass a ``db`` only to keep the
    public API stable; the value is unused for the cache lookup itself.
    """
    key = (table, columns)
    cached = _UNIQUE_CONSTRAINT_CACHE.get(key)
    if cached is not None:
        return cached

    dialect = db.bind.dialect.name if db.bind else "sqlite"
    if dialect != "sqlite":
        # For non-SQLite dialects, assume unique constraint is defined in model.
        _UNIQUE_CONSTRAINT_CACHE[key] = True
        return True

    # sqlite_master.sql stores the column list with each name in its
    # own single-quoted token, separated by ", ". We can't escape that
    # by string-formatting the names into the LIKE pattern — single
    # quotes inside a single-quoted SQL string are escape-by-doubling,
    # not by backslash. Use parameter binding for the LIKE fragment and
    # manually quote each column.
    col_list = ", ".join(f"'{c}'" for c in columns)
    like_fragment = f"%{col_list}%"
    result = db.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name=:table "
            "AND sql LIKE '%UNIQUE%' AND sql LIKE :like"
        ),
        {"table": table, "like": like_fragment},
    )
    has_unique = result.fetchone() is not None
    _UNIQUE_CONSTRAINT_CACHE[key] = has_unique
    return has_unique


# Phase 20 perf: any bar-related query that exceeds this threshold
# gets its query + EXPLAIN plan logged so we can spot regressions
# during development. 100ms is a generous threshold for a local
# SQLite DB with the indexes in place — a hit here is almost always
# a missing index or a bad query shape.
_SLOW_QUERY_THRESHOLD_MS = 100.0


def _log_slow_query(
    db: Session,
    op: str,
    elapsed_ms: float,
    **params,
) -> None:
    """Log a bar query that exceeded the slow-query threshold.

    Only triggers on queries ≥ ``_SLOW_QUERY_THRESHOLD_MS``. On SQLite
    we run ``EXPLAIN QUERY PLAN`` for the operation so the log entry
    contains the actual index/scan choices. On other dialects we just
    log the elapsed time and parameters — those have their own
    query-plan tooling (e.g. ``pg_stat_statements``).
    """
    if elapsed_ms < _SLOW_QUERY_THRESHOLD_MS:
        return

    dialect = db.bind.dialect.name if db.bind else "sqlite"
    param_str = ", ".join(f"{k}={v!r}" for k, v in params.items())
    logger.warning(
        f"slow_query: {op} took {elapsed_ms:.1f}ms ({param_str})"
    )
    if dialect != "sqlite":
        return

    # Pull the most recent statement from the connection's raw SQLite
    # handle and run EXPLAIN QUERY PLAN against it. This requires that
    # the caller has already executed the query (which it has — we
    # only know the elapsed time after the call returns).
    try:
        conn = db.connection().connection.dbapi_connection
        # ``last_query_rowset`` isn't a documented public API but is
        # used by SQLAlchemy's own dialect debug tooling; fall back to
        # nothing if it's unavailable.
        sql = getattr(conn, "_last_query_rowset", None) or getattr(
            conn, "last_query", None
        )
        if not sql:
            return
        plan_rows = db.execute(text(f"EXPLAIN QUERY PLAN {sql}")).fetchall()
        plan_lines = [", ".join(str(c) for c in row) for row in plan_rows]
        logger.warning(f"slow_query: plan for {op} -> {' | '.join(plan_lines)}")
    except Exception as e:
        # EXPLAIN is best-effort: if the driver version exposes the
        # internals differently, we still have the timing + parameter
        # log above.
        logger.debug(f"slow_query: EXPLAIN unavailable for {op}: {e}")
