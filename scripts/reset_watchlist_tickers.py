#!/usr/bin/env python3
"""
Reset watchlist tickers: backup, delete, and re-add every symbol across all
watchlists while preserving each symbol's ``entity_type`` ("stock"/"etf"),
``is_enabled`` state, position, and notes.

Run in two phases so the backend/frontend/workers restart BETWEEN the delete
and the re-add (clears stuck RQ backfill state; the fresh ingestion service
then re-registers the re-added symbols for live tracking with the fixed
20-symbol Webull batch chunking):

    # 1. stop services (backend/frontend/rq workers)
    python3 scripts/reset_watchlist_tickers.py delete    # backup + wipe symbols
    # 2. restart services
    python3 scripts/reset_watchlist_tickers.py re-add    # re-add via API, same types

Use ``show`` at any time to inspect the current state without writing.

Run from the repo root.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

# repo root on sys.path so `from backend...` resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.database import SessionLocal  # noqa: E402
from backend.models.watchlist import WatchlistSymbol  # noqa: E402
from backend.repositories.watchlist_repository import WatchlistRepository  # noqa: E402

_BACKUP_DIR = os.path.join(_REPO_ROOT, "data")
_API_BASE = "http://127.0.0.1:5001"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup_path() -> str:
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    return os.path.join(_BACKUP_DIR, f"watchlist_reset_backup_{_timestamp()}.json")


def show() -> None:
    db = SessionLocal()
    try:
        repo = WatchlistRepository(db)
        watchlists = repo.get_watchlists(active_only=False)
        total = 0
        for wl in watchlists:
            symbols = repo.get_all_watchlist_symbols(wl.id, include_disabled=True)
            total += len(symbols)
            print(f"watchlist {wl.id:>2} {wl.name:<16} active={wl.is_active} ({len(symbols)})")
            for ws in symbols:
                print(
                    f"    {ws.symbol:<8} type={ws.entity_type!r:<7} "
                    f"enabled={bool(ws.is_enabled)} pos={ws.position} notes={ws.notes!r}"
                )
        print(f"\nTOTAL symbol rows across {len(watchlists)} watchlist(s): {total}")
    finally:
        db.close()


def delete() -> None:
    db = SessionLocal()
    try:
        repo = WatchlistRepository(db)
        watchlists = repo.get_watchlists(active_only=False)

        backup_path = _backup_path()
        dump = []
        deleted = 0
        for wl in watchlists:
            symbols = repo.get_all_watchlist_symbols(wl.id, include_disabled=True)
            for ws in symbols:
                dump.append(
                    {
                        "watchlist_id": wl.id,
                        "watchlist_name": wl.name,
                        "symbol": ws.symbol.upper(),
                        "entity_type": ws.entity_type,  # preserve exact current type
                        "is_enabled": bool(ws.is_enabled),
                        "position": ws.position,
                        "notes": ws.notes,
                    }
                )

        with open(backup_path, "w") as f:
            json.dump(dump, f, indent=2, default=str, sort_keys=True)

        # nuke every WatchlistSymbol row.
        db.query(WatchlistSymbol).delete()
        db.commit()
        deleted = len(dump)

        print(f"Backed up {deleted} symbol row(s) to {backup_path}")
        print(
            f"Deleted all {deleted} WatchlistSymbol row(s) across {len(watchlists)} watchlist(s)."
        )
        print("Watchlists themselves were NOT deleted — only their tickers.")
        print(f"Latest backup: {backup_path}")
    finally:
        db.close()


def re_add() -> None:
    backups = sorted(
        f
        for f in os.listdir(_BACKUP_DIR)
        if f.startswith("watchlist_reset_backup_") and f.endswith(".json")
    )
    if not backups:
        print("No backup found in data/. Run 'delete' first.")
        sys.exit(1)
    backup_path = os.path.join(_BACKUP_DIR, backups[-1])
    with open(backup_path) as f:
        dump = json.load(f)

    import requests

    # Resolve each watchlist name -> id from the running API (ids are stable
    # but we read them live to be safe).
    wl_resp = requests.get(f"{_API_BASE}/api/watchlists", timeout=10)
    wl_resp.raise_for_status()
    watchlists = {wl["name"]: wl["id"] for wl in wl_resp.json()}

    imported, skipped, errors = [], [], []
    for row in dump:
        wl_name = row["watchlist_name"]
        if wl_name not in watchlists:
            errors.append(f"{row['symbol']}@{wl_name}: watchlist not found")
            continue
        wid = watchlists[wl_name]
        symbol = row["symbol"].upper()
        body = {
            "symbol": symbol,
            "is_enabled": row.get("is_enabled", True),
            "entity_type": row.get("entity_type"),  # preserves stock/etf exactly
        }
        r = requests.post(f"{_API_BASE}/api/watchlists/{wid}/symbols", json=body, timeout=30)
        if r.status_code in (200, 201):
            imported.append(f"{symbol}@{wl_name}={body['entity_type']}")
        elif r.status_code == 400:
            skipped.append(f"{symbol}@{wl_name}")
        else:
            errors.append(f"{symbol}@{wl_name}: HTTP {r.status_code} {r.text[:120]}")

    print(f"Re-added {len(imported)} symbol(s) (entity_type preserved):")
    for s in imported:
        print(f"    + {s}")
    if skipped:
        print(f"Skipped (already present): {len(skipped)}")
    if errors:
        print(f"Errors: {len(errors)}")
        for e in errors:
            print(f"    ! {e}")
        sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset watchlist tickers (backup/delete/re-add).")
    parser.add_argument("command", choices=["show", "delete", "re-add"])
    args = parser.parse_args()
    if args.command == "show":
        show()
    elif args.command == "delete":
        delete()
    elif args.command == "re-add":
        re_add()


if __name__ == "__main__":
    main()
