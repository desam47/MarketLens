"""
Repository for backtest runs and trades.

Mirrors ``AlertRepository`` style: a self-managed SQLAlchemy session
plus a method-per-query interface. Returns ORM objects (not DTOs);
serialization is the router's job.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import desc

from backend.database import SessionLocal
from backend.models import BacktestRun, BacktestTrade


class BacktestRepository:
    """CRUD for backtest runs and their child trade rows."""

    def __init__(self, db=None) -> None:
        self._owns_session = db is None
        self.db = db or SessionLocal()

    def close(self) -> None:
        if self._owns_session:
            self.db.close()

    # --- Run reads ------------------------------------------------------

    def get_run(self, run_id: int) -> BacktestRun | None:
        return self.db.query(BacktestRun).filter(BacktestRun.id == run_id).first()

    def list_runs(self, limit: int = 50) -> list[BacktestRun]:
        return self.db.query(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(limit).all()

    # --- Run writes -----------------------------------------------------

    def create_run(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        signals_requested: str,
        strategy_version: str | None = None,
        status: str = "pending",
    ) -> BacktestRun:
        run = BacktestRun(
            symbol=symbol.upper(),
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            signals_requested=signals_requested,
            strategy_version=strategy_version,
            status=status,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def update_run_status(
        self,
        run_id: int,
        status: str,
        *,
        total_bars: int | None = None,
        total_signals: int | None = None,
        win_rate_1d: float | None = None,
        avg_return_1d: float | None = None,
        avg_return_5d: float | None = None,
        avg_return_20d: float | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
        # --- Phase 14 extended metrics ---
        median_return_1d: float | None = None,
        median_return_5d: float | None = None,
        median_return_20d: float | None = None,
        max_drawdown: float | None = None,
        sharpe_ratio: float | None = None,
        profit_factor: float | None = None,
        mfe_avg: float | None = None,
        mae_avg: float | None = None,
        signal_frequency: float | None = None,
        equity_curve_json: str | None = None,
        out_of_sample: bool | None = None,
        overfitting_warning: str | None = None,
    ) -> BacktestRun | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        run.status = status
        if total_bars is not None:
            run.total_bars = total_bars
        if total_signals is not None:
            run.total_signals = total_signals
        if win_rate_1d is not None:
            run.win_rate_1d = win_rate_1d
        if avg_return_1d is not None:
            run.avg_return_1d = avg_return_1d
        if avg_return_5d is not None:
            run.avg_return_5d = avg_return_5d
        if avg_return_20d is not None:
            run.avg_return_20d = avg_return_20d
        if error is not None:
            run.error = error
        if completed_at is not None:
            run.completed_at = completed_at
        # --- Phase 14 ---
        if median_return_1d is not None:
            run.median_return_1d = median_return_1d
        if median_return_5d is not None:
            run.median_return_5d = median_return_5d
        if median_return_20d is not None:
            run.median_return_20d = median_return_20d
        if max_drawdown is not None:
            run.max_drawdown = max_drawdown
        if sharpe_ratio is not None:
            run.sharpe_ratio = sharpe_ratio
        if profit_factor is not None:
            run.profit_factor = profit_factor
        if mfe_avg is not None:
            run.mfe_avg = mfe_avg
        if mae_avg is not None:
            run.mae_avg = mae_avg
        if signal_frequency is not None:
            run.signal_frequency = signal_frequency
        if equity_curve_json is not None:
            run.equity_curve_json = equity_curve_json
        if out_of_sample is not None:
            run.out_of_sample = out_of_sample
        if overfitting_warning is not None:
            run.overfitting_warning = overfitting_warning
        self.db.commit()
        self.db.refresh(run)
        return run

    def delete_run(self, run_id: int) -> bool:
        run = self.get_run(run_id)
        if run is None:
            return False
        self.db.delete(run)
        self.db.commit()
        return True

    # --- Trade writes ---------------------------------------------------

    def add_trades(self, run_id: int, trades: Iterable[BacktestTrade]) -> int:
        """Bulk-insert a batch of ``BacktestTrade`` rows for a run.

        Returns the number of rows actually written.
        """
        trades = list(trades)
        if not trades:
            return 0
        for t in trades:
            t.run_id = run_id
            self.db.add(t)
        self.db.commit()
        return len(trades)

    # --- Trade reads ----------------------------------------------------

    def get_trades(self, run_id: int) -> list[BacktestTrade]:
        return (
            self.db.query(BacktestTrade)
            .filter(BacktestTrade.run_id == run_id)
            .order_by(BacktestTrade.entry_date.asc())
            .all()
        )
