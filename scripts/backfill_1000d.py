#!/usr/bin/env python3
"""
scripts/backfill_1000d.py — Bulk backfill for all (or specified) symbols.

Usage:
    python scripts/backfill_1000d.py              # backfill all watched symbols
    python scripts/backfill_1000d.py AAPL TSLA    # backfill only AAPL and TSLA
    python scripts/backfill_1000d.py --dry-run    # list what would be backfilled
    python scripts/backfill_1000d.py --days=500   # smaller/faster reseed window

Phase 3.3.17: CLI tool for seeding a fresh DB or rebuilding history after
a BACKFILL_1M/1H/1D_DAYS change. Runs synchronously so it can be used in
a cron job or deployment script without worrying about asyncio event loops.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure the project root is on the path so backend/ imports resolve.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_1000d")


def _get_symbols(symbols: list[str]) -> list[str]:
    """Load symbols from the active watchlist if none were passed on the CLI."""
    if symbols:
        return [s.upper() for s in symbols]
    from backend.database import SessionLocal
    from backend.repositories.watchlist_repository import WatchlistRepository

    db = SessionLocal()
    try:
        repo = WatchlistRepository(db)
        watchlists = repo.get_watchlists(active_only=True)
        syms = []
        for wl in watchlists:
            for ws in wl.symbols:
                if ws.is_enabled and ws.symbol not in syms:
                    syms.append(ws.symbol)
        return syms
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill bar history for symbols (Phase 3.3.17)"
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        metavar="SYMBOL",
        help="Symbol(s) to backfill. Omit to backfill all watched symbols.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List symbols that would be backfilled without running the backfill.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "Override how far back to backfill, clamping every tier down "
            "(never up past its own BACKFILL_1M/1H/1D_DAYS ceiling). "
            "Default: each tier uses its own .env default, unclamped."
        ),
    )
    args = parser.parse_args()

    symbols = _get_symbols(args.symbols)
    if not symbols:
        logger.error("No symbols found (empty watchlist and none specified on CLI).")
        sys.exit(1)

    logger.info(f"Will backfill {len(symbols)} symbol(s): {symbols}")

    if args.dry_run:
        logger.info("--dry-run: skipping actual backfill")
        sys.exit(0)

    from backend.market_data.services.backfill_service import backfill_symbol_history_sync

    days = args.days
    logger.info(
        f"Window: override={days}d" if days is not None
        else "Window: each tier's own BACKFILL_1M/1H/1D_DAYS default"
    )

    total_t1 = 0
    total_t2 = 0
    errors = 0
    overall_start = time.monotonic()

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] Starting backfill for {symbol} ...")
        try:
            result = backfill_symbol_history_sync(symbol, days=days)
            if result.get("skipped"):
                logger.info(f"  skipped (already in progress)")
                continue
            t1 = result["tier1_written"]
            t2 = result["tier2_written"]
            dur = result["duration_s"]
            total_t1 += t1
            total_t2 += t2
            logger.info(
                f"  done in {dur:.1f}s — tier1={t1} 1m bars, tier2={t2} 1d bars"
            )
        except Exception as e:
            errors += 1
            logger.error(f"  FAILED: {e}")

    elapsed = time.monotonic() - overall_start
    logger.info(
        f"\nCompleted in {elapsed:.1f}s — "
        f"{total_t1} tier-1 bars, {total_t2} tier-2 bars, {errors} errors"
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
