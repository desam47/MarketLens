"""
BacktestRun and BacktestTrade SQLAlchemy models.

A ``BacktestRun`` captures the configuration and summary metrics of a
single signal-replay backtest. Each fired signal is recorded as a
``BacktestTrade`` row holding the entry price/date plus the forward
1d/5d/20d returns so the dashboard can render a per-signal detail
view.

The extended metric set (max drawdown, Sharpe, profit factor, median
return, MFE/MAE, signal frequency, equity curve, OOS flag, and an
overfitting warning) is what lets Phase 14 go from "signal counter"
to "actual strategy evaluator". The equity curve is stored as a JSON
array of `[date, cumulative_return_pct]` pairs so the dashboard can
plot it without re-iterating bars.
"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.database import Base
from backend.utils.timezone import now_ny


class BacktestRun(Base):
    """One signal-replay backtest run.

    ``signals_requested`` is stored as a CSV of signal names (e.g.
    "RSI_OVERSOLD,MACD_BEARISH") so the run is self-describing even
    after the request payload is gone. ``status`` is one of
    ``pending`` / ``running`` / ``completed`` / ``failed``.

    Metrics beyond the v1 set (win_rate / avg_return) are nullable so
    the migration is non-breaking for runs persisted before Phase 14's
    metric expansion. ``equity_curve_json`` is a JSON-encoded list of
    ``[timestamp_iso, cumulative_return_pct]`` pairs; it's stored as
    text rather than a JSON column so SQLite can write it without a
    type adapter.
    """
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    signals_requested = Column(String(255), nullable=False)
    strategy_version = Column(String(50), nullable=True)  # e.g. "v1.0", "rsi14-macd"; enables reproducible comparisons across runs
    status = Column(String(20), nullable=False, default="pending", index=True)
    total_bars = Column(Integer, nullable=True)
    total_signals = Column(Integer, nullable=True)
    win_rate_1d = Column(Float, nullable=True)
    avg_return_1d = Column(Float, nullable=True)
    avg_return_5d = Column(Float, nullable=True)
    avg_return_20d = Column(Float, nullable=True)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=now_ny, index=True)
    completed_at = Column(DateTime, nullable=True)

    # --- Phase 14 extended metrics ---
    # Median return per window. Stored as percentages (e.g. ``1.23``).
    median_return_1d = Column(Float, nullable=True)
    median_return_5d = Column(Float, nullable=True)
    median_return_20d = Column(Float, nullable=True)
    # Max drawdown over the equity curve, expressed as a *negative*
    # percentage (e.g. ``-12.4``). None when there are no trades.
    max_drawdown = Column(Float, nullable=True)
    # Annualized Sharpe ratio (assumes 252 trading days / year). None
    # when there are fewer than 2 trades or zero return variance.
    sharpe_ratio = Column(Float, nullable=True)
    # Profit factor = gross profits / gross losses. ``inf`` is
    # represented as a very large float to fit in the column. None
    # when there are no closed trades.
    profit_factor = Column(Float, nullable=True)
    # Average MFE / MAE across all trades, expressed as percentages.
    mfe_avg = Column(Float, nullable=True)
    mae_avg = Column(Float, nullable=True)
    # Signal frequency: signals per trading day (signals_count / total_bars).
    signal_frequency = Column(Float, nullable=True)
    # JSON-encoded list of [timestamp_iso, cum_return_pct] pairs.
    equity_curve_json = Column(Text, nullable=True)
    # True when the run covers an out-of-sample test slice of a
    # walk-forward analysis; otherwise False (the default).
    out_of_sample = Column(Boolean, nullable=True, default=False)
    # Free-text warning string when the engine flags the run as
    # potentially overfit. Empty/None when the run is healthy.
    overfitting_warning = Column(String(500), nullable=True)

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

    Phase 14 adds ``mfe`` (maximum favorable excursion) and ``mae``
    (maximum adverse excursion) over the 20-bar window. These track
    the peak and trough closes relative to the entry price, as
    percentages, so the user can see how "deep" each trade went both
    ways even if the final exit was unremarkable.
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
    # Maximum favorable excursion: best close in [entry+1, entry+20]
    # relative to entry price, as a percentage.
    mfe = Column(Float, nullable=True)
    # Maximum adverse excursion: worst close in [entry+1, entry+20]
    # relative to entry price, as a percentage (negative).
    mae = Column(Float, nullable=True)

    # Phase 19 — regime tagged at entry time from the ADX+RSI bar-window
    # classifier. One of "risk_on", "risk_off", "neutral", or None when
    # regime tagging was disabled for this run.
    regime_at_entry = Column(String(20), nullable=True)

    # Relationship back to the run
    run = relationship("BacktestRun", back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<BacktestTrade(id={self.id}, run_id={self.run_id}, "
            f"signal={self.signal!r}, entry={self.entry_price})>"
        )

