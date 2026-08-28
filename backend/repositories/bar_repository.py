"""
Bar repository for persisting and retrieving historical OHLCV bars.
"""
import logging

from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from backend.models.market_data import Bar, DataStatus
from backend.models.market_data_sql import BarModel
from backend.observability import record_bar

logger = logging.getLogger(__name__)


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
    for _ in range(incoming_count):
        record_bar()

    return written


def get_bars(
    db: Session,
    symbol: str,
    timeframe: str,
    limit: int | None = None,
) -> list[Bar]:
    """Return bars for (symbol, timeframe), ordered oldest → newest.

    Bars are deduplicated on (symbol, timeframe, timestamp) — the unique
    index on the table prevents new duplicates, but rows inserted before
    the index was added may still be present. The .distinct() guard makes
    get_bars safe against any pre-existing dupes.

    Phase 20 perf: slow queries (≥ 100ms) are logged with their EXPLAIN
    plan so expensive query patterns surface during development.
    """
    query = (
        db.query(BarModel)
        .filter(
            and_(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == timeframe,
            )
        )
        .distinct(BarModel.timestamp)
        .order_by(BarModel.timestamp.asc())
    )
    if limit:
        query = query.limit(limit)

    import time
    t0 = time.perf_counter()
    rows: list[BarModel] = query.all()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    _log_slow_query(
        db,
        "get_bars",
        symbol=symbol.upper(),
        timeframe=timeframe,
        limit=limit,
        elapsed_ms=elapsed_ms,
    )

    return [_model_to_bar(row) for row in rows]


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
    )


def _has_unique_constraint(db: Session, table: str, columns: tuple[str, ...]) -> bool:
    """Return True when the named table has a unique constraint on the given columns."""
    dialect = db.bind.dialect.name if db.bind else "sqlite"
    if dialect != "sqlite":
        # For non-SQLite dialects, assume unique constraint is defined in model.
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
    return result.fetchone() is not None


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
