"""
Finnhub REST API service for company data, news, and analyst sentiment (v2.2).

This is NOT a ``MarketDataProvider`` — it exposes Finnhub endpoints that
the provider chain can't represent (company fundamentals, news, etc.).
Routes in ``backend/api/finnhub/router.py`` call into this service.

**Free tier:** 30 req/sec rate limit (IP-based, no API key required).
"""
import logging
import os
from datetime import date, datetime, timezone

import requests

from backend.config.settings import settings as _settings
from backend.models.finnhub import (
    AnalystRecommendation,
    CompanyFinancials,
    CompanyMetrics,
    CompanyProfile,
    InsiderSentiment,
    NewsItem,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"


class FinnhubService:
    """Wrapper around the Finnhub REST API for company data and news."""

    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        self.api_key = api_key or _settings.finnhub.api_key or os.getenv("FINNHUB_API_KEY", "")
        self.timeout = timeout or _settings.finnhub.request_timeout

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Authenticated GET. Rate limits and other HTTP errors → RuntimeError."""
        url = f"{_BASE_URL}/{endpoint}"
        all_params: dict = dict(params) if params else {}
        if self.api_key:
            all_params["token"] = self.api_key
        try:
            r = requests.get(url, params=all_params, timeout=self.timeout)
        except requests.RequestException as e:
            raise RuntimeError(f"Finnhub request failed: {e}") from e

        if r.status_code == 429:
            raise RuntimeError("Finnhub rate limited (HTTP 429)")
        if r.status_code == 403:
            raise RuntimeError("Finnhub forbidden — check API key (HTTP 403)")
        if not r.ok:
            raise RuntimeError(
                f"Finnhub HTTP {r.status_code} for {endpoint}: {r.text[:200]}"
            )

        data = r.json()
        if not data:
            raise ValueError(f"Finnhub returned empty response for {endpoint}")
        return data

    # ---------------------------------------------------------------- company

    def get_company_profile(self, symbol: str) -> CompanyProfile:
        """Fetch company profile from /stock/profile2."""
        data = self._get("stock/profile2", {"symbol": symbol})
        if not data or not data.get("ticker"):
            raise ValueError(f"No company profile for {symbol}")
        return CompanyProfile(
            country=data.get("country"),
            currency=data.get("currency"),
            exchange=data.get("exchange"),
            finnhub_industry=data.get("finnhubIndustry"),
            ipo=data.get("ipo"),
            logo=data.get("logo"),
            market_capitalization=data.get("marketCapitalization"),
            name=data.get("name"),
            ticker=data.get("ticker"),
            weburl=data.get("weburl"),
        )

    def get_company_metrics(self, symbol: str) -> CompanyMetrics:
        """Fetch key financial metrics from /stock/metric."""
        data = self._get("stock/metric", {"symbol": symbol})
        if not data or not data.get("metric"):
            raise ValueError(f"No metrics for {symbol}")
        m = data.get("metric", {})

        return CompanyMetrics(
            symbol=symbol.upper(),
            pe_basic_eps=m.get("peBasicExtraTTM"),
            pe_diluted_eps=m.get("peDilutedExtraTTM"),
            peg=m.get("peg"),
            dividend_yield_annual=m.get("dividendYieldIndicatedAnnual"),
            beta=m.get("beta"),
            high_52w=m.get("52WeekHigh"),
            low_52w=m.get("52WeekLow"),
            eps_basic=m.get("epsBasicExtraTTM"),
            eps_diluted=m.get("epsDilutedExtraTTM"),
            book_value_per_share=m.get("bookValuePerShareQuarterly"),
            cash_per_share=m.get("cashPerSharePerShareQuarterly"),
            debt_equity=m.get("totalDebtToEquityQuarterly"),
            gross_margin=m.get("grossMarginQuarterly"),
            net_margin=m.get("netMarginQuarterly"),
            operating_margin=m.get("operatingMarginQuarterly"),
            shares_outstanding=m.get("shareOutstandingQuarterly"),
            target_mean_price=m.get("priceTargetMean"),
            target_high_price=m.get("priceTargetHigh"),
            target_low_price=m.get("priceTargetLow"),
            recommendation=str(m.get("recommendationMean")) if m.get("recommendationMean") is not None else None,
            recommendation_count=m.get("numberOfAnalystOpinions"),
        )

    def get_company_financials(self, symbol: str) -> CompanyFinancials:
        """Fetch financials from /stock/financials."""
        data = self._get("stock/financials", {"symbol": symbol, "statement": "ic"})
        # ic = income statement; bs = balance sheet; cf = cash flow
        if not data or not data.get("data"):
            # Try the other statements if income statement is missing
            bs_data = self._safe_get("stock/financials", {"symbol": symbol, "statement": "bs"})
            cf_data = self._safe_get("stock/financials", {"symbol": symbol, "statement": "cf"})
            return self._build_financials(symbol, {}, bs_data, cf_data)
        income_rows = data.get("data", [])
        bs_data = self._safe_get("stock/financials", {"symbol": symbol, "statement": "bs"})
        cf_data = self._safe_get("stock/financials", {"symbol": symbol, "statement": "cf"})
        return self._build_financials(symbol, income_rows, bs_data, cf_data)

    def _safe_get(self, endpoint: str, params: dict) -> dict:
        """GET that returns empty dict on error instead of raising."""
        try:
            return self._get(endpoint, params)
        except Exception as e:
            logger.debug("Finnhub %s unavailable: %s", endpoint, e)
            return {}

    def _build_financials(
        self,
        symbol: str,
        income_rows: list,
        bs_data: dict,
        cf_data: dict,
    ) -> CompanyFinancials:
        """Aggregate income statement, balance sheet, and cash flow data."""
        # Income statement — use the most recent period (first row)
        income = income_rows[0] if income_rows else {}
        bs_rows = (bs_data or {}).get("data", [])
        bs = bs_rows[0] if bs_rows else {}
        cf_rows = (cf_data or {}).get("data", [])
        cf = cf_rows[0] if cf_rows else {}

        # Free cash flow = operating cash flow - capex (if capex available)
        ocf = cf.get("operatingCashFlow")
        capex = cf.get("capitalExpenditure")
        free_cash_flow = None
        if ocf is not None and capex is not None:
            free_cash_flow = float(ocf) - float(capex)

        return CompanyFinancials(
            symbol=symbol.upper(),
            total_revenue=income.get("revenue"),
            cost_of_revenue=income.get("costOfRevenue"),
            gross_profit=income.get("grossProfit"),
            operating_expense=income.get("operatingExpenses"),
            operating_income=income.get("operatingIncome"),
            net_income=income.get("netIncome"),
            eps_basic=income.get("eps"),
            eps_diluted=income.get("epsDiluted"),
            total_assets=bs.get("totalAssets"),
            total_liabilities=bs.get("totalLiabilities"),
            total_equity=bs.get("totalEquity"),
            operating_cash_flow=cf.get("operatingCashFlow"),
            investing_cash_flow=cf.get("investingCashFlow"),
            financing_cash_flow=cf.get("financingCashFlow"),
            free_cash_flow=free_cash_flow,
        )

    # ---------------------------------------------------------------- analysts

    def get_analyst_recommendations(self, symbol: str) -> list[AnalystRecommendation]:
        """Fetch analyst buy/hold/sell consensus from /stock/recommendation."""
        rows = self._get("stock/recommendation", {"symbol": symbol})
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"No recommendations for {symbol}")

        recommendations: list[AnalystRecommendation] = []
        for row in rows:
            buy = int(row.get("buy", 0))
            hold = int(row.get("hold", 0))
            sell = int(row.get("sell", 0))
            strong_buy = int(row.get("strongBuy", 0))
            strong_sell = int(row.get("strongSell", 0))
            total = buy + hold + sell + strong_buy + strong_sell
            buy_pct = (buy + strong_buy) / total if total > 0 else None
            sell_pct = (sell + strong_sell) / total if total > 0 else None
            period = row.get("period", "")
            recommendations.append(
                AnalystRecommendation(
                    symbol=symbol.upper(),
                    buy=buy,
                    hold=hold,
                    sell=sell,
                    strong_buy=strong_buy,
                    strong_sell=strong_sell,
                    period=period,
                    buy_pct=buy_pct,
                    sell_pct=sell_pct,
                )
            )
        return recommendations

    # ---------------------------------------------------------------- insider

    def get_insider_sentiment(
        self, symbol: str, from_date: date, to_date: date
    ) -> list[InsiderSentiment]:
        """Fetch insider trading sentiment from /stock/insider-sentiment."""
        data = self._get(
            "stock/insider-sentiment",
            {
                "symbol": symbol,
                "from": from_date.strftime("%Y-%m-%d"),
                "to": to_date.strftime("%Y-%m-%d"),
            },
        )
        rows = data.get("data", [])
        if not rows:
            return []
        sentiments: list[InsiderSentiment] = []
        for row in rows:
            sentiments.append(
                InsiderSentiment(
                    symbol=symbol.upper(),
                    name=None,  # Not provided by Finnhub at this endpoint
                    sector=None,
                    market_cap=None,
                    change=row.get("change"),
                    sentiment=row.get("mspr"),
                    year=int(row.get("year", 0)),
                    month=int(row.get("month", 0)),
                )
            )
        return sentiments

    # ---------------------------------------------------------------- news

    def get_company_news(
        self, symbol: str, from_date: date, to_date: date
    ) -> list[NewsItem]:
        """Fetch company-specific news from /company-news."""
        rows = self._get(
            "company-news",
            {
                "symbol": symbol,
                "from": from_date.strftime("%Y-%m-%d"),
                "to": to_date.strftime("%Y-%m-%d"),
            },
        )
        if not isinstance(rows, list):
            return []
        return [self._parse_news_item(item, default_symbol=symbol) for item in rows]

    def get_market_news(self, category: str = "general") -> list[NewsItem]:
        """Fetch general market news from /news.

        ``category`` is one of: general, forex, crypto, merger.
        """
        rows = self._get("news", {"category": category})
        if not isinstance(rows, list):
            return []
        return [self._parse_news_item(item, default_symbol=None) for item in rows]

    @staticmethod
    def _parse_news_item(item: dict, default_symbol: str | None) -> NewsItem:
        """Convert a Finnhub news JSON object into a NewsItem."""
        ts_epoch = int(item.get("datetime", 0))
        return NewsItem(
            id=int(item.get("id", 0)),
            symbol=item.get("related") or default_symbol,
            category=item.get("category"),
            datetime=datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
            headline=item.get("headline", ""),
            image=item.get("image"),
            related=item.get("related"),
            source=item.get("source"),
            summary=item.get("summary"),
            url=item.get("url"),
        )

    # ---------------------------------------------------------------- peers

    def get_peers(self, symbol: str) -> list[str]:
        """Fetch industry peer symbols from /stock/peers."""
        peers = self._safe_get("stock/peers", {"symbol": symbol})
        if not isinstance(peers, list):
            return []
        return [str(p) for p in peers]


# Module-level singleton — routes import this directly
finnhub_service = FinnhubService()
