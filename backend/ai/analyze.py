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
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from backend.ai.context import AnalysisContext, InsufficientDataError, build_context
from backend.ai.manager import ai_manager
from backend.ai.prompt import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_ANALYST_ONLY,
    AnalysisResponse,
    UncertaintyResponse,
    build_user_prompt,
    make_analysis_response_format,
    parse_ai_reply,
    render_system_prompt,
    summarize_context,
)

logger = logging.getLogger(__name__)


# O7: adaptive temperature defaults based on context data quality.
# When the context is sparse (few populated fields), a higher temperature
# lets the AI hedge its answers ("I'm not confident in X because Y is
# missing"). When the context is rich, a lower temperature gives more
# deterministic, focused responses. An explicit caller override always wins.
_TEMPERATURE_HIGH_DATA = 0.2
_TEMPERATURE_LOW_DATA = 0.5


def _data_quality(ctx: AnalysisContext) -> float:
    """Score context completeness in [0, 1].

    Counts optional dict/list fields that are populated (non-empty) and
    divides by the total number of such fields. Scalar fields (symbol,
    price, etc.) are always present by construction, so they don't count.
    """
    optional_fields = [
        ctx.timeframe_scores, ctx.trend_state, ctx.market_structure,
        ctx.market_regime, ctx.relative_strength, ctx.sector_alignment,
        ctx.support_resistance, ctx.trend_transition,
        ctx.historical_signal_stats, ctx.news, ctx.fundamentals,
        ctx.divergence, ctx.tape, ctx.track_record,
    ]
    populated = sum(1 for f in optional_fields if f)
    return populated / len(optional_fields)


def _adaptive_temperature(ctx: AnalysisContext) -> float:
    """Pick a temperature based on context data quality.

    Rich context → lower temperature for deterministic, focused answers.
    Sparse context → higher temperature for hedged, cautious responses.
    """
    quality = _data_quality(ctx)
    if quality >= 0.5:
        return _TEMPERATURE_HIGH_DATA
    return _TEMPERATURE_LOW_DATA


# === O4: short-term analysis result cache =============================
# Deduplicates analysis for the same symbol/timeframe/advisory within a
# short window so that "Re-run" clicks and simultaneous digest movers
# don't each rebuild the full context and re-call the AI provider.

_ANALYSIS_TTL = 45.0
_ANALYSIS_CACHE_MAX_ENTRIES = 128

_analysis_cache: OrderedDict[tuple, tuple[float, AnalysisResponse | UncertaintyResponse]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(
    symbol: str,
    timeframe: str,
    advisory: bool,
    portfolio_symbols: list[str] | None,
    model: str | None,
    max_tokens: int | None,
    temperature: float | None,
) -> tuple:
    """Stable, hashable cache key for an analysis call.

    Includes every input that changes the *result*, not just the symbol:
    ``portfolio_symbols`` (peer context), ``model`` (O12 routing), and the
    ``max_tokens``/``temperature`` overrides. A previous version keyed only
    on (symbol, timeframe, advisory), so re-running an analysis with peer
    tickers returned a stale no-peer result. ``system_prompt_override`` is
    handled separately (it always bypasses the cache entirely).
    """
    peers = tuple(portfolio_symbols) if portfolio_symbols else ()
    mt = max_tokens or 0
    t = temperature if temperature is not None else -1.0
    return (symbol.upper(), timeframe, advisory, peers, model or "", mt, t)


def _clear_analysis_cache() -> None:
    """Empty the analysis result cache. Intended for test teardown."""
    with _cache_lock:
        _analysis_cache.clear()


def _cache_result(
    key: tuple | None,
    result: AnalysisResponse | UncertaintyResponse,
) -> None:
    """Store a result in the short-term cache with TTL + size-bounded eviction."""
    if key is None:
        return
    now = time.monotonic()
    with _cache_lock:
        _analysis_cache[key] = (now, result)
        if len(_analysis_cache) > _ANALYSIS_CACHE_MAX_ENTRIES:
            _analysis_cache.popitem(last=False)


async def analyze_symbol(
    symbol: str,
    timeframe: str = "1d",
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    system_prompt_override: str | None = None,
    advisory: bool = True,
    portfolio_symbols: list[str] | None = None,
    model: str | None = None,
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
        temperature: Override temperature for this call. When ``None``
            (default), an adaptive temperature is chosen based on context
            data quality: 0.2 for rich contexts, 0.5 for sparse ones.
            Use this parameter only to force a specific value.
        system_prompt_override: If provided, use this rendered system prompt
            instead of the built-in ``SYSTEM_PROMPT``. Used when a saved
            user template is selected for the request.
        advisory: When True (default) the AI also produces a
            ``trade_plan`` (recommendation + entry/stop/targets). When
            False it stays analyst-only — used by batch callers like the
            digest that only want the read. Ignored if
            ``system_prompt_override`` is set.
        portfolio_symbols (O10): an optional list of peer tickers the
            caller already knows about (e.g. the active watchlist). When
            provided, ``build_context`` scans up to 8 peers and the AI
            can reason about cross-ticker confluence/divergence instead
            of analyzing in isolation.
        model (O12): optional chain-entry name (e.g. ``"openai:gpt-4o"``
            or ``"ollama:qwen3:14b"``) to route this specific call
            through a particular provider/model instead of the default
            chain. Useful when a caller wants a stronger model for a
            formal analysis without reconfiguring the whole chain.
    """
    # --- Step 1: check short-term cache (O4) ---
    # Skip cache when a custom system prompt is provided — the caller
    # explicitly wants a fresh, template-driven analysis, not a cached
    # result from a different prompt. The cache key deliberately includes
    # every input that changes the result (symbol, timeframe, advisory,
    # and the caller-variance trio: portfolio_symbols, model, and the
    # max_tokens/temperature overrides) so peer/model-specific analyses
    # aren't served a stale no-peer result.
    if system_prompt_override is None:
        cache_key = _cache_key(
            symbol, timeframe, advisory,
            portfolio_symbols, model, max_tokens, temperature,
        )
        now = time.monotonic()
        with _cache_lock:
            hit = _analysis_cache.get(cache_key)
            if hit is not None and now - hit[0] < _ANALYSIS_TTL:
                _analysis_cache.move_to_end(cache_key)
                return hit[1]
    else:
        cache_key = None

    # --- Step 2: gather structured quant context ---
    ctx: AnalysisContext | None = None
    try:
        ctx = build_context(symbol, timeframe, portfolio_symbols=portfolio_symbols)
    except InsufficientDataError as e:
        logger.info("Insufficient data for AI analysis of %s: %s", symbol, e)
        result = _uncertainty(f"Quantitative data not available: {e}")
        _cache_result(cache_key, result)
        return result
    except Exception as e:
        # Unexpected error in the context builder — propagate
        logger.exception("Context builder failed for %s: %s", symbol, e)
        raise

    # --- Step 2: ask the AI ---
    if system_prompt_override:
        system_prompt = system_prompt_override
    else:
        base = SYSTEM_PROMPT if advisory else SYSTEM_PROMPT_ANALYST_ONLY
        # I1 + I2: inject confidence calibration (win-rate) and
        # regime-aware risk-first framing into the system prompt based
        # on the live context data.
        system_prompt = render_system_prompt(
            base,
            track_record=ctx.track_record,
            market_regime=ctx.market_regime,
            correlation_context=ctx.correlation_context,
        )

    # O7 + I2: when the caller hasn't explicitly overridden temperature,
    # pick one based on context data quality AND market regime — sparse
    # data or high-volatility regime → higher temperature (hedged,
    # cautious); rich data in calm regime → lower temperature.
    if temperature is not None:
        final_temperature = temperature
    else:
        final_temperature = _adaptive_temperature(ctx)
        # I2: high vol / crisis → extra conservatism
        regime = ctx.market_regime.get("regime", "")
        if regime in ("high_volatility", "crisis"):
            final_temperature = min(final_temperature, 0.15)

    # O11: when the primary provider supports structured output, request a
    # JSON-mode reply and skip regex extraction during parsing. Gated on the
    # whole chain (not just the primary) so a structured-capable *fallback*
    # still receives response_format when it ends up answering — otherwise
    # ai_resp.structured is False for that provider and parsing needlessly
    # falls back to regex. See chain_supports_structured_output().
    response_format = make_analysis_response_format() if ai_manager.chain_supports_structured_output() else None

    ai_resp = await ai_manager.complete(
        prompt=build_user_prompt(summarize_context(ctx.compact())),
        system=system_prompt,
        max_tokens=max_tokens,
        temperature=final_temperature,
        response_format=response_format,
        model=model,
    )

    if ai_resp.text is None:
        if ai_resp.provider == "disabled":
            msg = "AI analysis is disabled (set AI_ENABLED=true to enable)"
        else:
            msg = f"AI providers unavailable (tried: {ai_resp.provider})"
        logger.info("AI unavailable for %s: %s", symbol, msg)
        result = _uncertainty(msg, provider=ai_resp.provider, model=ai_resp.model)
        _cache_result(cache_key, result)
        return result

    # --- Step 3: parse and validate ---
    try:
        parsed = parse_ai_reply(ai_resp.text, structured=ai_resp.structured)
    except ValueError as e:
        logger.warning(
            "AI reply failed validation for %s (provider=%s): %s",
            symbol,
            ai_resp.provider,
            e,
        )
        result = _uncertainty(
            f"AI response could not be parsed: {e}. "
            f"Provider: {ai_resp.provider}. Model: {ai_resp.model}.",
            provider=ai_resp.provider,
            model=ai_resp.model,
        )
        _cache_result(cache_key, result)
        return result

    # Validate that the trend agrees broadly with the engine's direction.
    # The AI is allowed to disagree (e.g. "mixed" when signals conflict),
    # but if the engine says "downtrend" and the AI says "bullish" with
    # no "mixed" justification, we warn rather than block. The quant
    # engine's score remains the source of truth.
    ctx_dir = ctx.trend_state.get("direction", "")
    ai_trend = parsed.trend
    if ctx_dir and ai_trend not in ("mixed", "uncertain"):
        _log_trend_disagreements(symbol, ctx_dir, ai_trend, parsed)

    # O8: calibrate the AI's confidence against actual resolved outcomes
    # for this symbol.  If the AI has been wrong more than right, dampen
    # the declared confidence toward neutral.
    if parsed.confidence is not None:
        parsed.confidence = _calibrate_confidence(
            parsed.confidence, ctx.track_record,
        )

    # Surface the quantitative context that drove this read (regime, MTF
    # scores, the symbol's track record, and peer alignment) on the
    # response itself — they were injected into the prompt but never
    # returned, so the UI couldn't render a regime badge, the MTF
    # confidence row, a track-record strip, or peer-alignment summary.
    # Not AI output: copied verbatim from build_context().
    parsed.market_regime = ctx.market_regime or {}
    parsed.timeframe_scores = ctx.timeframe_scores or {}
    parsed.track_record = ctx.track_record or {}
    parsed.correlation_context = ctx.correlation_context or {}

    # Record which provider/model actually answered — found live
    # 2026-09-10: every caller previously reported the configured
    # PRIMARY (ai_manager.settings.provider/model) here instead,
    # silently misattributing fallback-served analyses to the primary.
    parsed.provider = ai_resp.provider
    parsed.model = ai_resp.model

    # Found live 2026-09-11: the prompt didn't actually ask for
    # key_levels to be labeled (a gap now closed in SYSTEM_PROMPT), so
    # weaker/older-cached-prompt replies can still come back as bare
    # numbers ("756.64") with nothing saying support or resistance.
    # Defense in depth for whatever slips through despite the prompt
    # fix — label anything still bare, relative to the live price.
    parsed.key_levels = _label_bare_key_levels(parsed.key_levels, ctx.price)

    # Single choke point (2026-09-11): every caller of analyze_symbol()
    # funnels through here, so this is the one place that needs to know
    # about trade-plan outcome tracking. Best-effort — a capture failure
    # must never surface as an analysis failure.
    try:
        from backend.ai.trade_plan_tracker import record_trade_plan

        record_trade_plan(symbol, parsed)
    except Exception as e:  # noqa: BLE001
        logger.warning("trade plan capture failed for %s: %s", symbol, e)

    _cache_result(cache_key, parsed)
    return parsed


# --- Internals -------------------------------------------------------

_BARE_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")

# O8: track-record confidence calibration.
# When the AI's historical win-rate on a symbol is below this threshold we
# dampen the declared confidence toward 0.5 so the UI never shows high
# certainty on a ticker the AI has been wrong about.
_CONFIDENCE_DAMPING_THRESHOLD = 0.5
# How much of the original gap (confidence — 0.5) survives when damping
# is applied. 0.5 means halve the distance to neutral.
_CONFIDENCE_DAMPING_FACTOR = 0.5
# Below this sample size we skip calibration — not enough resolved calls
# to draw statistical conclusions.
_CONFIDENCE_MIN_SAMPLE = 5


def _calibrate_confidence(
    confidence: float,
    track_record: dict[str, Any],
) -> float:
    """Dampen (or leave unchanged) an AI-declared confidence score based on
    the symbol's historical resolved outcome track record.

    When the AI's own resolved win-rate on this ticker has been below
    50% over ≥ 5 resolved calls, the declared confidence is pulled
    halfway toward 0.5 — so a 0.9 becomes 0.7 and a 0.2 becomes 0.35.
    This is a *conservative* adjustment: it never pushes confidence
    above what the AI declared, and it leaves high-accuracy or
    insufficient-sample records untouched.
    """
    if not track_record:
        return confidence
    win_rate = track_record.get("win_rate")
    sample_size = track_record.get("sample_size", 0)
    if win_rate is None or sample_size < _CONFIDENCE_MIN_SAMPLE:
        return confidence
    if win_rate >= _CONFIDENCE_DAMPING_THRESHOLD:
        return confidence
    neutral = 0.5
    gap = confidence - neutral
    dampened_gap = gap * _CONFIDENCE_DAMPING_FACTOR
    return neutral + dampened_gap


def _label_bare_key_levels(levels: list[str], price: float | None) -> list[str]:
    """Label any key_levels entry that's just a bare number ("756.64")
    with support/resistance relative to the live price, instead of
    showing an unexplained number in the UI. A level at or below price
    is support, above is resistance — the same convention
    build_context() itself uses for support_resistance (see
    context.py, "level.price <= latest_close").

    No-ops (returns levels unchanged) when price is unknown, or for
    any entry that isn't a bare number (already labeled, or some other
    free-text shape the AI produced).
    """
    if price is None:
        return levels
    out: list[str] = []
    for lvl in levels:
        stripped = lvl.strip()
        if not _BARE_NUMBER_RE.match(stripped):
            out.append(lvl)
            continue
        label = "support" if float(stripped) <= price else "resistance"
        out.append(f"{stripped} {label}")
    return out


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
