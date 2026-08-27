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

    # Composite indexes for common queries
    __table_args__ = (
        Index('ix_bars_symbol_timeframe_timestamp', 'symbol', 'timeframe', 'timestamp'),
        Index('ix_bars_provider_symbol', 'provider', 'symbol'),
        Index('ix_bars_timeframe_timestamp', 'timeframe', 'timestamp'),
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