"""
Symbol purge service — Phase 3.x.

Provides ``purge_symbol_from_database(symbol)`` which deletes every
per-symbol row from every table in the DB:

  - bars
  - historical_signals
  - quotes
  - market_status
  - alerts  (and alert_triggers via cascade)
  - ai_analysis_jobs
  - backtest_runs  (and backtest_trades via cascade)
  - drawing_tools

Tables that are NOT purged (correctly):
  - watchlist_symbols  — always deleted by the caller (FK constraint)
  - custom_indicators — owned by watchlist, not symbol
  - ai_templates      — global, not per-symbol
  - provider_status    — per-provider, not per-symbol

Usage from the API layer::

    from backend.services.purge_service import purge_symbol_from_database

    deleted = purge_symbol_from_database("AAPL")
    logger.info(f"purged {deleted} rows for AAPL")

Usage from the ingestion service (after a symbol leaves all watchlists)::

    from backend.services.purge_service import purge_symbol_from_database
    purge_symbol_from_database(symbol)
"""
from __future__ import annotations

import logging
from typing import TypedDict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.database import SessionLocal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed result dict
# ---------------------------------------------------------------------------

class PurgeResult(TypedDict):
    symbol: str
    bars: int
    signals: int
    quotes: int
    market_status: int
    alerts: int
    alert_triggers: int
    ai_analysis_jobs: int
    backtest_runs: int
    backtest_trades: int
    drawing_tools: int
    total: int


# ---------------------------------------------------------------------------
# Individual table deleters (one per table, so callers can also call
# them individually if needed — e.g. the router currently calls
# delete_bars_for_symbol, etc. from the repositories directly).
# ---------------------------------------------------------------------------

def _delete_bars(db: Session, symbol: str) -> int:
    from backend.models.market_data_sql import BarModel
    result = db.execute(
        delete(BarModel).where(BarModel.symbol == symbol.upper())
    )
    return result.rowcount


def _delete_signals(db: Session, symbol: str) -> int:
    from backend.models import HistoricalSignal
    result = db.execute(
        delete(HistoricalSignal).where(HistoricalSignal.symbol == symbol.upper())
    )
    return result.rowcount


def _delete_quotes(db: Session, symbol: str) -> int:
    from backend.models import QuoteModel
    result = db.execute(
        delete(QuoteModel).where(QuoteModel.symbol == symbol.upper())
    )
    return result.rowcount


def _delete_market_status(db: Session, symbol: str) -> int:
    from backend.models import MarketStatusModel
    result = db.execute(
        delete(MarketStatusModel).where(MarketStatusModel.symbol == symbol.upper())
    )
    return result.rowcount


def _delete_alerts_and_triggers(db: Session, symbol: str) -> tuple[int, int]:
    """Delete alerts for symbol, then orphaned alert_triggers (no FK cascade configured).

    We must fetch alert IDs before deleting them, then use those IDs to
    clean up triggers — the subquery approach fails because Alert rows
    are gone by the time the trigger DELETE runs.
    """
    from backend.models import Alert, AlertTrigger

    # Collect alert IDs first (before DELETE removes them).
    alert_ids = list(
        db.execute(
            select(Alert.id).where(Alert.symbol == symbol.upper())
        ).scalars()
    )
    alert_count = len(alert_ids)

    # Delete orphaned triggers.
    orphaned_triggers = 0
    if alert_ids:
        trigger_result = db.execute(
            delete(AlertTrigger).where(AlertTrigger.alert_id.in_(alert_ids))
        )
        orphaned_triggers = trigger_result.rowcount

    # Delete the alerts themselves.
    if alert_ids:
        db.execute(delete(Alert).where(Alert.id.in_(alert_ids)))

    return alert_count, orphaned_triggers


def _delete_ai_analysis_jobs(db: Session, symbol: str) -> int:
    from backend.models import AIAnalysisJob
    result = db.execute(
        delete(AIAnalysisJob).where(AIAnalysisJob.symbol == symbol.upper())
    )
    return result.rowcount


def _delete_backtest_runs(db: Session, symbol: str) -> tuple[int, int]:
    """Delete backtest_runs for symbol, then orphaned BacktestTrade rows.

    The SQLAlchemy ``cascade="all, delete-orphan"`` on ``BacktestRun.trades``
    only fires on ORM-level deletes. Since we use a core ``delete()`` for
    performance, we explicitly clean up the child rows first.
    """
    from backend.models import BacktestRun, BacktestTrade

    # Find the run IDs for this symbol so we can count + delete their trades.
    run_ids = list(
        db.execute(
            select(BacktestRun.id).where(BacktestRun.symbol == symbol.upper())
        ).scalars()
    )

    if not run_ids:
        return 0, 0

    # Count + delete trades for those runs.
    trade_count = db.execute(
        delete(BacktestTrade).where(BacktestTrade.run_id.in_(run_ids))
    ).rowcount

    # Now delete the runs themselves.
    run_count = db.execute(
        delete(BacktestRun).where(BacktestRun.id.in_(run_ids))
    ).rowcount

    return run_count, trade_count


def _delete_drawing_tools(db: Session, symbol: str) -> int:
    from backend.models import DrawingTool
    result = db.execute(
        delete(DrawingTool).where(DrawingTool.symbol == symbol.upper())
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Top-level purge — all tables in one transaction
# ---------------------------------------------------------------------------

def purge_symbol_from_database(symbol: str) -> PurgeResult:
    """Delete every per-symbol row for ``symbol`` across all tables.

    Returns a ``PurgeResult`` dict with the count deleted per table.
    Uses a single transaction — if any delete fails the whole thing
    rolls back and an exception propagates.

    Safe to call multiple times (idempotent).
    """
    if not symbol:
        return PurgeResult(
            symbol="", bars=0, signals=0, quotes=0, market_status=0,
            alerts=0, alert_triggers=0, ai_analysis_jobs=0,
            backtest_runs=0, backtest_trades=0, drawing_tools=0, total=0,
        )

    symbol = symbol.upper()
    db = SessionLocal()
    try:
        bars = _delete_bars(db, symbol)
        signals = _delete_signals(db, symbol)
        quotes = _delete_quotes(db, symbol)
        market_status = _delete_market_status(db, symbol)
        alerts, alert_triggers = _delete_alerts_and_triggers(db, symbol)
        ai_analysis_jobs = _delete_ai_analysis_jobs(db, symbol)
        backtest_runs, backtest_trades = _delete_backtest_runs(db, symbol)
        drawing_tools = _delete_drawing_tools(db, symbol)

        db.commit()

        total = (
            bars + signals + quotes + market_status
            + alerts + alert_triggers + ai_analysis_jobs
            + backtest_runs + backtest_trades + drawing_tools
        )

        if total > 0:
            logger.info(
                f"purge_symbol_from_database({symbol}): "
                f"bars={bars}, signals={signals}, quotes={quotes}, "
                f"market_status={market_status}, alerts={alerts}, "
                f"alert_triggers={alert_triggers}, ai_jobs={ai_analysis_jobs}, "
                f"backtest_runs={backtest_runs}, backtest_trades={backtest_trades}, "
                f"drawing_tools={drawing_tools} → total={total}"
            )

        return PurgeResult(
            symbol=symbol,
            bars=bars,
            signals=signals,
            quotes=quotes,
            market_status=market_status,
            alerts=alerts,
            alert_triggers=alert_triggers,
            ai_analysis_jobs=ai_analysis_jobs,
            backtest_runs=backtest_runs,
            backtest_trades=backtest_trades,
            drawing_tools=drawing_tools,
            total=total,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def purge_symbol_from_database_safe(symbol: str) -> PurgeResult:
    """``purge_symbol_from_database`` wrapped in a try/except.

    Logs a warning on failure but never raises. Use this from the API layer
    so a purge failure doesn't crash the HTTP response.
    """
    if not symbol:
        return PurgeResult(
            symbol="", bars=0, signals=0, quotes=0, market_status=0,
            alerts=0, alert_triggers=0, ai_analysis_jobs=0,
            backtest_runs=0, backtest_trades=0, drawing_tools=0, total=0,
        )
    try:
        return purge_symbol_from_database(symbol)
    except Exception as e:
        logger.warning(
            f"purge_symbol_from_database({symbol}) failed: {e} — "
            f"data may be orphaned"
        )
        return PurgeResult(
            symbol=symbol.upper(), bars=0, signals=0, quotes=0,
            market_status=0, alerts=0, alert_triggers=0,
            ai_analysis_jobs=0, backtest_runs=0, backtest_trades=0,
            drawing_tools=0, total=0,
        )
