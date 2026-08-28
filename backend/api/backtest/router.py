"""
Backtest API endpoints.

Wraps the process-wide ``backtest_engine`` singleton. The POST
endpoint runs the backtest synchronously and returns the resulting
``BacktestRun`` (status will be ``completed`` or ``failed`` by the
time the response is sent).

Phase 14 adds a ``POST /walk-forward`` endpoint that splits a date
range into train/test windows and returns a list of run_ids.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from backend.backtesting.engine import (
    DEFAULT_SIGNALS,
    BacktestConfig,
    WalkForwardConfig,
    backtest_engine,
    walk_forward_analyze,
)
from backend.repositories.backtest_repository import BacktestRepository

from ..dependencies import get_db

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# --- Request / Response models -----------------------------------------


class BacktestCreate(BaseModel):
    """Request body for ``POST /api/backtest/``.

    ``signals`` is optional; when omitted (or empty) the engine
    falls back to ``DEFAULT_SIGNALS`` (RSI/MACD/HIGH_VOLUME).
    """

    symbol: str
    start_date: datetime
    end_date: datetime
    signals: list[str] | None = None
    timeframe: str = "1d"
    # Optional override for the strategy version stamped onto the run.
    # When omitted, the server-side TrendSettings.strategy_version is used.
    strategy_version: str | None = None
    # Optional OOS flag. The engine itself doesn't read this; it's
    # reserved for callers who run their own train/test split and want
    # to label the run as out-of-sample in the DB.
    out_of_sample: bool | None = None

    @field_validator("symbol")
    @classmethod
    def _strip_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be empty")
        return v

    @field_validator("timeframe")
    @classmethod
    def _validate_timeframe(cls, v: str) -> str:
        # v1 only supports daily bars — the engine iterates one bar
        # at a time and the forward-return math assumes daily.
        if v != "1d":
            raise ValueError("only timeframe='1d' is supported in v1")
        return v

    @field_validator("end_date")
    @classmethod
    def _validate_dates(cls, v: datetime, info) -> datetime:
        start = info.data.get("start_date")
        if start is not None and v <= start:
            raise ValueError("end_date must be after start_date")
        return v

    def effective_signals(self) -> list[str]:
        if not self.signals:
            return list(DEFAULT_SIGNALS)
        return list(self.signals)


class BacktestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    signals_requested: str
    strategy_version: str | None = None
    status: str
    total_bars: int | None = None
    total_signals: int | None = None
    win_rate_1d: float | None = None
    avg_return_1d: float | None = None
    avg_return_5d: float | None = None
    avg_return_20d: float | None = None
    error: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    # --- Phase 14 extended metrics ---
    median_return_1d: float | None = None
    median_return_5d: float | None = None
    median_return_20d: float | None = None
    max_drawdown: float | None = None
    sharpe_ratio: float | None = None
    profit_factor: float | None = None
    mfe_avg: float | None = None
    mae_avg: float | None = None
    signal_frequency: float | None = None
    equity_curve_json: str | None = None
    out_of_sample: bool | None = None
    overfitting_warning: str | None = None


class BacktestTradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    signal: str
    entry_date: datetime
    entry_price: float
    exit_date_1d: datetime | None = None
    exit_price_1d: float | None = None
    return_1d: float | None = None
    exit_date_5d: datetime | None = None
    exit_price_5d: float | None = None
    return_5d: float | None = None
    exit_date_20d: datetime | None = None
    exit_price_20d: float | None = None
    return_20d: float | None = None
    mfe: float | None = None
    mae: float | None = None


class WalkForwardCreate(BaseModel):
    """Request body for ``POST /api/backtest/walk-forward``."""

    symbol: str
    start_date: datetime
    end_date: datetime
    signals: list[str] | None = None
    timeframe: str = "1d"
    n_splits: int = 4
    test_pct: float = 0.25
    strategy_version: str | None = None

    @field_validator("symbol")
    @classmethod
    def _strip_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be empty")
        return v

    @field_validator("timeframe")
    @classmethod
    def _validate_timeframe(cls, v: str) -> str:
        if v != "1d":
            raise ValueError("only timeframe='1d' is supported in v1")
        return v

    @field_validator("n_splits")
    @classmethod
    def _validate_n_splits(cls, v: int) -> int:
        if v < 2 or v > 12:
            raise ValueError("n_splits must be between 2 and 12")
        return v

    @field_validator("test_pct")
    @classmethod
    def _validate_test_pct(cls, v: float) -> float:
        if v <= 0.0 or v >= 0.5:
            raise ValueError("test_pct must be between 0 and 0.5")
        return v

    @field_validator("end_date")
    @classmethod
    def _validate_dates(cls, v: datetime, info) -> datetime:
        start = info.data.get("start_date")
        if start is not None and v <= start:
            raise ValueError("end_date must be after start_date")
        return v

    def effective_signals(self) -> list[str]:
        if not self.signals:
            return list(DEFAULT_SIGNALS)
        return list(self.signals)


class WalkForwardResponse(BaseModel):
    """Response body for ``POST /api/backtest/walk-forward``.

    The ``run_ids`` list contains the per-split run ids in chronological
    order: each split yields an in-sample run followed by an
    out-of-sample run. ``runs`` is the same set of ``BacktestRun``
    objects hydrated for the client to display in a single table.
    """

    run_ids: list[int]
    runs: list[BacktestResponse]


# --- Endpoints ---------------------------------------------------------


@router.get("/", response_model=list[BacktestResponse])
def list_runs(limit: int = 50, db: Session = Depends(get_db)):
    """List recent backtest runs (newest first)."""
    repo = BacktestRepository(db=db)
    return repo.list_runs(limit=limit)


@router.post("/", response_model=BacktestResponse, status_code=status.HTTP_201_CREATED)
def create_backtest(payload: BacktestCreate, db: Session = Depends(get_db)):
    """Run a backtest synchronously and return the resulting run.

    The engine persists a ``BacktestRun`` with ``status='running'``
    before iteration starts, so even a crash mid-run leaves a
    queryable record. On return the status will be ``completed``
    (with metrics) or ``failed`` (with ``error`` populated).

    We use the request-scoped ``db`` session to re-fetch the run
    before returning so Pydantic can serialize its attributes (the
    engine closes its own internal sessions, which would otherwise
    leave the returned ORM object detached).
    """
    config = BacktestConfig(
        symbol=payload.symbol,
        start_date=payload.start_date,
        end_date=payload.end_date,
        signals=payload.effective_signals(),
        timeframe=payload.timeframe,
        strategy_version=payload.strategy_version,
    )
    run_id = backtest_engine.run(config)
    # Patch the OOS flag if the caller provided one.
    if payload.out_of_sample is not None:
        repo = BacktestRepository(db=db)
        repo.update_run_status(run_id, status="completed", out_of_sample=payload.out_of_sample)
    # Re-fetch on the open request session so the response model can
    # read all attributes before the session is closed by the
    # dependency teardown.
    repo = BacktestRepository(db=db)
    fresh = repo.get_run(run_id)
    if fresh is None:
        raise HTTPException(status_code=500, detail="Backtest run not found after creation")
    return fresh


@router.post("/walk-forward", response_model=WalkForwardResponse)
def walk_forward(payload: WalkForwardCreate, db: Session = Depends(get_db)):
    """Run a walk-forward analysis.

    Splits ``[start_date, end_date)`` into ``n_splits`` equal windows,
    each split into a train slice (in-sample) and a test slice
    (out-of-sample) using ``test_pct``. Returns the list of run ids
    and the hydrated ``BacktestRun`` rows for the dashboard.
    """
    config = WalkForwardConfig(
        symbol=payload.symbol,
        start_date=payload.start_date,
        end_date=payload.end_date,
        signals=payload.effective_signals(),
        timeframe=payload.timeframe,
        n_splits=payload.n_splits,
        test_pct=payload.test_pct,
        strategy_version=payload.strategy_version,
    )
    run_ids = walk_forward_analyze(config)
    if not run_ids:
        raise HTTPException(
            status_code=400,
            detail="date range too small to produce any walk-forward splits",
        )
    # Hydrate all runs for the response.
    repo = BacktestRepository(db=db)
    runs = [repo.get_run(rid) for rid in run_ids]
    runs = [r for r in runs if r is not None]
    return WalkForwardResponse(run_ids=run_ids, runs=runs)


@router.get("/{run_id}", response_model=BacktestResponse)
def get_run(run_id: int, db: Session = Depends(get_db)):
    repo = BacktestRepository(db=db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return run


@router.get("/{run_id}/trades", response_model=list[BacktestTradeResponse])
def get_trades(run_id: int, db: Session = Depends(get_db)):
    """Per-signal trade rows for a run (entry/exit/forward returns)."""
    repo = BacktestRepository(db=db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return repo.get_trades(run_id)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: int, db: Session = Depends(get_db)):
    """Delete a backtest run and its child trade rows."""
    repo = BacktestRepository(db=db)
    ok = repo.delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return None
