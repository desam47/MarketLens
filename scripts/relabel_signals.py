#!/usr/bin/env python3
"""
One-off repair: give every stored historical signal the trend it had AT ITS OWN BAR.

The old bulk recorder stamped each backfilled bar with the live trend engine's *current* score
(and today's market regime), so a backfill's rows all shared one label -- 92-99% of the table.
This replays each (symbol, timeframe)'s stored bars through a private trend engine and rewrites
the label columns (trend_score/state, strength, momentum, structure, market_regime). Prices and
forward outcomes are not touched. See ``backend/services/signal_replay.py``.

    python scripts/relabel_signals.py                 # dry run: report, write nothing
    python scripts/relabel_signals.py --apply         # save a label backup, then rewrite
    python scripts/relabel_signals.py --restore FILE  # put a saved backup back

Safe against the running server: short transactions, chunked writes, idempotent.
"""

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.models import HistoricalSignal  # noqa: E402
from backend.services.signal_replay import (  # noqa: E402
    export_labels,
    relabel_signals,
    restore_labels,
)


def _stamped_rows(db) -> int:
    """Rows sharing one trend_score with 10+ siblings of the same (symbol, timeframe): the
    signature of a stamped backfill."""
    per_score = (
        db.query(func.count().label("n"))
        .filter(HistoricalSignal.trend_score.isnot(None))
        .group_by(HistoricalSignal.symbol, HistoricalSignal.timeframe, HistoricalSignal.trend_score)
        .subquery()
    )
    return db.query(func.coalesce(func.sum(per_score.c.n), 0)).filter(per_score.c.n >= 10).scalar()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--restore", metavar="FILE", help="restore label columns from a saved backup")
    ap.add_argument(
        "--backup",
        metavar="FILE",
        default=str(
            ROOT / "data" / f"historical_signal_labels_pre_relabel_{date.today():%Y%m%d}.csv.gz"
        ),
    )
    ap.add_argument("--symbols", nargs="*", help="limit to these symbols")
    ap.add_argument("--timeframes", nargs="*", help="limit to these timeframes")
    ap.add_argument("--chunk-size", type=int, default=5000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    db = SessionLocal()
    try:
        if args.restore:
            print(
                f"restored {restore_labels(db, args.restore, args.chunk_size):,} rows from {args.restore}"
            )
            return 0

        pairs = (
            db.query(HistoricalSignal.symbol, HistoricalSignal.timeframe)
            .distinct()
            .order_by(HistoricalSignal.symbol, HistoricalSignal.timeframe)
            .all()
        )
        if args.symbols:
            wanted = {s.upper() for s in args.symbols}
            pairs = [p for p in pairs if p.symbol in wanted]
        if args.timeframes:
            pairs = [p for p in pairs if p.timeframe in set(args.timeframes)]

        before = _stamped_rows(db)
        if args.apply:
            Path(args.backup).parent.mkdir(parents=True, exist_ok=True)
            print(f"backup: {export_labels(db, args.backup):,} rows -> {args.backup}")

        totals = {"rows": 0, "changed": 0, "labelled": 0, "unlabelled": 0}
        t0 = time.time()
        for i, (symbol, timeframe) in enumerate(pairs, 1):
            stats = relabel_signals(db, symbol, timeframe, args.chunk_size, dry_run=not args.apply)
            for k in totals:
                totals[k] += stats[k]
            print(
                f"[{i}/{len(pairs)}] {symbol:6} {timeframe:4} rows={stats['rows']:>7,} "
                f"changed={stats['changed']:>7,} labelled={stats['labelled']:>7,} "
                f"unlabelled={stats['unlabelled']:>6,}",
                flush=True,
            )

        mode = "APPLIED" if args.apply else "DRY RUN (nothing written)"
        print(
            f"\n{mode}: {len(pairs)} pairs in {time.time() - t0:.0f}s -- "
            + ", ".join(f"{k}={v:,}" for k, v in totals.items())
        )
        print(f"rows sharing a stamped score before: {before:,}; now: {_stamped_rows(db):,}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
