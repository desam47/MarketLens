"""
Finnhub API routes — company data, news, and analyst sentiment (v2.2).

All endpoints use Cache-Control: max-age=3600 (1 hour) since Finnhub
free tier is rate-limited and company data changes infrequently.
"""
import logging
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.market_data.services.finnhub_service import FinnhubService, finnhub_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/finnhub",
    tags=["finnhub"],
    responses={404: {"description": "Not found"}},
)

# Cache duration in seconds — company data and financials change infrequently.
_CACHE_MAX_AGE = 3600  # 1 hour


def _service() -> FinnhubService:
    return finnhub_service


@router.get("/health")
async def health_check(service: Annotated[FinnhubService, Depends(_service)], response: Response):
    """Verify Finnhub API connectivity using a known symbol (AAPL)."""
    try:
        service.get_company_profile("AAPL")
        return {"status": "ok", "provider": "finnhub"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Finnhub unavailable: {e}")


@router.get("/company/{symbol}")
async def get_company_profile(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
):
    """Company profile: name, country, exchange, industry, logo, web URL."""
    try:
        profile = service.get_company_profile(symbol.upper())
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return profile
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/metrics/{symbol}")
async def get_company_metrics(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
):
    """Key financial metrics: P/E, EPS, beta, 52-week high/low, margins."""
    try:
        metrics = service.get_company_metrics(symbol.upper())
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return metrics
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/financials/{symbol}")
async def get_company_financials(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
):
    """Financials: income statement, balance sheet, cash flow."""
    try:
        financials = service.get_company_financials(symbol.upper())
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return financials
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/news/{symbol}")
async def get_company_news(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
    from_date: date | None = Query(default=None, description="Start date (YYYY-MM-DD). Defaults to 7 days ago."),
    to_date: date | None = Query(default=None, description="End date (YYYY-MM-DD). Defaults to today."),
):
    """Company-specific news articles."""
    if from_date is None:
        from_date = date.today() - timedelta(days=7)
    if to_date is None:
        to_date = date.today()
    try:
        news = service.get_company_news(symbol.upper(), from_date, to_date)
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return news
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/market-news")
async def get_market_news(
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
    category: str = Query(default="general", description="News category: general, forex, crypto, merger"),
):
    """General market news articles."""
    try:
        news = service.get_market_news(category)
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return news
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/recommendations/{symbol}")
async def get_analyst_recommendations(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
):
    """Analyst buy/hold/sell consensus over time."""
    try:
        recommendations = service.get_analyst_recommendations(symbol.upper())
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return recommendations
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/insider/{symbol}")
async def get_insider_sentiment(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
    from_date: date | None = Query(default=None, description="Start date (YYYY-MM-DD). Defaults to 1 year ago."),
    to_date: date | None = Query(default=None, description="End date (YYYY-MM-DD). Defaults to today."),
):
    """Insider trading sentiment by month."""
    if from_date is None:
        from_date = date.today() - timedelta(days=365)
    if to_date is None:
        to_date = date.today()
    try:
        sentiment = service.get_insider_sentiment(symbol.upper(), from_date, to_date)
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return sentiment
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/peers/{symbol}")
async def get_peers(
    symbol: str,
    service: Annotated[FinnhubService, Depends(_service)],
    response: Response,
):
    """Industry peer symbols for a given company."""
    try:
        peers = service.get_peers(symbol.upper())
        response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
        return {"symbol": symbol.upper(), "peers": peers}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))