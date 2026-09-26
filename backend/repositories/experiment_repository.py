"""
Repository for Experiment CRUD and query helpers.

Mirrors the ``BacktestRepository`` style: self-managed SQLAlchemy
session, method-per-query, returns ORM objects (no DTOs).
"""

import json
from datetime import datetime

from sqlalchemy import desc

from backend.database import SessionLocal
from backend.models import BacktestRun, BacktestTrade, Experiment


class ExperimentRepository:
    """CRUD for ``Experiment`` rows plus backtest-run helpers."""

    def __init__(self, db=None) -> None:
        self._owns_session = db is None
        self.db = db or SessionLocal()

    def close(self) -> None:
        if self._owns_session:
            self.db.close()

    # --- Create -----------------------------------------------------------

    def create(
        self,
        name: str,
        strategy_version: str,
        parameters_json: str,
        symbols: str,
        start_date: datetime,
        end_date: datetime,
        is_start: datetime,
        is_end: datetime,
        val_start: datetime,
        val_end: datetime,
        oos_start: datetime | None,
        oos_end: datetime | None,
        signals_requested: str,
        n_splits: int,
        val_pct: float,
        oos_pct: float,
        status: str = "pending",
    ) -> Experiment:
        exp = Experiment(
            name=name,
            strategy_version=strategy_version,
            parameters_json=parameters_json,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            is_start=is_start,
            is_end=is_end,
            val_start=val_start,
            val_end=val_end,
            oos_start=oos_start,
            oos_end=oos_end,
            signals_requested=signals_requested,
            n_splits=n_splits,
            val_pct=val_pct,
            oos_pct=oos_pct,
            status=status,
        )
        self.db.add(exp)
        self.db.commit()
        self.db.refresh(exp)
        return exp

    # --- Reads -----------------------------------------------------------

    def get(self, experiment_id: int) -> Experiment | None:
        return self.db.query(Experiment).filter(Experiment.id == experiment_id).first()

    def list_experiments(
        self,
        limit: int = 20,
        status: str | None = None,
    ) -> list[Experiment]:
        q = self.db.query(Experiment)
        if status is not None:
            q = q.filter(Experiment.status == status)
        return q.order_by(desc(Experiment.created_at)).limit(limit).all()

    # --- Updates ---------------------------------------------------------

    def update_status(
        self,
        experiment_id: int,
        status: str,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> Experiment | None:
        exp = self.get(experiment_id)
        if exp is None:
            return None
        exp.status = status
        if error is not None:
            exp.error = error
        if completed_at is not None:
            exp.completed_at = completed_at
        self.db.commit()
        self.db.refresh(exp)
        return exp

    def update_metrics(
        self,
        experiment_id: int,
        slice_prefix: str,
        metrics: dict,
    ) -> Experiment | None:
        """Persist aggregated metrics for one slice (is_ / val_ / oos_).

        ``metrics`` maps BacktestRun column names (e.g. ``win_rate_1d``)
        to their values. ``update_metrics`` maps these to the Experiment
        column name (e.g. ``win_rate_1d`` → ``is_win_rate`` by stripping
        the ``_1d`` suffix when present, then prepending the prefix).
        """
        exp = self.get(experiment_id)
        if exp is None:
            return None
        for key, val in metrics.items():
            # Map BacktestRun key to Experiment column:
            # win_rate_1d → is_win_rate  (suffix _1d is the window; not part of the col name)
            # avg_return_5d → is_avg_return_5d  (suffix _5d stays)
            # total_signals → is_total_signals  (no suffix; stays)
            col_name = _map_metric_key(slice_prefix, key)
            if hasattr(exp, col_name):
                setattr(exp, col_name, val)
        self.db.commit()
        self.db.refresh(exp)
        return exp

    def update_overfit(
        self,
        experiment_id: int,
        overfit_score: float,
        overfitting_warning: str | None,
    ) -> Experiment | None:
        exp = self.get(experiment_id)
        if exp is None:
            return None
        exp.overfit_score = overfit_score
        exp.overfitting_warning = overfitting_warning
        self.db.commit()
        self.db.refresh(exp)
        return exp

    def update_run_ids(
        self,
        experiment_id: int,
        run_ids: "list[int]",
    ) -> Experiment | None:
        exp = self.get(experiment_id)
        if exp is None:
            return None
        exp.run_ids_json = json.dumps(run_ids)
        self.db.commit()
        self.db.refresh(exp)
        return exp

    def finalize(
        self,
        experiment_id: int,
        is_metrics: dict,
        val_metrics: dict,
        oos_metrics: dict,
        overfit_score: float,
        overfitting_warning: str | None,
        run_ids: "list[int]",
        completed_at: datetime,
    ) -> "Experiment | None":
        """Persist all end-of-run fields in a single commit.

        Replaces the previous pattern of five separate commits
        (update_metrics×3, update_overfit, update_run_ids, update_status)
        that left partial DB state when the process was killed between any
        two of them.  All fields are written on the same ORM instance and
        committed once; if the commit fails the experiment stays in
        status="running" with no partial writes.
        """
        exp = self.get(experiment_id)
        if exp is None:
            return None
        for key, val in is_metrics.items():
            col_name = _map_metric_key("is_", key)
            if hasattr(exp, col_name):
                setattr(exp, col_name, val)
        for key, val in val_metrics.items():
            col_name = _map_metric_key("val_", key)
            if hasattr(exp, col_name):
                setattr(exp, col_name, val)
        for key, val in oos_metrics.items():
            col_name = _map_metric_key("oos_", key)
            if hasattr(exp, col_name):
                setattr(exp, col_name, val)
        exp.overfit_score = overfit_score
        exp.overfitting_warning = overfitting_warning
        exp.run_ids_json = json.dumps(run_ids)
        exp.status = "completed"
        exp.completed_at = completed_at
        self.db.commit()
        self.db.refresh(exp)
        return exp

    # --- Delete ----------------------------------------------------------

    def delete(self, experiment_id: int) -> bool:
        """Delete an experiment and all its child BacktestRun rows."""
        exp = self.get(experiment_id)
        if exp is None:
            return False

        # Cascade: delete all BacktestRuns that belong to this experiment.
        # run_ids are stored as a JSON array on the Experiment row.
        import json

        run_ids: list[int] = []
        if exp.run_ids_json:
            try:
                run_ids = json.loads(exp.run_ids_json)
            except (json.JSONDecodeError, TypeError):
                run_ids = []

        for rid in run_ids:
            run = self.db.query(BacktestRun).filter(BacktestRun.id == rid).first()
            if run:
                self.db.delete(run)

        self.db.delete(exp)
        self.db.commit()
        return True

    # --- Run helpers -----------------------------------------------------

    def get_runs_for_experiment(
        self,
        experiment_id: int,
    ) -> list[BacktestRun]:
        """Return all BacktestRun rows linked to this experiment."""
        import json

        exp = self.get(experiment_id)
        if exp is None:
            return []
        run_ids: list[int] = []
        if exp.run_ids_json:
            try:
                run_ids = json.loads(exp.run_ids_json)
            except (json.JSONDecodeError, TypeError):
                return []
        if not run_ids:
            return []
        return self.db.query(BacktestRun).filter(BacktestRun.id.in_(run_ids)).all()

    def get_regime_breakdown(
        self,
        experiment_id: int,
    ) -> dict:
        """Group trades by ``regime_at_entry``, compute avg return + win rate per bucket.

        Returns a dict of the form:
        {
            "risk_on":  { "count": N, "avg_return_1d": float, "win_rate_1d": float },
            "risk_off": { ... },
            "neutral":  { ... },
            "unknown":  { ... },
        }
        Unknown means ``regime_at_entry`` is None (regime tagging was off).
        """
        runs = self.get_runs_for_experiment(experiment_id)
        run_ids = [r.id for r in runs]
        if not run_ids:
            return _empty_breakdown()

        trades = self.db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all()

        buckets: dict[str, list[BacktestTrade]] = {
            "risk_on": [],
            "risk_off": [],
            "neutral": [],
            "unknown": [],
        }
        for t in trades:
            regime = t.regime_at_entry or "unknown"
            if regime not in buckets:
                regime = "unknown"
            buckets[regime].append(t)

        return {regime: _regime_stats(trades) for regime, trades in buckets.items()}


# --- Helpers ------------------------------------------------------------


def _empty_breakdown() -> dict:
    return {
        regime: {"count": 0, "avg_return_1d": None, "win_rate_1d": None}
        for regime in ("risk_on", "risk_off", "neutral", "unknown")
    }


# Metric-key → Experiment column name (with prefix) translation.
# BacktestRun uses ``win_rate_1d`` (1d window) but the Experiment column
# is just ``is_win_rate`` (the slice IS uses 1d forward returns by
# convention). For multi-window metrics (avg_return_5d, avg_return_20d)
# the suffix stays because each window has its own column.
_METRIC_KEY_MAP = {
    "win_rate_1d": "win_rate",  # Experiment only stores 1d win rate per slice
}


def _map_metric_key(slice_prefix: str, key: str) -> str:
    base = _METRIC_KEY_MAP.get(key, key)
    return f"{slice_prefix}{base}"


def _regime_stats(trades: list[BacktestTrade]) -> dict:
    count = len(trades)
    if count == 0:
        return {"count": 0, "avg_return_1d": None, "win_rate_1d": None}

    returns = [t.return_1d for t in trades if t.return_1d is not None]
    wins = [t for t in trades if t.return_1d is not None and t.return_1d > 0]

    avg_return = round(sum(returns) / len(returns), 6) if returns else None
    win_rate = round(len(wins) / len(returns), 4) if returns else None

    return {
        "count": count,
        "avg_return_1d": avg_return,
        "win_rate_1d": win_rate,
    }
