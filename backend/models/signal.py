"""
SQLAlchemy models for historical signal storage.

Each row records a snapshot of the market state at a specific bar close:
trend, regime, RS, sector alignment, volume, and later-computed
forward outcomes (returns, MFE, MAE).

Outcomes are NOT computed at insert time — a background job fills them
in once the required future bars exist, ensuring no look-ahead bias.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)

from backend.database import Base


class HistoricalSignal(Base):
    """One signal snapshot per (symbol, timeframe, bar_close).

    Persisted by SignalRecorder on every bar close. Forward outcomes
    (returns, MFE, MAE) are null at insert and backfilled by
    _backfill_outcomes() once enough future bars exist.
    """
    __tablename__ = "historical_signals"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, index=True)

    # Price at the time of the signal
    price = Column(Float, nullable=True)

    # Trend engine snapshot
    trend_score = Column(Float, nullable=True)        # -100..+100
    trend_state = Column(String(20), nullable=True)  # "bullish" | "bearish" | "neutral"
    strength = Column(Float, nullable=True)           # 0.0..1.0

    # Context
    market_regime = Column(String(20), nullable=True)    # "risk_on" | "risk_off" | "neutral" | "transition"
    relative_strength = Column(String(30), nullable=True)  # "strong_outperformer" | ...
    sector_alignment = Column(Float, nullable=True)       # 0.0..1.0

    # Bar context
    volume_state = Column(String(20), nullable=True)   # "expansion" | "normal" | "contraction"
    momentum = Column(Float, nullable=True)             # short-term momentum score
    structure = Column(String(20), nullable=True)      # TrendClassification value

    # Configuration
    confidence_inputs = Column(Text, nullable=True)    # JSON string of raw indicator values
    strategy_version = Column(String(20), nullable=True, default="v1")
    data_quality = Column(String(20), nullable=True)  # "good" | "stale" | "low_confidence"

    # Forward outcomes — null until backfilled
    return_5b = Column(Float, nullable=True)
    return_10b = Column(Float, nullable=True)
    return_20b = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)   # Maximum Favorable Excursion (best % gain while in trade)
    mae = Column(Float, nullable=True)   # Maximum Adverse Excursion (worst % loss while in trade)

    # Index for outcome backfill queries: find signals that need outcomes computed.
    _outcome_missing = Column(
        "outcome_computed",
        Boolean,
        nullable=True,
        default=False,
        index=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return (f"<HistoricalSignal(id={self.id}, symbol={self.symbol} "
                f"@{self.timestamp} tf={self.timeframe} score={self.trend_score:.1f})>")
