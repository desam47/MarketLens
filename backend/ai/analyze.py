"""
Phase 16 — High-level AI market analysis.

``analyze_symbol(symbol, timeframe)`` is the single entry point.
It:

1. Calls ``build_context()`` → raises ``InsufficientDataError`` → caller
   returns ``uncertaintyResponse``.
2. Calls ``ai_manager.complete()`` → if disabled or unavailable →
   returns ``uncertaintyResponse``.
3. Parses the reply with ``parse_ai_reply()`` → raises ``ValueError`` →
   returns ``uncertaintyResponse``.
4. Returns the validated ``AnalysisResponse``.

The function never raises for expected failure modes (no data, AI off,
AI returns gibberish). Only unexpected errors (DB down, module import
failures) propagate.

Per spec:
- AI must NEVER directly calculate raw indicators
- AI output must be structured (validated with Pydantic)
- AI must NEVER overwrite quantitative truth
- If insufficient data → uncertainty response
- Do not make AI issue trade orders
"""
from __future__ import annotations

import logging

from backend.ai.context import AnalysisContext, InsufficientDataError, build_context
from backend.ai.manager import ai_manager
from backend.ai.prompt import (
    SYSTEM_PROMPT,
    AnalysisResponse,
    UncertaintyResponse,
    build_user_prompt,
    parse_ai_reply,
)

logger = logging.getLogger(__name__)


def analyze_symbol(
    symbol: str,
    timeframe: str = "1d",
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    system_prompt_override: str | None = None,
) -> AnalysisResponse | UncertaintyResponse:
    """Run a full AI analysis for ``symbol``.

    Returns a validated ``AnalysisResponse`` when the AI produced a
    clean structured reply. Returns an ``UncertaintyResponse`` for
    any expected failure mode:

    - AI is disabled (``AI_ENABLED=false``)
    - No quantitative data for the symbol (cold start)
    - All AI providers in the fallback chain are unavailable
    - The AI reply failed to parse as JSON

    The caller can render both types identically — ``UncertaintyResponse``
    is a strict subtype of ``AnalysisResponse`` (same fields).

    Never raises for expected failures.

    Args:
        symbol: Ticker symbol to analyze.
        timeframe: Primary analysis timeframe (default "1d").
        max_tokens: Override max tokens for this call.
        temperature: Override temperature for this call.
        system_prompt_override: If provided, use this rendered system prompt
            instead of the built-in ``SYSTEM_PROMPT``. Used when a saved
            user template is selected for the request.
    """
    # --- Step 1: gather structured quant context ---
    ctx: AnalysisContext | None = None
    try:
        ctx = build_context(symbol, timeframe)
    except InsufficientDataError as e:
        logger.info("Insufficient data for AI analysis of %s: %s", symbol, e)
        return _uncertainty(f"Quantitative data not available: {e}")
    except Exception as e:
        # Unexpected error in the context builder — propagate
        logger.exception("Context builder failed for %s: %s", symbol, e)
        raise

    # --- Step 2: ask the AI ---
    system_prompt = system_prompt_override if system_prompt_override else SYSTEM_PROMPT
    ai_resp = ai_manager.complete(
        prompt=build_user_prompt(ctx.to_dict()),
        system=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    if ai_resp.text is None:
        if ai_resp.provider == "disabled":
            msg = "AI analysis is disabled (set AI_ENABLED=true to enable)"
        else:
            msg = f"AI providers unavailable (tried: {ai_resp.provider})"
        logger.info("AI unavailable for %s: %s", symbol, msg)
        return _uncertainty(msg, provider=ai_resp.provider, model=ai_resp.model)

    # --- Step 3: parse and validate ---
    try:
        parsed = parse_ai_reply(ai_resp.text)
    except ValueError as e:
        logger.warning(
            "AI reply failed validation for %s (provider=%s): %s",
            symbol,
            ai_resp.provider,
            e,
        )
        return _uncertainty(
            f"AI response could not be parsed: {e}. "
            f"Provider: {ai_resp.provider}. Model: {ai_resp.model}.",
            provider=ai_resp.provider,
            model=ai_resp.model,
        )

    # Validate that the trend agrees broadly with the engine's direction.
    # The AI is allowed to disagree (e.g. "mixed" when signals conflict),
    # but if the engine says "downtrend" and the AI says "bullish" with
    # no "mixed" justification, we warn rather than block. The quant
    # engine's score remains the source of truth.
    ctx_dir = ctx.trend_state.get("direction", "")
    ai_trend = parsed.trend
    if ctx_dir and ai_trend not in ("mixed", "uncertain"):
        _log_trend_disagreements(symbol, ctx_dir, ai_trend, parsed)

    # Record which provider/model actually answered — found live
    # 2026-09-10: every caller previously reported the configured
    # PRIMARY (ai_manager.settings.provider/model) here instead,
    # silently misattributing fallback-served analyses to the primary.
    parsed.provider = ai_resp.provider
    parsed.model = ai_resp.model
    return parsed


# --- Internals -------------------------------------------------------


def _uncertainty(
    reason: str, *, provider: str = "none", model: str = "unknown",
) -> UncertaintyResponse:
    return UncertaintyResponse(
        summary=reason,
        trend="uncertain",
        confidence=0.0,
        provider=provider,
        model=model,
    )


def _log_trend_disagreements(
    symbol: str,
    ctx_direction: str,
    ai_trend: str,
    response: AnalysisResponse,
) -> None:
    """Warn when the AI trend contradicts the engine's primary direction."""
    # Map engine directions to the AI's vocabulary
    engine_bullish = ctx_direction in ("uptrend", "bullish")
    engine_bearish = ctx_direction in ("downtrend", "bearish")
    ai_bullish = ai_trend == "bullish"
    ai_bearish = ai_trend == "bearish"

    if engine_bearish and ai_bullish:
        logger.warning(
            "AI bullish for %s but engine direction is %r. "
            "Check supporting_factors: %s",
            symbol,
            ctx_direction,
            response.supporting_factors[:3],
        )
    elif engine_bullish and ai_bearish:
        logger.warning(
            "AI bearish for %s but engine direction is %r. "
            "Check supporting_factors: %s",
            symbol,
            ctx_direction,
            response.supporting_factors[:3],
        )
