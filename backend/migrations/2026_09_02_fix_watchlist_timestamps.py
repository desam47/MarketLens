"""Fix naive-UTC timestamps in watchlist tables → naive NY (EDT/EST).

Watchlist.created_at, watchlist.updated_at, and watchlist_symbols.added_at
were stored with datetime.utcnow() — a naive datetime that was actually UTC.
JavaScript treats the resulting ISO string as browser local time, so the
frontend showed times 4–5 hours off.

Starting 2026-09-02 the project uses naive America/New_York datetimes.
This migration subtracts 4 hours (EDT offset) from all three columns.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fix_watchlist_timestamps")

DB_PATH = "sqlite:////Users/dips/projects/MarketLens/marketlens.db"


def shift_column(engine, table: str, column: str) -> int:
    """Subtract 4 hours from every value in <table>.<column> (UTC→NY EDT shift)."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                f"UPDATE {table} "
                f"SET {column} = datetime({column}, '-4 hours') "
                f"WHERE {column} IS NOT NULL"
            )
        )
        return result.rowcount


def main() -> int:
    engine = create_engine(DB_PATH)

    log.info("Fixing watchlist timestamps...")
    n = shift_column(engine, "watchlists", "created_at")
    log.info(f"  Fixed watchlists.created_at: {n} rows")

    n = shift_column(engine, "watchlists", "updated_at")
    log.info(f"  Fixed watchlists.updated_at: {n} rows")

    n = shift_column(engine, "watchlist_symbols", "added_at")
    log.info(f"  Fixed watchlist_symbols.added_at: {n} rows")

    log.info("Migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
