"""
Market data models for MarketLens
"""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, field_serializer

from backend.utils.timezone import format_edt_iso


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

    @field_serializer("timestamp")
    def _ser_ts(self, ts: datetime) -> str:
        # Always emit with explicit EDT/EST offset.
        return format_edt_iso(ts)

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
    # Phase 3.1: 'raw' = from provider, 'resampled' = computed from 1m at read time.
    # Only present when the source is known. Default None for backward compat with
    # callers that don't send this field.
    source: str | None = None

    @field_serializer("timestamp")
    def _ser_ts(self, ts: datetime) -> str:
        # Always emit with explicit EDT/EST offset so JavaScript parses
        # the value correctly regardless of browser timezone.
        return format_edt_iso(ts)

class MarketStatus(BaseModel):
    symbol: str
    is_open: bool
    next_open: datetime | None = None
    next_close: datetime | None = None
    timezone: str
    provider: str
    timestamp: datetime

    @field_serializer("timestamp", "next_open", "next_close")
    def _ser_ny(self, ts: datetime | None) -> str | None:
        # Emit as America/New_York ISO with explicit offset (e.g. -04:00 / -05:00)
        # so JavaScript parses it correctly regardless of browser timezone.
        return format_edt_iso(ts)

class ProviderStatus(BaseModel):
    provider_name: str
    is_healthy: bool
    latency_ms: float | None = None
    rate_limit_remaining: int | None = None
    last_success: datetime | None = None
    error_message: str | None = None
    timestamp: datetime
    # v2.2 — circuit breaker observability.
    circuit_breaker_state: str = "CLOSED"
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    # Cumulative error_count for the last reporting window. Useful for
    # the health endpoint to surface a single "error_count" alongside
    # the running breaker stats above.
    error_count: int = 0
    last_error: str | None = None

    @field_serializer("timestamp", "last_success")
    def _ser_ny(self, ts: datetime | None) -> str | None:
        # Emit as America/New_York ISO with explicit offset (e.g. -04:00 / -05:00)
        # so JavaScript parses it correctly regardless of browser timezone.
        return format_edt_iso(ts)

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
