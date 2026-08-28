"""
Strategy Lab API endpoints.

Prefix: ``/api/strategy-lab``

Exposes the parameterized 3-way IS/Val/OOS experiment layer. All
experiments run asynchronously: the POST endpoint spawns a
``BacktestEngine.run`` per (symbol, slice) pair and returns the
experiment_id immediately so the front-end can poll ``GET /{id}``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.backtesting.engine import DEFAULT_SIGNALS
from backend.backtesting.experiment_runner import (
    ExperimentConfig,
    run_experiment,
)
from backend.backtesting.parameters import ExperimentParameters
from backend.repositories.experiment_repository import ExperimentRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategy-lab", tags=["strategy-lab"])


# --- Request models ----------------------------------------------------


class ExperimentParametersRequest(BaseModel):
    """Subset of ``ExperimentParameters`` — any field may be omitted to
    accept the server-side defaults."""

    model_config = ConfigDict(extra="ignore")

    rsi_period: int | None = Field(default=None, ge=2, le=100)
    macd_fast: int | None = Field(default=None, ge=2, le=200)
    macd_slow: int | None = Field(default=None, ge=2, le=200)
    macd_signal: int | None = Field(default=None, ge=2, le=100)
    adx_period: int | None = Field(default=None, ge=2, le=100)
    atr_period: int | None = Field(default=None, ge=2, le=100)
    supertrend_atr_period: int | None = Field(default=None, ge=2, le=50)
    supertrend_multiplier: float | None = Field(default=None, ge=0.5, le=10.0)
    bollinger_period: int | None = Field(default=None, ge=2, le=200)
    bollinger_std_dev: float | None = Field(default=None, ge=0.5, le=5.0)
    ema_fast_period: int | None = Field(default=None, ge=2, le=200)
    ema_slow_period: int | None = Field(default=None, ge=2, le=500)

    rsi_oversold: float | None = Field(default=None, ge=1, le=50)
    rsi_overbought: float | None = Field(default=None, ge=50, le=99)
    adx_trending_threshold: float | None = Field(default=None, ge=10, le=50)
    rsi_bullish_ceiling: float | None = Field(default=None, ge=45, le=70)
    rsi_bearish_floor: float | None = Field(default=None, ge=30, le=55)

    weight_ema: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_rsi: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_macd: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_adx: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_volume: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_momentum: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_supertrend: float | None = Field(default=None, ge=0.0, le=2.0)
    weight_bollinger: float | None = Field(default=None, ge=0.0, le=2.0)

    def to_experiment_parameters(self) -> ExperimentParameters:
        """Merge submitted overrides with server-side defaults."""
        return ExperimentParameters(**{k: v for k, v in self.model_dump().items() if v is not None})


class ExperimentCreate(BaseModel):
    """Request body for ``POST /api/strategy-lab/``."""

    name: str = Field(..., min_length=1, max_length=200)
    symbols: list[str] = Field(..., min_length=1)
    start_date: datetime
    end_date: datetime
    signals: list[str] | None = None
    parameters: ExperimentParametersRequest | None = None
    n_splits: int = Field(default=3, ge=2, le=10)
    val_pct: float = Field(default=0.20, ge=0.05, lt=1.0)
    oos_pct: float = Field(default=0.20, ge=0.05, lt=1.0)
    strategy_version: str | None = None

    @field_validator("symbols")
    @classmethod
    def _upper_symbols(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s.strip()]

    @field_validator("end_date")
    @classmethod
    def _validate_dates(cls, v: datetime, info) -> datetime:
        start = info.data.get("start_date")
        if start is not None and v <= start:
            raise ValueError("end_date must be after start_date")
        return v


class ExperimentCompare(BaseModel):
    """Request body for ``POST /api/strategy-lab/compare``."""

    experiment_ids: list[int] = Field(..., min_length=2, max_length=5)


# --- Response models ----------------------------------------------------


class SliceMetrics(BaseModel):
    """Aggregated metrics for one IS/Val/OOS slice."""

    model_config = ConfigDict(from_attributes=True)

    win_rate: float | None = None
    avg_return_1d: float | None = None
    avg_return_5d: float | None = None
    avg_return_20d: float | None = None
    median_return_1d: float | None = None
    sharpe: float | None = None
    profit_factor: float | None = None
    max_drawdown: float | None = None
    total_signals: int | None = None
    signal_frequency: float | None = None


class RegimeBucket(BaseModel):
    """Per-regime trade statistics."""

    model_config = ConfigDict(from_attributes=True)

    count: int = 0
    avg_return_1d: float | None = None
    win_rate_1d: float | None = None


class RegimeBreakdown(BaseModel):
    """Trade breakdown by regime_at_entry."""

    risk_on: RegimeBucket | None = None
    risk_off: RegimeBucket | None = None
    neutral: RegimeBucket | None = None
    unknown: RegimeBucket | None = None


class ExperimentResponse(BaseModel):
    """Response for a single experiment including IS/Val/OOS metrics."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    strategy_version: str
    parameters_json: str
    symbols: str
    start_date: datetime
    end_date: datetime
    # Slice boundaries
    is_start: datetime
    is_end: datetime
    val_start: datetime
    val_end: datetime
    oos_start: datetime | None
    oos_end: datetime | None
    # Config
    signals_requested: str
    n_splits: int
    val_pct: float
    oos_pct: float
    # Lifecycle
    status: str
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    # Metrics
    is_win_rate: float | None = None
    is_avg_return_1d: float | None = None
    is_avg_return_5d: float | None = None
    is_avg_return_20d: float | None = None
    is_median_return_1d: float | None = None
    is_sharpe: float | None = None
    is_profit_factor: float | None = None
    is_max_drawdown: float | None = None
    is_total_signals: int | None = None
    is_signal_frequency: float | None = None
    val_win_rate: float | None = None
    val_avg_return_1d: float | None = None
    val_avg_return_5d: float | None = None
    val_avg_return_20d: float | None = None
    val_median_return_1d: float | None = None
    val_sharpe: float | None = None
    val_profit_factor: float | None = None
    val_max_drawdown: float | None = None
    val_total_signals: int | None = None
    val_signal_frequency: float | None = None
    oos_win_rate: float | None = None
    oos_avg_return_1d: float | None = None
    oos_avg_return_5d: float | None = None
    oos_avg_return_20d: float | None = None
    oos_median_return_1d: float | None = None
    oos_sharpe: float | None = None
    oos_profit_factor: float | None = None
    oos_max_drawdown: float | None = None
    oos_total_signals: int | None = None
    oos_signal_frequency: float | None = None
    # Overfit
    overfit_score: float | None = None
    overfitting_warning: str | None = None
    # Runs
    run_ids_json: str | None = None


class ExperimentCompareResponse(BaseModel):
    """Side-by-side comparison of two or more experiments."""

    experiments: list[ExperimentResponse]
    regime_breakdown: list[dict] | None = None


# --- Helpers ------------------------------------------------------------


def _build_slice_metrics(exp: Any, prefix: str) -> SliceMetrics:
    return SliceMetrics(
        win_rate=getattr(exp, f"{prefix}win_rate", None),
        avg_return_1d=getattr(exp, f"{prefix}avg_return_1d", None),
        avg_return_5d=getattr(exp, f"{prefix}avg_return_5d", None),
        avg_return_20d=getattr(exp, f"{prefix}avg_return_20d", None),
        median_return_1d=getattr(exp, f"{prefix}median_return_1d", None),
        sharpe=getattr(exp, f"{prefix}sharpe", None),
        profit_factor=getattr(exp, f"{prefix}profit_factor", None),
        max_drawdown=getattr(exp, f"{prefix}max_drawdown", None),
        total_signals=getattr(exp, f"{prefix}total_signals", None),
        signal_frequency=getattr(exp, f"{prefix}signal_frequency", None),
    )


# --- Endpoints -----------------------------------------------------------


@router.post("/", response_model=ExperimentResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_experiment(body: ExperimentCreate) -> ExperimentResponse:
    """Create and immediately run a 3-way IS/Val/OOS experiment.

    The experiment is queued and runs synchronously in this process.
    Poll ``GET /api/strategy-lab/{id}`` for status and results.
    """
    params = body.parameters.to_experiment_parameters() if body.parameters else ExperimentParameters()
    signals = body.signals if body.signals else DEFAULT_SIGNALS

    config = ExperimentConfig(
        name=body.name,
        symbols=body.symbols,
        start_date=body.start_date,
        end_date=body.end_date,
        signals=signals,
        parameters=params,
        n_splits=body.n_splits,
        val_pct=body.val_pct,
        oos_pct=body.oos_pct,
        strategy_version=body.strategy_version,
    )

    # Run synchronously (fast enough for most experiments; <30s for 3 symbols × 3 slices)
    try:
        experiment_id = run_experiment(config)
    except Exception as exc:
        logger.exception("Experiment %s raised during run_experiment", body.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Experiment failed: {exc}",
        ) from exc

    repo = ExperimentRepository()
    try:
        exp = repo.get(experiment_id)
    finally:
        repo.close()

    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found after completion")

    return ExperimentResponse.model_validate(exp)


@router.get("/", response_model=list[ExperimentResponse])
async def list_experiments(
    limit: int = 20,
    status: str | None = None,
) -> list[ExperimentResponse]:
    """List recent experiments, optionally filtered by status."""
    repo = ExperimentRepository()
    try:
        experiments = repo.list_experiments(limit=limit, status=status)
    finally:
        repo.close()
    return [ExperimentResponse.model_validate(e) for e in experiments]


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(experiment_id: int) -> ExperimentResponse:
    """Get a single experiment with all aggregated IS/Val/OOS metrics."""
    repo = ExperimentRepository()
    try:
        exp = repo.get(experiment_id)
    finally:
        repo.close()

    if exp is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return ExperimentResponse.model_validate(exp)


@router.get("/{experiment_id}/runs")
async def get_experiment_runs(experiment_id: int) -> dict:
    """Get all BacktestRun rows for this experiment, with regime breakdown."""
    repo = ExperimentRepository()
    try:
        exp = repo.get(experiment_id)
        if exp is None:
            raise HTTPException(status_code=404, detail="Experiment not found")

        runs = repo.get_runs_for_experiment(experiment_id)
        breakdown = repo.get_regime_breakdown(experiment_id)
    finally:
        repo.close()

    # Serialize runs — only the fields the front-end needs.
    run_rows = []
    for r in runs:
        run_rows.append({
            "id": r.id,
            "symbol": r.symbol,
            "start_date": r.start_date.isoformat() if r.start_date else None,
            "end_date": r.end_date.isoformat() if r.end_date else None,
            "status": r.status,
            "out_of_sample": r.out_of_sample,
            "win_rate_1d": r.win_rate_1d,
            "avg_return_1d": r.avg_return_1d,
            "avg_return_5d": r.avg_return_5d,
            "avg_return_20d": r.avg_return_20d,
            "sharpe_ratio": r.sharpe_ratio,
            "profit_factor": r.profit_factor,
            "max_drawdown": r.max_drawdown,
            "total_signals": r.total_signals,
            "signal_frequency": r.signal_frequency,
            "regime_at_entry": None,  # aggregated below
        })

    return {
        "runs": run_rows,
        "regime_breakdown": breakdown,
    }


@router.post("/compare", response_model=ExperimentCompareResponse)
async def compare_experiments(body: ExperimentCompare) -> ExperimentCompareResponse:
    """Compare two or more experiments side-by-side.

    Returns all experiments plus a combined regime breakdown across
    all included experiments.
    """
    repo = ExperimentRepository()
    try:
        experiments = []
        all_run_ids: list[int] = []
        for eid in body.experiment_ids:
            exp = repo.get(eid)
            if exp is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Experiment {eid} not found",
                )
            experiments.append(ExperimentResponse.model_validate(exp))
            if exp.run_ids_json:
                try:
                    all_run_ids.extend(json.loads(exp.run_ids_json))
                except (json.JSONDecodeError, TypeError):
                    pass
    finally:
        repo.close()

    # Aggregate regime breakdown across all experiments.
    if all_run_ids:
        from backend.database import SessionLocal
        from backend.models import BacktestTrade

        db = SessionLocal()
        try:
            trades = (
                db.query(BacktestTrade)
                .filter(BacktestTrade.run_id.in_(all_run_ids))
                .all()
            )
        finally:
            db.close()

        buckets: dict[str, list] = {
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

        regime_breakdown = []
        for regime, t_list in buckets.items():
            rets = [t.return_1d for t in t_list if t.return_1d is not None]
            count = len(t_list)
            avg_return = round(sum(rets) / len(rets), 6) if rets else None
            win_rate = (
                round(len([t for t in t_list if t.return_1d and t.return_1d > 0]) / len(rets), 4)
                if rets else None
            )
            regime_breakdown.append({
                "regime": regime,
                "count": count,
                "avg_return_1d": avg_return,
                "win_rate_1d": win_rate,
            })
    else:
        regime_breakdown = None

    return ExperimentCompareResponse(
        experiments=experiments,
        regime_breakdown=regime_breakdown,
    )


@router.delete("/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(experiment_id: int) -> None:
    """Delete an experiment and all its child BacktestRun rows."""
    repo = ExperimentRepository()
    try:
        exp = repo.get(experiment_id)
        if exp is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        repo.delete(experiment_id)
    finally:
        repo.close()
