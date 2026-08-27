"""
BacktestRun and BacktestTrade SQLAlchemy models.

A ``BacktestRun`` captures the configuration and summary metrics of a
single signal-replay backtest. Each fired signal is recorded as a
``BacktestTrade`` row holding the entry price/date plus the forward
1d/5d/20d returns so the dashboard can render a per-signal detail
view.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from backend.database import Base


class BacktestRun(Base):
    """One signal-replay backtest run.

    ``signals_requested`` is stored as a CSV of signal names (e.g.
    "RSI_OVERSOLD,MACD_BEARISH") so the run is self-describing even
    after the request payload is gone. ``status`` is one of
    ``pending`` / ``running`` / ``completed`` / ``failed``.
    """
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    signals_requested = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    total_bars = Column(Integer, nullable=True)
    total_signals = Column(Integer, nullable=True)
    win_rate_1d = Column(Float, nullable=True)
    avg_return_1d = Column(Float, nullable=True)
    avg_return_5d = Column(Float, nullable=True)
    avg_return_20d = Column(Float, nullable=True)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationship to trades
    trades = relationship(
        "BacktestTrade",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<BacktestRun(id={self.id}, symbol={self.symbol}, "
            f"status={self.status!r}, signals={self.signals_requested!r})>"
        )


class BacktestTrade(Base):
    """A single signal that fired during a backtest, plus forward returns.

    Forward returns are computed from bar-close-to-bar-close over the
    next 1, 5, and 20 trading days. Returns are expressed as
    percentages (e.g. ``2.34`` = +2.34%). ``None`` when the forward
    window extends past the end of available history.
    """
    __tablename__ = "backtest_trades"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"),
                    nullable=False, index=True)
    signal = Column(String(40), nullable=False)
    entry_date = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_date_1d = Column(DateTime, nullable=True)
    exit_price_1d = Column(Float, nullable=True)
    return_1d = Column(Float, nullable=True)
    exit_date_5d = Column(DateTime, nullable=True)
    exit_price_5d = Column(Float, nullable=True)
    return_5d = Column(Float, nullable=True)
    exit_date_20d = Column(DateTime, nullable=True)
    exit_price_20d = Column(Float, nullable=True)
    return_20d = Column(Float, nullable=True)

    # Relationship back to the run
    run = relationship("BacktestRun", back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<BacktestTrade(id={self.id}, run_id={self.run_id}, "
            f"signal={self.signal!r}, entry={self.entry_price})>"
        )
