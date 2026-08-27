"""
Backtest API endpoints.

Wraps the process-wide ``backtest_engine`` singleton. The POST
endpoint runs the backtest synchronously and returns the resulting
``BacktestRun`` (status will be ``completed`` or ``failed`` by the
time the response is sent).
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from backend.backtesting.engine import (
    DEFAULT_SIGNALS,
    BacktestConfig,
    backtest_engine,
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
    )
    run_id = backtest_engine.run(config)
    # Re-fetch on the open request session so the response model can
    # read all attributes before the session is closed by the
    # dependency teardown.
    repo = BacktestRepository(db=db)
    fresh = repo.get_run(run_id)
    if fresh is None:
        raise HTTPException(status_code=500, detail="Backtest run not found after creation")
    return fresh


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
