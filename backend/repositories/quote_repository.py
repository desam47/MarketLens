"""
Repository for quotes and market status data.

Provides bulk-delete helpers called by the watchlist router when a symbol
is removed from all watchlists — ensuring no orphaned per-symbol data leaks
into the DB — plus the quote read/write helpers used by the ingestion
service. Quotes previously used raw ``QuoteModel`` ORM access directly in
``ingestion_service.py`` while bars went through ``bar_repository`` —
moved here 2026-09-17 for consistency (no behavior change).
"""
import logging

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models.market_data import DataStatus, Quote
from backend.models.market_data_sql import MarketStatusModel, QuoteModel

logger = logging.getLogger(__name__)


def add_quote(db: Session, quote: Quote) -> QuoteModel:
    """Build a ``QuoteModel`` row from a ``Quote`` and add it to ``db``.

    Does not commit — the caller controls the transaction (ingestion
    commits once after adding every quote in a batch).
    """
    db_quote = QuoteModel(
        symbol=quote.symbol,
        price=quote.price,
        bid=quote.bid,
        ask=quote.ask,
        volume=quote.volume,
        timestamp=quote.timestamp,
        provider=quote.provider,
        data_status=quote.data_status.value,
    )
    db.add(db_quote)
    return db_quote


def _model_to_quote(row: QuoteModel) -> Quote:
    """Convert a ``QuoteModel`` row back to a Pydantic ``Quote``."""
    try:
        status = DataStatus(row.data_status)
    except ValueError:
        # Defensive: handle legacy data written with non-enum values (e.g. 'ok').
        logger.warning(
            "Unknown data_status '%s' for %s quote; treating as LIVE",
            row.data_status, row.symbol,
        )
        status = DataStatus.LIVE
    return Quote(
        symbol=row.symbol,
        price=row.price,
        timestamp=row.timestamp,
        provider=row.provider,
        data_status=status,
        bid=row.bid,
        ask=row.ask,
        volume=row.volume,
    )


def get_latest_quote(symbol: str) -> Quote | None:
    """Get the most recent quote for ``symbol`` from the DB, or ``None``."""
    db = SessionLocal()
    try:
        row = (
            db.query(QuoteModel)
            .filter(QuoteModel.symbol == symbol)
            .order_by(QuoteModel.timestamp.desc())
            .first()
        )
        return _model_to_quote(row) if row is not None else None
    finally:
        db.close()


def get_quote_history(symbol: str, limit: int = 100) -> list[Quote]:
    """Get up to ``limit`` most recent quotes for ``symbol``, newest first."""
    db = SessionLocal()
    try:
        rows = (
            db.query(QuoteModel)
            .filter(QuoteModel.symbol == symbol)
            .order_by(QuoteModel.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [_model_to_quote(row) for row in rows]
    finally:
        db.close()


def delete_quotes_for_symbol(symbol: str) -> int:
    """Delete every quote row for ``symbol`` from the DB.

    Returns the number of rows deleted. Use this when a symbol is removed
    from EVERY watchlist.
    """
    if not symbol:
        return 0
    db = SessionLocal()
    try:
        count = db.query(QuoteModel).filter(
            QuoteModel.symbol == symbol.upper()
        ).delete()
        db.commit()
        return count
    finally:
        db.close()


def delete_market_status_for_symbol(symbol: str) -> int:
    """Delete every market status row for ``symbol`` from the DB.

    Returns the number of rows deleted. Use this when a symbol is removed
    from EVERY watchlist.
    """
    if not symbol:
        return 0
    db = SessionLocal()
    try:
        count = db.query(MarketStatusModel).filter(
            MarketStatusModel.symbol == symbol.upper()
        ).delete()
        db.commit()
        return count
    finally:
        db.close()
