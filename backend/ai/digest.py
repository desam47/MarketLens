"""
AI feature 2 (Version 4): daily/session AI digest.

Aggregates already-running engines (market regime, the scanner's
already-warm cache, the active watchlist) into a single structured
payload, then asks the AI for a short natural-language narrative on
top. Pure aggregation — no asyncio, no scheduling — so it's testable
in isolation; scheduling lives in backend/ai/digest_service.py.

Follows the same structure as backend.ai.context.build_context(): a
dataclass-ish payload dict built with the "one broken sub-source
degrades to empty, never kills the whole thing" convention, then a
narrate step that mirrors backend.ai.analyze.analyze_symbol's
prompt -> AI -> parse -> graceful-uncertainty-on-failure shape.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.ai.analyze import analyze_symbol
from backend.ai.manager import ai_manager
from backend.ai.prompt import (
    DIGEST_SYSTEM_PROMPT,
    DigestNarrative,
    build_digest_user_prompt,
    parse_digest_reply,
)
from backend.ai.sync_bridge import run_sync
from backend.config.settings import settings

logger = logging.getLogger(__name__)


def _safe_call(fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return default


def build_digest_payload(watchlist_id: int | None = None, aggregate_all: bool = False, db=None) -> dict[str, Any]:
    """Gather a structured digest payload, in-process, no HTTP round-trips.

    Never raises — every section degrades to an empty/absent value on
    failure, same philosophy as build_context(). An empty watchlist or
    a cold scanner cache produces a digest with no movers, not an
    exception.
    """
    from backend.nl_search.executor import _resolve_watchlist_symbols
    from backend.scanner.scanner import market_scanner

    top_movers_count = settings.ai_digest.top_movers_count

    # --- Market regime ---
    regime: dict[str, Any] = {}

    def _get_regime() -> dict[str, Any]:
        from backend.api.market_context.router import get_engine as get_context_engine

        sig = get_context_engine().get_current_context()
        return sig.to_dict() if sig is not None else {}

    regime = _safe_call(_get_regime, default={})

    # --- Watchlist symbols ---
    if aggregate_all:
        # gather symbols from all active watchlists
        if db is None:
            from backend.database import SessionLocal
            db = SessionLocal()
        from backend.repositories.watchlist_repository import WatchlistRepository
        repo = WatchlistRepository(db)
        all_wls = repo.get_watchlists(active_only=True)
        symbol_set = set()
        for wl in all_wls:
            syms = repo.get_watchlist_symbols(wl.id, enabled_only=True)
            symbol_set.update(ws.symbol for ws in syms)
        symbols = list(symbol_set)
    else:
        symbols = _safe_call(_resolve_watchlist_symbols, None, db, default=[]) or []

    # --- Scanner cache (whatever's already warm — no fresh scan
    # triggered here; the digest reads, it doesn't force work) ---
    # Ensure the scanner has scanned all symbols so we can aggregate).
    # Use run_sync to drive the async scan.
    run_sync(market_scanner.scan_symbols_async(symbols))
    cache = {
        s: market_scanner.scan_results[s]
        for s in symbols
        if s in market_scanner.scan_results
    }

    # Descending by signed score: strongest bullish first, strongest
    # bearish last.
    ranked = sorted(
        cache.values(),
        key=lambda r: _safe_call(r.calculate_signed_total_score, default=0.0),
        reverse=True,
    )
    # Split into genuinely bullish (score > 0) and genuinely bearish
    # (score < 0) candidate pools *before* taking the top N — taking
    # "first N regardless of sign" from a small watchlist can consume
    # every symbol (bullish AND bearish) into top_bullish alone,
    # leaving nothing for top_bearish once same-symbol dedup runs.
    bullish_candidates = [
        r for r in ranked if _safe_call(r.calculate_signed_total_score, default=0.0) > 0
    ]
    bearish_candidates = [
        r for r in ranked if _safe_call(r.calculate_signed_total_score, default=0.0) < 0
    ]
    top_bullish = bullish_candidates[:top_movers_count]
    # bearish_candidates is still in descending order (weakest-bearish
    # first, strongest-bearish last) since it's a filtered slice of
    # `ranked` — take the tail (most negative) and reverse so the
    # strongest bearish mover is first, matching top_bullish's order.
    top_bearish = list(reversed(bearish_candidates[-top_movers_count:])) if bearish_candidates else []

    rsi_extremes = [
        {
            "symbol": r.symbol,
            "rsi": round(float(r.indicator_values.get("rsi")), 1),
            "signal": "oversold" if "RSI_OVERSOLD" in (r.signals or []) else "overbought",
        }
        for r in cache.values()
        if r.indicator_values.get("rsi") is not None
        and ("RSI_OVERSOLD" in (r.signals or []) or "RSI_OVERBOUGHT" in (r.signals or []))
    ]

    mtf_bullish_count = sum(1 for r in cache.values() if "MULTI_TIMEFRAME_BULLISH" in (r.signals or []))
    mtf_bearish_count = sum(1 for r in cache.values() if "MULTI_TIMEFRAME_BEARISH" in (r.signals or []))

    # --- Per-mover AI blurb (feature 1's enriched analyze_symbol) —
    # only for the top movers, not the whole watchlist, to bound AI
    # call volume/latency. This is the digest's only AI cost; every
    # other section above is pure quant. ---
    def _mover_dict(r) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "symbol": r.symbol,
            "score": round(_safe_call(r.calculate_signed_total_score, default=0.0), 2),
        }
        # advisory=False: the digest is a descriptive read, not a place
        # for per-mover trade plans. ``analyze_symbol`` is async and
        # this whole module runs sync (in a to_thread worker via the
        # digest router) — bridge via run_sync inside the lambda so
        # _safe_call sees the coroutine's *result*, not the coroutine.
        analysis = _safe_call(
            lambda: run_sync(analyze_symbol(r.symbol, advisory=False, portfolio_symbols=symbols)),
            default=None,
        )
        if analysis is not None and not getattr(analysis, "is_uncertain", True):
            entry["blurb"] = analysis.summary
        return entry

    # O5: parallelize mover analysis with a bounded thread pool. Each
    # worker thread calls run_sync() → the shared bridge loop drives the
    # async analyze_symbol() calls. Provider httpx clients cap at ~10
    # concurrent connections, so max_workers=4 is a conservative bound
    # that stays well under that limit while cutting wall-clock time from
    # N × per-call-latency to ~⌈N/4⌉ × per-call-latency.
    # Results are collected by submission index (not completion order) to
    # preserve the ranked ordering from above. A single failed analysis
    # degrades to a symbol+score entry (no blurb) instead of aborting the
    # whole digest.
    all_movers = list(top_bullish) + list(top_bearish)
    bullish_count = len(top_bullish)
    mover_results: list[dict[str, Any]] = [{}] * len(all_movers)

    if all_movers:
        max_workers = min(4, len(all_movers))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_mover_dict, r) for r in all_movers]
            for i, future in enumerate(futures):
                try:
                    mover_results[i] = future.result()
                except Exception as e:  # noqa: BLE001
                    r = all_movers[i]
                    symbol = getattr(r, "symbol", "unknown")
                    logger.warning(
                        "Mover analysis failed for %s: %s", symbol, e,
                    )
                    mover_results[i] = {
                        "symbol": symbol,
                        "score": round(
                            _safe_call(r.calculate_signed_total_score, default=0.0), 2,
                        ),
                    }
        movers = {
            "top_bullish": mover_results[:bullish_count],
            "top_bearish": mover_results[bullish_count:],
        }
    else:
        movers = {"top_bullish": [], "top_bearish": []}

    return {
        "watchlist_size": len(symbols),
        "market_regime": regime,
        "movers": movers,
        "rsi_extremes": rsi_extremes[:10],
        "mtf_alignment_counts": {
            "bullish": mtf_bullish_count,
            "bearish": mtf_bearish_count,
        },
    }


def narrate_digest(payload: dict[str, Any]) -> DigestNarrative:
    """Ask the AI for a short narrative on top of the structured payload.

    Never raises — AI-off or a bad reply both degrade to a plain,
    non-AI narrative built directly from the payload (still useful,
    just not AI-written), matching analyze_symbol's uncertainty
    contract: a missing/bad AI answer must not break the caller.
    """
    # Runs in a loop-less worker thread (digest router pushes
    # generate_and_store_digest through asyncio.to_thread) — bridge the
    # async manager calls.
    if not run_sync(ai_manager.is_available()):
        return _fallback_narrative(payload)

    try:
        resp = run_sync(ai_manager.complete(
            prompt=build_digest_user_prompt(payload),
            system=DIGEST_SYSTEM_PROMPT,
            max_tokens=400,
        ))
        if resp.text is None:
            return _fallback_narrative(payload)
        return parse_digest_reply(resp.text)
    except Exception as e:  # noqa: BLE001
        logger.warning("Digest narration failed: %s", e)
        return _fallback_narrative(payload)


def _fallback_narrative(payload: dict[str, Any]) -> DigestNarrative:
    regime = payload.get("market_regime", {}).get("regime", "unknown")
    bullish = payload.get("movers", {}).get("top_bullish", [])
    bearish = payload.get("movers", {}).get("top_bearish", [])
    headline_movers = [m["symbol"] for m in (bullish[:3] + bearish[:3])]
    return DigestNarrative(
        narrative=(
            f"Market regime: {regime}. "
            f"{len(bullish)} bullish and {len(bearish)} bearish movers "
            "in the watchlist. (AI narration unavailable — this is a "
            "plain summary of the structured data.)"
        ),
        headline_movers=headline_movers,
    )


def generate_and_store_digest(session: str, watchlist_id: int | None = None) -> dict[str, Any]:
    """Build the payload, narrate it, and persist an AIDigest row.

    Returns a plain dict (not the ORM row) so callers — the scheduler
    loop and the manual-trigger endpoint alike — don't need a live DB
    session held open past this call.
    """
    from backend.repositories.ai_digest_repository import AIDigestRepository

    payload = build_digest_payload(watchlist_id=watchlist_id, aggregate_all=(watchlist_id is None))
    narrative = narrate_digest(payload)

    repo = AIDigestRepository()
    try:
        row = repo.create(
            session=session,
            market_regime=payload.get("market_regime", {}).get("regime"),
            narrative=narrative.narrative,
            payload=json.dumps(payload, default=str),
        )
        return {
            "id": row.id,
            "session": row.session,
            "generated_at": row.generated_at,
            "market_regime": row.market_regime,
            "narrative": row.narrative,
            "headline_movers": narrative.headline_movers,
            "payload": payload,
        }
    finally:
        repo.close()
