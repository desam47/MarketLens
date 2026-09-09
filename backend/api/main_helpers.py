"""
Startup helpers: signal hygiene.

Phase 3.8.6+: ``run_signal_hygiene`` runs on every server start. It
fixes **signal gaps** — bars that exist in the DB but have no
``historical_signals`` row. Caused by the 5,000-bar cap in
``backfill_signals_for_symbol`` (since raised to 50,000). Re-running
with the higher cap fills the gap.

The function is idempotent: a second call after a clean DB is a no-op.

Retention enforcement (formerly also here, as a startup-only 1h-specific
365-day prune) moved out 2026-09-09: the rolling retention prune
(ingestion_service._ingest_1m_recent_window ->
bar_repository.prune_bars_by_retention) now runs continuously — every
~60s ingestion tick, not just at startup — and covers all 10 timeframes
via their own configured windows (RetentionSettings), not just 1h. That
supersedes what this module used to do.
"""
from __future__ import annotations

import logging

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models import HistoricalSignal
from backend.models.market_data_sql import BarModel
from backend.services.signal_recorder import signal_recorder

logger = logging.getLogger(__name__)


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


def run_signal_hygiene() -> dict[str, dict[str, int]]:
    """Run signal gap-fill for every watched symbol.

    Returns ``{symbol: {tf: filled_count}}`` so the caller can log a
    one-line summary per symbol. Retention enforcement used to happen
    here too (a startup-only 1h-specific prune) — moved to the
    continuously-running rolling retention prune, see this module's
    docstring.
    """
    symbols = _watched_symbols()
    if not symbols:
        return {}
    results: dict[str, dict[str, int]] = {}
    for sym in symbols:
        try:
            tfs = _fill_signal_gaps(sym)
        except Exception as e:
            logger.warning(f"Signal hygiene: gap-fill failed for {sym}: {e}")
            tfs = {}
        results[sym] = tfs
    return results
