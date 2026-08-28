"""
Market data models for MarketLens
"""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class DataStatus(StrEnum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    HISTORICAL = "HISTORICAL"
    STALE = "STALE"
    ERROR = "ERROR"
    # Phase 4 additions — emitted by TimeframeEngine for data-quality issues.
    GAP = "GAP"                  # expected bar missing (gap between consecutive candles)
    INCOMPLETE = "INCOMPLETE"    # bar arrived with fewer ticks than expected
    DUPLICATE = "DUPLICATE"      # tick with same (timeframe, timestamp) seen twice

class Quote(BaseModel):
    symbol: str
    price: float
    timestamp: datetime
    provider: str
    data_status: DataStatus
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None

class Bar(BaseModel):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    timeframe: str  # e.g., "1m", "5m", "1h", "1d"
    provider: str
    data_status: DataStatus

class MarketStatus(BaseModel):
    symbol: str
    is_open: bool
    next_open: datetime | None = None
    next_close: datetime | None = None
    timezone: str
    provider: str
    timestamp: datetime

class ProviderStatus(BaseModel):
    provider_name: str
    is_healthy: bool
    latency_ms: float | None = None
    rate_limit_remaining: int | None = None
    last_success: datetime | None = None
    error_message: str | None = None
    timestamp: datetime

class ProviderCapabilities(BaseModel):
    provider_name: str
    supports_historical_bars: bool = True
    supports_latest_quote: bool = True
    supports_latest_bar: bool = True
    supports_batch_quotes: bool = False
    supports_market_status: bool = True
    min_timeframe: str = "1m"
    max_timeframe: str = "1d"
    max_history_range: str = "5y"
