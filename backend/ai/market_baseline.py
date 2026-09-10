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
from typing import Any

from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)


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


def build_market_baseline() -> dict[str, Any]:
    """Assemble the market-wide baseline block. Never raises."""
    return {
        "as_of": now_ny().isoformat(),
        "regime_live": _safe(_live_regime, {}),
        "digest": _safe(_latest_digest, {}),
        "watchlist_snapshot": _safe(_watchlist_snapshot, {"scored": [], "all_symbols": []}),
    }
