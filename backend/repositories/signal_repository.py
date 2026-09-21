"""
Repository for historical signal storage and research queries.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from backend.models import HistoricalSignal


class SignalRepository:
    """CRUD + research queries for HistoricalSignal records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Core CRUD -----------------------------------------------------------

    def create(self, **fields) -> HistoricalSignal:
        """Insert a single HistoricalSignal row."""
        signal = HistoricalSignal(**fields)
        self.db.add(signal)
        self.db.commit()
        self.db.refresh(signal)
        return signal

    def bulk_create(self, records: list[dict[str, Any]]) -> int:
        """Insert multiple HistoricalSignal rows efficiently.

        Returns the number of rows inserted.
        """
        if not records:
            return 0
        objects = [HistoricalSignal(**r) for r in records]
        self.db.bulk_save_objects(objects, return_defaults=False)
        self.db.commit()
        return len(objects)

    def get_by_id(self, signal_id: int) -> HistoricalSignal | None:
        return self.db.query(HistoricalSignal).filter(HistoricalSignal.id == signal_id).first()

    def get_latest(self, symbol: str, timeframe: str) -> HistoricalSignal | None:
        """Most recent signal for a symbol/timeframe."""
        return (
            self.db.query(HistoricalSignal)
            .filter(
                HistoricalSignal.symbol == symbol.upper(),
                HistoricalSignal.timeframe == timeframe,
            )
            .order_by(desc(HistoricalSignal.timestamp))
            .first()
        )

    def get_latest_per_timeframe(
        self, symbol: str, timeframes: list[str]
    ) -> dict[str, HistoricalSignal]:
        """Most recent signal for a symbol, one per timeframe.

        Returns a mapping of timeframe -> signal. Timeframes with no
        signal are omitted from the result; callers can detect "no data
        for this symbol" by checking whether the dict is empty.
        """
        symbol = symbol.upper()
        latest: dict[str, HistoricalSignal] = {}
        for tf in timeframes:
            signal = (
                self.db.query(HistoricalSignal)
                .filter(
                    HistoricalSignal.symbol == symbol,
                    HistoricalSignal.timeframe == tf,
                )
                .order_by(desc(HistoricalSignal.timestamp))
                .first()
            )
            if signal is not None:
                latest[tf] = signal
        return latest

    def get_history(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 1000,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        symbols: list[str] | None = None,
        completed_only: bool = False,
    ) -> list[HistoricalSignal]:
        """Historical signal records with optional filters.

        ``symbol`` filters to a single symbol; ``symbols`` filters to any
        of a list. If both are given, ``symbol`` wins (single-symbol query).

        ``completed_only`` skips rows where return_5b IS NULL (i.e. outcomes
        haven't been computed yet because not enough future bars exist).
        """
        q = self.db.query(HistoricalSignal)

        if symbol:
            q = q.filter(HistoricalSignal.symbol == symbol.upper())
        elif symbols:
            q = q.filter(HistoricalSignal.symbol.in_([s.upper() for s in symbols]))
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        if start_time:
            q = q.filter(HistoricalSignal.timestamp >= start_time)
        if end_time:
            q = q.filter(HistoricalSignal.timestamp <= end_time)
        if completed_only:
            q = q.filter(HistoricalSignal.return_5b.isnot(None))

        return q.order_by(desc(HistoricalSignal.timestamp)).limit(limit).all()

    def delete_older_than(self, days: int = 90) -> int:
        """Delete signals older than ``days`` days. Returns count deleted."""
        cutoff = datetime.now() - timedelta(days=days)
        count = (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.timestamp < cutoff)
            .delete(synchronize_session="fetch")
        )
        self.db.commit()
        return count

    def delete_for_symbol(self, symbol: str) -> int:
        """Delete all signal rows for ``symbol`` (all timeframes).

        Called by the watchlist removal path when a symbol leaves every
        active watchlist — bars and signals are both purged so nothing
        orphaned remains in the DB.
        """
        sym = symbol.upper()
        count = (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.symbol == sym)
            .delete(synchronize_session="fetch")
        )
        self.db.commit()
        return count

    # --- Research / outcome queries -----------------------------------------

    def get_signals_needing_outcomes(self, limit: int = 100) -> list[HistoricalSignal]:
        """Signals whose forward outcomes haven't been computed yet.

        Any row with return_5b IS NULL needs processing — that covers both
        freshly-created signals and rows whose outcomes were reset. Sorting
        oldest-first ensures we fill in order from the beginning of history.
        """
        return (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.return_5b.is_(None))
            .order_by(HistoricalSignal.timestamp.asc())
            .limit(limit)
            .all()
        )

    def update_outcomes(
        self,
        signal_id: int,
        return_5b: float | None,
        return_10b: float | None,
        return_20b: float | None,
        mfe: float | None,
        mae: float | None,
        commit: bool = True,
    ) -> HistoricalSignal | None:
        """Update forward outcomes for a signal row.

        When ``commit=True`` (the default), the session commits immediately.
        Pass ``commit=False`` when batching multiple updates together; the
        caller is responsible for calling ``db.commit()`` once at the end.
        """
        signal = self.get_by_id(signal_id)
        if signal is None:
            return None
        signal.return_5b = return_5b
        signal.return_10b = return_10b
        signal.return_20b = return_20b
        signal.mfe = mfe
        signal.mae = mae
        signal._outcome_missing = False
        if commit:
            self.db.commit()
        self.db.refresh(signal)
        return signal

    def count_by_regime(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """Count of signals grouped by market regime.

        If ``symbols`` is given, only signals for those symbols are counted.
        """
        q = self.db.query(
            HistoricalSignal.market_regime,
            func.count(HistoricalSignal.id).label("count"),
        ).filter(HistoricalSignal.market_regime.isnot(None))
        if symbols:
            q = q.filter(HistoricalSignal.symbol.in_([s.upper() for s in symbols]))
        rows = q.group_by(HistoricalSignal.market_regime).all()
        return [{"regime": r.market_regime, "count": r.count} for r in rows]

    def get_performance_by_regime(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """Average forward returns grouped by market regime.

        Only includes signals that have outcomes computed. If ``symbols``
        is given, only signals for those symbols are included.
        """
        q = self.db.query(
            HistoricalSignal.market_regime,
            func.avg(HistoricalSignal.return_5b).label("avg_return_5b"),
            func.avg(HistoricalSignal.return_10b).label("avg_return_10b"),
            func.avg(HistoricalSignal.return_20b).label("avg_return_20b"),
            func.avg(HistoricalSignal.mfe).label("avg_mfe"),
            func.avg(HistoricalSignal.mae).label("avg_mae"),
            func.count(HistoricalSignal.id).label("count"),
        ).filter(
            HistoricalSignal.market_regime.isnot(None),
            HistoricalSignal.return_5b.isnot(None),
        )
        if symbols:
            q = q.filter(HistoricalSignal.symbol.in_([s.upper() for s in symbols]))
        rows = q.group_by(HistoricalSignal.market_regime).all()
        return [
            {
                "regime": r.market_regime,
                "count": r.count,
                "avg_return_5b": round(float(r.avg_return_5b), 4) if r.avg_return_5b else None,
                "avg_return_10b": round(float(r.avg_return_10b), 4) if r.avg_return_10b else None,
                "avg_return_20b": round(float(r.avg_return_20b), 4) if r.avg_return_20b else None,
                "avg_mfe": round(float(r.avg_mfe), 4) if r.avg_mfe else None,
                "avg_mae": round(float(r.avg_mae), 4) if r.avg_mae else None,
            }
            for r in rows
        ]

    def get_signals_by_trend_state(
        self, trend_state: str, limit: int = 100
    ) -> list[HistoricalSignal]:
        """Signals with a specific trend state."""
        return (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.trend_state == trend_state)
            .order_by(desc(HistoricalSignal.timestamp))
            .limit(limit)
            .all()
        )

    def get_signal_count(self) -> int:
        """Total count of historical signals."""
        return self.db.query(func.count(HistoricalSignal.id)).scalar() or 0

    def get_stats(self, symbol: str, timeframe: str | None = None) -> dict[str, Any]:
        """Track-record statistics for one symbol (optionally one timeframe).

        ``total``            every stored signal.
        ``with_outcomes``    those whose forward outcome has been computed.
        ``avg_return_5b`` / ``avg_return_10b``   mean raw forward return (percent price change
                             over the next 5 / 10 bars of that timeframe), None if none computed.
        ``win_rate``         share of DIRECTIONAL signals that called it right: a bullish one wins
                             when its 5-bar return is positive, a bearish one when it is negative.
                             Neutral signals carry no call and are excluded; None if there are none.

        Returns are stored raw (not direction-adjusted), so the win rate has to be computed
        against each signal's own ``trend_state`` rather than as "return > 0".
        """
        q = self.db.query(HistoricalSignal).filter(HistoricalSignal.symbol == symbol.upper())
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        with_outcome = HistoricalSignal.return_5b.isnot(None)
        directional = with_outcome & HistoricalSignal.trend_state.in_(("bullish", "bearish"))
        called_it = (
            (HistoricalSignal.trend_state == "bullish") & (HistoricalSignal.return_5b > 0)
        ) | ((HistoricalSignal.trend_state == "bearish") & (HistoricalSignal.return_5b < 0))
        row = q.with_entities(
            func.count(HistoricalSignal.id),
            func.sum(case((with_outcome, 1), else_=0)),
            func.avg(HistoricalSignal.return_5b),
            func.avg(HistoricalSignal.return_10b),
            func.sum(case((directional, 1), else_=0)),
            func.sum(case((directional & called_it, 1), else_=0)),
        ).one()
        total, with_outcomes, avg5, avg10, n_directional, n_wins = row
        return {
            "total": int(total or 0),
            "with_outcomes": int(with_outcomes or 0),
            "avg_return_5b": round(float(avg5), 4) if avg5 is not None else None,
            "avg_return_10b": round(float(avg10), 4) if avg10 is not None else None,
            "win_rate": round(int(n_wins or 0) / int(n_directional), 4) if n_directional else None,
        }


# Signals are derived from bars, and startup "signal hygiene" (backend/api/main_helpers.py) compares
# each timeframe's bar count with its signal count and regenerates signals whenever bars outnumber
# them. So a timeframe's signals must outlive its bars by a margin: pruned to the bar window
# exactly, ordinary clock skew between the two hourly prunes would open a small gap and trigger a
# full re-backfill.
SIGNAL_RETENTION_MARGIN_DAYS = 2


def prune_signals_by_retention(
    db: Session,
    chunk_size: int = 5000,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete each timeframe's signals once they are older than that timeframe's BAR retention
    window (``settings.retention``: 16 days for 1m-30m, 366 for 1h/4h, 1096 for 1d/1wk) plus
    ``SIGNAL_RETENTION_MARGIN_DAYS``.

    Before this, signals had no retention at all: about 47,000 rows a day (roughly 30 MB), so
    ~10 GB a year, and intraday signals outlived the bars they were computed from by months.
    Deletes in short chunked transactions so the SQLite write lock is never held for long.
    Returns ``{timeframe: rows_deleted}`` for timeframes that lost rows.
    """
    from backend.config.settings import settings
    from backend.utils.timezone import now_ny

    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    now = now or now_ny()
    timeframes = [row[0] for row in db.query(HistoricalSignal.timeframe).distinct().all()]
    deleted_by_tf: dict[str, int] = {}
    for tf in timeframes:
        days = settings.retention.days_for(tf) + SIGNAL_RETENTION_MARGIN_DAYS
        cutoff = now - timedelta(days=days)
        total = 0
        while True:
            ids = (
                select(HistoricalSignal.id)
                .where(HistoricalSignal.timeframe == tf, HistoricalSignal.timestamp < cutoff)
                .limit(chunk_size)
            )
            deleted = (
                db.query(HistoricalSignal)
                .filter(HistoricalSignal.id.in_(ids))
                .delete(synchronize_session=False)
            )
            db.commit()
            total += deleted
            if deleted < chunk_size:
                break
        if total:
            deleted_by_tf[tf] = total
    return deleted_by_tf


def delete_signals_for_symbol(symbol: str) -> int:
    """Delete every signal row for ``symbol`` from the DB.

    Returns the number of rows deleted. Use this when a symbol is removed
    from every watchlist and we no longer want any historical signals for it.
    """
    from backend.database import SessionLocal  # avoid circular import

    if not symbol:
        return 0
    db = SessionLocal()
    try:
        repo = SignalRepository(db)
        return repo.delete_for_symbol(symbol)
    finally:
        db.close()
