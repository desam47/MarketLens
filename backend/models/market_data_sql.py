"""
SQLAlchemy models for persisting market data
"""

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text

from backend.database import Base


class QuoteModel(Base):
    """SQLAlchemy model for storing market quotes"""
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), index=True, nullable=False)
    price = Column(Float, nullable=False)
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    volume = Column(Integer, nullable=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    data_status = Column(String(20), nullable=False)

    # Composite indexes for common queries
    __table_args__ = (
        Index('ix_quotes_symbol_timestamp', 'symbol', 'timestamp'),
        Index('ix_quotes_provider_symbol', 'provider', 'symbol'),
    )

    def __repr__(self):
        return f"<QuoteModel(symbol='{self.symbol}', price={self.price}, timestamp='{self.timestamp}')>"


class BarModel(Base):
    """SQLAlchemy model for storing OHLCV bars/candles"""
    __tablename__ = "bars"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), index=True, nullable=False)
    timeframe = Column(String(10), nullable=False, index=True)  # 1m, 5m, 1h, 1d, etc.
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)  # Bar close time
    provider = Column(String(50), nullable=False)
    data_status = Column(String(20), nullable=False)
    # Phase 3.1: 'raw' for rows ingested from a provider, 'resampled' for
    # rows derived from 1m at read time. Higher-TF rows are read-time only
    # and never persisted, so existing rows default to 'raw'.
    source = Column(String(20), nullable=False, server_default="raw")
    # Equity session classification at ingest time — 'premarket' (04:00-09:30
    # ET), 'regular' (09:30-16:00 ET), or 'after_hours' (16:00-20:00 ET),
    # matching backend.engines.market_calendar.SessionType. Only meaningful
    # for 1m rows fetched with extended-hours enabled (WebullProvider); every
    # other row defaults to 'regular', which matches actual historical
    # behavior (all ingestion was RTH-only before extended-hours support was
    # added). Sub-hour resampling (_resample_and_upsert) filters on this so
    # 2m/3m/5m/15m/30m/1h/4h/1d/1wk stay regular-session-only even once
    # premarket/after-hours 1m rows exist in the table.
    session = Column(String(20), nullable=False, server_default="regular")

    # Composite indexes for common queries.
    # ix_bars_symbol_timeframe_timestamp is UNIQUE so concurrent upsert_bars
    # calls cannot create duplicate bars for the same (symbol, timeframe, timestamp).
    # Phase 3.1: the index is named ``ix_bars_source_timeframe`` (column
    # order: source first, then timeframe) so the most selective predicate
    # — filtering by source='raw' or 'resampled' — is the prefix. Renamed
    # from the previous ``ix_bars_timeframe_source`` order. See Alembic
    # migration ``rename_bars_index_to_source_timeframe``.
    __table_args__ = (
        Index('ix_bars_symbol_timeframe_timestamp', 'symbol', 'timeframe', 'timestamp', unique=True),
        Index('ix_bars_provider_symbol', 'provider', 'symbol'),
        Index('ix_bars_timeframe_timestamp', 'timeframe', 'timestamp'),
        Index('ix_bars_source_timeframe', 'source', 'timeframe'),
    )

    def __repr__(self):
        return f"<BarModel(symbol='{self.symbol}', timeframe='{self.timeframe}', open={self.open}, close={self.close}, timestamp='{self.timestamp}')>"


class MarketStatusModel(Base):
    """SQLAlchemy model for storing market status information"""
    __tablename__ = "market_status"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), index=True, nullable=False)
    is_open = Column(Boolean, nullable=False)
    next_open = Column(DateTime, nullable=True)
    next_close = Column(DateTime, nullable=True)
    timezone = Column(String(50), nullable=False)
    provider = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)

    # Indexes
    __table_args__ = (
        Index('ix_market_status_symbol_timestamp', 'symbol', 'timestamp'),
    )

    def __repr__(self):
        return f"<MarketStatusModel(symbol='{self.symbol}', is_open={self.is_open}, timestamp='{self.timestamp}')>"


class ProviderStatusModel(Base):
    """SQLAlchemy model for storing provider health/status information"""
    __tablename__ = "provider_status"

    id = Column(Integer, primary_key=True, index=True)
    provider_name = Column(String(50), nullable=False, index=True)
    is_healthy = Column(Boolean, nullable=False)
    latency_ms = Column(Float, nullable=True)
    rate_limit_remaining = Column(Integer, nullable=True)
    last_success = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    timestamp = Column(DateTime, nullable=False, index=True)

    # Indexes
    __table_args__ = (
        Index('ix_provider_status_provider_timestamp', 'provider_name', 'timestamp'),
    )

    def __repr__(self):
        return f"<ProviderStatusModel(provider='{self.provider_name}', healthy={self.is_healthy}, timestamp='{self.timestamp}')>"
