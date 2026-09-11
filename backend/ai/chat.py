"""
Version 4, AI feature 4 — conversational AI chat panel.

Scope (see docs/plan): single-turn-per-message (build_context() is
rebuilt fresh per message, not held as server-side "memory"), no
streaming. Read-only relationship to features 2-3 — this module never
triggers a new digest or alert commentary; it only reads what already
exists.

Tool-calling was originally excluded entirely, deliberately deferred
as "a separate future decision" (see docs/Version_4/v4_plan.md). Later
resolved to a single, narrow tool: the AI can request a real
analyze_symbol() run (the same function AIAnalysisPanel's "Re-run"
button calls) when the trader explicitly asks for a fresh/official
analysis, via ChatReplyResponse.wants_reanalysis. No open-ended
function-calling loop — one fixed action, decided in the same
completion call that would otherwise produce a normal reply, executed
synchronously before the turn's assistant message is persisted.

Follows analyze_symbol's "never raise for an expected failure mode"
contract: AI off, InsufficientDataError, or a malformed reply all
degrade to a stored assistant message explaining that (grounded=False)
rather than an HTTP error or a crashed request. The reanalysis tool
call inherits the same contract — it never raises either.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from backend.ai.analyze import analyze_symbol
from backend.ai.chat_symbols import resolve_turn_symbols
from backend.ai.context import InsufficientDataError, build_context
from backend.ai.manager import ai_manager
from backend.ai.market_baseline import build_market_baseline
from backend.ai.prompt import (
    CHAT_SYSTEM_PROMPT,
    UncertaintyResponse,
    build_chat_prompt,
    parse_chat_reply,
)
from backend.ai.reply_stream import ReplyExtractor
from backend.models import AlertTrigger, ChatMessage
from backend.models.chat import UNIVERSAL_SYMBOL
from backend.repositories.chat_repository import ChatRepository

logger = logging.getLogger(__name__)

# How many prior turns (user + assistant messages combined) to include
# as transcript text in the prompt.
_TRANSCRIPT_TURNS = 6
_TRANSCRIPT_MSG_CHARS = 600  # per-message clip inside the transcript

# Per-turn intent — keeps aux-data HTTP and prompt tokens off turns that
# don't ask for that material. Each pattern is deliberately generous:
# a false positive just adds a section, a false negative omits one the
# trader can ask for again.
_NEWS_INTENT = re.compile(
    r"\b(news|headline|catalyst|announc\w+|report\w*|filing|earnings|upgrade|"
    r"downgrade|analyst|rating|price target|what happened|sell[- ]?off|selloff|"
    r"rall\w+|spik\w+|plung\w+|surg\w+|why (is|did|are|has|s|'s)\b|"
    r"mov\w+ (on|because|after|due))\b", re.I)
_FUNDA_INTENT = re.compile(
    r"\b(fundamental\w*|valuation|p/?e\b|pe ratio|peg\b|eps\b|revenue|sales|"
    r"profit\w*|margin\w*|balance sheet|debt|cash ?flow|fcf\b|dividend|yield|"
    r"market ?cap|book value|financ\w+|forward pe|multiple|buyback)\b", re.I)
_MARKET_INTENT = re.compile(
    r"\b(market|markets|s&p|spx|spy\b|nasdaq|dow\b|russell|indices|index|"
    r"regime|risk[- ]?on|risk[- ]?off|breadth|rotation|macro|the fed|rates|"
    r"vix\b|sentiment|overall|broad(er)?|environment|backdrop|my (watchlist|"
    r"names|book|portfolio))\b", re.I)
_STATS_INTENT = re.compile(
    r"\b(win[- ]?rate|hit[- ]?rate|historical\w*|backtest|track record|"
    r"how often|batting average|expectancy|sample size|base rate)\b", re.I)

# Short-lived per-symbol context cache — a burst of follow-ups about one
# name rebuilt the whole scan + aux-data each turn.
_CTX_TTL = 12.0
_CTX_CAP = 64
_ctx_lock = threading.Lock()
_ctx_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()


def _clip(text: str) -> str:
    text = text or ""
    if len(text) <= _TRANSCRIPT_MSG_CHARS:
        return text
    return text[:_TRANSCRIPT_MSG_CHARS] + " …[truncated]"


def _build_context_cached(sym: str, *, news: bool, funda: bool, diverg: bool) -> dict:
    """``build_context(...).to_dict()`` behind a 12s TTL cache.

    A failed build (InsufficientDataError etc.) is NOT cached — it
    propagates so a transient miss retries on the next turn.
    """
    key = (sym, news, funda, diverg)
    now = time.monotonic()
    with _ctx_lock:
        hit = _ctx_cache.get(key)
        if hit is not None and now - hit[0] < _CTX_TTL:
            _ctx_cache.move_to_end(key)
            return hit[1]
    ctx = build_context(
        sym, include_news=news, include_fundamentals=funda, include_divergence=diverg,
    ).to_dict()
    with _ctx_lock:
        _ctx_cache[key] = (now, ctx)
        _ctx_cache.move_to_end(key)
        while len(_ctx_cache) > _CTX_CAP:
            _ctx_cache.popitem(last=False)
    return ctx


def _build_alert_context(db, alert_trigger_id: int | None) -> dict | None:
    """Load the alert/trigger this chat was opened from, if any.

    Read-only — never re-triggers alert commentary generation, just
    reads what's already on the row (message/observed_value/
    ai_commentary if it's been generated by then).
    """
    if alert_trigger_id is None:
        return None
    trigger = db.query(AlertTrigger).filter(AlertTrigger.id == alert_trigger_id).first()
    if trigger is None:
        return None
    return {
        "symbol": trigger.symbol,
        "message": trigger.message,
        "observed_value": trigger.observed_value,
        "ai_commentary": trigger.ai_commentary,
        "triggered_at": str(trigger.triggered_at) if trigger.triggered_at else None,
    }


@dataclass
class _Turn:
    """Everything a chat turn needs after context assembly — shared by
    the blocking (:func:`answer_chat_message`) and streaming
    (:func:`stream_chat_message`) paths."""

    symbol_blocks: list[dict]
    unavailable: list[str]
    market_baseline: dict | None
    transcript: list[tuple[str, str]]
    user_content: str
    alert_context: dict | None
    capped: bool
    base: list[str]

    @property
    def focus(self) -> list[str]:
        return [b["symbol"] for b in self.symbol_blocks if b["availability"]["engine_warm"]]

    @property
    def partial(self) -> list[str]:
        return [b["symbol"] for b in self.symbol_blocks if not b["availability"]["engine_warm"]]


def _prepare_turn(repo: ChatRepository, session_id: int, user_content: str) -> _Turn:
    """Persist the user message and assemble the turn's quant context.

    Resolves the turn's tickers from the text (0..N, capped), attaches a
    cheap market-wide baseline when relevant, and fans the per-symbol
    context builds out over a thread pool.
    """
    session = repo.get_session(session_id)
    if session is None:
        raise ValueError(f"chat session {session_id} not found")

    repo.add_message(session_id, "user", user_content)

    history = repo.get_messages(session_id, limit=_TRANSCRIPT_TURNS + 1)
    # Exclude the message we just added — it's passed separately as
    # `new_message`, not duplicated into the transcript. Long prior
    # replies are clipped so the transcript can't dominate.
    transcript = [(m.role, _clip(m.content)) for m in history[:-1]][-_TRANSCRIPT_TURNS:]

    alert_context = _build_alert_context(repo.db, session.alert_trigger_id)

    base = (
        [session.symbol]
        if session.scope in ("symbol", "alert")
        and session.symbol
        and session.symbol != UNIVERSAL_SYMBOL
        else []
    )
    symbols, capped = resolve_turn_symbols(user_content, transcript, base)
    single = len(symbols) == 1

    # What this turn actually asks for — skip the rest.
    want_news = single and bool(_NEWS_INTENT.search(user_content))
    want_funda = single and bool(_FUNDA_INTENT.search(user_content))
    want_stats = bool(_STATS_INTENT.search(user_content))
    want_baseline = (not symbols) or bool(_MARKET_INTENT.search(user_content))

    def _ctx(sym: str) -> dict:
        return _build_context_cached(sym, news=want_news, funda=want_funda, diverg=single)

    # The market baseline and each per-symbol context are independent
    # blocking calls (DB reads, a scan, aux-data HTTP) — fan them out.
    symbol_blocks: list[dict] = []
    unavailable: list[str] = []
    market_baseline: dict | None = None
    with ThreadPoolExecutor(max_workers=4) as ex:
        baseline_fut = ex.submit(build_market_baseline) if want_baseline else None
        ctx_futs = {sym: ex.submit(_ctx, sym) for sym in symbols}
        if baseline_fut is not None:
            try:
                market_baseline = baseline_fut.result()
            except Exception as e:  # noqa: BLE001 — baseline never blocks the turn
                logger.warning("chat market baseline failed: %s", e)
        for sym in symbols:  # preserve the resolved order
            try:
                ctx = ctx_futs[sym].result()
            except InsufficientDataError:
                unavailable.append(sym)
                continue
            except Exception as e:  # noqa: BLE001 — per-symbol, never abort the turn
                logger.warning("chat build_context(%s) failed: %s", sym, e)
                unavailable.append(sym)
                continue
            avail = _availability(ctx)
            symbol_blocks.append(
                {
                    "symbol": sym,
                    "context": _prune_context(ctx, avail, keep_stats=want_stats),
                    "availability": avail,
                }
            )

    return _Turn(
        symbol_blocks=symbol_blocks,
        unavailable=unavailable,
        market_baseline=market_baseline,
        transcript=transcript,
        user_content=user_content,
        alert_context=alert_context,
        capped=capped,
        base=base,
    )


def answer_chat_message(
    session_id: int, user_content: str
) -> tuple[ChatMessage, bool, list[str], list[str], list[str]]:
    """Persist ``user_content``, generate a reply, persist the assistant
    ChatMessage, and return
    ``(message, grounded, focus, partial, unavailable)``.

    Universal chat (2026-09-10): the turn resolves its own tickers from
    the message text (0..N, capped), always attaches a cheap
    market-wide baseline, and builds per-ticker quant context for each
    resolved symbol. ``focus`` is the tickers that got a full
    (warm-engine) context block; ``partial`` the ones with only live
    price / indicators (not in a watchlist); ``unavailable`` the ones
    that were named but had no data at all.

    ``grounded`` / ``focus`` / ``unavailable`` aren't persisted columns
    — they're per-turn hints for the router's response.

    Never raises for an expected failure mode — AI-off, a per-symbol
    context failure, or a malformed AI reply all produce a stored
    assistant message explaining that, not an exception. An unexpected
    failure (e.g. the session doesn't exist) still raises.
    """
    repo = ChatRepository()
    try:
        turn = _prepare_turn(repo, session_id, user_content)
        reply_text, grounded = _generate_reply(
            turn.symbol_blocks, turn.unavailable, turn.market_baseline, turn.transcript,
            turn.user_content, turn.alert_context, turn.capped, turn.base,
        )
        grounded = grounded and not turn.unavailable  # deterministic fail-safe
        assistant_message = repo.add_message(session_id, "assistant", reply_text)
        return assistant_message, grounded, turn.focus, turn.partial, turn.unavailable
    finally:
        repo.close()


def stream_chat_message(
    session_id: int, user_content: str
) -> Iterator[tuple]:
    """Streaming sibling of :func:`answer_chat_message`.

    Yields, in order:
      ``("meta", {"focus", "partial", "unavailable"})``  — once, after prep
      ``("delta", text)``                                — 0..N incremental reply chunks
      ``("final", (message, grounded, focus, partial, unavailable))``  — once

    Same never-raise-for-an-expected-failure contract: AI-off / a
    malformed reply / a provider error all still end in a persisted
    assistant row and a ``final`` event. An unexpected failure before
    prep completes (e.g. bad session id) propagates on first iteration.
    """
    repo = ChatRepository()
    try:
        turn = _prepare_turn(repo, session_id, user_content)
        yield ("meta", {
            "focus": turn.focus, "partial": turn.partial, "unavailable": turn.unavailable,
        })

        final_text: str | None = None
        grounded = False
        try:
            for kind, payload in _generate_reply_streaming(turn):
                if kind == "delta":
                    yield ("delta", payload)
                else:  # "result"
                    final_text, grounded = payload
        except Exception as e:  # noqa: BLE001 — mirror _generate_reply's contract
            logger.warning("Chat stream generation raised: %s", e)
            final_text, grounded = (
                "Something went wrong reaching the AI provider — please try again.", False,
            )

        if final_text is None:
            final_text, grounded = (
                "AI is currently unavailable, so I can't answer that right now.", False,
            )
        grounded = grounded and not turn.unavailable
        msg = repo.add_message(session_id, "assistant", final_text)
        yield ("final", (msg, grounded, turn.focus, turn.partial, turn.unavailable))
    finally:
        repo.close()


def _availability(ctx: dict) -> dict:
    """Classify how much live quant data a context dict actually carries.

    ``engine_warm`` is the key signal: a ticker not in a watchlist gets
    a live price + best-effort indicators from ``build_context`` but its
    trend engine was never fed, so its multi-timeframe scores are all
    'unknown'/0.
    """
    tf_scores = ctx.get("timeframe_scores") or {}
    warm = bool(ctx.get("trend_state")) or any(
        (s or {}).get("direction") not in (None, "unknown", "neutral")
        and (s or {}).get("confidence", 0) > 0
        for s in tf_scores.values()
    )
    momentum = ctx.get("momentum") or {}
    return {
        "engine_warm": warm,
        "has_price": ctx.get("price") is not None,
        "has_rsi": momentum.get("rsi") is not None,
        "has_support_resistance": bool(ctx.get("support_resistance")),
        "note": "" if warm else (
            "not in your watchlist — live price / indicators only, "
            "no multi-timeframe trend or confidence"
        ),
    }


def _prune_context(ctx: dict, avail: dict, *, keep_stats: bool = False) -> dict:
    """Trim ``build_context``'s dict down to what a chat turn needs:

    - drop empty sections,
    - for a cold engine, drop the composite scores (market_structure /
      trend_transition) that read like a confidence number the model
      must not quote for an untracked name,
    - drop the verbose ``historical_signal_stats`` table unless the turn
      asked about win rates / base rates,
    - cap ``news`` to the 4 most recent items.
    """
    cold_only = {"market_structure", "trend_transition"}
    out: dict = {}
    for k, v in ctx.items():
        if v is None or v == {} or v == []:
            continue
        if not avail["engine_warm"] and k in cold_only:
            continue
        if k == "historical_signal_stats" and not keep_stats:
            continue
        if k == "news" and isinstance(v, list):
            v = v[:4]
        out[k] = v
    return out


def _generate_reply(
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    user_content: str,
    alert_context: dict | None,
    capped: bool,
    base_symbols: list[str],
) -> tuple[str, bool]:
    """Call the AI and parse its reply. Never raises — degrades to a
    plain reply with grounded=False.

    When the AI's reply asks for ``wants_reanalysis``, runs the chat's
    one tool (see module docstring) for the named ticker instead.
    """
    # Friendly degrade for a legacy single-symbol session whose only
    # ticker has no data (keeps the pre-universal wording).
    if (
        not symbol_blocks
        and unavailable
        and base_symbols
        and set(unavailable) == {s.upper() for s in base_symbols}
    ):
        return f"I don't have enough data on {unavailable[0]} yet to answer that.", False

    if not ai_manager.is_available():
        return "AI is currently unavailable, so I can't answer that right now.", False

    budget = max(2000, ai_manager.settings.max_tokens - 500)
    try:
        resp = ai_manager.complete(
            prompt=build_chat_prompt(
                symbol_blocks, unavailable, market_baseline, transcript,
                user_content, alert_context,
                capped_note=_capped_note(capped, symbol_blocks), token_budget=budget,
            ),
            system=CHAT_SYSTEM_PROMPT,
            max_tokens=500,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Chat AI call raised: %s", e)
        return "Something went wrong reaching the AI provider — please try again.", False

    if resp.text is None:
        return "AI is currently unavailable, so I can't answer that right now.", False

    try:
        parsed = parse_chat_reply(resp.text)
    except Exception as e:  # noqa: BLE001
        logger.info("Chat reply failed to parse: %s", e)
        return "I couldn't process that — could you rephrase?", False

    return _finalize_parsed(parsed, symbol_blocks)


def _capped_note(capped: bool, symbol_blocks: list[dict]) -> str | None:
    if not capped:
        return None
    shown = ", ".join(b["symbol"] for b in symbol_blocks) or "the first few"
    return f"(You named more tickers than I can dig into at once — I looked at {shown}.)"


def _finalize_parsed(parsed, symbol_blocks: list[dict]) -> tuple[str, bool]:
    """A parsed ``ChatReplyResponse`` -> ``(final_text, grounded)``,
    running the chat's one tool (a real ``analyze_symbol`` run) when the
    model set ``wants_reanalysis``."""
    if parsed.wants_reanalysis:
        known = [b["symbol"] for b in symbol_blocks]
        target = (parsed.reanalysis_symbol or "").upper().strip()
        if not target and len(known) == 1:
            target = known[0]
        if target and target in known:
            return _run_reanalysis(target)
        if known:
            return "Which ticker should I run the full analysis for?", True
        return "Tell me which ticker you'd like me to run the full analysis for.", False
    return parsed.reply, parsed.grounded


def _generate_reply_streaming(turn: _Turn) -> Iterator[tuple]:
    """Streaming variant of :func:`_generate_reply`.

    Yields ``("delta", text)`` for each incremental piece of the reply,
    then exactly one ``("result", (final_text, grounded))``. Never raises
    — every failure mode ends in a ``("result", ...)``.

    The streamed deltas are the model's ``reply`` field decoded live from
    the partial JSON. The trailing ``result`` is authoritative: on the
    reanalysis-tool path it differs from what was streamed, and the
    caller overwrites the bubble with it.
    """
    if (
        not turn.symbol_blocks
        and turn.unavailable
        and turn.base
        and set(turn.unavailable) == {s.upper() for s in turn.base}
    ):
        yield ("result", (
            f"I don't have enough data on {turn.unavailable[0]} yet to answer that.", False,
        ))
        return

    if not ai_manager.is_available():
        yield ("result", (
            "AI is currently unavailable, so I can't answer that right now.", False,
        ))
        return

    budget = max(2000, ai_manager.settings.max_tokens - 500)
    prompt = build_chat_prompt(
        turn.symbol_blocks, turn.unavailable, turn.market_baseline, turn.transcript,
        turn.user_content, turn.alert_context,
        capped_note=_capped_note(turn.capped, turn.symbol_blocks), token_budget=budget,
    )

    raw = ""
    extractor = ReplyExtractor()
    try:
        if ai_manager.settings.chat_streaming:
            for chunk in ai_manager.stream(prompt, system=CHAT_SYSTEM_PROMPT, max_tokens=500):
                raw += chunk
                delta = extractor.feed(raw)
                if delta:
                    yield ("delta", delta)
        else:
            resp = ai_manager.complete(prompt, system=CHAT_SYSTEM_PROMPT, max_tokens=500)
            raw = resp.text or ""
            delta = extractor.feed(raw)
            if delta:
                yield ("delta", delta)
    except Exception as e:  # noqa: BLE001
        logger.warning("Chat streaming AI call raised: %s", e)
        yield ("result", (
            "Something went wrong reaching the AI provider — please try again.", False,
        ))
        return

    if not raw.strip():
        yield ("result", (
            "AI is currently unavailable, so I can't answer that right now.", False,
        ))
        return

    try:
        parsed = parse_chat_reply(raw)
    except Exception as e:  # noqa: BLE001
        logger.info("Chat reply failed to parse: %s", e)
        streamed = extractor.text.strip()
        yield ("result", (streamed or "I couldn't process that — could you rephrase?", False))
        return

    yield ("result", _finalize_parsed(parsed, turn.symbol_blocks))


def _run_reanalysis(symbol: str) -> tuple[str, bool]:
    """Execute the chat's one tool call: a real analyze_symbol() run.

    Reuses analyze_symbol() itself — the same function
    AIAnalysisPanel's "Re-run" button calls — so a chat-triggered
    reanalysis carries the exact same safety contract (never raises;
    degrades to an UncertaintyResponse on AI-off/no-data/malformed
    reply). That degrade path is rendered here as a normal,
    grounded=False chat reply rather than surfaced as an error.
    """
    try:
        result = analyze_symbol(symbol)
    except Exception as e:  # noqa: BLE001 — the tool call must never crash the turn
        logger.warning("Chat-triggered reanalysis failed for %s: %s", symbol, e)
        return (
            "I tried to re-run the analysis but hit an error — please try again.",
            False,
        )

    if isinstance(result, UncertaintyResponse):
        return f"I tried to re-run the analysis for {symbol}, but {result.summary}", False

    return (
        f"I re-ran the analysis for {symbol}: trend is now {result.trend} "
        f"({result.confidence:.0%} confidence). {result.summary}",
        True,
    )
