"""
yfinance implementation of FundamentalProvider.

Fundamentals are fetched from ``yf.Ticker.info`` which returns a flat dict
of company metrics. Field names map directly to the Yahoo Finance API
schema; unavailable fields are left as ``None``.
"""

import logging

from pydantic import ValidationError

from backend.models.aux_data import FundamentalsItem, FundamentalsResponse
from backend.utils.timezone import now_ny

from ..provider import FundamentalProvider

logger = logging.getLogger(__name__)

# Fields we read from ``yf.Ticker.info``.
_INFO_MAPPING: list[str] = [
    "longName",
    "shortName",
    "sector",
    "industry",
    "marketCap",
    "sharesOutstanding",
    "totalRevenue",
    "netIncomeToCommon",
    "trailingEps",
    "forwardEps",
    "earningsGrowth",
    "revenueGrowth",
    "trailingPE",
    "forwardPE",
    "pegRatio",
    "priceToBook",
    "priceToSalesTrailing12Months",
    "totalDebt",
    "totalCash",
    "debtToEquity",
    "currentRatio",
    "dividendYield",
    "payoutRatio",
    # Found live 2026-09-10: these are the real yfinance .info key
    # names (verified directly against AAPL's raw info dict) — the
    # "heldBy*" names below were wrong and always missing, so
    # institutional/insider ownership was None for every symbol.
    "heldPercentInsiders",
    "heldPercentInstitutions",
    "shortPercentOfFloat",
    "targetMeanPrice",
    "recommendationKey",
    "beta",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
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

            kwargs = dict(
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
                institutional_ownership=self._safe_float(info.get("heldPercentInstitutions")),
                insider_ownership=self._safe_float(info.get("heldPercentInsiders")),
                short_float=self._safe_float(info.get("shortPercentOfFloat")),
                analyst_target=info.get("targetMeanPrice"),
                recommendation=info.get("recommendationKey"),
                beta=info.get("beta"),
                week_52_high=info.get("fiftyTwoWeekHigh"),
                week_52_low=info.get("fiftyTwoWeekLow"),
            )
            data = self._build_item_resilient(kwargs)

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
    def _build_item_resilient(kwargs: dict) -> FundamentalsItem:
        """Construct ``FundamentalsItem``, dropping only the individual
        field(s) that fail validation instead of losing the whole
        snapshot.

        Found live 2026-09-10: net_income wrongly required ``ge=0``
        (fixed directly on the model — a net loss is real, meaningful
        data), but a single bad field crashing construction of the
        whole 25+-field object was the bigger problem: one invalid
        value wiped out company_name/sector/market_cap/eps/etc. too,
        even though every other field was perfectly valid. Several
        other ratio-shaped fields here (forward_pe, peg_ratio,
        price_to_book, debt_to_equity) can also legitimately go
        negative for a distressed or loss-making company, so this
        degrades one field at a time generally rather than requiring
        every future sign edge case to be caught in advance.
        """
        try:
            return FundamentalsItem(**kwargs)
        except ValidationError as e:
            bad_fields = {err["loc"][0] for err in e.errors() if err.get("loc")}
            if not bad_fields:
                raise
            logger.info(
                "Dropping invalid fundamentals field(s) %s for %s: %s",
                bad_fields,
                kwargs.get("symbol"),
                e,
            )
            cleaned = {k: (None if k in bad_fields else v) for k, v in kwargs.items()}
            try:
                return FundamentalsItem(**cleaned)
            except ValidationError:
                # Extremely unlikely (we just nulled exactly the fields
                # that failed) — fall back to a fully-empty item rather
                # than raise, matching this provider's existing
                # never-let-one-bad-symbol-500-the-request contract.
                return FundamentalsItem(symbol=str(kwargs["symbol"]))

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
