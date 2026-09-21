"""
Pydantic models for Finnhub API responses (v2.2).

These models represent Finnhub-specific data that is not available from
Yahoo Finance or Webull: company profile, key metrics, financials,
news, analyst recommendations, and insider sentiment.
"""

from datetime import datetime

from pydantic import BaseModel


class CompanyProfile(BaseModel):
    """Company profile from Finnhub /stock/profile2.

    Returns company identity information: country of incorporation,
    exchange of listing, industry classification, logo URL, etc.
    """

    country: str | None = None
    currency: str | None = None
    exchange: str | None = None
    finnhub_industry: str | None = None  # Finnhub's industry field (finnhubIndustry in JSON)
    ipo: str | None = None
    logo: str | None = None
    market_capitalization: float | None = None
    name: str | None = None
    ticker: str | None = None
    weburl: str | None = None


class CompanyMetrics(BaseModel):
    """Key financial metrics from Finnhub /stock/metric.

    Valuation ratios, per-share data, margins, and analyst consensus.
    """

    symbol: str
    # Valuation
    pe_basic_eps: float | None = None
    pe_diluted_eps: float | None = None
    peg: float | None = None
    # Dividends
    dividend_yield_annual: float | None = None
    # Price
    beta: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    # Per-share
    eps_basic: float | None = None
    eps_diluted: float | None = None
    book_value_per_share: float | None = None
    cash_per_share: float | None = None
    debt_equity: float | None = None
    # Margins
    gross_margin: float | None = None
    net_margin: float | None = None
    operating_margin: float | None = None
    # Shares
    shares_outstanding: int | None = None
    # Analyst
    target_mean_price: float | None = None
    target_high_price: float | None = None
    target_low_price: float | None = None
    recommendation: str | None = None  # buy/hold/sell
    recommendation_count: int | None = None


class CompanyFinancials(BaseModel):
    """Financial data from Finnhub /stock/financials.

    Aggregated income statement, balance sheet, and cash flow data.
    """

    symbol: str
    # Income statement
    total_revenue: float | None = None
    cost_of_revenue: float | None = None
    gross_profit: float | None = None
    operating_expense: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_basic: float | None = None
    eps_diluted: float | None = None
    # Balance sheet
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    # Cash flow
    operating_cash_flow: float | None = None
    investing_cash_flow: float | None = None
    financing_cash_flow: float | None = None
    free_cash_flow: float | None = None


class NewsItem(BaseModel):
    """A single news article from Finnhub /company-news or /news.

    Used for both company-specific news and general market news.
    """

    id: int
    symbol: str | None = None  # Populated for company-news; None for market-news
    category: str | None = None
    datetime: datetime
    headline: str
    image: str | None = None
    related: str | None = None  # Comma-separated related tickers
    source: str | None = None
    summary: str | None = None
    url: str | None = None


class AnalystRecommendation(BaseModel):
    """Analyst consensus from Finnhub /stock/recommendation.

    Represents a single period's analyst ratings distribution.
    """

    symbol: str
    buy: int
    hold: int
    sell: int
    strong_buy: int
    strong_sell: int
    period: str  # e.g. "2024-Q1"
    buy_pct: float | None = None  # Computed: buy / total
    sell_pct: float | None = None  # Computed: sell / total


class InsiderSentiment(BaseModel):
    """Insider sentiment from Finnhub /stock/insider-sentiment.

    Aggregated insider trading data per month.
    """

    symbol: str
    name: str | None = None
    sector: str | None = None
    market_cap: float | None = None
    change: float | None = None  # Shares bought/sold net (positive=buy, negative=sell)
    sentiment: float | None = None  # -1 to 1 (buy/sell weighted)
    year: int
    month: int


class StockSymbol(BaseModel):
    """A single stock symbol from Finnhub /stock/symbol."""

    description: str | None = None
    display_symbol: str | None = None
    symbol: str | None = None
    type: str | None = None
