"""
Repository for historical signal storage and research queries.
"""
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, func
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
        return self.db.query(HistoricalSignal).filter(
            HistoricalSignal.id == signal_id
        ).first()

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

    def get_history(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 1000,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[HistoricalSignal]:
        """Historical signal records with optional filters."""
        q = self.db.query(HistoricalSignal)

        if symbol:
            q = q.filter(HistoricalSignal.symbol == symbol.upper())
        if timeframe:
            q = q.filter(HistoricalSignal.timeframe == timeframe)
        if start_time:
            q = q.filter(HistoricalSignal.timestamp >= start_time)
        if end_time:
            q = q.filter(HistoricalSignal.timestamp <= end_time)

        return (
            q.order_by(desc(HistoricalSignal.timestamp))
            .limit(limit)
            .all()
        )

    def delete_older_than(self, days: int = 90) -> int:
        """Delete signals older than ``days`` days. Returns count deleted."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        count = (
            self.db.query(HistoricalSignal)
            .filter(HistoricalSignal.timestamp < cutoff)
            .delete(synchronize_session="fetch")
        )
        self.db.commit()
        return count

    # --- Research / outcome queries -----------------------------------------

    def get_signals_needing_outcomes(self, limit: int = 100) -> list[HistoricalSignal]:
        """Signals whose forward outcomes haven't been computed yet.

        Scans for the oldest N rows where return_5b IS NULL and outcome_computed
        is not True, sorted oldest-first so we fill in order.
        """
        return (
            self.db.query(HistoricalSignal)
            .filter(
                HistoricalSignal.return_5b.is_(None),
                func.coalesce(HistoricalSignal._outcome_missing, True).is_(True),
            )
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
    ) -> HistoricalSignal | None:
        """Update forward outcomes for a signal row."""
        signal = self.get_by_id(signal_id)
        if signal is None:
            return None
        signal.return_5b = return_5b
        signal.return_10b = return_10b
        signal.return_20b = return_20b
        signal.mfe = mfe
        signal.mae = mae
        signal._outcome_missing = False
        self.db.commit()
        self.db.refresh(signal)
        return signal

    def count_by_regime(self) -> list[dict[str, Any]]:
        """Count of signals grouped by market regime."""
        rows = (
            self.db.query(
                HistoricalSignal.market_regime,
                func.count(HistoricalSignal.id).label("count"),
            )
            .filter(HistoricalSignal.market_regime.isnot(None))
            .group_by(HistoricalSignal.market_regime)
            .all()
        )
        return [{"regime": r.market_regime, "count": r.count} for r in rows]

    def get_performance_by_regime(self) -> list[dict[str, Any]]:
        """Average forward returns grouped by market regime.

        Only includes signals that have outcomes computed.
        """
        rows = (
            self.db.query(
                HistoricalSignal.market_regime,
                func.avg(HistoricalSignal.return_5b).label("avg_return_5b"),
                func.avg(HistoricalSignal.return_10b).label("avg_return_10b"),
                func.avg(HistoricalSignal.return_20b).label("avg_return_20b"),
                func.avg(HistoricalSignal.mfe).label("avg_mfe"),
                func.avg(HistoricalSignal.mae).label("avg_mae"),
                func.count(HistoricalSignal.id).label("count"),
            )
            .filter(
                HistoricalSignal.market_regime.isnot(None),
                HistoricalSignal.return_5b.isnot(None),
            )
            .group_by(HistoricalSignal.market_regime)
            .all()
        )
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
