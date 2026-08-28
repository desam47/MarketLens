"""
Phase 19 — Experiment SQLAlchemy model.

One ``Experiment`` represents a named Strategy Lab run with a fixed set
of indicator parameters applied across one or more symbols over a date
range. It splits the range into in-sample / validation / out-of-sample
slices and aggregates the backtest metrics across all symbol-runs.

A single ``BacktestRun`` can belong to multiple experiments (e.g.
"RSI(7) baseline" and "RSI(7) vs RSI(14)" share the RSI(7) runs). To
avoid a many-to-many join table, run IDs are stored as a JSON array in
``run_ids_json``.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint

from backend.database import Base


class Experiment(Base):
    """One named Strategy Lab experiment.

    An experiment defines a set of indicator parameters and a date range,
    then launches one ``BacktestRun`` per requested symbol × slice
    (in-sample, validation, out-of-sample). The run IDs are stored as a
    JSON array so the front-end can fetch individual rows for per-symbol
    detail without a join table.
    """
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("name", "strategy_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Human-readable experiment name, e.g. "RSI(7) vs RSI(14) comparison"
    name = Column(String(200), nullable=False, index=True)
    # Bumped per config change; references TrendSettings.strategy_version
    # when the experiment uses defaults.
    strategy_version = Column(String(50), nullable=False, index=True)
    # Serialised ``ExperimentParameters`` JSON.
    parameters_json = Column(Text, nullable=False)
    # Symbols as a comma-separated string.
    symbols = Column(String(500), nullable=False)
    # The full experiment date range.
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)

    # --- 3-way split boundaries ---
    is_start = Column(DateTime, nullable=False)
    is_end = Column(DateTime, nullable=False)
    val_start = Column(DateTime, nullable=False)
    val_end = Column(DateTime, nullable=False)
    # OOS is nullable when the history is too short for a third slice.
    oos_start = Column(DateTime, nullable=True)
    oos_end = Column(DateTime, nullable=True)

    # Configuration
    signals_requested = Column(String(255), nullable=False)
    n_splits = Column(Integer, nullable=False, default=3)
    val_pct = Column(Float, nullable=False, default=0.20)
    oos_pct = Column(Float, nullable=False, default=0.20)

    # Lifecycle
    status = Column(String(20), nullable=False, default="pending", index=True)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)

    # --- Aggregated in-sample metrics ---
    is_win_rate = Column(Float, nullable=True)
    is_avg_return_1d = Column(Float, nullable=True)
    is_avg_return_5d = Column(Float, nullable=True)
    is_avg_return_20d = Column(Float, nullable=True)
    is_median_return_1d = Column(Float, nullable=True)
    is_sharpe = Column(Float, nullable=True)
    is_profit_factor = Column(Float, nullable=True)
    is_max_drawdown = Column(Float, nullable=True)
    is_total_signals = Column(Integer, nullable=True)
    is_signal_frequency = Column(Float, nullable=True)

    # --- Aggregated validation metrics ---
    val_win_rate = Column(Float, nullable=True)
    val_avg_return_1d = Column(Float, nullable=True)
    val_avg_return_5d = Column(Float, nullable=True)
    val_avg_return_20d = Column(Float, nullable=True)
    val_median_return_1d = Column(Float, nullable=True)
    val_sharpe = Column(Float, nullable=True)
    val_profit_factor = Column(Float, nullable=True)
    val_max_drawdown = Column(Float, nullable=True)
    val_total_signals = Column(Integer, nullable=True)
    val_signal_frequency = Column(Float, nullable=True)

    # --- Aggregated out-of-sample metrics ---
    oos_win_rate = Column(Float, nullable=True)
    oos_avg_return_1d = Column(Float, nullable=True)
    oos_avg_return_5d = Column(Float, nullable=True)
    oos_avg_return_20d = Column(Float, nullable=True)
    oos_median_return_1d = Column(Float, nullable=True)
    oos_sharpe = Column(Float, nullable=True)
    oos_profit_factor = Column(Float, nullable=True)
    oos_max_drawdown = Column(Float, nullable=True)
    oos_total_signals = Column(Integer, nullable=True)
    oos_signal_frequency = Column(Float, nullable=True)

    # --- Overfitting ---
    # 0.0 = safe, 1.0+ = dangerous. Computed after all slices complete.
    overfit_score = Column(Float, nullable=True)
    overfitting_warning = Column(String(500), nullable=True)

    # JSON array of BacktestRun IDs created by this experiment.
    run_ids_json = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Experiment(id={self.id}, name={self.name!r}, "
            f"status={self.status!r}, symbols={self.symbols!r})>"
        )
