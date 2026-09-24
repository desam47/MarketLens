#!/usr/bin/env python3
"""
One-off repair: settle every stored 1m bar from Alpaca's consolidated (SIP) feed (MD-03).

Webull's extended-hours 1m volume is about half the market's, its stream (Nasdaq Basic) less,
and Alpaca IEX bars a few percent. For each symbol with 1m bars this replaces every stored minute
older than 15 minutes with its SIP bar, then rebuilds the 2m-30m, 1h and 4h bars over them. The
live ingestion loop keeps new minutes settled from here on. See
``backend/market_data/sip_settle.py``.

    python scripts/settle_1m_from_sip.py --db /abs/path/marketlens.db           # dry run
    python scripts/settle_1m_from_sip.py --db /abs/path/marketlens.db --apply   # write

Back the database up first. A dry run does every step and rolls it back. Each symbol is its own
transaction. Historical signals are not touched. Restart the server afterwards so trend engines
re-seed from the settled bars.

Only Alpaca is called (read-only market data). Nothing here imports the market-data manager,
which would authenticate Webull.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.market_data.providers.alpaca_provider import AlpacaProvider  # noqa: E402
from backend.market_data.sip_settle import settle_symbol  # noqa: E402
from backend.repositories.watchlist_repository import WatchlistRepository  # noqa: E402
from backend.utils.timezone import NY  # noqa: E402


def _session_factory(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record):
        # The running server writes to the same file; wait for its locks instead of failing.
        conn.execute("PRAGMA busy_timeout = 30000")

    return sessionmaker(bind=engine)


def _watched_symbols(db) -> list[str]:
    """What the ingestion service tracks. Symbols in no watchlist are left alone: settling would
    add minutes for them that nothing keeps current (MD-04)."""
    repo = WatchlistRepository(db)
    return sorted(
        {
            ws.symbol.upper()
            for wl in repo.get_watchlists(active_only=True)
            for ws in repo.get_watchlist_symbols(wl.id, enabled_only=True)
        }
    )


def _alpaca_fetch():
    provider = AlpacaProvider()

    def fetch(symbol: str, start: datetime, end: datetime):
        bars = provider.get_bars_between(
            symbol, "1m", start.replace(tzinfo=NY), end.replace(tzinfo=NY)
        )
        time.sleep(0.35)  # stay well under Alpaca's 200 requests a minute
        return bars

    return fetch


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", required=True, help="absolute path of the SQLite database")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument(
        "--symbols",
        help="comma-separated symbols (default: the enabled symbols of active watchlists)",
    )
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute() or not db_path.exists():
        print(f"--db must be an existing absolute path: {args.db}", file=sys.stderr)
        return 2

    Session = _session_factory(db_path)
    with Session() as db:
        symbols = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else _watched_symbols(db)
        )

    fetch = _alpaca_fetch()
    now = datetime.now(NY).replace(tzinfo=None)
    until = now - timedelta(minutes=15)
    mode = "APPLY" if args.apply else "DRY RUN (rolled back)"
    print(f"{mode}: {len(symbols)} symbols in {db_path}, minutes before {until:%Y-%m-%d %H:%M}")
    print(
        f"{'symbol':8} {'replaced':>40} {'added':>6} {'volume before':>15} {'after':>15} {'ratio':>6}"
    )
    totals = {"replaced": 0, "added": 0, "before": 0, "after": 0}
    failures = []
    for symbol in symbols:
        with Session() as db:
            try:
                r = settle_symbol(db, symbol, fetch, until, now)
                if args.apply:
                    db.commit()
                else:
                    db.rollback()
            except Exception as exc:  # noqa: BLE001 - one symbol's failure must not stop the rest
                db.rollback()
                failures.append(symbol)
                print(f"{symbol:8} FAILED, left unchanged: {type(exc).__name__}: {exc}")
                continue
        replaced = ",".join(f"{p}={n}" for p, n in sorted(r.replaced.items()))
        ratio = r.volume_after / r.volume_before if r.volume_before else 0.0
        print(
            f"{symbol:8} {replaced[:40]:>40} {r.added:>6} {r.volume_before:>15,}"
            f" {r.volume_after:>15,} {ratio:>6.2f}"
        )
        totals["replaced"] += sum(r.replaced.values())
        totals["added"] += r.added
        totals["before"] += r.volume_before
        totals["after"] += r.volume_after

    print(
        f"\n{totals['replaced']} minutes replaced with SIP bars, {totals['added']} added."
        f" 1m volume {totals['before']:,} -> {totals['after']:,}."
    )
    if failures:
        print(f"Failed, unchanged: {', '.join(failures)}")
    if args.apply:
        print("Restart the server so trend engines re-seed from the settled bars.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
