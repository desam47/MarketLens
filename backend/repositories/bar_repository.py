"""
Bar repository for persisting and retrieving historical OHLCV bars.
"""
import collections.abc
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from backend.models.market_data import Bar, DataStatus
from backend.models.market_data_sql import BarModel
from backend.observability import record_bar, record_bars

logger = logging.getLogger(__name__)

# Phase 3.1: map each target timeframe to its 1m multiplier.
# Used to compute how many 1m bars to fetch when producing N output bars.
# Each value is a conservative upper bound on the 1m data per output bar,
# used for ``fetch_limit = limit * multiplier``.
#
# Phase 3.7: all 10 timeframes are now stored rows (1m/2m/3m/5m/15m/30m/1h/4h/1d/1wk).
# The multiplier and widening tables cover every timeframe so get_bars()
# can serve them all directly from the DB.
_TF_MULTIPLIER: dict[str, int] = {
    "2m": 2,
    "3m": 3,
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
    "2m": 0,
    "3m": 0,
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
    desc: bool = False,
) -> list[Bar]:
    """Return bars for (symbol, timeframe), ordered oldest → newest by default.

    Phase 3.7: the storage layer holds ALL 10 timeframes as direct rows
    (1m/2m/3m/5m/15m/30m/1h/4h/1d/1wk). The resample-at-write loops in
    ingestion_service.py persist non-base timeframes, so ``get_bars``
    becomes a straight DB query — no read-time resampling required.

    ``from_ts`` / ``to_ts`` are optional time-range filters and translate
    into a ``WHERE timestamp >= from_ts AND timestamp <= to_ts`` clause.
    ``limit`` (if set) limits the number of rows returned.
    ``desc=True`` returns the most recent ``limit`` bars ordered newest → oldest.

    The ``(symbol, timeframe, timestamp)`` unique index on the table
    provides deduplication.

    Phase 20 perf: slow queries (≥ 100ms) are logged with their EXPLAIN
    plan so expensive query patterns surface during development.
    """
    import time

    rows, elapsed_ms = _fetch_bars(
        db,
        symbol=symbol,
        timeframe=timeframe,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        desc=desc,
    )

    _log_slow_query(
        db,
        "get_bars",
        symbol=symbol.upper(),
        timeframe=timeframe,
        limit=limit,
        from_ts=from_ts,
        to_ts=to_ts,
        elapsed_ms=elapsed_ms,
    )

    return [_model_to_bar(row) for row in rows]


def _fetch_bars(
    db: Session,
    symbol: str,
    timeframe: str,
    from_ts: datetime | None,
    to_ts: datetime | None,
    limit: int | None,
    desc: bool = False,
) -> tuple[list[BarModel], float]:
    """Fetch bars for ``symbol`` at ``timeframe`` directly from the DB.

    Phase 3.7: all 10 timeframes are now stored rows, so this is a
    straight indexed query. Returns rows and elapsed wall-clock time in
    milliseconds. Ordered oldest → newest by default; ``desc=True`` orders
    newest → oldest.
    """
    import time

    query = (
        db.query(BarModel)
        .filter(
            and_(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == timeframe,
            )
        )
        .order_by(BarModel.timestamp.desc() if desc else BarModel.timestamp.asc())
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
    col_list = ", ".join(columns)
    like_fragment = f"%({col_list})%"
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


# Phase 3.3.9: Bar retention helpers.
#
# These functions delete bars in chunks so a long retention window doesn't
# generate one giant transaction. The caller is expected to be running
# inside a background loop or script, so yielding control back to the event
# loop is the caller's responsibility — we keep these synchronous so they
# can be used both from sync (script) and async (loop) code.


def prune_bars_older_than(
    db: Session,
    cutoff: datetime,
    chunk_size: int = 1000,
) -> int:
    """Delete bars older than ``cutoff`` in chunks of ``chunk_size`` rows.

    Returns the total number of rows deleted across all chunks. The function
    commits after each chunk so progress is durable if the process is
    killed mid-run.

    Phase 3.3.9: runs on every ingestion tick. We rely on the
    ``(symbol, timeframe, timestamp)`` unique index for cheap row lookup;
    for SQLite the index keeps the DELETE plan index-driven.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")

    total_deleted = 0
    # We loop until a chunk deletes 0 rows. We use a subquery with LIMIT
    # so SQLite picks an index scan instead of a full table scan.
    while True:
        # Pick the rows to delete in this chunk: any bar whose timestamp
        # is older than cutoff. We order by id so the LIMIT is stable.
        subq = (
            select(BarModel.id)
            .where(BarModel.timestamp < cutoff)
            .order_by(BarModel.id.asc())
            .limit(chunk_size)
        )
        deleted = db.query(BarModel).filter(BarModel.id.in_(subq)).delete(
            synchronize_session=False
        )
        db.commit()
        if deleted == 0:
            break
        total_deleted += deleted
        logger.debug(
            f"prune_bars_older_than: deleted {deleted} rows "
            f"(total {total_deleted}) older than {cutoff.isoformat()}"
        )
        # If a chunk didn't fill up, we've drained everything.
        if deleted < chunk_size:
            break

    if total_deleted:
        logger.info(
            f"prune_bars_older_than: removed {total_deleted} bars older than "
            f"{cutoff.isoformat()}"
        )
    return total_deleted


def bulk_delete_bars(
    db: Session,
    symbols: collections.abc.Iterable[str],
    cutoff: datetime | None = None,
    chunk_size: int = 1000,
) -> int:
    """Delete all bars for ``symbols`` (optionally also older than ``cutoff``).

    Used by the watchlist purge path when a symbol is removed from every
    watchlist. ``symbols`` is normalised to upper-case. Returns the total
    rows deleted.

    If ``cutoff`` is None, ALL bars for the symbols are deleted.
    """
    sym_list = [s.upper() for s in symbols if s]
    if not sym_list:
        return 0
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")

    total_deleted = 0
    while True:
        subq = (
            select(BarModel.id)
            .where(BarModel.symbol.in_(sym_list))
            .order_by(BarModel.id.asc())
        )
        if cutoff is not None:
            subq = subq.where(BarModel.timestamp < cutoff)
        subq = subq.limit(chunk_size)

        deleted = db.query(BarModel).filter(BarModel.id.in_(subq)).delete(
            synchronize_session=False
        )
        db.commit()
        if deleted == 0:
            break
        total_deleted += deleted
        if deleted < chunk_size:
            break

    if total_deleted:
        logger.info(
            f"bulk_delete_bars: removed {total_deleted} bars for {len(sym_list)} symbols"
        )
    return total_deleted


# Phase 3.3.10: per-symbol purge helpers.
#
# These are thin wrappers around bulk_delete_bars but they exist as
# separate, intent-revealing functions so the watchlist code reads
# naturally: "if the symbol is no longer watched, delete_bars_for_symbol".


def delete_bars_for_symbol(symbol: str) -> int:
    """Delete every bar row for ``symbol`` from the DB.

    Returns the number of rows deleted. Use this when a symbol is removed
    from EVERY watchlist and we no longer want any history for it.
    """
    from backend.database.db import SessionLocal  # avoid circular import

    if not symbol:
        return 0
    db = SessionLocal()
    try:
        return bulk_delete_bars(db, [symbol.upper()])
    finally:
        db.close()


def delete_bars_for_symbols(symbols: collections.abc.Iterable[str]) -> int:
    """Delete every bar row for each symbol in ``symbols``.

    Returns the total number of rows deleted across all symbols.
    """
    from backend.database.db import SessionLocal

    sym_list = [s for s in symbols if s]
    if not sym_list:
        return 0
    db = SessionLocal()
    try:
        return bulk_delete_bars(db, sym_list)
    finally:
        db.close()
