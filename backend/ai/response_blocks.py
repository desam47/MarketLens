"""Typed, persisted blocks for Chat responses.

The model may still produce a concise prose reply, but the application owns
the structured envelope.  This keeps numerical/provenance metadata out of
untrusted markdown and lets the frontend render historical answers without
rerunning tools.
"""

from __future__ import annotations

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
]


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
) -> BlockQuality:
    successful = next((item for item in reversed(trace) if item.get("ok") is True), None)
    failed = any(item.get("ok") is False for item in trace)
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
    return BlockQuality(
        state=state,
        grounded=bool(grounded),
        confidence=confidence,
        provider=successful.get("provider") if successful else None,
        source_timestamp=successful.get("source_timestamp") if successful else None,
        freshness_seconds=successful.get("freshness_seconds") if successful else None,
        session=successful.get("session") if successful else None,
        timeframe=successful.get("timeframe") if successful else None,
        fallback=bool(successful.get("fallback")) if successful else False,
    )


def _calculation_block(trace: list[dict[str, Any]], quality: BlockQuality) -> ResponseBlock | None:
    for item in reversed(trace):
        if item.get("kind") != "calculation" or not isinstance(item.get("data"), dict):
            continue
        return ResponseBlock(
            id="calculation-1",
            type="calculation",
            data=item["data"],
            quality=quality,
        )
    return None


def _visual_blocks(trace: list[dict[str, Any]], quality: BlockQuality) -> list[ResponseBlock]:
    """Turn bounded tool visual payloads into typed UI blocks."""
    blocks: list[ResponseBlock] = []
    for index, item in enumerate(trace, start=1):
        visual_type = item.get("visual_type")
        visual_data = item.get("visual_data")
        if visual_type not in {
            "chart", "indicator_table", "options_chain", "risk_card", "scenario",
            "session_stats", "historical_outcomes", "comparison_table", "ranked_results",
        } or not isinstance(visual_data, dict):
            continue
        blocks.append(
            ResponseBlock(
                id=f"visual-{index}",
                type=visual_type,
                data=visual_data,
                quality=quality,
            )
        )
    return blocks


def build_response_blocks(
    *,
    content: str,
    grounded: bool,
    focus: list[str],
    partial: list[str],
    unavailable: list[str],
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build and validate the application-owned block envelope.

    The returned dictionaries are JSON-safe and suitable for persistence.
    ``content`` remains authoritative prose for old clients and exports.
    """

    trace = list(trace or [])
    quality = _quality(
        grounded=grounded,
        focus=focus,
        partial=partial,
        unavailable=unavailable,
        trace=trace,
    )
    blocks: list[ResponseBlock] = [
        ResponseBlock(
            id="prose-1",
            type="prose",
            data={"text": content},
            quality=quality,
        )
    ]

    evidence = [
        {
            key: item.get(key)
            for key in (
                "tool",
                "provider",
                "source_timestamp",
                "freshness_seconds",
                "session",
                "timeframe",
                "fallback",
                "warnings",
                "status",
            )
            if item.get(key) is not None
        }
        for item in trace
        if item.get("tool") or item.get("provider")
    ]
    if focus or partial or unavailable or evidence:
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
                },
                quality=quality,
            )
        )

    calculation = _calculation_block(trace, quality)
    if calculation is not None:
        blocks.append(calculation)
    blocks.extend(_visual_blocks(trace, quality))

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
            data={
                "items": [
                    "Show the source data",
                    "Recheck with current data",
                ]
            },
            quality=quality,
        )
    )
    return [block.model_dump(mode="json") for block in blocks]


__all__ = ["BlockQuality", "ResponseBlock", "build_response_blocks"]
