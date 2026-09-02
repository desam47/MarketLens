#!/usr/bin/env python3
"""
One-time 1m backfill for Phase 3.1.

Fetches 3 months of 1m bars for all watched symbols from yfinance
and stores them in the DB. This populates the 1m store so that
higher timeframes (5m, 15m, 30m, 1h, 1d, 1wk) can be resampled
at read time without gaps.

Run once:
    python scripts/backfill_1m.py

Idempotent: safe to re-run. A flag file (data/migrations/3.1_1m_backfill.json)
is written after a successful run so subsequent invocations exit early.

Requires: yfinance, backend package, DB connection.

---
Webull 1m historical limit:
  Webull SDK only provides 1m data for the last ~30 days. Older 1m
  data must come from yfinance. The backfill script uses yfinance
  exclusively to avoid this limitation.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# ── repo root on sys.path ──────────────────────────────────────────────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.database import SessionLocal
from backend.repositories.bar_repository import upsert_bars
from backend.market_data.providers.yfinance_provider import YFinanceProvider
from backend.models.market_data import Bar, DataStatus

# ── config ──────────────────────────────────────────────────────────────────────
# How far back to backfill. 3 months is sufficient for Phase 3.1's NYSE
# 1d resampling to have complete calendar-day buckets for any recent signal.
_BACKFILL_DAYS = 90  # days

# Flag file written after a successful backfill. The script exits early if this
# file exists and contains "completed": true.
_FLAG_FILE = os.path.join(_REPO_ROOT, "data", "migrations", "3.1_1m_backfill.json")


def _ensure_flag_dir():
    flag_dir = os.path.dirname(_FLAG_FILE)
    os.makedirs(flag_dir, exist_ok=True)


def _write_flag(complete: bool, symbols: list[str], bars_written: int, ts: str):
    _ensure_flag_dir()
    with open(_FLAG_FILE, "w") as f:
        json.dump({
            "completed": complete,
            "symbols": symbols,
            "bars_written": bars_written,
            "timestamp": ts,
            "backfill_days": _BACKFILL_DAYS,
        }, f, indent=2)


def _read_flag() -> dict | None:
    if os.path.exists(_FLAG_FILE):
        try:
            with open(_FLAG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ── symbols ────────────────────────────────────────────────────────────────────

def _load_watchlist_symbols() -> list[str]:
    """Load all enabled watchlist symbols from the DB."""
    from backend.repositories.watchlist_repository import WatchlistRepository
    db = SessionLocal()
    try:
        repo = WatchlistRepository(db)
        watchlists = repo.get_watchlists(active_only=True)
        seen = set()
        symbols = []
        for wl in watchlists:
            wl_symbols = repo.get_watchlist_symbols(wl.id, enabled_only=True)
            for ws in wl_symbols:
                sym = ws.symbol.upper()
                if sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
        return symbols
    finally:
        db.close()


# ── main ────────────────────────────────────────────────────────────────────────

def backfill_1m(force: bool = False):
    """Fetch and store 3 months of 1m bars for all watched symbols.

    Args:
        force: if True, ignore the flag file and re-run regardless.
    """
    _ensure_flag_dir()
    flag = _read_flag()
    if flag and flag.get("completed") and not force:
        print(f"✅ Backfill already completed at {flag.get('timestamp')}")
        print(f"   Symbols: {flag.get('symbols')}")
        print(f"   Bars written: {flag.get('bars_written')}")
        print("   Run with --force to re-backfill.")
        return

    symbols = _load_watchlist_symbols()
    if not symbols:
        print("⚠  No watchlist symbols found. Nothing to backfill.")
        _write_flag(complete=False, symbols=[], bars_written=0,
                    ts=datetime.now(timezone.utc).isoformat())
        return

    # yfinance is the only provider that can reliably backfill 3mo of 1m.
    provider = YFinanceProvider()
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=_BACKFILL_DAYS)
    total_written = 0

    print(f"📥 Starting 1m backfill: {len(symbols)} symbols, "
          f"{_BACKFILL_DAYS} days ({start_date.date()} → {end_date.date()})")
    print(f"   Using yfinance (Webull/Finnhub 1m is limited to ~30 days)")
    print(f"   Flag file: {_FLAG_FILE}")

    # Write a "running" flag so a crashed run is distinguishable from a completed one.
    _write_flag(complete=False, symbols=symbols, bars_written=0,
                ts=datetime.now(timezone.utc).isoformat())

    for i, symbol in enumerate(symbols, 1):
        range_days = _BACKFILL_DAYS
        range_str = f"{range_days}d"

        print(f"[{i}/{len(symbols)}] {symbol}: fetching {range_str} of 1m...", end=" ", flush=True)
        try:
            bars: list[Bar] = provider.get_historical_bars(
                symbol, timeframe="1m", range_=range_str,
            )
            if not bars:
                print("0 bars (no data or outside trading hours)")
                continue

            # Upsert into DB — existing rows are updated on conflict.
            db = SessionLocal()
            try:
                written = upsert_bars(db, bars)
                db.commit()
                total_written += written
                print(f"{written} bars stored ({len(bars)} fetched)")
            except Exception as e:
                db.rollback()
                print(f"DB write failed: {e}")
            finally:
                db.close()

        except Exception as e:
            print(f"FAILED: {e}")

        # Respect yfinance rate limits: be kind to their servers.
        time.sleep(0.5)

    ts = datetime.now(timezone.utc).isoformat()
    if total_written > 0:
        _write_flag(complete=True, symbols=symbols, bars_written=total_written, ts=ts)
        print(f"\n✅ Backfill complete: {total_written} bars stored for {len(symbols)} symbols")
        print(f"   Flag written to {_FLAG_FILE}")
    else:
        print(f"\n⚠  Backfill completed but no bars were stored. Check provider credentials.")
        _write_flag(complete=False, symbols=symbols, bars_written=0, ts=ts)


if __name__ == "__main__":
    force = "--force" in sys.argv
    if "--dry-run" in sys.argv:
        flag = _read_flag()
        if flag:
            print(f"Flag: {json.dumps(flag, indent=2)}")
        else:
            print("No flag file found — backfill has not been run yet.")
        sys.exit(0)
    backfill_1m(force=force)
