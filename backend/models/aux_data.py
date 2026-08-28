"""
Auxiliary data models for Phase 18 — News, Fundamentals, and Options.

These are kept separate from core market_data.py so the trend engine
never pulls them in transitively. Each group has its own Pydantic
models and a lightweight provider-status envelope.
"""
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OptionsType(StrEnum):
    CALL = "call"
    PUT = "put"


class UnusualActivity(StrEnum):
    """Options activity intensity relative to 30-day average."""
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    UNUSUAL = "unusual"


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

class NewsItem(BaseModel):
    """A single news article or headline for a symbol."""
    headline: str
    source: str
    timestamp: datetime
    symbol: str
    relevance: float = Field(ge=0.0, le=1.0, description="Relevance score 0–1")


class NewsResponse(BaseModel):
    """Envelope returned by NewsProvider.get_news()."""
    symbol: str
    items: list[NewsItem]
    provider: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------

class FundamentalsItem(BaseModel):
    """Fundamental snapshot for a single symbol.

    Fields that cannot be determined from the upstream provider are
    left as ``None`` rather than defaulting to sentinel values.
    All financial figures are in raw units (USD) or percentages;
    the frontend is responsible for formatting.
    """
    symbol: str

    # Company identity
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None

    # Size
    market_cap: float | None = Field(default=None, ge=0, description="USD")
    shares_outstanding: float | None = Field(default=None, ge=0)

    # Income statement
    revenue: float | None = Field(default=None, ge=0, description="USD")
    net_income: float | None = Field(default=None, ge=0, description="USD")
    eps: float | None = Field(default=None, description="Earnings per share (TTM)")
    eps_growth: float | None = Field(
        default=None,
        description="YoY EPS growth rate as a decimal (e.g. 0.15 = 15%)"
    )

    # Valuation
    pe_ratio: float | None = Field(default=None, ge=0, description="Trailing P/E")
    forward_pe: float | None = Field(default=None, ge=0)
    peg_ratio: float | None = Field(default=None, ge=0)
    price_to_book: float | None = Field(default=None, ge=0)
    price_to_sales: float | None = Field(default=None, ge=0)

    # Balance sheet
    total_debt: float | None = Field(default=None, ge=0, description="USD")
    total_cash: float | None = Field(default=None, ge=0, description="USD")
    debt_to_equity: float | None = Field(default=None, ge=0)
    current_ratio: float | None = Field(default=None, ge=0)

    # Dividends
    dividend_yield: float | None = Field(
        default=None, ge=0,
        description="Annual dividend / current price as a decimal"
    )
    payout_ratio: float | None = Field(default=None, ge=0, le=1.0)

    # Ownership
    institutional_ownership: float | None = Field(
        default=None, ge=0, le=1.0,
        description="Fraction of shares held by institutions"
    )
    insider_ownership: float | None = Field(
        default=None, ge=0, le=1.0,
        description="Fraction of shares held by insiders"
    )
    short_float: float | None = Field(
        default=None, ge=0, le=1.0,
        description="Fraction of float sold short"
    )

    # Analyst consensus
    analyst_target: float | None = Field(default=None, ge=0, description="USD")
    recommendation: str | None = Field(
        default=None,
        description="One of: strong_buy, buy, hold, sell, strong_sell"
    )

    # Beta / volatility
    beta: float | None = Field(default=None, ge=0)
    week_52_high: float | None = Field(default=None, ge=0)
    week_52_low: float | None = Field(default=None, ge=0)


class FundamentalsResponse(BaseModel):
    """Envelope returned by FundamentalProvider.get_fundamentals()."""
    symbol: str
    data: FundamentalsItem
    provider: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

class OptionContract(BaseModel):
    """A single options contract (call or put)."""
    strike: float = Field(ge=0)
    expiration: str = Field(description="YYYY-MM-DD")
    option_type: OptionsType
    bid: float | None = Field(default=None, ge=0)
    ask: float | None = Field(default=None, ge=0)
    last: float | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    implied_volatility: float | None = Field(
        default=None, ge=0,
        description="Decimal IV (e.g. 0.30 = 30%)"
    )
    delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    gamma: float | None = Field(default=None, ge=-1.0, le=1.0)
    theta: float | None = Field(default=None)
    vega: float | None = Field(default=None)
    rho: float | None = Field(default=None)
    in_the_money: bool = False


class OptionsChain(BaseModel):
    """Full options chain for one expiration date."""
    symbol: str
    expiration: str = Field(description="YYYY-MM-DD")
    calls: list[OptionContract] = Field(default_factory=list)
    puts: list[OptionContract] = Field(default_factory=list)
    put_call_ratio: float | None = Field(
        default=None, ge=0,
        description="Total put OI / total call OI"
    )
    total_call_volume: int | None = Field(default=None, ge=0)
    total_put_volume: int | None = Field(default=None, ge=0)
    avg_iv_call: float | None = Field(default=None, ge=0)
    avg_iv_put: float | None = Field(default=None, ge=0)
    unusual_activity: UnusualActivity = UnusualActivity.NORMAL


class OptionsResponse(BaseModel):
    """Envelope returned by OptionsProvider.get_options()."""
    symbol: str
    chains: list[OptionsChain] = Field(
        default_factory=list,
        description="One chain per available expiration date"
    )
    expirations: list[str] = Field(
        default_factory=list,
        description="All available expiration dates (YYYY-MM-DD)"
    )
    near_term_iv: float | None = Field(
        default=None, ge=0,
        description="Average IV of near-term (≤30 DTE) options"
    )
    iv_rank: float | None = Field(
        default=None, ge=0, le=100,
        description="IV Rank 0–100"
    )
    provider: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Provider status
# ---------------------------------------------------------------------------

class AuxProviderStatus(BaseModel):
    """Lightweight provider health envelope — mirrors ProviderStatus in market_data."""
    provider_name: str
    provider_type: Literal["news", "fundamentals", "options"]
    is_healthy: bool
    last_error: str | None = None
    last_success: datetime | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
