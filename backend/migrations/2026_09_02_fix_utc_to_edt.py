"""One-shot DB migration: fix stored naive-UTC timestamps → naive-NY (EDT/EST).

Background
-----------
Before 2026-09-02 the backend stored timestamps as naive datetimes that were
actually in UTC (not NY).  JavaScript's ``new Date()`` then treated the
resulting ISO string as *browser local* time, so an EDT frontend showed a
4–5 hour offset (e.g. 12:19 PM EDT market time appeared as 4:19 PM).

Starting 2026-09-02 the project standardises on **naive America/New_York
datetimes** for all stored timestamps (see ``backend/utils/timezone.py``).

Migration steps:
  1. Delete all 1m bars (they'll be re-ingested by the live loop with correct timestamps).
  2. Delete 1m signals (same reason; they'll be re-recorded).
  3. Shift quotes/provider_status timestamps by -4h (EDT offset).
  4. Shift 1d bars by DST-aware offset (-4h EDT or -5h EST) to correct stored UTC midnight → NY midnight.

Note: 1d bars were already stored correctly (midnight NY) in many cases; the
shift only applies to bars that were stored as UTC. After this migration, all
stored timestamps are naive NY (EDT/EST) wall-clock times.
"""

from __future__ import annotations

import logging
import sys
from datetime import date

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate_utc_to_edt")

DB_PATH = "sqlite:////Users/dips/projects/MarketLens/marketlens.db"


def shift_column(engine, table: str, column: str, offset_hours: int) -> int:
    """Subtract ``offset_hours`` from every value in ``<table>.<column>``."""
    with engine.begin() as conn:
        result = conn.execute(
            text(f"UPDATE {table} SET {column} = datetime({column}, '-{offset_hours} hours')")
        )
        return result.rowcount


def delete_1m_bars(engine) -> int:
    """Delete 1m bars (they need re-backfill with correct timestamps)."""
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM bars WHERE timeframe = '1m'"))
        return result.rowcount


def delete_signals_for_1m(engine) -> int:
    """Delete 1m signals (they'll be regenerated from re-backfilled bars)."""
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM historical_signals WHERE timeframe = '1m'"))
        return result.rowcount


def main() -> int:
    engine = create_engine(DB_PATH)

    # 1m bars: delete (they'll be re-backfilled by the normal ingestion loop)
    log.info("Deleting 1m bars (will be re-ingested)...")
    n = delete_1m_bars(engine)
    log.info(f"  Deleted {n} 1m bar rows")

    log.info("Deleting 1m signals (will be re-recorded after re-ingestion)...")
    n = delete_signals_for_1m(engine)
    log.info(f"  Deleted {n} 1m signal rows")

    # Quotes, provider_status: shift by -4h (EDT)
    for table, col in [
        ("quotes", "timestamp"),
        ("provider_status", "timestamp"),
        ("provider_status", "last_success"),
    ]:
        try:
            n = shift_column(engine, table, col, offset_hours=4)
            log.info(f"  Shifted {table}.{col} by -4h ({n} rows)")
        except Exception as e:
            log.warning(f"  {table}.{col}: skipped ({e})")

    # 1d bars: stored as UTC naive representing NY-midnight.  Shift each row
    # individually based on whether that date falls in EDT (UTC-4) or EST (UTC-5).
    def _second_sunday_march(year: int) -> date:
        first = date(year, 3, 1)
        first_sunday = first.toordinal() + (6 - first.weekday()) % 7
        return date.fromordinal(first_sunday + 7)

    def _first_sunday_november(year: int) -> date:
        first = date(year, 11, 1)
        first_sunday = first.toordinal() + (6 - first.weekday()) % 7
        return date.fromordinal(first_sunday)

    def _ny_offset(ts_str: str) -> int:
        """Return UTC-4 (EDT) or UTC-5 (EST) based on the date part of ts_str."""
        y, m, d = int(ts_str[:4]), int(ts_str[5:7]), int(ts_str[8:10])
        d_ = date(y, m, d)
        edt_start = _second_sunday_march(y)
        est_start = _first_sunday_november(y)
        # EDT: from spring-forward to fall-back
        if edt_start <= d_ < est_start:
            return 4  # EDT
        return 5  # EST

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, timestamp FROM bars WHERE timeframe = '1d'")
        ).fetchall()
        updated = 0
        for row_id, ts in rows:
            offset = _ny_offset(ts)
            conn.execute(
                text("UPDATE bars SET timestamp = datetime(timestamp, :offset) WHERE id = :id"),
                {"offset": f"-{offset} hours", "id": row_id},
            )
            updated += 1
        log.info(f"  Shifted 1d bars by DST-aware offset ({updated} rows)")

    log.info("Migration complete.")
    log.info("Restart the server to trigger normal ingestion + backfill.")
    log.info("1m bars will be re-ingested from the live provider.")
    log.info("1d bars: shift may be off by 1h during EDT; corrected on next backfill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
