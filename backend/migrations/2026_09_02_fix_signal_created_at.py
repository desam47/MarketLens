"""Fix HistoricalSignal.created_at: UTC naive → NY naive.

created_at was stored with datetime.utcnow() (naive UTC). The API serializer
adds a -04:00 / -05:00 suffix, treating it as NY. The result: timestamps
appear 4 hours in the future, causing the frontend to show "Stuck · 4h ago".

This migration subtracts 4 hours from every created_at value, converting
the stored UTC time to the correct NY representation.
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fix_signal_created_at")

DB_PATH = "sqlite:////Users/dips/projects/MarketLens/marketlens.db"


def main() -> int:
    engine = create_engine(DB_PATH)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE historical_signals "
                "SET created_at = datetime(created_at, '-4 hours') "
                "WHERE created_at IS NOT NULL"
            )
        )
        log.info(f"Fixed created_at: {result.rowcount} rows updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
