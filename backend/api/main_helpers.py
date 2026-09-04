"""
Startup helpers: signal hygiene, retention enforcement.

Phase 3.8.6+: ``run_signal_hygiene`` runs on every server start. It
fixes two known issues from earlier phases:

1. **Signal gaps** — bars that exist in the DB but have no
   ``historical_signals`` row. Caused by the 5,000-bar cap in
   ``backfill_signals_for_symbol`` (since raised to 50,000). Re-running
   with the higher cap fills the gap.

2. **1h retention drift** — historical backfills that ran with the
   2-year cap (before the 1-year cap was enforced) left bars older
   than 1 year in the DB. We prune them so the on-disk window matches
   the configured cap.

The function is idempotent: a second call after a clean DB is a no-op.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from backend.database import SessionLocal
from backend.models import HistoricalSignal
from backend.models.market_data_sql import BarModel
from backend.services.signal_recorder import signal_recorder

logger = logging.getLogger(__name__)

# 1h cap — must match the backfill tier2_days in backfill_service.py.
_1H_RETENTION_DAYS = 365


def _watched_symbols() -> list[str]:
    """Return upper-case symbol list across all active watchlists."""
    from backend.models import WatchlistSymbol
    db = SessionLocal()
    try:
        rows = (
            db.query(WatchlistSymbol.symbol)
            .filter(WatchlistSymbol.is_enabled)
            .distinct()
            .all()
        )
        return [s[0].upper() for s in rows]
    finally:
        db.close()


def _fill_signal_gaps(symbol: str) -> dict[str, int]:
    """Record signals for any (symbol, tf) that has more bars than signals.

    Returns a {tf: filled_count} map.
    """
    db = SessionLocal()
    try:
        # Per-timeframe bar count vs signal count.
        bar_counts = dict(
            db.query(BarModel.timeframe, func.count(BarModel.id))
            .filter(BarModel.symbol == symbol)
            .group_by(BarModel.timeframe)
            .all()
        )
        sig_counts = dict(
            db.query(HistoricalSignal.timeframe, func.count(HistoricalSignal.id))
            .filter(HistoricalSignal.symbol == symbol)
            .group_by(HistoricalSignal.timeframe)
            .all()
        )
    finally:
        db.close()

    filled: dict[str, int] = {}
    for tf, bar_n in bar_counts.items():
        sig_n = sig_counts.get(tf, 0)
        gap = bar_n - sig_n
        if gap > 0:
            # Re-run the per-timeframe backfill with the high cap.
            n = signal_recorder.backfill_signals_for_symbol(
                symbol, timeframe=tf, max_bars=50000
            )
            if n:
                filled[tf] = n
    return filled


def _prune_out_of_cap(symbol: str) -> int:
    """Drop 1h bars/signals older than the 1-year cap.

    Returns the total rows deleted (bars + signals).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_1H_RETENTION_DAYS)
    db = SessionLocal()
    try:
        bars = (
            db.query(BarModel)
            .filter(
                BarModel.symbol == symbol,
                BarModel.timeframe == "1h",
                BarModel.timestamp < cutoff,
            )
            .delete(synchronize_session=False)
        )
        sigs = (
            db.query(HistoricalSignal)
            .filter(
                HistoricalSignal.symbol == symbol,
                HistoricalSignal.timeframe == "1h",
                HistoricalSignal.timestamp < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
    finally:
        db.close()
    return bars + sigs


def run_signal_hygiene() -> dict[str, tuple[dict[str, int], int]]:
    """Run gap-fill + retention enforcement for every watched symbol.

    Returns ``{symbol: ({tf: filled_count}, pruned_rows)}`` so the
    caller can log a one-line summary per symbol.
    """
    symbols = _watched_symbols()
    if not symbols:
        return {}
    results: dict[str, tuple[dict[str, int], int]] = {}
    for sym in symbols:
        try:
            tfs = _fill_signal_gaps(sym)
        except Exception as e:
            logger.warning(f"Signal hygiene: gap-fill failed for {sym}: {e}")
            tfs = {}
        try:
            pruned = _prune_out_of_cap(sym)
        except Exception as e:
            logger.warning(f"Signal hygiene: prune failed for {sym}: {e}")
            pruned = 0
        results[sym] = (tfs, pruned)
    return results
