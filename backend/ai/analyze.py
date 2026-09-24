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

import asyncio
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from backend.ai.context import AnalysisContext, InsufficientDataError, build_context
from backend.ai.manager import ai_manager
from backend.ai.prompt import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_ANALYST_ONLY,
    AnalysisResponse,
    UncertaintyReason,
    UncertaintyResponse,
    build_user_prompt,
    make_analysis_response_format,
    parse_ai_reply,
    render_system_prompt,
    summarize_context,
)
from backend.ai.provider import AIResponse, StreamAttribution

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
        ctx.timeframe_scores,
        ctx.trend_state,
        ctx.market_structure,
        ctx.market_regime,
        ctx.relative_strength,
        ctx.sector_alignment,
        ctx.support_resistance,
        ctx.trend_transition,
        ctx.historical_signal_stats,
        ctx.news,
        ctx.fundamentals,
        ctx.divergence,
        ctx.tape,
        ctx.track_record,
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
_CACHEABLE_UNCERTAINTY_REASONS = frozenset({"insufficient_data"})

_analysis_cache: OrderedDict[tuple, tuple[float, AnalysisResponse | UncertaintyResponse]] = (
    OrderedDict()
)
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
    """Store a successful or deterministic no-data result in the short cache.

    Provider outages and malformed replies are transient. Caching either makes
    a recovered provider look unavailable for the rest of the TTL, so only an
    ``insufficient_data`` uncertainty is cacheable alongside successful
    analyses.
    """
    if key is None:
        return
    if (
        isinstance(result, UncertaintyResponse)
        and result.uncertainty_reason not in _CACHEABLE_UNCERTAINTY_REASONS
    ):
        return
    now = time.monotonic()
    with _cache_lock:
        _analysis_cache[key] = (now, result)
        if len(_analysis_cache) > _ANALYSIS_CACHE_MAX_ENTRIES:
            _analysis_cache.popitem(last=False)


def _cached_copy(
    cached_at: float,
    result: AnalysisResponse | UncertaintyResponse,
    now: float,
) -> AnalysisResponse | UncertaintyResponse:
    """Mark a cache hit and advance its server-authored market-data age."""
    update: dict[str, Any] = {"cache_status": "cached"}
    age = result.data_age_seconds
    if isinstance(age, (int, float)):
        update["data_age_seconds"] = max(0.0, age + (now - cached_at))
    return result.model_copy(update=update)


def _with_request_metadata(
    result: AnalysisResponse | UncertaintyResponse,
    symbol: str,
    timeframe: str,
    *,
    cache_status: str = "fresh",
) -> AnalysisResponse | UncertaintyResponse:
    """Attach server-authored target metadata when no market context exists."""
    result.symbol = symbol.upper()
    result.timeframe = timeframe
    result.cache_status = cache_status
    return result


def _with_context_metadata(
    result: AnalysisResponse | UncertaintyResponse,
    ctx: AnalysisContext,
    *,
    cache_status: str = "fresh",
) -> AnalysisResponse | UncertaintyResponse:
    """Attach immutable market-data provenance from the quantitative context."""
    result.symbol = ctx.symbol
    result.timeframe = ctx.timeframe
    result.price = ctx.price
    result.source_timestamp = ctx.timestamp
    result.data_age_seconds = ctx.data_age_seconds
    result.data_status = ctx.data_status
    result.market_data_provider = ctx.market_data_provider
    result.market_session = ctx.market_session
    result.cache_status = cache_status
    return result


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
    force_refresh: bool = False,
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
            symbol,
            timeframe,
            advisory,
            portfolio_symbols,
            model,
            max_tokens,
            temperature,
        )
        if not force_refresh:
            now = time.monotonic()
            with _cache_lock:
                hit = _analysis_cache.get(cache_key)
                if hit is not None and now - hit[0] < _ANALYSIS_TTL:
                    _analysis_cache.move_to_end(cache_key)
                    return _cached_copy(hit[0], hit[1], now)
    else:
        cache_key = None

    # --- Step 2: gather structured quant context ---
    ctx: AnalysisContext | None = None
    try:
        # Blocking work (scan, DB reads, provider quotes, a thread-pool
        # fan-out): keep it off the event loop so other requests keep flowing.
        ctx = await asyncio.to_thread(
            build_context, symbol, timeframe, portfolio_symbols=portfolio_symbols
        )
    except InsufficientDataError as e:
        logger.info("Insufficient data for AI analysis of %s: %s", symbol, e)
        result = _uncertainty(
            f"Quantitative data not available: {e}",
            reason_enum="insufficient_data",
        )
        result = _with_request_metadata(result, symbol, timeframe)
        _cache_result(cache_key, result)
        return result
    except Exception as e:
        # Unexpected error in the context builder — propagate
        logger.exception("Context builder failed for %s: %s", symbol, e)
        raise

    # --- Step 2: build the LLM request (prompt/temperature/response_format) ---
    system_prompt, final_temperature, response_format = _build_request(
        ctx,
        system_prompt_override,
        advisory,
        temperature,
    )

    # --- Step 3: ask the LLM ---
    ai_resp = await ai_manager.complete(
        prompt=build_user_prompt(summarize_context(ctx.compact())),
        system=system_prompt,
        max_tokens=max_tokens,
        temperature=final_temperature,
        response_format=response_format,
        model=model,
    )

    return _finalize_analysis(symbol, ctx, ai_resp, cache_key)


def _build_request(
    ctx: AnalysisContext,
    system_prompt_override: str | None,
    advisory: bool,
    temperature: float | None,
) -> tuple[str, float, dict[str, Any] | None]:
    """Render the system prompt, pick a temperature, and decide whether to
    request structured (JSON-mode) output.

    Factored out so both ``analyze_symbol()`` (blocking) and
    ``analyze_symbol_stream()`` (SSE) build an identical request from the
    same context — no drift between the two call paths.
    """
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
    # sparse data gets a higher temperature for cautious, hedged language;
    # rich data and high-volatility/crisis regimes use a lower temperature
    # for focused, conservative output.
    if temperature is not None:
        final_temperature = temperature
    else:
        final_temperature = _adaptive_temperature(ctx)
        # I2: high vol / crisis → extra conservatism
        regime = ctx.market_regime.get("regime", "")
        if regime in ("high_volatility", "crisis"):
            final_temperature = min(final_temperature, 0.15)

    # O11: request JSON mode when the chain supports it (gated on the
    # whole chain so a structured-capable fallback still gets mode when
    # it ends up answering). See f1afc3c / chain_supports_structured_output.
    response_format = (
        make_analysis_response_format() if ai_manager.chain_supports_structured_output() else None
    )
    return system_prompt, final_temperature, response_format


def _finalize_analysis(
    symbol: str,
    ctx: AnalysisContext,
    ai_resp: AIResponse,
    cache_key: tuple | None,
) -> AnalysisResponse | UncertaintyResponse:
    """Parse, validate, calibrate, attribute, and cache the AI reply.

    Shared by ``analyze_symbol()`` and ``analyze_symbol_stream()`` so the
    parsed/decorated response is identical whether the LLM was called via
    ``complete()`` or ``stream()``. Returns an ``UncertaintyResponse``
    (cached) when the AI is disabled/unavailable or its reply failed
    validation — never raises for those expected failure modes.
    """
    if ai_resp.text is None:
        if ai_resp.provider == "disabled":
            msg = "AI analysis is disabled (set AI_ENABLED=true to enable)"
            reason = "disabled"
        else:
            msg = f"AI providers unavailable (tried: {ai_resp.provider})"
            reason = "providers_unavailable"
        logger.info("AI unavailable for %s: %s", symbol, msg)
        result = _uncertainty(
            msg, reason_enum=reason, provider=ai_resp.provider, model=ai_resp.model
        )
        result = _with_context_metadata(result, ctx)
        _cache_result(cache_key, result)
        return result

    # --- parse and validate ---
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
            reason_enum="parse_failed",
            provider=ai_resp.provider,
            model=ai_resp.model,
        )
        result = _with_context_metadata(result, ctx)
        _cache_result(cache_key, result)
        return result

    # The model may describe a cross-timeframe disagreement, but it must not
    # present the opposite of the requested engine timeframe as a clean call.
    # Keep its narrative available while making the surfaced trend explicitly
    # mixed and evidence-backed.
    _enforce_trend_alignment(symbol, ctx, parsed)

    # parsed.confidence is already hard-capped at _CONFIDENCE_MAX (see
    # AnalysisResponse._cap_confidence) by the time we see it here. The
    # TRUE value the AI declared, if it exceeded that cap, was preserved
    # into confidence_declared at construction time (see
    # _preserve_raw_confidence) — prefer that over parsed.confidence so
    # a 1.0 reply's provenance isn't lost behind the 0.95 cap.
    capped_confidence = parsed.confidence
    raw_confidence = (
        parsed.confidence_declared if parsed.confidence_declared is not None else capped_confidence
    )
    if capped_confidence is not None:
        parsed.confidence = _calibrate_confidence(
            capped_confidence,
            ctx.track_record,
        )
        if raw_confidence != parsed.confidence:
            parsed.confidence_declared = raw_confidence
            parsed.confidence_sample_size = (
                ctx.track_record.get("sample_size") if ctx.track_record else None
            )
        else:
            parsed.confidence_declared = None
            parsed.confidence_sample_size = None

    # Surface the quantitative context that drove this read on the
    # response itself — they were injected into the prompt but never
    # returned, so the UI couldn't render a regime badge, the MTF
    # confidence row, the track-record strip, or peer alignment.
    # Not AI output: copied verbatim from build_context().
    parsed.market_regime = ctx.market_regime or {}
    parsed.timeframe_scores = ctx.timeframe_scores or {}
    parsed.track_record = ctx.track_record or {}
    parsed.correlation_context = ctx.correlation_context or {}

    # Attribute to whoever actually answered (not the configured primary).
    parsed.provider = ai_resp.provider
    parsed.model = ai_resp.model

    # Defense in depth: label any bare key levels relative to live price.
    parsed.key_levels = _label_bare_key_levels(parsed.key_levels, ctx.price)

    # A structured model reply is not sufficient evidence for an actionable
    # setup. Keep the narrative, but suppress a buy/sell plan unless its
    # levels are compatible with the current quote and engine structure.
    # A plan that failed its own consistency check while parsing already
    # carries an "unavailable" validation with the reason; keep it.
    if parsed.trade_plan_validation.get("status") != "unavailable":
        parsed.trade_plan_validation = _plan_validation(ctx, parsed.trade_plan)
    if parsed.trade_plan_validation.get("status") == "unavailable":
        parsed.trade_plan = None

    parsed = _with_context_metadata(parsed, ctx)
    _cache_result(cache_key, parsed)
    return parsed


def _result_to_dict(result: AnalysisResponse | UncertaintyResponse) -> dict[str, Any]:
    """JSON-serializable dict of a finalized result for SSE 'final' frames.

    Includes ``is_uncertain`` so the frame genuinely matches the
    blocking endpoint's ``AnalyzeResponse`` shape, as the SSE route's
    own docstring claims — that field only exists on the router's
    wrapper model (computed there via ``isinstance(result,
    UncertaintyResponse)``), and was missing from this dict entirely
    until 2026-09-16. Computed here, not in the router, because this is
    the last point that still holds the concrete result type — the
    router only ever sees the already-dumped dict.
    """
    data = result.model_dump(mode="json")
    data["is_uncertain"] = isinstance(result, UncertaintyResponse)
    return data


async def analyze_symbol_stream(
    symbol: str,
    timeframe: str = "1d",
    *,
    advisory: bool = True,
    system_prompt_override: str | None = None,
    portfolio_symbols: list[str] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    force_refresh: bool = False,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream an AI analysis as ``(kind, payload)`` pairs for SSE.

    Frames (mirror the chat SSE convention in chat_router.py):
      ``("meta", {...})``  — once, up front: {symbol, timeframe,
                            track_record, model}
      ``("delta", <str>)`` — incremental summary text as it arrives
      ``("final", {...})`` — the full finalized AnalysisResponse /
                            UncertaintyResponse dict, after parse + cache
      ``("error", <str>)` — if the stream can't even start

    Only the summary is streamed token-by-token; the structured fields
    (trend, confidence, trade_plan, market_regime, ...) are emitted once
    in the ``final`` frame after the full reply is accumulated and
    validated via ``_finalize_analysis()`` — identical handling to the
    blocking ``analyze_symbol()``.
    """
    # --- cache check (O4) ---
    if system_prompt_override is None:
        cache_key = _cache_key(
            symbol,
            timeframe,
            advisory,
            portfolio_symbols,
            model,
            max_tokens,
            temperature,
        )
        cached = None
        if not force_refresh:
            now = time.monotonic()
            with _cache_lock:
                hit = _analysis_cache.get(cache_key)
                if hit is not None and now - hit[0] < _ANALYSIS_TTL:
                    _analysis_cache.move_to_end(cache_key)
                    cached = _cached_copy(hit[0], hit[1], now)
        if cached is not None:
            yield (
                "meta",
                {
                    "symbol": cached.symbol,
                    "timeframe": cached.timeframe,
                    "track_record": getattr(cached, "track_record", {}) or {},
                    "model": getattr(cached, "model", None),
                },
            )
            yield ("delta", cached.summary or "")
            yield ("final", _result_to_dict(cached))
            return
    else:
        cache_key = None

    # --- build context ---
    try:
        # Blocking work (scan, DB reads, provider quotes, a thread-pool
        # fan-out): keep it off the event loop so other requests keep flowing.
        ctx = await asyncio.to_thread(
            build_context, symbol, timeframe, portfolio_symbols=portfolio_symbols
        )
    except InsufficientDataError as e:
        logger.info("Insufficient data for AI analysis of %s: %s", symbol, e)
        result = _uncertainty(
            f"Quantitative data not available: {e}",
            reason_enum="insufficient_data",
        )
        result = _with_request_metadata(result, symbol, timeframe)
        _cache_result(cache_key, result)
        yield (
            "meta",
            {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "track_record": {},
                "model": model,
            },
        )
        yield ("final", _result_to_dict(result))
        return

    system_prompt, final_temperature, response_format = _build_request(
        ctx,
        system_prompt_override,
        advisory,
        temperature,
    )

    yield (
        "meta",
        {
            "symbol": ctx.symbol,
            "timeframe": ctx.timeframe,
            "track_record": ctx.track_record or {},
            "model": model,
        },
    )

    # --- stream the LLM ---
    accumulated: list[str] = []
    # Populated by ai_manager.stream() itself at the moment a provider
    # actually answers — race-free, unlike the old last_answered() read
    # (a process-wide singleton any concurrent request could overwrite
    # between this stream ending and this coroutine reading it back).
    attribution = StreamAttribution()
    try:
        async for piece in ai_manager.stream(
            prompt=build_user_prompt(summarize_context(ctx.compact())),
            system=system_prompt,
            max_tokens=max_tokens,
            temperature=final_temperature,
            response_format=response_format,
            model=model,
            attribution=attribution,
        ):
            accumulated.append(piece)
            yield ("delta", piece)
    except Exception as e:  # noqa: BLE001
        logger.warning("AI stream failed for %s: %s", symbol, e)
        if not accumulated:
            # Nothing was emitted — the stream couldn't start. Surface an
            # error frame rather than silently returning nothing.
            yield (
                "error",
                "The analysis stream could not be started; retry, or use POST /api/ai/analyze for a blocking reply.",
            )
            return
        # Partial text already shown — fall through and finalize what we have.

    text = "".join(accumulated) if accumulated else None
    if text is None:
        # Disabled, or every provider was unavailable before first chunk
        # — no provider ever answered this request, so "none" (matching
        # complete()'s equivalent all-failed path), not a guess from
        # whichever provider happened to answer a DIFFERENT request last.
        prov = "disabled" if not ai_manager.enabled else "none"
        ai_resp = AIResponse(
            text=None, provider=prov, model=model or ai_manager.settings.model, structured=False
        )
    else:
        prov = attribution.provider or (
            ai_manager.settings.provider if ai_manager.enabled else "disabled"
        )
        mod = attribution.model or (model or ai_manager.settings.model)
        ai_resp = AIResponse(text=text, provider=prov, model=mod, structured=attribution.structured)

    result = _finalize_analysis(symbol, ctx, ai_resp, cache_key)
    yield ("final", _result_to_dict(result))


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

# A plan is a research aid, not an order. These are deliberately broad sanity
# bounds: the plan must be near the observed market and visible structure, but
# the validator never invents an alternative entry/stop/target.
_MAX_ENTRY_DISTANCE_FROM_QUOTE = 0.15
_MAX_STOP_DISTANCE_FROM_ENTRY = 0.20
_MAX_TARGET_DISTANCE_PAST_STRUCTURE = 0.15
_MAX_REGULAR_SESSION_QUOTE_AGE_SECONDS = 15 * 60
_PLAN_BLOCKING_DATA_STATUSES = {"STALE", "ERROR", "UNKNOWN", "GAP", "INCOMPLETE", "DUPLICATE"}


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


def _structural_prices(context: dict[str, Any], side: str) -> list[float]:
    """Read bounded, numeric support/resistance prices from analysis context."""
    levels = context.get(side, []) if isinstance(context, dict) else []
    out: list[float] = []
    if not isinstance(levels, list):
        return out
    for level in levels:
        value = level.get("price") if isinstance(level, dict) else level
        if isinstance(value, (int, float)) and value > 0:
            out.append(float(value))
    return out[:3]


def _plan_validation(
    ctx: AnalysisContext,
    plan: Any,
) -> dict[str, Any]:
    """Verify an actionable model plan against quoted price and structure.

    This intentionally returns evidence or a reason, never replacement prices.
    Callers remove plans whose status is ``unavailable`` so only a validated
    setup can reach the trader-facing card or outcome tracker.
    """
    if plan is None or plan.recommendation not in {"buy", "sell"}:
        return {"status": "not_applicable"}

    data_status = (ctx.data_status or "UNKNOWN").upper()
    if data_status in _PLAN_BLOCKING_DATA_STATUSES:
        return {
            "status": "unavailable",
            "reason": f"Market data is {data_status.lower()}, so no actionable setup was validated.",
        }

    market_session = (getattr(ctx, "market_session", None) or "unknown").lower()
    quote_age = getattr(ctx, "data_age_seconds", None)
    if market_session == "regular":
        if not isinstance(quote_age, (int, float)):
            return {
                "status": "unavailable",
                "reason": "Quote freshness is unavailable during the regular session, so no actionable setup was validated.",
            }
        if quote_age > _MAX_REGULAR_SESSION_QUOTE_AGE_SECONDS:
            return {
                "status": "unavailable",
                "reason": "The current quote is more than 15 minutes old during the regular session, so no actionable setup was validated.",
            }

    price = ctx.price
    lo, hi = plan.entry_zone_low, plan.entry_zone_high
    entry = (lo + hi) / 2 if lo is not None and hi is not None else lo if lo is not None else hi
    if price is None or entry is None or plan.stop_loss is None or not plan.targets:
        return {
            "status": "unavailable",
            "reason": "Entry, stop, target, and current quote are required to validate an actionable setup.",
        }
    entry_low = min(value for value in (lo, hi) if value is not None)
    entry_high = max(value for value in (lo, hi) if value is not None)
    if plan.recommendation == "buy":
        if plan.stop_loss >= entry_low:
            return {
                "status": "unavailable",
                "reason": "The buy stop must be below the entire entry zone.",
            }
        if any(target <= entry_high for target in plan.targets):
            return {
                "status": "unavailable",
                "reason": "Buy targets must be above the entire entry zone.",
            }
    else:
        if plan.stop_loss <= entry_high:
            return {
                "status": "unavailable",
                "reason": "The sell stop must be above the entire entry zone.",
            }
        if any(target >= entry_low for target in plan.targets):
            return {
                "status": "unavailable",
                "reason": "Sell targets must be below the entire entry zone.",
            }
    if abs(entry - price) / price > _MAX_ENTRY_DISTANCE_FROM_QUOTE:
        return {
            "status": "unavailable",
            "reason": "The proposed entry is too far from the current quote to validate safely.",
        }
    if abs(entry - plan.stop_loss) / entry > _MAX_STOP_DISTANCE_FROM_ENTRY:
        return {
            "status": "unavailable",
            "reason": "The proposed stop is too far from entry to validate against current structure.",
        }

    supports = _structural_prices(ctx.support_resistance, "supports")
    resistances = _structural_prices(ctx.support_resistance, "resistances")
    if not supports or not resistances:
        return {
            "status": "unavailable",
            "reason": "Current support and resistance evidence is insufficient to validate an actionable setup.",
        }

    first_target = plan.targets[0]
    if plan.recommendation == "buy":
        nearest_support = max(supports)
        furthest_resistance = max(resistances)
        if plan.stop_loss > nearest_support * 1.03:
            return {
                "status": "unavailable",
                "reason": "The buy stop is not anchored at or below nearby support.",
            }
        if first_target > furthest_resistance * (1 + _MAX_TARGET_DISTANCE_PAST_STRUCTURE):
            return {
                "status": "unavailable",
                "reason": "The buy target extends too far beyond visible resistance.",
            }
    else:
        nearest_resistance = min(resistances)
        furthest_support = min(supports)
        if plan.stop_loss < nearest_resistance * 0.97:
            return {
                "status": "unavailable",
                "reason": "The sell stop is not anchored at or above nearby resistance.",
            }
        if first_target < furthest_support * (1 - _MAX_TARGET_DISTANCE_PAST_STRUCTURE):
            return {
                "status": "unavailable",
                "reason": "The sell target extends too far beyond visible support.",
            }

    validation = {
        "status": "verified",
        "quote_price": round(price, 2),
        "supports": [round(level, 2) for level in supports],
        "resistances": [round(level, 2) for level in resistances],
        "data_status": data_status,
    }
    if market_session in {"premarket", "after_hours", "closed"}:
        validation["freshness_note"] = (
            "Validated outside regular trading hours against the latest available quote."
        )
    return validation


def _uncertainty(
    reason: str,
    *,
    reason_enum: UncertaintyReason = "none",
    provider: str = "none",
    model: str = "unknown",
) -> UncertaintyResponse:
    return UncertaintyResponse(
        summary=reason,
        trend="uncertain",
        confidence=0.0,
        provider=provider,
        model=model,
        uncertainty_reason=reason_enum,
    )


def _enforce_trend_alignment(
    symbol: str,
    ctx: AnalysisContext,
    response: AnalysisResponse,
) -> None:
    """Prevent an unsupported model trend from contradicting engine truth."""
    engine_direction = ctx.trend_state.get("direction", "")
    engine_trend = {
        "uptrend": "bullish",
        "bullish": "bullish",
        "downtrend": "bearish",
        "bearish": "bearish",
    }.get(engine_direction)
    if engine_trend is None or response.trend in ("mixed", "uncertain", engine_trend):
        return

    model_trend = response.trend
    response.trend = "mixed"
    response.confidence = min(response.confidence, 0.5)
    conflict = (
        f"Model proposed {model_trend}, but the quantitative {ctx.timeframe} signal is "
        f"{engine_trend}; shown as mixed until that conflict is resolved."
    )
    if conflict not in response.timeframe_conflicts:
        response.timeframe_conflicts.append(conflict)
    logger.warning(
        "AI %s for %s contradicted the quantitative %s signal; surfaced as mixed",
        model_trend,
        symbol,
        ctx.timeframe,
    )
