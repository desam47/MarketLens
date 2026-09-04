"""
Repository for quotes and market status data.

Provides bulk-delete helpers called by the watchlist router when a symbol
is removed from all watchlists — ensuring no orphaned per-symbol data leaks
into the DB.
"""
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models.market_data_sql import QuoteModel, MarketStatusModel


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
