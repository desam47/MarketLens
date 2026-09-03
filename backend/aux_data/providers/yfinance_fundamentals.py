"""
yfinance implementation of FundamentalProvider.

Fundamentals are fetched from ``yf.Ticker.info`` which returns a flat dict
of company metrics. Field names map directly to the Yahoo Finance API
schema; unavailable fields are left as ``None``.
"""
import logging
from datetime import datetime

from backend.utils.timezone import now_ny

from backend.models.aux_data import FundamentalsItem, FundamentalsResponse

from ..provider import FundamentalProvider

logger = logging.getLogger(__name__)

# Fields we read from ``yf.Ticker.info``.
_INFO_MAPPING: list[str] = [
    "longName", "shortName", "sector", "industry",
    "marketCap", "sharesOutstanding",
    "totalRevenue", "netIncomeToCommon",
    "trailingEps", "forwardEps", "earningsGrowth", "revenueGrowth",
    "trailingPE", "forwardPE", "pegRatio", "priceToBook", "priceToSalesTrailing12Months",
    "totalDebt", "totalCash", "debtToEquity", "currentRatio",
    "dividendYield", "payoutRatio",
    "heldByInsiders", "heldByInstitutions", "shortPercentOfFloat",
    "targetMeanPrice", "recommendationKey",
    "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
]


class YFinanceFundamentalsProvider(FundamentalProvider):
    """Yahoo Finance fundamentals via yfinance."""

    def __init__(self) -> None:
        super().__init__("yfinance_fundamentals")

    def get_fundamentals(self, symbol: str) -> FundamentalsResponse:
        """Fetch fundamental snapshot for ``symbol`` via yfinance."""
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol.upper())
            info: dict = ticker.info or {}

            data = FundamentalsItem(
                symbol=symbol.upper(),
                company_name=info.get("longName") or info.get("shortName"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                market_cap=info.get("marketCap"),
                shares_outstanding=info.get("sharesOutstanding"),
                revenue=info.get("totalRevenue"),
                net_income=info.get("netIncomeToCommon"),
                eps=info.get("trailingEps"),
                eps_growth=self._safe_float(info.get("earningsGrowth")),
                forward_pe=info.get("forwardPE"),
                peg_ratio=info.get("pegRatio"),
                price_to_book=info.get("priceToBook"),
                price_to_sales=info.get("priceToSalesTrailing12Months"),
                total_debt=info.get("totalDebt"),
                total_cash=info.get("totalCash"),
                debt_to_equity=self._safe_float(info.get("debtToEquity")),
                current_ratio=info.get("currentRatio"),
                dividend_yield=self._safe_float(info.get("dividendYield")),
                payout_ratio=info.get("payoutRatio"),
                institutional_ownership=self._safe_float(info.get("heldByInstitutions")),
                insider_ownership=self._safe_float(info.get("heldByInsiders")),
                short_float=self._safe_float(info.get("shortPercentOfFloat")),
                analyst_target=info.get("targetMeanPrice"),
                recommendation=info.get("recommendationKey"),
                beta=info.get("beta"),
                week_52_high=info.get("fiftyTwoWeekHigh"),
                week_52_low=info.get("fiftyTwoWeekLow"),
            )

            # trailing P/E must come from trailingEps / price — yfinance may not
            # surface it directly.
            if data.pe_ratio is None and data.eps and data.eps > 0:
                try:
                    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
                    if current_price:
                        data.pe_ratio = round(current_price / data.eps, 2)
                except Exception:
                    pass

            self._mark_ok()
            return FundamentalsResponse(
                symbol=symbol.upper(),
                data=data,
                provider=self.name,
                timestamp=now_ny(),
            )

        except Exception as exc:
            logger.warning("YFinanceFundamentalsProvider failed for %s: %s", symbol, exc)
            self._mark_error(exc)
            return FundamentalsResponse(
                symbol=symbol.upper(),
                data=FundamentalsItem(symbol=symbol.upper()),
                provider=self.name,
                timestamp=now_ny(),
            )

    @staticmethod
    def _safe_float(value, default=None):
        """Return float or None, ignoring sentinel values like NaN."""
        if value is None:
            return default
        try:
            f = float(value)
            if f != f:  # NaN guard
                return default
            return f
        except (TypeError, ValueError):
            return default
