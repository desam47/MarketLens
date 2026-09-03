"""One-shot cleanup: delete signals that have no backing bars.

When bars are deleted (timezone migration, watchlist cleanup, etc.), the
signal rows that reference them are left behind. Those signals are
meaningless: their price snapshot is detached from any bar, and any
forward outcomes that were computed are no longer reproducible.

This migration finds every (symbol, timeframe) combination in
``historical_signals`` that has zero rows in ``bars`` and deletes all
signals for those combinations.

It also handles a related case: SPY 1d bars still exist (62 rows), but
SPY 1d signals (if any) are stale because the live ingestion loop no
longer tracks SPY/QQQ (2026-09-02 regime-symbol removal). We keep
the 1d bars but delete the 1d signals.
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("delete_orphan_signals")

DB_PATH = "sqlite:////Users/dips/projects/MarketLens/marketlens.db"


def find_orphan_combos(conn) -> list[tuple[str, str]]:
    """Return (symbol, timeframe) pairs that have signals but no bars."""
    rows = conn.execute(
        text(
            "SELECT DISTINCT s.symbol, s.timeframe "
            "FROM historical_signals s "
            "LEFT JOIN bars b "
            "  ON s.symbol = b.symbol AND s.timeframe = b.timeframe "
            "WHERE b.id IS NULL "
            "ORDER BY s.symbol, s.timeframe"
        )
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def find_stale_combos(conn, watched_symbols: set[str]) -> list[tuple[str, str]]:
    """Return (symbol, timeframe) pairs whose symbol is not in any watchlist.

    A signal is "stale" if its symbol isn't actively watched — the
    bars may still exist but the user no longer wants this symbol in
    the dashboard. We delete the signals so they don't pollute the
    research views.
    """
    rows = conn.execute(
        text(
            "SELECT DISTINCT s.symbol, s.timeframe "
            "FROM historical_signals s "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM watchlist_symbols ws "
            "  JOIN watchlists w ON ws.watchlist_id = w.id "
            "  WHERE UPPER(ws.symbol) = s.symbol AND w.is_active = 1 "
            ")"
        )
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def delete_signals_for(conn, combos: list[tuple[str, str]]) -> int:
    """Delete all signals for the given (symbol, timeframe) pairs."""
    if not combos:
        return 0
    total = 0
    for sym, tf in combos:
        result = conn.execute(
            text(
                "DELETE FROM historical_signals "
                "WHERE symbol = :sym AND timeframe = :tf"
            ),
            {"sym": sym, "tf": tf},
        )
        log.info(f"  Deleted {result.rowcount} signals for {sym} {tf}")
        total += result.rowcount
    return total


def main() -> int:
    engine = create_engine(DB_PATH)
    with engine.begin() as conn:
        # 1. Delete signals whose (symbol, timeframe) has zero bars.
        log.info("Step 1: Find (symbol, timeframe) pairs with no bars...")
        orphans = find_orphan_combos(conn)
        log.info(f"  Found {len(orphans)} orphan pairs: {orphans}")
        deleted_orphans = delete_signals_for(conn, orphans)

        # 2. Delete signals whose symbol isn't in any active watchlist.
        log.info("Step 2: Find symbols not in any active watchlist...")
        stale = find_stale_combos(conn, set())
        log.info(f"  Found {len(stale)} stale pairs: {stale}")
        deleted_stale = delete_signals_for(conn, stale)

        log.info("Migration complete.")
        log.info(f"  Deleted {deleted_orphans} orphan signals (no backing bars)")
        log.info(f"  Deleted {deleted_stale} stale signals (unwatched symbols)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
