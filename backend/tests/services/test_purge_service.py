"""
Tests for backend.services.purge_service.

Verifies that ``purge_symbol_from_database`` removes every per-symbol row
from every table that has a symbol column.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest

from backend.database import SessionLocal
from backend.models import (
    AIAnalysisJob,
    Alert,
    AlertTrigger,
    BackfillJob,
    BacktestRun,
    BarModel,
    DrawingTool,
    HistoricalSignal,
    MarketStatusModel,
    QuoteModel,
)
from backend.services.purge_service import (
    purge_symbol_from_database,
    purge_symbol_from_database_safe,
)

logger = logging.getLogger(__name__)


# Each test gets a unique symbol so we never collide with a previous run.
SYM_PREFIX = "ZZPURGE"


def _unique_symbol() -> str:
    """Generate a unique test symbol so tests don't conflict."""
    import uuid
    return f"{SYM_PREFIX}{uuid.uuid4().hex[:6].upper()}"


def _insert_bar(db, symbol: str, timeframe: str = "1m", offset_min: int = 0) -> BarModel:
    bar = BarModel(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.utcnow() - timedelta(minutes=offset_min),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        provider="test",
        data_status="live",
    )
    db.add(bar)
    db.flush()
    return bar


def _insert_signal(db, symbol: str, timeframe: str = "1m") -> HistoricalSignal:
    sig = HistoricalSignal(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.utcnow(),
        trend_state="bullish",
        trend_score=65.0,
        price=100.0,
    )
    db.add(sig)
    db.flush()
    return sig


def _insert_quote(db, symbol: str) -> QuoteModel:
    q = QuoteModel(
        symbol=symbol,
        price=100.0,
        bid=99.5,
        ask=100.5,
        volume=100,
        timestamp=datetime.utcnow(),
        provider="test",
        data_status="live",
    )
    db.add(q)
    db.flush()
    return q


def _insert_market_status(db, symbol: str) -> MarketStatusModel:
    ms = MarketStatusModel(
        symbol=symbol,
        is_open=True,
        next_open=datetime.utcnow() + timedelta(hours=1),
        next_close=datetime.utcnow() + timedelta(hours=7),
        timezone="America/New_York",
        provider="test",
        timestamp=datetime.utcnow(),
    )
    db.add(ms)
    db.flush()
    return ms


def _insert_alert(db, symbol: str) -> Alert:
    a = Alert(
        name=f"test alert {symbol}",
        symbol=symbol,
        condition_type="PRICE_ABOVE",
        parameter="200",
        is_enabled=True,
    )
    db.add(a)
    db.flush()
    return a


def _insert_alert_trigger(db, symbol: str, alert: Alert) -> AlertTrigger:
    t = AlertTrigger(
        alert_id=alert.id,
        symbol=symbol,
        triggered_at=datetime.utcnow(),
        observed_value="201.5",
        message="price crossed above 200",
    )
    db.add(t)
    db.flush()
    return t


def _insert_ai_job(db, symbol: str) -> AIAnalysisJob:
    job = AIAnalysisJob(
        job_id=f"test-{symbol}-{datetime.utcnow().timestamp()}",
        symbol=symbol,
        timeframe="1d",
        status="finished",
        result='{"summary": "ok"}',
    )
    db.add(job)
    db.flush()
    return job


def _insert_backfill_job(db, symbol: str) -> BackfillJob:
    import uuid
    job = BackfillJob(
        job_id=f"backfill:{symbol}:{uuid.uuid4().hex[:8]}",
        symbol=symbol,
        status="completed",
        tier1_written=10,
        tier2_written=5,
        tier3_written=2,
    )
    db.add(job)
    db.flush()
    return job


def _insert_backtest_run(db, symbol: str) -> BacktestRun:
    run = BacktestRun(
        symbol=symbol,
        timeframe="1d",
        start_date=datetime.utcnow() - timedelta(days=30),
        end_date=datetime.utcnow(),
        signals_requested="RSI_OVERSOLD",
        status="completed",
    )
    db.add(run)
    db.flush()
    return run


def _insert_drawing(db, symbol: str) -> DrawingTool:
    d = DrawingTool(
        symbol=symbol,
        timeframe="1d",
        drawing_type="trend_line",
        start_timestamp=datetime.utcnow().isoformat(),
        start_price=100.0,
        end_timestamp=datetime.utcnow().isoformat(),
        end_price=105.0,
    )
    db.add(d)
    db.flush()
    return d


def _count_for_symbol(db, model, symbol: str) -> int:
    """Count rows for ``symbol`` in ``model`` table."""
    return db.query(model).filter(model.symbol == symbol.upper()).count()


@pytest.fixture
def test_symbol():
    """Provide a unique symbol and clean up after the test."""
    sym = _unique_symbol()
    yield sym
    # Final cleanup — purge any leftover rows in case the test failed
    # mid-insert.
    try:
        purge_symbol_from_database(sym)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_purge_removes_bars(test_symbol):
    """Bars are deleted."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_bar(db, sym, "1m", 0)
        _insert_bar(db, sym, "1m", 1)
        _insert_bar(db, sym, "1h", 0)
        db.commit()
        assert _count_for_symbol(db, BarModel, sym) == 3
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["bars"] == 3
    assert result["total"] == 3

    db = SessionLocal()
    try:
        assert _count_for_symbol(db, BarModel, sym) == 0
    finally:
        db.close()


def test_purge_removes_signals(test_symbol):
    """HistoricalSignals are deleted."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_signal(db, sym, "1m")
        _insert_signal(db, sym, "1h")
        db.commit()
        assert _count_for_symbol(db, HistoricalSignal, sym) == 2
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["signals"] == 2


def test_purge_removes_quotes_and_market_status(test_symbol):
    """Quotes and market_status rows are deleted."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_quote(db, sym)
        _insert_quote(db, sym)
        _insert_market_status(db, sym)
        db.commit()
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["quotes"] == 2
    assert result["market_status"] == 1


def test_purge_removes_alerts_and_triggers(test_symbol):
    """Alert rows and their triggers are both deleted."""
    sym = test_symbol
    db = SessionLocal()
    try:
        a1 = _insert_alert(db, sym)
        a2 = _insert_alert(db, sym)
        _insert_alert_trigger(db, sym, a1)
        _insert_alert_trigger(db, sym, a2)
        db.commit()
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["alerts"] == 2
    assert result["alert_triggers"] == 2

    db = SessionLocal()
    try:
        assert _count_for_symbol(db, Alert, sym) == 0
        assert _count_for_symbol(db, AlertTrigger, sym) == 0
    finally:
        db.close()


def test_purge_removes_ai_jobs(test_symbol):
    """AI analysis jobs are deleted."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_ai_job(db, sym)
        _insert_ai_job(db, sym)
        db.commit()
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["ai_analysis_jobs"] == 2


def test_purge_removes_backfill_jobs(test_symbol):
    """BackfillJob rows are deleted — added when the RQ-based backfill
    pipeline replaced the old dual-trigger design; previously the wiring
    (PurgeResult key, _delete_backfill_jobs) existed but nothing actually
    inserted a row and confirmed it got deleted (found via a 2026-09-08
    post-redesign completeness audit)."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_backfill_job(db, sym)
        _insert_backfill_job(db, sym)
        db.commit()
        assert _count_for_symbol(db, BackfillJob, sym) == 2
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["backfill_jobs"] == 2
    assert result["total"] == 2

    db = SessionLocal()
    try:
        assert _count_for_symbol(db, BackfillJob, sym) == 0
    finally:
        db.close()


def test_purge_removes_backtest_runs(test_symbol):
    """Backtest runs are deleted (trades via in_())."""
    sym = test_symbol
    db = SessionLocal()
    try:
        run = _insert_backtest_run(db, sym)
        # Add a trade manually since the cascade is via ORM only.
        from backend.models import BacktestTrade
        for _ in range(3):
            db.add(BacktestTrade(
                run_id=run.id,
                signal="BULLISH_TREND",
                entry_date=datetime.utcnow(),
                entry_price=100.0,
            ))
        db.commit()
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["backtest_runs"] == 1
    assert result["backtest_trades"] == 3


def test_purge_removes_drawing_tools(test_symbol):
    """Drawing tools are deleted."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_drawing(db, sym)
        _insert_drawing(db, sym)
        db.commit()
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["drawing_tools"] == 2


def test_purge_all_tables_at_once(test_symbol):
    """All tables are purged in a single transaction."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_bar(db, sym)
        _insert_signal(db, sym)
        _insert_quote(db, sym)
        _insert_market_status(db, sym)
        a = _insert_alert(db, sym)
        _insert_alert_trigger(db, sym, a)
        _insert_ai_job(db, sym)
        _insert_backfill_job(db, sym)
        run = _insert_backtest_run(db, sym)
        from backend.models import BacktestTrade
        db.add(BacktestTrade(run_id=run.id, signal="X", entry_date=datetime.utcnow(), entry_price=100.0))
        _insert_drawing(db, sym)
        db.commit()
    finally:
        db.close()

    result = purge_symbol_from_database(sym)
    assert result["bars"] == 1
    assert result["signals"] == 1
    assert result["quotes"] == 1
    assert result["market_status"] == 1
    assert result["alerts"] == 1
    assert result["alert_triggers"] == 1
    assert result["ai_analysis_jobs"] == 1
    assert result["backfill_jobs"] == 1
    assert result["backtest_runs"] == 1
    assert result["backtest_trades"] == 1
    assert result["drawing_tools"] == 1
    assert result["total"] == 11


def test_purge_is_idempotent(test_symbol):
    """Calling purge twice on the same symbol deletes nothing on the 2nd call."""
    sym = test_symbol
    db = SessionLocal()
    try:
        _insert_bar(db, sym)
        db.commit()
    finally:
        db.close()

    r1 = purge_symbol_from_database(sym)
    assert r1["bars"] == 1
    r2 = purge_symbol_from_database(sym)
    assert r2["total"] == 0


def test_purge_preserves_other_symbols(test_symbol):
    """Purging symbol A does NOT delete symbol B's rows."""
    sym_a = test_symbol
    sym_b = _unique_symbol()
    try:
        db = SessionLocal()
        try:
            _insert_bar(db, sym_a)
            _insert_bar(db, sym_b)
            db.commit()
        finally:
            db.close()

        purge_symbol_from_database(sym_a)

        db = SessionLocal()
        try:
            assert _count_for_symbol(db, BarModel, sym_a) == 0
            assert _count_for_symbol(db, BarModel, sym_b) == 1
        finally:
            db.close()
    finally:
        purge_symbol_from_database(sym_b)


def test_purge_safe_does_not_raise_on_empty(test_symbol):
    """``_safe`` variant returns zero counts on empty input without raising."""
    result = purge_symbol_from_database_safe("")
    assert result["total"] == 0
    assert result["symbol"] == ""


def test_purge_safe_handles_unknown_table_gracefully(monkeypatch, test_symbol):
    """If a per-table delete raises, the safe variant returns 0 for that table
    and continues to the next one.

    We simulate by monkey-patching one of the deleters to raise.
    """
    from backend.services import purge_service

    monkeypatch.setattr(
        purge_service, "_delete_bars",
        lambda db, symbol: (_ for _ in ()).throw(RuntimeError("simulated")),
    )

    # Safe variant should not raise; the failed table returns 0.
    result = purge_symbol_from_database_safe(test_symbol)
    assert result["bars"] == 0
