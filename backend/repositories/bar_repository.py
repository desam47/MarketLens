"""
Bar repository for persisting and retrieving historical OHLCV bars.
"""
import collections.abc
import logging
from datetime import datetime, timedelta, timezone
from datetime import time as _time

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from backend.engines.market_calendar import EASTERN, classify_bar_session, us_market_calendar
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
        session=bar.session,
    )


def upsert_bars(db: Session, bars: list[Bar]) -> int:
    """Bulk-insert or update a list of bars.

    Uses ``insert(...).on_conflict_do_update`` when the uniqueness
    constraint is in place; falls back to per-row merge when it isn't.
    Returns the number of rows written.
    """
    if not bars:
        return 0

    # Recompute session from each bar's own timestamp — do not trust
    # whatever the provider (or resampler) set. Found live 2026-09-09:
    # WebullProvider correctly self-tags session on bars it returns, but
    # Alpaca (used as a 1m gap-fill provider) returns genuine premarket
    # ticks on its own IEX feed without being asked and without tagging
    # them, so its Bar objects fell back to the model's 'regular' default
    # — a mistagged bar then slipped past ingestion_service.
    # _resample_and_upsert's (former) session filter into a resampled
    # bucket. upsert_bars is the single write chokepoint for every bar
    # regardless of provider or caller, so this is the one place a
    # timestamp-derived fact like this can be enforced instead of relying
    # on N independent sources to each self-report it correctly.
    #
    # Covers 1m AND the sub-hour resampled timeframes (2m/3m/5m/15m/30m —
    # by request 2026-09-09, these now carry premarket/after_hours data
    # too, same as 1m). resample_ohlcv() doesn't set session on the bars
    # it builds (it only aggregates OHLCV), so without this every
    # resampled bar would default to 'regular' regardless of its real
    # session. Safe to reclassify unconditionally: sub-hour bucket
    # boundaries all divide evenly into the 09:30/16:00/04:00/20:00
    # session edges, so a bucket never straddles two sessions.
    # 1h/4h/1d/1wk are excluded — never fetched/resampled with extended
    # hours, so they keep whatever they arrive with (always 'regular').
    _SESSION_TAGGED_TFS = ("1m", "2m", "3m", "5m", "15m", "30m")
    for b in bars:
        if b.timeframe in _SESSION_TAGGED_TFS:
            b.session = classify_bar_session(b.timestamp)

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

        rows = [
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
                "session": b.session,
            }
            for b in bars
        ]
        # ONE compiled statement executed with a list of parameter rows
        # (executemany), in the caller's transaction and committed once, so the
        # batch stays all-or-nothing. The previous shapes were far worse:
        #   * a single multi-VALUES statement for the whole batch binds rows x 12
        #     variables — SQLite rejects it past SQLITE_MAX_VARIABLE_NUMBER
        #     ("too many SQL variables" lost whole 1m-ingest cycles), and
        #   * even below that limit, SQLAlchemy compiling a statement with
        #     thousands of bound parameters is pure-Python CPU that holds the GIL:
        #     53 ms for 721 rows, measured, vs 3.9 ms this way (13.6x) — that GIL
        #     hold stalled the API's event loop on every ingest cycle.
        # Executemany binds 12 variables per row, so no batch size can hit the
        # variable limit either.
        stmt = sqlite_insert(BarModel)
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
                "session": stmt.excluded.session,
            },
        )
        written = db.connection().execute(stmt, rows).rowcount
        db.commit()
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
                existing.session = bar.session
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
        session=getattr(row, "session", None) or "regular",
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
    timeframe: str | None = None,
) -> int:
    """Delete bars older than ``cutoff`` in chunks of ``chunk_size`` rows.

    ``timeframe``: when given, only that timeframe's bars are considered
    (used by ``prune_bars_by_retention`` — each timeframe has its own
    cutoff). ``None`` (default) prunes across every timeframe with the
    one cutoff, same as before per-timeframe retention existed.

    Returns the total number of rows deleted across all chunks. The function
    commits after each chunk so progress is durable if the process is
    killed mid-run.

    Phase 3.3.9 originally ran this on every ingestion tick (~60s);
    ``prune_bars_by_retention`` is now called from a dedicated hourly loop
    (``MarketDataIngestionService._retention_prune_loop``) instead, since
    retention windows are configured in days and don't need re-checking
    every tick. We rely on the ``(symbol, timeframe, timestamp)`` unique
    index for cheap row lookup; for SQLite the index keeps the DELETE plan
    index-driven.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")

    total_deleted = 0
    # We loop until a chunk deletes 0 rows. We use a subquery with LIMIT
    # so SQLite picks an index scan instead of a full table scan.
    while True:
        # Pick the rows to delete in this chunk: any bar whose timestamp
        # is older than cutoff. We order by id so the LIMIT is stable.
        conditions = [BarModel.timestamp < cutoff]
        if timeframe is not None:
            conditions.append(BarModel.timeframe == timeframe)
        subq = (
            select(BarModel.id)
            .where(and_(*conditions))
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
            + (f" (timeframe={timeframe})" if timeframe else "")
        )
        # If a chunk didn't fill up, we've drained everything.
        if deleted < chunk_size:
            break

    if total_deleted:
        logger.info(
            f"prune_bars_older_than: removed {total_deleted} bars older than "
            f"{cutoff.isoformat()}" + (f" (timeframe={timeframe})" if timeframe else "")
        )
    return total_deleted


def prune_bars_by_retention(db: Session, chunk_size: int = 1000) -> dict[str, int]:
    """Prune every stored timeframe to its own configured retention window.

    Reads ``settings.retention`` (per-timeframe days — see
    ``RetentionSettings`` for defaults and rationale) and runs
    ``prune_bars_older_than`` once per timeframe with that timeframe's own
    cutoff. Returns ``{timeframe: rows_deleted}`` for timeframes that
    actually had something pruned (timeframes with 0 deletions are
    omitted, so callers can log/skip cheaply on the common no-op case).
    """
    from backend.config.settings import settings as _settings

    now = datetime.now()
    deleted_by_tf: dict[str, int] = {}
    for tf in _TF_MULTIPLIER.keys() | {"1m"}:
        days = _settings.retention.days_for(tf)
        cutoff = now - timedelta(days=days)
        deleted = prune_bars_older_than(db, cutoff, chunk_size=chunk_size, timeframe=tf)
        if deleted:
            deleted_by_tf[tf] = deleted
    return deleted_by_tf


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


# ---------------------------------------------------------------------------
# Data-quality audit: duplicate calendar-day bars.
# ---------------------------------------------------------------------------
#
# 2026-09-09 incident: webull stamps 1d bars at 00:00, yahoo_finance at
# 09:30. The DB's uniqueness is on the *exact* timestamp, so those two
# conventions never collided — any trading day a yahoo_finance fallback
# touched got a second, independent row instead of updating the existing
# one. By the time this was noticed, 63% of all stored 1d rows were
# duplicated this way, with close prices differing by up to 2.4% between
# the two rows for the same day. The write-path bug is fixed (both
# providers now normalize onto the same timestamp — see _normalize_1d_bar
# in ingestion_service.py), but nothing was watching for this class of
# bug, which is why it went unnoticed for as long as it did. This audit
# exists so a regression (a new provider added without normalization, a
# normalizer that stops firing, etc.) surfaces immediately instead of
# silently accumulating again.
#
# Only meaningful for CALENDAR-based timeframes (1d, 1wk), where distinct
# providers can legitimately stamp the "same" period at different times
# of day. Intraday timeframes (1m..4h) don't have this failure mode — an
# exact-timestamp collision on a fixed-width bucket IS the correct
# de-dup key for those.
_CALENDAR_TIMEFRAMES: tuple[str, ...] = ("1d", "1wk")


def find_duplicate_calendar_bars(
    db: Session, timeframe: str, symbol: str | None = None
) -> list[dict]:
    """Find (symbol, calendar_date) groups with more than one stored row.

    ``timeframe`` must be one of ``_CALENDAR_TIMEFRAMES`` ("1d"/"1wk") —
    raises ValueError otherwise, since this check is meaningless (and
    would misfire) for intraday timeframes.

    Returns a list of dicts, one per duplicated day, each with the
    symbol, date, row count, and per-row (id, timestamp, provider, close)
    detail — enough to decide which row to keep without a follow-up query.
    Empty list means no duplicates found (the healthy state).
    """
    if timeframe not in _CALENDAR_TIMEFRAMES:
        raise ValueError(
            f"find_duplicate_calendar_bars only applies to {_CALENDAR_TIMEFRAMES}, "
            f"got {timeframe!r} — intraday timeframes use exact-timestamp "
            f"buckets, which are already a correct de-dup key."
        )

    query = db.query(BarModel).filter(BarModel.timeframe == timeframe)
    if symbol:
        query = query.filter(BarModel.symbol == symbol.upper())
    rows = query.order_by(BarModel.symbol, BarModel.timestamp).all()

    by_day: dict[tuple[str, object], list[BarModel]] = {}
    for r in rows:
        by_day.setdefault((r.symbol, r.timestamp.date()), []).append(r)

    out: list[dict] = []
    for (sym, date), group in by_day.items():
        if len(group) < 2:
            continue
        out.append({
            "symbol": sym,
            "date": date.isoformat(),
            "count": len(group),
            "rows": [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat(),
                    "provider": r.provider,
                    "close": r.close,
                }
                for r in group
            ],
        })
    return out


# ---------------------------------------------------------------------------
# Gap detection — companion to find_duplicate_calendar_bars above. That
# finds too MANY rows for a bucket; this finds too FEW (zero).
#
# Introduced alongside the RQ-based backfill pipeline
# (backend/market_data/services/backfill_service.py) so a newly-added
# symbol's 1m/1h/1d history is verified complete — and any holes patched
# from the provider fallback chain — before it's resampled into
# 2m/3m/5m/15m/30m/4h/1wk, instead of resampling whatever a single fetch
# window happened to return.
# ---------------------------------------------------------------------------

# Bucket width, in minutes, for the intraday sub-hour timeframes. 1h and 4h
# are handled separately below since their step is hours, not minutes.
_INTRADAY_BUCKET_MINUTES: dict[str, int] = {
    "1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
}

# Regular session bounds (ET). Only the regular session is enumerated as
# "expected" — pre/post-market coverage is provider-dependent and
# legitimately sparse, so treating it as expected would manufacture
# false-positive gaps.
_SESSION_OPEN = _time(9, 30)
_SESSION_CLOSE = _time(16, 0)

# Timeframes expected_bar_timestamps/find_gaps know how to enumerate.
# 1wk is deliberately excluded — a handful of weekly buckets over years is
# not where the "gaps" problem the backfill pipeline patches actually
# occurs; it fully derives from an already gap-verified 1d series.
_GAP_CHECK_TIMEFRAMES: tuple[str, ...] = (
    "1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d",
)


def expected_bar_timestamps(
    symbol: str, timeframe: str, start: datetime, end: datetime,
) -> list[datetime]:
    """Enumerate the canonical bucket-start timestamps a fully-populated
    ``timeframe`` series should have between ``start`` and ``end`` (both
    inclusive).

    Returned timestamps are naive NY-local, matching how bars are stored
    (see ``backend/utils/timezone.py``'s naive-NY convention). Bucketing
    mirrors the floor arithmetic used elsewhere for these timeframes —
    ``TimeframeEngine._get_candle_start_time`` for intraday/1h,
    ``MarketDataIngestionService._resample_1h_to_4h_and_upsert`` for 4h —
    duplicated here rather than imported so this stays a light,
    dependency-free repository-layer utility (the same pattern
    ``ingestion_service._RESAMPLE_WIDENING_HOURS`` already uses for
    mirroring, not importing, this module's own ``_WIDENING_HOURS``).

    Raises ``ValueError`` for any timeframe not in ``_GAP_CHECK_TIMEFRAMES``
    (``symbol`` is currently unused but kept in the signature — a future
    per-symbol trading-calendar override, e.g. a different listing venue,
    would need it without changing every call site).
    """
    if timeframe not in _GAP_CHECK_TIMEFRAMES:
        raise ValueError(
            f"expected_bar_timestamps only covers {_GAP_CHECK_TIMEFRAMES}, "
            f"got {timeframe!r}"
        )

    out: list[datetime] = []
    day = start.date()
    end_date = end.date()
    while day <= end_date:
        # Trading-day check needs a tz-aware probe — USMarketCalendar treats
        # naive input as UTC (the provider-layer convention elsewhere in the
        # app), which would misclassify an NY-local naive noon as a
        # different weekday/date near midnight. Build it explicitly ET-aware.
        probe = datetime.combine(day, _time(12, 0), tzinfo=EASTERN)
        if us_market_calendar.is_trading_day(probe):
            if timeframe == "1d":
                out.append(datetime.combine(day, _time(0, 0)))
            else:
                session_start = datetime.combine(day, _SESSION_OPEN)
                session_end = datetime.combine(day, _SESSION_CLOSE)
                if timeframe in _INTRADAY_BUCKET_MINUTES:
                    bucket_minutes = _INTRADAY_BUCKET_MINUTES[timeframe]
                    step = timedelta(minutes=bucket_minutes)
                    minute = session_start.minute - (session_start.minute % bucket_minutes)
                    t = session_start.replace(minute=minute, second=0, microsecond=0)
                elif timeframe == "1h":
                    step = timedelta(hours=1)
                    t = session_start.replace(minute=0, second=0, microsecond=0)
                else:  # "4h"
                    step = timedelta(hours=4)
                    hour_floor = (session_start.hour // 4) * 4
                    t = session_start.replace(hour=hour_floor, minute=0, second=0, microsecond=0)
                while t < session_end:
                    out.append(t)
                    t += step
        day += timedelta(days=1)

    # Trim to the actually-requested range — a day-granularity loop can
    # overshoot start/end by a few buckets at the edges.
    return [ts for ts in out if start <= ts <= end]


def find_gaps(
    db: Session, symbol: str, timeframe: str, start: datetime, end: datetime,
) -> list[datetime]:
    """Return the expected bucket-start timestamps missing from the DB for
    ``symbol``/``timeframe`` between ``start`` and ``end`` (inclusive).

    Companion to ``find_duplicate_calendar_bars`` — that finds too MANY
    rows for a bucket, this finds too FEW (zero). Raises for any timeframe
    ``expected_bar_timestamps`` doesn't cover.
    """
    expected = set(expected_bar_timestamps(symbol, timeframe, start, end))
    if not expected:
        return []
    rows = (
        db.query(BarModel.timestamp)
        .filter(
            BarModel.symbol == symbol.upper(),
            BarModel.timeframe == timeframe,
            BarModel.timestamp >= start,
            BarModel.timestamp <= end,
        )
        .all()
    )
    actual = {r[0] for r in rows}
    return sorted(expected - actual)
