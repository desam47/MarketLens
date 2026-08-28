"""
Phase 16 — AI analysis REST endpoint.

``POST /api/ai/analyze`` — run a full AI market analysis for a symbol.

The endpoint calls ``analyze_symbol()`` and returns either the validated
``AnalysisResponse`` or an ``UncertaintyResponse``. Both share the same
schema (the UI can handle them identically). The response also includes
the raw model name and provider for debugging.

Query parameters:
- ``symbol`` (required): ticker to analyze
- ``timeframe`` (optional, default "1d"): primary analysis window
- ``max_tokens`` (optional): override max_tokens for this call
- ``temperature`` (optional): override temperature for this call

``GET /api/ai/status`` — health snapshot of the AI provider chain.
``GET /api/ai/config`` — frontend-safe configuration (no API key).
"""
from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from backend.ai import (
    UncertaintyResponse,
    ai_manager,
)
from backend.ai.analyze import analyze_symbol

router = APIRouter(prefix="/api/ai", tags=["ai"])


# --- Request / Response models ---------------------------------------


class AnalyzeRequest(BaseModel):
    """Optional body for ``POST /api/ai/analyze``."""

    symbol: str = Field(..., min_length=1, max_length=10)
    timeframe: str = Field(default="1d", pattern=r"^(1d|1h|4h|15m|5m|1m)$")
    max_tokens: int | None = Field(default=None, ge=100, le=8192)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ProviderStatusResponse(BaseModel):
    name: str
    healthy: bool
    is_primary: bool
    error: str | None = None


class ConfigResponse(BaseModel):
    """Frontend-safe view of the AI configuration."""

    enabled: bool
    provider: str
    fallback_providers: list[str]
    model: str
    base_url: str
    timeout: float
    max_tokens: int
    temperature: float
    api_key_set: bool


class AnalyzeResponse(BaseModel):
    """Response body for ``POST /api/ai/analyze``.

    ``trend`` is "uncertain" when the system fell back to an
    ``UncertaintyResponse`` (AI off, no data, provider unavailable,
    or parse failure). The UI should render a neutral info card
    in that case.
    """

    summary: str
    trend: str
    confidence: float
    supporting_factors: list[str]
    risk_factors: list[str]
    timeframe_conflicts: list[str]
    key_levels: list[str]
    provider: str = "unknown"
    model: str = "unknown"
    is_uncertain: bool = False


# --- Endpoints -----------------------------------------------------


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
)
def analyze(
    symbol: str = Query(..., min_length=1, max_length=10),
    timeframe: str = Query(default="1d", pattern=r"^(1d|1h|4h|15m|5m|1m)$"),
    max_tokens: int | None = Query(default=None, ge=100, le=8192),
    temperature: float | None = Query(default=None, ge=0.0, le=2.0),
) -> AnalyzeResponse:
    """Run an AI market analysis for ``symbol``.

    The quant engine gathers structured context (scores, regime, RS,
    sector, S/R, transitions, historical stats). The AI produces a
    structured summary. If AI is disabled or unavailable, the response
    returns with ``trend="uncertain"`` and a descriptive ``summary``.

    This endpoint intentionally never returns HTTP 500 for expected
    failure modes (no data, AI off, provider down, parse failure).
    """
    result = analyze_symbol(
        symbol=symbol.upper(),
        timeframe=timeframe,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    return AnalyzeResponse(
        summary=result.summary,
        trend=result.trend,
        confidence=result.confidence,
        supporting_factors=result.supporting_factors,
        risk_factors=result.risk_factors,
        timeframe_conflicts=result.timeframe_conflicts,
        key_levels=result.key_levels,
        provider=ai_manager.settings.provider,
        model=ai_manager.settings.model,
        is_uncertain=isinstance(result, UncertaintyResponse),
    )


@router.get("/status", response_model=list[ProviderStatusResponse])
def ai_status() -> list[ProviderStatusResponse]:
    """Return the health of each provider in the AI chain."""
    return [
        ProviderStatusResponse(name=s.name, healthy=s.healthy, is_primary=s.is_primary, error=s.error)
        for s in ai_manager.status()
    ]


@router.get("/config", response_model=ConfigResponse)
def ai_config() -> ConfigResponse:
    """Return the frontend-safe AI configuration (no API key)."""
    cfg = ai_manager.safe_config()
    return ConfigResponse(**cfg)


class ConfigUpdateRequest(BaseModel):
    """Fields that can be updated at runtime without a server restart."""
    enabled: bool | None = None


@router.patch("/config", response_model=ConfigResponse)
def update_ai_config(body: ConfigUpdateRequest) -> ConfigResponse:
    """Update the AI configuration at runtime.

    Only ``enabled`` is supported for now — flipping it True/False
    takes effect immediately without restarting the server.
    """
    if body.enabled is not None:
        ai_manager.set_enabled(body.enabled)
    cfg = ai_manager.safe_config()
    return ConfigResponse(**cfg)
