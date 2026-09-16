"""
Market-wide baseline for the universal AI Hub chat (2026-09-10).

A cheap, always-attached context block so a no-ticker question ("what's
the market doing?", "which of my names look weak?") is answerable
without building per-symbol context. Pure in-process reads only — NO
HTTP, NO AI call, NO fresh scan:

  - the latest stored AI digest (movers / RSI extremes / MTF counts),
  - the live market-regime signal (warm singleton),
  - the current scanner scores for watchlist symbols already in cache.

Every section is best-effort; a failure degrades that section to ``{}``
and never raises. In particular it must NEVER call
``build_digest_payload()`` — that runs ``analyze_symbol`` per mover.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)

# Short in-process cache — the baseline is assembled from the latest
# digest, the live-regime singleton and the scanner's cached scores,
# none of which move meaningfully within a few seconds, but chat rebuilt
# it on every single message.
_CACHE_TTL = 20.0
_cache_lock = threading.Lock()
_cached: dict[str, Any] | None = None
_cached_at = 0.0


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        logger.info("market_baseline section failed: %s", e)
        return default


def _latest_digest() -> dict[str, Any]:
    from backend.repositories.ai_digest_repository import AIDigestRepository

    repo = AIDigestRepository()
    try:
        row = repo.get_latest("close") or repo.get_latest("premarket")
        if row is None:
            return {}
        payload = {}
        if row.payload:
            try:
                payload = json.loads(row.payload)
            except (ValueError, TypeError):
                payload = {}
        return {
            "session": row.session,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "market_regime": row.market_regime,
            "narrative": row.narrative,
            "movers": payload.get("movers", {}),
            "rsi_extremes": payload.get("rsi_extremes", []),
            "mtf_alignment_counts": payload.get("mtf_alignment_counts", {}),
        }
    finally:
        repo.close()


def _live_regime() -> dict[str, Any]:
    from backend.api.market_context.router import get_engine

    sig = get_engine().get_current_context()
    return sig.to_dict() if sig is not None else {}


def _active_alerts() -> list[dict[str, Any]]:
    """Every enabled alert, for the chat's delete_alert tool to match
    against ("delete my NVDA alert" -> find it here, never invent an
    id). Pure in-process DB read — no HTTP, no AI."""
    from backend.repositories.alert_repository import AlertRepository

    repo = AlertRepository()
    try:
        return [
            {
                "id": a.id, "symbol": a.symbol, "name": a.name,
                "condition_type": a.condition_type, "parameter": a.parameter,
            }
            for a in repo.get_all_enabled()
        ]
    finally:
        repo.close()


def _watchlist_index() -> list[dict[str, Any]]:
    """Every real watchlist's name + member count (NOT its contents —
    see CHAT_SYSTEM_PROMPT rule 10). Lets the model recognize and refer
    to a real watchlist by name (e.g. offering the actual options when a
    request is ambiguous) instead of operating fully blind, while the
    app still authoritatively resolves any actual action/read against a
    named watchlist itself (backend.ai.chat._resolve_watchlist,
    _resolve_named_watchlist_symbols) rather than trusting the model's
    own judgement for the real side effect or the real member list.
    """
    from backend.database import SessionLocal
    from backend.repositories.watchlist_repository import WatchlistRepository

    db = SessionLocal()
    try:
        repo = WatchlistRepository(db)
        return [
            {
                "name": wl.name,
                "symbol_count": len([s for s in wl.symbols if s.is_enabled]),
            }
            for wl in repo.get_watchlists(active_only=True)
        ]
    finally:
        db.close()


def _watchlist_snapshot() -> dict[str, Any]:
    from backend.api.main_helpers import _watched_symbols
    from backend.scanner.scanner import market_scanner

    known = sorted(set(_watched_symbols()))
    scored = []
    for sym in known:
        r = market_scanner.scan_results.get(sym)
        if r is None:
            continue
        try:
            score = round(float(r.calculate_signed_total_score()), 2)
        except Exception:  # noqa: BLE001
            continue
        scored.append({"symbol": sym, "signed_score": score, "signals": list(r.signals)})
    scored.sort(key=lambda d: d["signed_score"], reverse=True)
    return {"scored": scored, "all_symbols": known}


def invalidate_cache() -> None:
    """Drop the cached snapshot so the next ``build_market_baseline()``
    call rebuilds instead of serving a stale one — call this right
    after something the baseline reports on changes (an alert/watchlist
    CRUD action from chat; see ``backend.ai.chat._run_action``'s
    ``_BASELINE_MUTATING_ACTIONS``).

    Without this, a chat action followed by a related question within
    ``_CACHE_TTL`` seconds (e.g. "create an NVDA alert" then "change it
    to 230") could see a pre-mutation snapshot — the cache's own
    docstring assumes nothing it reads "moves meaningfully" within a
    few seconds, which is false for a mutation the trader's own last
    message just caused.
    """
    global _cached, _cached_at
    with _cache_lock:
        _cached, _cached_at = None, 0.0


def build_market_baseline(*, use_cache: bool = True) -> dict[str, Any]:
    """Assemble the market-wide baseline block. Never raises.

    Cached for ``_CACHE_TTL`` seconds — pass ``use_cache=False`` to force
    a rebuild. ``as_of`` reflects when the cached snapshot was taken, so
    a consumer can see its age.
    """
    global _cached, _cached_at
    if use_cache:
        with _cache_lock:
            if _cached is not None and (time.monotonic() - _cached_at) < _CACHE_TTL:
                return _cached

    result = {
        "as_of": now_ny().isoformat(),
        "regime_live": _safe(_live_regime, {}),
        "digest": _safe(_latest_digest, {}),
        "watchlist_snapshot": _safe(_watchlist_snapshot, {"scored": [], "all_symbols": []}),
        "watchlists": _safe(_watchlist_index, []),
        "active_alerts": _safe(_active_alerts, []),
    }
    with _cache_lock:
        _cached, _cached_at = result, time.monotonic()
    return result
