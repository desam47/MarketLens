"""
Bar repository for persisting and retrieving historical OHLCV bars.
"""
import logging

from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from backend.models.market_data import Bar, DataStatus
from backend.models.market_data_sql import BarModel

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

    return written


def get_bars(
    db: Session,
    symbol: str,
    timeframe: str,
    limit: int | None = None,
) -> list[Bar]:
    """Return bars for (symbol, timeframe), ordered oldest → newest."""
    query = (
        db.query(BarModel)
        .filter(
            and_(
                BarModel.symbol == symbol.upper(),
                BarModel.timeframe == timeframe,
            )
        )
        .order_by(BarModel.timestamp.asc())
    )
    if limit:
        query = query.limit(limit)

    rows: list[BarModel] = query.all()
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
