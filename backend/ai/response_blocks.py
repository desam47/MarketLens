"""Typed, persisted blocks for Chat responses.

The model may still produce a concise prose reply, but the application owns
the structured envelope.  This keeps numerical/provenance metadata out of
untrusted markdown and lets the frontend render historical answers without
rerunning tools.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BlockType = Literal[
    "prose",
    "calculation",
    "evidence",
    "warning",
    "suggested_followups",
    "action_confirmation",
    "comparison_table",
    "ranked_results",
    "chart",
    "indicator_table",
    "options_chain",
    "risk_card",
    "scenario",
    "session_stats",
    "historical_outcomes",
    "report",
    "journal_save",
    "verification",
]

_STALE_AFTER_SECONDS = 900.0


class BlockQuality(BaseModel):
    """Evidence-derived quality metadata shared by every block."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["verified", "partial", "unavailable", "stale", "unknown"]
    grounded: bool
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provider: str | None = None
    source_timestamp: str | None = None
    freshness_seconds: float | None = Field(default=None, ge=0.0)
    session: str | None = None
    timeframe: str | None = None
    fallback: bool = False
    entitlement: str | None = None
    freshness_status: Literal["fresh", "recent", "stale", "unknown"] | None = None
    stale_after_seconds: float | None = None
    evidence_fingerprint: str | None = None
    material_change_detected: bool = False


class ResponseBlock(BaseModel):
    """Stable wire contract for one Chat response block."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    type: BlockType
    data: dict[str, Any]
    quality: BlockQuality


def _quality(
    *,
    grounded: bool,
    focus: list[str],
    partial: list[str],
    unavailable: list[str],
    trace: list[dict[str, Any]],
    evidence_fingerprint: str | None = None,
    material_change_detected: bool = False,
) -> BlockQuality:
    evidence_items = [item for item in trace if _is_evidence_item(item)]
    successful = _weakest_successful(evidence_items)
    failed = any(item.get("ok") is False for item in evidence_items)
    if unavailable or failed or not grounded:
        state = "unavailable" if unavailable and not focus and not partial else "partial"
    elif partial:
        state = "partial"
    elif successful and successful.get("fallback"):
        state = "stale"
    elif focus or successful:
        state = "verified"
    else:
        state = "unknown"

    confidence = 1.0 if state == "verified" else 0.5 if state == "partial" else 0.0
    freshness_seconds = successful.get("freshness_seconds") if successful else None
    if not isinstance(freshness_seconds, (int, float)):
        freshness_status = "unknown" if successful else None
    else:
        freshness_status = _age_status(successful, freshness_seconds)
    if successful and (successful.get("fallback") or freshness_status == "stale") and state not in {"unavailable", "partial"}:
        state = "stale"
        confidence = 0.5
    return BlockQuality(
        state=state,
        grounded=bool(grounded),
        confidence=confidence,
        provider=successful.get("provider") if successful else None,
        source_timestamp=successful.get("source_timestamp") if successful else None,
        freshness_seconds=freshness_seconds,
        session=successful.get("session") if successful else None,
        timeframe=successful.get("timeframe") if successful else None,
        fallback=bool(successful.get("fallback")) if successful else False,
        entitlement=successful.get("entitlement") if successful else None,
        freshness_status=freshness_status,
        stale_after_seconds=_STALE_AFTER_SECONDS if successful else None,
        evidence_fingerprint=evidence_fingerprint,
        material_change_detected=material_change_detected,
    )


_NON_EVIDENCE_KINDS = {"model", "model_call", "observability", "server_reply", "step", "regeneration"}


def _is_evidence_item(item: dict[str, Any]) -> bool:
    return bool(item.get("tool")) and item.get("kind") not in _NON_EVIDENCE_KINDS


def _age_status(item: dict[str, Any], freshness_seconds: float) -> str:
    """fresh / recent / stale for an item with a known data age.

    Daily bars are judged by date, not age: yesterday's close is the newest
    complete daily close all through today's session, so it is "recent"
    rather than stale however many seconds old it is.
    """
    if freshness_seconds <= 60:
        return "fresh"
    if str(item.get("timeframe") or "").lower() == "1d" and item.get("source_timestamp"):
        from backend.engines.market_calendar import daily_data_is_current

        return "recent" if daily_data_is_current(item["source_timestamp"]) else "stale"
    return "recent" if freshness_seconds <= _STALE_AFTER_SECONDS else "stale"


def _weakest_successful(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The successful evidence item that most limits the answer's quality.

    A fallback source first, then the oldest known data age, then data with
    no freshness at all; among equals the latest item. The turn-level label
    must reflect the stalest input, not whichever tool happened to run last.
    """
    successful = [item for item in items if item.get("ok") is True]
    if not successful:
        return None

    def rank(indexed: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        index, item = indexed
        age = item.get("freshness_seconds")
        known = isinstance(age, (int, float))
        return (1 if item.get("fallback") else 0, float(age) if known else -1.0, index)

    return max(enumerate(successful), key=rank)[1]


def _item_quality(
    item: dict[str, Any],
    *,
    evidence_fingerprint: str | None = None,
    material_change_detected: bool = False,
) -> BlockQuality:
    """Quality for a block built from exactly one tool result."""
    ok = item.get("ok") is True
    freshness_seconds = item.get("freshness_seconds")
    if not isinstance(freshness_seconds, (int, float)):
        freshness_seconds = None
        freshness_status = None if item.get("kind") == "calculation" else "unknown"
    else:
        freshness_status = _age_status(item, freshness_seconds)
    if not ok:
        state, confidence = "unavailable", 0.0
    elif item.get("fallback") or freshness_status == "stale":
        state, confidence = "stale", 0.5
    else:
        state, confidence = "verified", 1.0
    return BlockQuality(
        state=state,
        grounded=ok,
        confidence=confidence,
        provider=item.get("provider"),
        source_timestamp=item.get("source_timestamp"),
        freshness_seconds=freshness_seconds,
        session=item.get("session"),
        timeframe=item.get("timeframe"),
        fallback=bool(item.get("fallback")),
        entitlement=item.get("entitlement"),
        freshness_status=freshness_status,
        stale_after_seconds=_STALE_AFTER_SECONDS if freshness_seconds is not None else None,
        evidence_fingerprint=evidence_fingerprint,
        material_change_detected=material_change_detected,
    )


def evidence_fingerprint(value: Any) -> str | None:
    """Return a stable digest for evidence, excluding prose-only metadata."""
    if isinstance(value, list) and value and isinstance(value[0], dict) and "type" in value[0]:
        value = [
            {
                "type": block.get("type"),
                "data": block.get("data"),
                "quality": {
                    key: block.get("quality", {}).get(key)
                    for key in ("provider", "source_timestamp", "freshness_seconds", "session", "timeframe", "fallback")
                    if isinstance(block.get("quality"), dict) and block.get("quality", {}).get(key) is not None
                },
            }
            for block in value
            if isinstance(block, dict) and block.get("type") not in {"prose", "suggested_followups"}
        ]
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        value = [
            {
                key: item.get(key)
                for key in ("tool", "provider", "ok", "data", "visual_data", "symbol", "symbols", "result")
                if item.get(key) is not None
            }
            for item in value
            if isinstance(item, dict)
            and item.get("kind") not in {"model", "model_call", "observability", "server_reply", "step"}
            and (item.get("tool") or item.get("data") or item.get("visual_data"))
        ]
    if not value:
        return None
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _calculation_block(trace: list[dict[str, Any]], **quality_context: Any) -> ResponseBlock | None:
    for item in reversed(trace):
        if item.get("kind") != "calculation" or not isinstance(item.get("data"), dict):
            continue
        return ResponseBlock(
            id="calculation-1",
            type="calculation",
            data=item["data"],
            quality=_item_quality(item, **quality_context),
        )
    return None


def _visual_blocks(trace: list[dict[str, Any]], **quality_context: Any) -> list[ResponseBlock]:
    """Turn bounded tool visual payloads into typed UI blocks.

    Each block's quality comes from the one tool result that produced it.
    """
    blocks: list[ResponseBlock] = []
    for index, item in enumerate(trace, start=1):
        visual_type = item.get("visual_type")
        visual_data = item.get("visual_data")
        if visual_type not in {
            "chart", "indicator_table", "options_chain", "risk_card", "scenario",
            "session_stats", "historical_outcomes", "comparison_table", "ranked_results",
            "report", "journal_save",
        } or not isinstance(visual_data, dict):
            continue
        blocks.append(
            ResponseBlock(
                id=f"visual-{index}",
                type=visual_type,
                data=visual_data,
                quality=_item_quality(item, **quality_context),
            )
        )
    return blocks


def _trace_freshness(item: dict[str, Any]) -> tuple[str | None, float | None]:
    age = item.get("freshness_seconds")
    if not isinstance(age, (int, float)):
        return item.get("freshness_status"), _STALE_AFTER_SECONDS if item.get("freshness_status") else None
    return _age_status(item, age), _STALE_AFTER_SECONDS


def build_response_blocks(
    *,
    content: str,
    grounded: bool,
    focus: list[str],
    partial: list[str],
    unavailable: list[str],
    trace: list[dict[str, Any]] | None = None,
    preferences: dict[str, Any] | None = None,
    chart_state: dict[str, Any] | None = None,
    regeneration: dict[str, Any] | None = None,
    material_change_detected: bool = False,
    verification: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build and validate the application-owned block envelope.

    The returned dictionaries are JSON-safe and suitable for persistence.
    ``content`` remains authoritative prose for old clients and exports.

    ``preferences`` (5.7.3) is the trader's browser-local operating
    preferences (mode, timeframes, risk-per-trade, etc.) — used ONLY to
    pick which extra line ``_tailored_followups`` adds to the
    suggested_followups block. It never reaches a calculator input, a
    tool argument, or the evidence/quality metadata above, so a trader's
    preference can change which follow-up is *suggested*, never what a
    verified number *is*.
    """

    trace = list(trace or [])
    fingerprint = evidence_fingerprint(trace)
    quality = _quality(
        grounded=grounded,
        focus=focus,
        partial=partial,
        unavailable=unavailable,
        trace=trace,
        evidence_fingerprint=fingerprint,
        material_change_detected=material_change_detected,
    )
    blocks: list[ResponseBlock] = [
        ResponseBlock(
            id="prose-1",
            type="prose",
            data={"text": content},
            quality=quality,
        )
    ]
    if verification:
        blocks.append(
            ResponseBlock(
                id="verification-1",
                type="verification",
                data={
                    "version": verification.get("version"),
                    "status": verification.get("status", "degraded"),
                    "issues": list(verification.get("issues") or []),
                    "evidence_refs": list(verification.get("evidence_refs") or []),
                    "claim_count": verification.get("claim_count", 0),
                    "numeric_claim_count": verification.get("numeric_claim_count", 0),
                    "unsupported_claim_count": verification.get("unsupported_claim_count", 0),
                },
                quality=quality,
            )
        )

    evidence = []
    for item in trace:
        if item.get("kind") in {"model", "model_call", "observability", "server_reply", "step"}:
            continue
        if not item.get("tool"):
            continue
        freshness_status, stale_after_seconds = _trace_freshness(item)
        evidence.append({
            key: item.get(key)
            for key in (
                "evidence_id", "tool", "provider", "source_timestamp", "freshness_seconds", "session",
                "timeframe", "fallback", "entitlement", "warnings", "status",
            )
            if item.get(key) is not None
        } | ({"freshness_status": freshness_status, "stale_after_seconds": stale_after_seconds} if freshness_status else {})
          | ({"evidence_values": item["evidence_values"]} if isinstance(item.get("evidence_values"), dict) else {}))
    if focus or partial or unavailable or evidence or chart_state or regeneration:
        blocks.append(
            ResponseBlock(
                id="evidence-1",
                type="evidence",
                data={
                    "symbols": {
                        "verified": list(focus),
                        "partial": list(partial),
                        "unavailable": list(unavailable),
                    },
                    "items": evidence,
                    **({"chart_state": chart_state} if chart_state else {}),
                    **({"regeneration": regeneration} if regeneration else {}),
                    **({"evidence_fingerprint": fingerprint} if fingerprint else {}),
                },
                quality=quality,
            )
        )

    # Data blocks carry the quality of their own source; answer-level blocks
    # (prose, evidence, warnings, actions) carry the turn's weakest input.
    item_context = {"evidence_fingerprint": fingerprint, "material_change_detected": material_change_detected}
    calculation = _calculation_block(trace, **item_context)
    if calculation is not None:
        blocks.append(calculation)
    blocks.extend(_visual_blocks(trace, **item_context))

    warnings: list[str] = []
    for item in trace:
        warnings.extend(str(warning) for warning in item.get("warnings") or [])
        if item.get("error"):
            warnings.append(str(item["error"]))
    if unavailable:
        warnings.append(f"No verified data was available for: {', '.join(unavailable)}.")
    if partial:
        warnings.append(f"Partial coverage only for: {', '.join(partial)}.")
    if warnings:
        blocks.append(
            ResponseBlock(
                id="warning-1",
                type="warning",
                data={"items": list(dict.fromkeys(warnings))},
                quality=quality,
            )
        )

    action_steps = [
        item
        for item in trace
        if item.get("kind") == "step"
        and item.get("tool") in {
            "create_alert",
            "modify_alert",
            "delete_alert",
            "add_to_watchlist",
            "remove_from_watchlist",
            "create_watchlist",
            "delete_watchlist",
            "save_to_journal",
            "export_report",
        }
    ]
    if action_steps:
        blocks.append(
            ResponseBlock(
                id="action-1",
                type="action_confirmation",
                data={
                    "actions": [
                        {
                            "tool": item.get("tool"),
                            "status": item.get("status", "completed"),
                            "reason": item.get("reason"),
                            "detail": item.get("detail"),
                        }
                        for item in action_steps
                    ]
                },
                quality=quality,
            )
        )

    blocks.append(
        ResponseBlock(
            id="followups-1",
            type="suggested_followups",
            data={"items": _tailored_followups(preferences)},
            quality=quality,
        )
    )
    return [block.model_dump(mode="json") for block in blocks]


# Mode -> one extra suggestion appended after the two baseline ones. Each
# points at data the mode's own trader would check next — never a claim
# about what the verified answer says, so it's safe with no model or
# tool-evidence involvement.
_MODE_FOLLOWUP: dict[str, str] = {
    "day_trading": "Check the 1m/5m trend",
    "swing_trading": "Check the daily/4h trend",
    "options": "Check the options chain",
    "long_term_investing": "Check fundamentals",
}


def _tailored_followups(preferences: dict[str, Any] | None) -> list[str]:
    """The suggested_followups block's items — the baseline two, plus one
    extra keyed off the trader's stored mode (5.7.3) if set. Pure lookup,
    no model call, so a given mode always adds the same suggestion."""
    items = ["Show the source data", "Recheck with current data"]
    mode = (preferences or {}).get("mode")
    extra = _MODE_FOLLOWUP.get(mode) if isinstance(mode, str) else None
    if extra:
        items.append(extra)
    return items


__all__ = ["BlockQuality", "ResponseBlock", "build_response_blocks"]
