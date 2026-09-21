"""
Phase 19 — Experiment runner.

Splits the date range into in-sample / validation / out-of-sample
slices, runs a ``BacktestEngine.run`` for each (symbol, slice) pair
with regime tagging enabled, aggregates the resulting metrics, and
writes everything to a single ``Experiment`` row plus its
``run_ids_json`` array.

The 3-way split is intentionally different from the existing
``walk_forward_analyze()`` (which produces 2-way train/test pairs
across rolling windows). The Strategy Lab wants one pair of
in-sample / validation / OOS slices per experiment so the overfit
detector can compare all three.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from backend.config.settings import settings as _settings
from backend.models import BacktestRun
from backend.repositories.backtest_repository import BacktestRepository
from backend.repositories.experiment_repository import ExperimentRepository
from backend.utils.timezone import now_ny

from .engine import BacktestConfig, BacktestEngine
from .overfit import compute_overfit_report
from .parameters import ExperimentParameters

logger = logging.getLogger(__name__)


# --- Metrics keys per slice -------------------------------------------------

# Keys read from each ``BacktestRun`` row to build the per-slice aggregated
# metric dicts. Keeping them as a module constant makes it easy to add new
# metrics without touching the runner.
RUN_METRIC_KEYS: tuple[str, ...] = (
    "win_rate_1d",
    "avg_return_1d",
    "avg_return_5d",
    "avg_return_20d",
    "median_return_1d",
    "sharpe_ratio",
    "profit_factor",
    "max_drawdown",
    "signal_frequency",
    "total_signals",
)


@dataclass
class ExperimentConfig:
    """Configuration for a single Strategy Lab experiment."""

    name: str
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    signals: list[str]
    parameters: ExperimentParameters
    n_splits: int = 3
    val_pct: float = 0.20
    oos_pct: float = 0.20
    strategy_version: str | None = None

    def __post_init__(self) -> None:
        if self.val_pct + self.oos_pct >= 1.0:
            raise ValueError("val_pct + oos_pct must be < 1.0")
        if self.val_pct < 0 or self.oos_pct < 0:
            raise ValueError("val_pct and oos_pct must be non-negative")
        if self.n_splits < 2:
            raise ValueError("n_splits must be >= 2")


# --- 3-way split logic ------------------------------------------------------


def _split_slices(
    start: datetime, end: datetime, n_splits: int, val_pct: float, oos_pct: float
) -> list[tuple[str, datetime, datetime]]:
    """Return a list of (slice_name, start, end) for IS/Val/OOS.

    The total range is divided into ``n_splits`` equal windows; the
    **first** window is then further divided into IS / Val / OOS by
    ``val_pct`` / ``oos_pct``. Subsequent windows are not used for
    the 3-way split — the brief is one IS, one Val, one OOS, not
    rolling. A future phase could add rolling splits.
    """
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0:
        return []
    window_seconds = total_seconds / n_splits
    first_window_end = start + _seconds(window_seconds)

    is_end = start + _seconds(window_seconds * (1.0 - val_pct - oos_pct))
    val_end = start + _seconds(window_seconds * (1.0 - oos_pct))
    return [
        ("in_sample", start, is_end),
        ("validation", is_end, val_end),
        ("out_of_sample", val_end, first_window_end),
    ]


def _seconds(s: float):
    from datetime import timedelta

    return timedelta(seconds=s)


def _aggregate_metrics(runs: Iterable[BacktestRun]) -> dict:
    """Aggregate metric dicts from a list of completed runs.

    For each metric key, takes the simple mean across runs that have a
    non-null value. ``total_signals`` is summed (not averaged). The
    result is suitable for feeding to ``compute_overfit_report()`` and
    for storing on the ``Experiment`` row.
    """
    metrics: dict[str, float | int | None] = {k: None for k in RUN_METRIC_KEYS}
    sums: dict[str, float] = {k: 0.0 for k in RUN_METRIC_KEYS if k != "total_signals"}
    counts: dict[str, int] = {k: 0 for k in RUN_METRIC_KEYS if k != "total_signals"}
    total_signals = 0
    for run in runs:
        if run is None:
            continue
        for key in RUN_METRIC_KEYS:
            val = getattr(run, key, None)
            if val is None:
                continue
            if key == "total_signals":
                total_signals += int(val)
            else:
                sums[key] += float(val)
                counts[key] += 1
    for key in RUN_METRIC_KEYS:
        if key == "total_signals":
            metrics[key] = total_signals if total_signals > 0 else None
        elif counts[key] > 0:
            metrics[key] = round(sums[key] / counts[key], 6)
    return metrics


# --- Runner ------------------------------------------------------------------


def run_experiment(config: ExperimentConfig) -> int:
    """Run a 3-way IS/Val/OOS experiment across all symbols.

    Steps:
      1. Persist an ``Experiment`` row with status="running".
      2. Compute slice boundaries.
      3. For each (symbol, slice) pair: call ``BacktestEngine.run``
         with ``regime_tagging_enabled=True`` and the supplied
         ``ExperimentParameters``.
      4. Aggregate per-slice metrics across all symbols.
      5. Run ``compute_overfit_report()`` on the three aggregated dicts.
      6. Update the ``Experiment`` row with all aggregated metrics
         and the overfit score.
      7. Return the experiment_id.
    """
    exp_repo = ExperimentRepository()
    try:
        strategy_version = (
            config.strategy_version
            if config.strategy_version is not None
            else _settings.trend.strategy_version
        )
        signals_csv = ",".join(config.signals)
        symbols_csv = ",".join(s.upper() for s in config.symbols)

        slices = _split_slices(
            config.start_date,
            config.end_date,
            config.n_splits,
            config.val_pct,
            config.oos_pct,
        )
        # Pad missing slices (e.g. when OOS would be too short) with None.
        is_slice = next((s for s in slices if s[0] == "in_sample"), None)
        val_slice = next((s for s in slices if s[0] == "validation"), None)
        oos_slice = next((s for s in slices if s[0] == "out_of_sample"), None)

        exp = exp_repo.create(
            name=config.name,
            strategy_version=strategy_version,
            parameters_json=json.dumps(config.parameters.to_json_dict()),
            symbols=symbols_csv,
            start_date=config.start_date,
            end_date=config.end_date,
            is_start=is_slice[1] if is_slice else config.start_date,
            is_end=is_slice[2] if is_slice else config.end_date,
            val_start=val_slice[1] if val_slice else config.start_date,
            val_end=val_slice[2] if val_slice else config.end_date,
            oos_start=oos_slice[1] if oos_slice else None,
            oos_end=oos_slice[2] if oos_slice else None,
            signals_requested=signals_csv,
            n_splits=config.n_splits,
            val_pct=config.val_pct,
            oos_pct=config.oos_pct,
            status="running",
        )
        experiment_id = exp.id
    finally:
        exp_repo.close()

    # Run all (symbol, slice) backtests in a separate path so a single
    # crash doesn't leave the experiment stuck in "running".
    try:
        run_ids = _run_all_slices(config, slices, strategy_version)
    except Exception as exc:
        logger.exception("Experiment %s failed", experiment_id)
        _mark_failed(experiment_id, str(exc)[:500])
        return experiment_id

    # Aggregate per-slice metrics from the runs we just created.
    bt_repo = BacktestRepository()
    try:
        is_runs = _runs_by_slice(bt_repo, run_ids, "in_sample")
        val_runs = _runs_by_slice(bt_repo, run_ids, "validation")
        oos_runs = _runs_by_slice(bt_repo, run_ids, "out_of_sample")
        is_metrics = _aggregate_metrics(is_runs)
        val_metrics = _aggregate_metrics(val_runs)
        oos_metrics = _aggregate_metrics(oos_runs)
    finally:
        bt_repo.close()

    # Cross-split overfit detection.
    report = compute_overfit_report(is_metrics, val_metrics, oos_metrics)
    overfit_warning = "; ".join(report.warnings) if report.warnings else None

    # Persist aggregated metrics + overfit score.
    exp_repo = ExperimentRepository()
    try:
        exp_repo.update_metrics(
            experiment_id,
            slice_prefix="is_",
            metrics=is_metrics,
        )
        exp_repo.update_metrics(
            experiment_id,
            slice_prefix="val_",
            metrics=val_metrics,
        )
        exp_repo.update_metrics(
            experiment_id,
            slice_prefix="oos_",
            metrics=oos_metrics,
        )
        exp_repo.update_overfit(
            experiment_id,
            overfit_score=report.score,
            overfitting_warning=overfit_warning,
        )
        exp_repo.update_run_ids(experiment_id, run_ids)
        exp_repo.update_status(
            experiment_id,
            status="completed",
            completed_at=now_ny(),
        )
    finally:
        exp_repo.close()

    return experiment_id


def _run_all_slices(
    config: ExperimentConfig,
    slices: list[tuple[str, datetime, datetime]],
    strategy_version: str,
) -> list[int]:
    """Run every (symbol, slice) pair. Returns ordered list of run_ids."""
    engine = BacktestEngine()
    run_ids: list[int] = []
    for symbol in config.symbols:
        for slice_name, slice_start, slice_end in slices:
            slice_config = BacktestConfig(
                symbol=symbol,
                start_date=slice_start,
                end_date=slice_end,
                signals=config.signals,
                strategy_version=strategy_version,
                experiment_params=config.parameters,
                regime_tagging_enabled=True,
            )
            run_id = engine.run(slice_config)
            # Tag the run with its slice name so aggregation can
            # filter by IS / Val / OOS.
            _tag_run_slice(run_id, slice_name)
            run_ids.append(run_id)
    return run_ids


def _tag_run_slice(run_id: int, slice_name: str) -> None:
    """Mark a run as belonging to a particular slice.

    We don't have a dedicated ``slice`` column on ``BacktestRun``; the
    existing ``out_of_sample`` boolean plus a small convention is
    enough: ``in_sample=False, out_of_sample=True`` is reused for the
    OOS slice, and validation is encoded as ``out_of_sample=True``
    with the ``error`` field set to ``__slice:validation`` as a
    private marker. The aggregation helper matches on these.
    """
    repo = BacktestRepository()
    try:
        if slice_name == "out_of_sample":
            repo.update_run_status(run_id, status="completed", out_of_sample=True)
        elif slice_name == "validation":
            # Encode via error field as a private marker; clear later.
            repo.update_run_status(
                run_id,
                status="completed",
                out_of_sample=True,
                error="__slice:validation",
            )
        # in_sample: leave as-is (out_of_sample stays False)
    finally:
        repo.close()


def _runs_by_slice(
    repo: BacktestRepository,
    run_ids: list[int],
    slice_name: str,
) -> list[BacktestRun]:
    """Filter ``run_ids`` by slice tag.

    Slice tags were written into the run row by ``_tag_run_slice``.
    Validation rows are recognised by the ``__slice:validation`` marker
    in ``error``; OOS rows are recognised by ``out_of_sample=True`` with
    no marker; in-sample rows are the remainder.
    """
    runs: list[BacktestRun] = []
    for rid in run_ids:
        run = repo.get_run(rid)
        if run is None:
            continue
        err = run.error or ""
        if slice_name == "in_sample":
            if (run.out_of_sample is False or run.out_of_sample is None) and "__slice:" not in err:
                runs.append(run)
        elif slice_name == "validation":
            if "__slice:validation" in err:
                runs.append(run)
        elif slice_name == "out_of_sample":
            if (run.out_of_sample is True) and "__slice:" not in err:
                runs.append(run)
    return runs


def _mark_failed(experiment_id: int, error: str) -> None:
    repo = ExperimentRepository()
    try:
        repo.update_status(
            experiment_id,
            status="failed",
            error=error[:500],
            completed_at=now_ny(),
        )
    finally:
        repo.close()
