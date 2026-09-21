"""
Phase 18 — Auxiliary data API routes.

Provides three endpoint groups:
  GET /api/aux-data/news/{symbol}
  GET /api/aux-data/fundamentals/{symbol}
  GET /api/aux-data/options/{symbol}

Each group is independently gated: when its provider is disabled
(AUX_*_ENABLED=false) the endpoint returns 503 with a descriptive message.
The rest of the API is unaffected.
"""
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.aux_data.services.manager import aux_data_manager
from backend.models.aux_data import (
    AuxProviderStatus,
    FundamentalsResponse,
    NewsResponse,
    OptionsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/aux-data", tags=["aux-data"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _to_dashboard_tz(value: datetime | None) -> str:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value) or ""


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class NewsQuery(BaseModel):
    limit: int = Field(default=20, ge=1, le=50, description="Max articles to return")


class OptionsQuery(BaseModel):
    expiration: str | None = Field(
        default=None,
        description="YYYY-MM-DD. If omitted, returns the first 4 available dates.",
    )


class AuxDataStatusResponse(BaseModel):
    news: list[AuxProviderStatus]
    fundamentals: list[AuxProviderStatus]
    options: list[AuxProviderStatus]
    timestamp: datetime


class DisabledResponse(BaseModel):
    detail: str
    hint: str
    timestamp: str


def _disabled_resp(category: str) -> dict:
    """Return a 503 detail payload; passed straight to HTTPException."""
    env_var = {
        "news": "AUX_NEWS_ENABLED=true",
        "fundamentals": "AUX_FUNDAMENTALS_ENABLED=true",
        "options": "AUX_OPTIONS_ENABLED=true",
    }[category]
    return {
        "detail": f"The {category} provider is disabled.",
        "hint": f"Set {env_var} in your environment to enable it.",
        "timestamp": _to_dashboard_tz(_now_utc()),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/news/{symbol}",
    response_model=NewsResponse,
    responses={503: {"model": DisabledResponse, "description": "Provider disabled"}},
)
async def get_news(symbol: str, limit: int = 20):
    """Fetch recent news headlines for ``symbol``."""
    symbol = symbol.upper()
    if not aux_data_manager.news.is_enabled():
        raise HTTPException(status_code=503, detail=_disabled_resp("news"))
    try:
        result = aux_data_manager.get_news(symbol, limit=limit)
        return result
    except Exception as exc:
        logger.error("News endpoint failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch news: {exc!s}") from exc


@router.get(
    "/fundamentals/{symbol}",
    response_model=FundamentalsResponse,
    responses={503: {"model": DisabledResponse, "description": "Provider disabled"}},
)
async def get_fundamentals(symbol: str):
    """Fetch fundamental data snapshot for ``symbol``."""
    symbol = symbol.upper()
    if not aux_data_manager.fundamentals.is_enabled():
        raise HTTPException(status_code=503, detail=_disabled_resp("fundamentals"))
    try:
        result = aux_data_manager.get_fundamentals(symbol)
        return result
    except Exception as exc:
        logger.error("Fundamentals endpoint failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch fundamentals: {exc!s}") from exc


@router.get(
    "/options/{symbol}",
    response_model=OptionsResponse,
    responses={503: {"model": DisabledResponse, "description": "Provider disabled"}},
)
async def get_options(symbol: str, expiration: str | None = None):
    """Fetch options chain(s) for ``symbol``.

    Without ``expiration``: returns up to 4 near-term chains.
    With ``expiration=YYYY-MM-DD``: returns only that date's chain.
    """
    symbol = symbol.upper()
    if not aux_data_manager.options.is_enabled():
        raise HTTPException(status_code=503, detail=_disabled_resp("options"))
    try:
        result = aux_data_manager.get_options(symbol, expiration=expiration)
        return result
    except Exception as exc:
        logger.error("Options endpoint failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch options: {exc!s}") from exc


@router.get(
    "/providers",
    response_model=AuxDataStatusResponse,
    summary="List all auxiliary data providers and their health.",
)
async def get_aux_providers():
    """Return health status for every registered news/fundamentals/options provider."""
    return AuxDataStatusResponse(
        news=aux_data_manager.news.get_statuses(),
        fundamentals=aux_data_manager.fundamentals.get_statuses(),
        options=aux_data_manager.options.get_statuses(),
        timestamp=datetime.now(UTC),
    )
