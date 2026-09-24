#!/usr/bin/env python3
"""
One-off repair: put every stored 1h and 4h bar back on its clock hour (MD-01).

Webull and Yahoo hourly bars start on the half hour and were stored 30 minutes early. For each
symbol with 1h bars this rebuilds the 1h series from our 1m bars where they exist and from
Alpaca's consolidated (SIP) hourly bars elsewhere, deletes the shifted provider bars, rebuilds
4h from the result, and deletes the symbol's 1h and 4h historical signals so the recorder
re-records them. See ``backend/market_data/hourly_repair.py``.

    python scripts/repair_hourly_bars.py --db /abs/path/marketlens.db           # dry run
    python scripts/repair_hourly_bars.py --db /abs/path/marketlens.db --apply   # write

Back the database up first. A dry run does every step and rolls it back, so its report shows
exactly what --apply would write. Each symbol is its own transaction. Restart the server after
--apply: its trend engines and signal recorder hold state built from the old bars, and the
restart re-seeds them and re-records the deleted signals.

Only Alpaca is called (read-only market data). Nothing here imports the market-data manager,
which would authenticate Webull.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event, func  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.market_data.hourly_repair import misplaced_hours, repair_symbol  # noqa: E402
from backend.market_data.providers.alpaca_provider import AlpacaProvider  # noqa: E402
from backend.models import BarModel  # noqa: E402
from backend.utils.timezone import NY  # noqa: E402


def _session_factory(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record):
        # The running server writes to the same file; wait for its locks instead of failing.
        conn.execute("PRAGMA busy_timeout = 30000")

    return sessionmaker(bind=engine)


def _alpaca_fetch():
    provider = AlpacaProvider()

    def fetch(symbol: str, start: datetime, end: datetime):
        bars = provider.get_bars_between(
            symbol, "1h", start.replace(tzinfo=NY), end.replace(tzinfo=NY)
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
        "--symbols", help="comma-separated symbols (default: every symbol with 1h bars)"
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
            else [
                s
                for (s,) in db.query(BarModel.symbol)
                .filter(BarModel.timeframe == "1h")
                .group_by(BarModel.symbol)
                .order_by(BarModel.symbol)
            ]
        )

    fetch = _alpaca_fetch()
    now = datetime.now(NY).replace(tzinfo=None)
    mode = "APPLY" if args.apply else "DRY RUN (rolled back)"
    print(f"{mode}: {len(symbols)} symbols in {db_path}")
    print(
        f"{'symbol':8} {'1h before':>28} {'from 1m':>8} {'alpaca':>7} {'dropped':>8}"
        f" {'1h after':>9} {'4h':>11} {'signals 1h/4h':>14} {'misplaced':>9}"
    )
    totals = {"before": 0, "after": 0, "built": 0, "alpaca": 0, "dropped": 0, "sig": 0}
    failures = []
    for symbol in symbols:
        with Session() as db:
            try:
                r = repair_symbol(db, symbol, fetch, now)
                bad = misplaced_hours(db, symbol)
                if args.apply:
                    db.commit()
                else:
                    db.rollback()
            except Exception as exc:  # noqa: BLE001 - one symbol's failure must not stop the rest
                db.rollback()
                failures.append(symbol)
                print(f"{symbol:8} FAILED, left unchanged: {type(exc).__name__}: {exc}")
                continue
        before = ",".join(f"{p}={n}" for p, n in sorted(r.before_1h.items()))
        after = sum(r.after_1h.values())
        print(
            f"{symbol:8} {before[:28]:>28} {r.built_from_1m:>8} {r.from_provider:>7}"
            f" {r.dropped:>8} {after:>9} {f'{r.before_4h}->{r.after_4h}':>11}"
            f" {f'{r.signals_deleted["1h"]}/{r.signals_deleted["4h"]}':>14} {bad:>9}"
        )
        totals["before"] += sum(r.before_1h.values())
        totals["after"] += after
        totals["built"] += r.built_from_1m
        totals["alpaca"] += r.from_provider
        totals["dropped"] += r.dropped
        totals["sig"] += sum(r.signals_deleted.values())

    with Session() as db:
        off_hour = (
            db.query(func.count())
            .select_from(BarModel)
            .filter(
                BarModel.timeframe == "1h", func.strftime("%M:%S", BarModel.timestamp) != "00:00"
            )
            .scalar()
        )
    print(
        f"\n1h rows {totals['before']} -> {totals['after']}: {totals['built']} built from 1m,"
        f" {totals['alpaca']} from Alpaca SIP, {totals['dropped']} shifted hours with no"
        f" replacement. {totals['sig']} 1h/4h signals deleted. 1h rows off the hour: {off_hour}."
    )
    if failures:
        print(f"Failed, unchanged: {', '.join(failures)}")
    if args.apply:
        print("Restart the server so trend engines and the signal recorder re-seed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
