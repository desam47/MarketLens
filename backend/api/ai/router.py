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
- ``template_id`` (optional): override the system prompt with a saved
  user template (see ``/api/ai/templates``). Variables are
  substituted from the analysis context. Omit to use the default.

``GET /api/ai/status`` — health snapshot of the AI provider chain.
``GET /api/ai/config`` — frontend-safe configuration (no API key).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...ai import (
    UncertaintyResponse,
    ai_manager,
)
from ...ai.analyze import analyze_symbol, analyze_symbol_stream
from ...ai.prompt import TradePlan
from ...ai.tool_registry import ToolRequest, ToolResult, default_registry
from ...database import get_db
from ..ai_templates.router import resolve_and_render
from ..rate_limit import _ai_limiter, check_rate_limit

router = APIRouter(prefix="/api/ai", tags=["ai"])

logger = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _parse_portfolio_symbols(raw: str | None) -> list[str] | None:
    """Parse a comma-separated ``portfolio_symbols`` query string.

    Returns ``None`` when no peers were supplied (the default), so
    ``build_context`` skips the O10 peer scan entirely. Empty/whitespace
    entries are dropped — a trailing comma in the query string is a
    common typo and shouldn't crash the analysis.
    """
    if not raw:
        return None
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return symbols or None


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
    model: str | None = None


class ConfigResponse(BaseModel):
    """Frontend-safe view of the AI configuration."""

    enabled: bool
    provider: str
    fallback_providers: list[str]
    model: str
    # Which provider/model actually answered the most recent successful
    # call — model may be a gateway-side alias ("static-best-free") that
    # only resolves to a real name ("openai/gpt-oss-120b") once a request
    # is made. None until the first successful call since process start.
    last_provider: str | None = None
    last_model: str | None = None
    base_url: str
    timeout: float
    max_tokens: float
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
    # The advisory layer — present when analysis ran in advisor mode
    # (the default) and the AI produced a plan; null otherwise.
    trade_plan: TradePlan | None = None
    provider: str = "unknown"
    model: str = "unknown"
    is_uncertain: bool = False
    template_id: int | None = None
    template_name: str | None = None
    # Quantitative context surfaced alongside the AI read (see
    # AnalysisResponse.market_regime etc.) — regime badge, MTF
    # confidence row, track-record strip, peer alignment. Empty dicts
    # when the system fell back to uncertainty (no context available).
    market_regime: dict[str, Any] = Field(default_factory=dict)
    timeframe_scores: dict[str, Any] = Field(default_factory=dict)
    track_record: dict[str, Any] = Field(default_factory=dict)
    correlation_context: dict[str, Any] = Field(default_factory=dict)
    # Why the response fell back to uncertainty ("none" on success).
    # Drives actionable UI copy (disabled / insufficient_data /
    # providers_unavailable / parse_failed) instead of parsing the
    # free-text summary.
    uncertainty_reason: str = "none"
    confidence_declared: float | None = None
    confidence_sample_size: int | None = None
    # Server-authored market-data provenance. These values are copied from the
    # quantitative context, never from the model's natural-language reply.
    symbol: str = ""
    timeframe: str = ""
    price: float | None = None
    source_timestamp: str | None = None
    data_age_seconds: float | None = None
    data_status: str = "UNKNOWN"
    market_data_provider: str | None = None
    market_session: str = "unknown"
    cache_status: str = "fresh"
    trade_plan_validation: dict[str, Any] = Field(default_factory=dict)


# --- Endpoints -----------------------------------------------------


@router.post("/calculate", response_model=ToolResult, status_code=status.HTTP_200_OK)
async def calculate_metric(request: ToolRequest) -> ToolResult:
    """Run one deterministic, read-only MarketLens calculation.

    This endpoint is intentionally independent of the AI provider: callers
    receive validated arithmetic, formulas, and assumptions even when AI is
    disabled or unavailable.
    """

    result = default_registry.execute(request)
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error or "Calculation failed")
    return result


def _sync_resolve_template(db: Session, template_id: int | None):
    """Synchronous helper: resolve which AI template to use for a call."""
    from backend.models import AITemplate

    if template_id is not None:
        tmpl_obj = db.query(AITemplate).filter(AITemplate.id == template_id).first()
        if tmpl_obj is None:
            raise HTTPException(
                status_code=404,
                detail=f"AI template {template_id} not found",
            )
        return tmpl_obj.id, tmpl_obj

    # No explicit ID: look up the active default.
    tmpl_obj = (
        db.query(AITemplate)
        .filter(
            AITemplate.is_default == True,  # noqa: E712
            AITemplate.is_active == True,  # noqa: E712
        )
        .first()
    )
    return tmpl_obj.id if tmpl_obj else None, tmpl_obj


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
)
async def analyze(
    symbol: str = Query(..., min_length=1, max_length=10),
    timeframe: str = Query(default="1d", pattern=r"^(1d|1h|4h|15m|5m|1m)$"),
    max_tokens: int | None = Query(default=None, ge=100, le=8192),
    temperature: float | None = Query(default=None, ge=0.0, le=2.0),
    template_id: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Optional ID of a saved AI template. If provided, the template's "
            "system_prompt is rendered with the analysis context and used in "
            "place of the built-in SYSTEM_PROMPT. Variables declared by the "
            "template must be available in the context (symbol, timeframe, ...)."
        ),
    ),
    portfolio_symbols: str | None = Query(
        default=None,
        description=(
            "O10: comma-separated list of peer tickers the AI can compare "
            "against (e.g. 'MSFT,GOOG,SPY'). Up to 8 are scanned and their "
            "trend directions summarized so the AI can reason about cross-"
            "ticker confluence/divergence instead of analyzing in isolation."
        ),
    ),
    model: str | None = Query(
        default=None,
        description=(
            "O12: optional chain-entry name (e.g. 'openai:gpt-4o' or "
            "'ollama:qwen3:14b') to route this specific call through a "
            "particular provider/model instead of the default chain. The "
            "named entry is tried first, then falls through to the standard "
            "fallback chain."
        ),
    ),
    db: Session = Depends(get_db),
    _rl: None = Depends(check_rate_limit(_ai_limiter)),
) -> AnalyzeResponse:
    """Run an AI market analysis for ``symbol``.

    The quant engine gathers structured context (scores, regime, RS,
    sector, S/R, transitions, historical stats). The AI produces a
    structured summary. If AI is disabled or unavailable, the response
    returns with ``trend="uncertain"`` and a descriptive ``summary``.

    This endpoint intentionally never returns HTTP 500 for expected
    failure modes (no data, AI off, provider down, parse failure).

    If ``template_id`` is supplied, the template's system prompt is
    rendered with the available context variables (symbol, timeframe)
    and used in place of the built-in default.
    """
    # 1. Resolve the active template (DB read — run in thread to avoid
    #    blocking the FastAPI worker on I/O).
    resolved_template_id, tmpl_obj = await asyncio.to_thread(
        _sync_resolve_template, db, template_id
    )

    # 2. Render the template system prompt if one is active.
    #    resolve_and_render() may do DB reads — wrap it too.
    rendered_system: str | None = None
    if tmpl_obj is not None:
        rendered_system = await asyncio.to_thread(
            resolve_and_render,
            db,
            resolved_template_id,
            {"symbol": symbol.upper(), "timeframe": timeframe},
        )

    # 3. Call the LLM (blocking HTTP — the most expensive operation).
    #    The AI manager and providers are now async, so we await them directly.
    result = await analyze_symbol(
        symbol=symbol.upper(),
        timeframe=timeframe,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt_override=rendered_system,
        portfolio_symbols=_parse_portfolio_symbols(portfolio_symbols),
        model=model,
    )

    return AnalyzeResponse(
        summary=result.summary,
        trend=result.trend,
        confidence=result.confidence,
        supporting_factors=result.supporting_factors,
        risk_factors=result.risk_factors,
        timeframe_conflicts=result.timeframe_conflicts,
        key_levels=result.key_levels,
        trade_plan=result.trade_plan,
        provider=result.provider,
        model=result.model,
        is_uncertain=isinstance(result, UncertaintyResponse),
        template_id=resolved_template_id,
        template_name=tmpl_obj.name if tmpl_obj else None,
        market_regime=getattr(result, "market_regime", {}) or {},
        timeframe_scores=getattr(result, "timeframe_scores", {}) or {},
        track_record=getattr(result, "track_record", {}) or {},
        correlation_context=getattr(result, "correlation_context", {}) or {},
        uncertainty_reason=getattr(result, "uncertainty_reason", "none"),
        confidence_declared=getattr(result, "confidence_declared", None),
        confidence_sample_size=getattr(result, "confidence_sample_size", None),
        symbol=getattr(result, "symbol", symbol.upper()),
        timeframe=getattr(result, "timeframe", timeframe),
        price=getattr(result, "price", None),
        source_timestamp=getattr(result, "source_timestamp", None),
        data_age_seconds=getattr(result, "data_age_seconds", None),
        data_status=getattr(result, "data_status", "UNKNOWN"),
        market_data_provider=getattr(result, "market_data_provider", None),
        market_session=getattr(result, "market_session", "unknown"),
        cache_status=getattr(result, "cache_status", "fresh"),
        trade_plan_validation=getattr(result, "trade_plan_validation", {}) or {},
    )


@router.post("/analyze/stream")
async def analyze_stream(
    symbol: str = Query(..., min_length=1, max_length=10),
    timeframe: str = Query(default="1d", pattern=r"^(1d|1h|4h|15m|5m|1m)$"),
    max_tokens: int | None = Query(default=None, ge=100, le=8192),
    temperature: float | None = Query(default=None, ge=0.0, le=2.0),
    template_id: int | None = Query(
        default=None,
        ge=1,
    ),
    portfolio_symbols: str | None = Query(default=None),
    model: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _rl: None = Depends(check_rate_limit(_ai_limiter)),
) -> StreamingResponse:
    """Stream an AI market analysis for ``symbol`` over SSE.

    Frames (``text/event-stream``):
      ``meta``  — ``{symbol, timeframe, track_record, model}``, up front
      ``delta`` — ``{text}``, the summary as it's generated
      ``final`` — the full finalized ``AnalyzeResponse`` dict (identical
                  shape to ``POST /analyze``), once, after parse + cache
      ``error`` — ``{message}`` if the stream can't start

    Only the summary is streamed token-by-token; trend, confidence,
    trade_plan, regime, MTF scores, track record, and peer alignment
    arrive in the single ``final`` frame. The ``final`` frame is
    authoritative — clients should replace any partial summary with the
    parsed one (the same note as chat streaming). Never 500s for
    expected failure modes (no data, AI off, provider down, parse
    failure) — those surface as ``final`` uncertainty or ``error``.
    """
    resolved_template_id, tmpl_obj = await asyncio.to_thread(
        _sync_resolve_template, db, template_id
    )
    rendered_system: str | None = None
    if tmpl_obj is not None:
        rendered_system = await asyncio.to_thread(
            resolve_and_render,
            db,
            resolved_template_id,
            {"symbol": symbol.upper(), "timeframe": timeframe},
        )

    async def event_stream():
        try:
            async for kind, payload in analyze_symbol_stream(
                symbol=symbol.upper(),
                timeframe=timeframe,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt_override=rendered_system,
                portfolio_symbols=_parse_portfolio_symbols(portfolio_symbols),
                model=model,
            ):
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                elif kind == "final":
                    # Stamp the resolved template on the final response,
                    # matching the blocking endpoint's shape.
                    payload.setdefault("template_id", resolved_template_id)
                    payload.setdefault("template_name", tmpl_obj.name if tmpl_obj else None)
                    yield _sse("final", payload)
                elif kind == "error":
                    yield _sse("error", {"message": payload})
                else:
                    yield _sse(kind, payload)
        except Exception as e:  # noqa: BLE001
            logger.exception("analyze stream failed for %s: %s", symbol, e)
            yield _sse(
                "error",
                {
                    "message": "The analysis stream failed unexpectedly; retry, or use POST /api/ai/analyze."
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status", response_model=list[ProviderStatusResponse])
async def ai_status() -> list[ProviderStatusResponse]:
    """Return the health of each provider in the AI chain."""
    # ai_manager.status() walks the provider chain and may make a health
    # probe (HTTP ping) — we now await it directly.
    status_list = await ai_manager.status()
    return [
        ProviderStatusResponse(
            name=s.name,
            healthy=s.healthy,
            is_primary=s.is_primary,
            error=s.error,
            model=s.model,
        )
        for s in status_list
    ]


@router.get("/config", response_model=ConfigResponse)
async def ai_config() -> ConfigResponse:
    """Return the frontend-safe AI configuration (no API key)."""
    cfg = ai_manager.safe_config()
    return ConfigResponse(**cfg)


class ConfigUpdateRequest(BaseModel):
    """Fields that can be updated at runtime without a server restart."""

    enabled: bool | None = None


@router.patch("/config", response_model=ConfigResponse)
async def update_ai_config(body: ConfigUpdateRequest) -> ConfigResponse:
    """Update the AI configuration at runtime.

    Only ``enabled`` is supported for now — flipping it True/False
    takes effect immediately without restarting the server.
    """
    if body.enabled is not None:
        await asyncio.to_thread(ai_manager.set_enabled, body.enabled)
    cfg = ai_manager.safe_config()
    return ConfigResponse(**cfg)
