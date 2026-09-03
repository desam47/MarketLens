"""
Database configuration and session management
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config.settings import settings, _PROJECT_ROOT


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Using SQLAlchemy 2.0's typed DeclarativeBase instead of the deprecated
    `declarative_base()` factory function. All models in backend/models/
    inherit from this class.
    """

# Safety check: SQLite DB must live under the project root.
# Fail immediately (not silently) if someone starts the server from the
# backend/ directory with a relative DATABASE_URL — this prevents the
# "empty DB shadowing populated DB" class of bug.
_db_url = settings.database.url
if _db_url.startswith("sqlite:///"):
    _db_path = Path(_db_url.replace("sqlite:///", ""))
    if not _db_path.is_absolute():
        raise RuntimeError(
            f"SQLite DB path is not absolute: {_db_url!r}. "
            f"Set DATABASE_URL to an absolute path in .env to prevent this."
        )
    _db_canonical = _db_path.resolve()
    _root_canonical = _PROJECT_ROOT.resolve()
    if not str(_db_canonical).startswith(str(_root_canonical)):
        raise RuntimeError(
            f"SQLite DB must be inside project root {_root_canonical}. "
            f"Currently configured as: {_db_canonical}. "
            f"Set DATABASE_URL to an absolute path inside the project root."
        )


# ---------------------------------------------------------------------------
# Phase 3.3.1 — SQLite WAL mode + performance PRAGMAs
# ---------------------------------------------------------------------------
# WAL (Write-Ahead Logging) gives SQLite one writer + many concurrent
# readers without the "database is locked" errors that bite us in journal
# mode once the bars table grows. The trade-off is one extra .db-wal
# file alongside marketlens.db, which Litestream (3.3.2) streams
# continuously to S3/local disk.
#
# PRAGMA application rules:
#   * journal_mode is a *persistent* property stored in the DB file
#     itself — setting it once is enough.
#   * synchronous, cache_size, temp_store, mmap_size, wal_autocheckpoint
#     are *per-connection* properties. SQLAlchemy's connection pool
#     reuses connections, so we have to re-apply them every time a
#     connection is opened from the pool. The "connect" event below
#     fires for both new and pooled connections.
# ---------------------------------------------------------------------------
def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:")


# Applied once on first connect. journal_mode returns the new mode
# (e.g. "wal") so the test suite can verify WAL is active.
_SQLITE_PRAGMAS_ON_CONNECT = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    # Checkpoint every 1000 WAL pages (~4 MB). Lower = more frequent
    # fsyncs, snappier crash recovery, slower writes. 1000 is the
    # SQLite default and the sweet spot for our workload.
    "PRAGMA wal_autocheckpoint=1000",
    # Negative = KB. -64000 = 64 MB page cache.
    "PRAGMA cache_size=-64000",
    # Temp tables / indexes in RAM (vs. the default file-backed).
    "PRAGMA temp_store=MEMORY",
    # 256 MB mmap I/O for read-heavy workloads. Harmless on Linux/macOS;
    # ignored on platforms where mmap is not supported.
    "PRAGMA mmap_size=268435456",
]


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Attach a per-connection PRAGMA applier to a SQLite engine.

    Idempotent — safe to call multiple times. Skips non-SQLite engines
    so the function is a no-op for tests that point at a future
    Postgres URL via ``MARKETLENS_DB_OVERRIDE``.
    """
    if not _is_sqlite_url(str(engine.url)):
        return

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        # ``dbapi_connection`` is the raw pysqlite connection. We have
        # to use the DBAPI cursor here because PRAGMA statements are
        # SQLite-specific and don't go through SQLAlchemy's compiler.
        cursor = dbapi_connection.cursor()
        try:
            for pragma in _SQLITE_PRAGMAS_ON_CONNECT:
                cursor.execute(pragma)
        finally:
            cursor.close()


# Create engine
engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False} if _is_sqlite_url(_db_url) else {},
    echo=settings.database.echo
)

# Apply WAL + perf PRAGMAs to every connection from the pool.
_register_sqlite_pragmas(engine)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Phase 3.3.5 — VACUUM INTO + ANALYZE helpers
# ---------------------------------------------------------------------------
# VACUUM rebuilds the DB file to reclaim space from deleted rows. The
# classic `VACUUM` command rewrites the live DB in place, which locks
# the entire file for minutes on a 1GB DB. `VACUUM INTO` writes a
# fresh copy to a path of our choosing, leaving the live DB untouched
# and readable throughout. We can then `fs.rename` the snapshot over
# the live DB at the next maintenance window.
#
# ANALYZE refreshes the SQLite query-planner statistics so the indexes
# we just audited (3.3.4) keep getting picked correctly after the
# dataset shifts (e.g. after 3.3.9's prune removes a big batch of rows).
# ---------------------------------------------------------------------------
def vacuum_into(snapshot_path: str | Path) -> Path:
    """Run ``VACUUM INTO <snapshot_path>`` on the live DB.

    Non-blocking: produces a clean snapshot of the DB without rewriting
    the live file. The snapshot is byte-identical to what a fresh
    ``sqlite3 .dump`` would produce minus the schema headers.

    ``snapshot_path`` must be a path on the same filesystem as the live
    DB for SQLite's COW copy to be cheap. Returns the resolved path
    so the caller can ``stat()`` it.

    Raises:
        RuntimeError: if the engine is not SQLite, or if VACUUM fails.
    """
    if not _is_sqlite_url(str(engine.url)):
        raise RuntimeError("vacuum_into only works on SQLite engines")

    snapshot = Path(snapshot_path).resolve()
    snapshot.parent.mkdir(parents=True, exist_ok=True)

    # VACUUM cannot run inside a transaction; we use a dedicated
    # connection with isolation_level=None to disable SQLAlchemy's
    # implicit transaction wrapping.
    from sqlalchemy import create_engine as _ce
    raw_engine = _ce(str(engine.url), isolation_level=None)
    try:
        with raw_engine.connect() as conn:
            # PRAGMA parameters aren't allowed on VACUUM INTO; we
            # pass the path as a quoted SQL literal. Use the
            # SQLAlchemy text() builder for safe escaping.
            from sqlalchemy import text
            conn.execute(text(f"VACUUM INTO '{snapshot.as_posix()}'"))
    finally:
        raw_engine.dispose()

    return snapshot


def analyze_db() -> None:
    """Run ``ANALYZE`` on the live DB to refresh query-planner stats.

    Cheap (< 1s on a 1M-row DB) and safe to call after any bulk
    operation: prune, backfill, bulk delete. The query planner uses
    the stats to pick the best index for each query; stale stats
    after a big prune can cause the planner to choose a full scan
    over an index.
    """
    if not _is_sqlite_url(str(engine.url)):
        return

    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ANALYZE"))
        conn.commit()


# ---------------------------------------------------------------------------
# Guard: refuse metadata.drop_all() against the real project database
# ---------------------------------------------------------------------------
# On 2026-09-02 a test called ``Base.metadata.drop_all(bind=engine)`` in its
# setUp. ``engine`` is the production engine, so running the suite dropped
# every table in the developer's live database: the entire bars table and the
# user's watchlist. The watchlist was not recoverable from free pages.
#
# drop_all against the project DB is never what a test wants. Tests must bind
# to a temporary database instead — see backend/tests/test_bar_retention.py
# for the correct pattern. Set MARKETLENS_ALLOW_DROP_ALL=1 to override when a
# reset is genuinely intended (migrations, deliberate teardown scripts).

_PROTECTED_DB_PATH: str | None = None
if _is_sqlite_url(_db_url):
    try:
        _PROTECTED_DB_PATH = str(
            Path(_db_url.replace("sqlite:///", "")).expanduser().resolve()
        )
    except OSError:  # pragma: no cover - unresolvable path
        _PROTECTED_DB_PATH = None


def _targets_protected_db(bind) -> bool:
    """True when ``bind`` resolves to the project's real SQLite database."""
    if _PROTECTED_DB_PATH is None or bind is None:
        return False
    url = getattr(bind, "url", None)
    db_file = getattr(url, "database", None) if url is not None else None
    if not db_file:
        return False
    try:
        return Path(db_file).expanduser().resolve() == Path(_PROTECTED_DB_PATH)
    except OSError:  # pragma: no cover - unresolvable path
        return False


_unguarded_drop_all = Base.metadata.drop_all


def _guarded_drop_all(bind=None, tables=None, checkfirst=True):
    """``MetaData.drop_all`` that refuses to wipe the project database."""
    target = bind if bind is not None else engine
    if (
        _targets_protected_db(target)
        and os.environ.get("MARKETLENS_ALLOW_DROP_ALL") != "1"
    ):
        raise RuntimeError(
            "Refusing drop_all() against the project database at "
            f"{_PROTECTED_DB_PATH}.\n"
            "This drops every table, destroying all bars, quotes and "
            "watchlists.\n"
            "Tests must bind to a temporary database (see "
            "backend/tests/test_bar_retention.py).\n"
            "Set MARKETLENS_ALLOW_DROP_ALL=1 only if a full reset is intended."
        )
    return _unguarded_drop_all(bind=bind, tables=tables, checkfirst=checkfirst)


Base.metadata.drop_all = _guarded_drop_all
