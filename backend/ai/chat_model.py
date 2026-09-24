"""Chat's model calls: routing, completion, streaming, retries and turn budgets.

The only Chat module that holds ``ai_manager``.
"""

from __future__ import annotations

import logging
import re
import time

from backend.ai.manager import ai_manager
from backend.ai.prompt import (
    CHAT_SYSTEM_PROMPT,
    parse_chat_reply,
)
from backend.ai.provider import StreamAttribution
from backend.ai.reply_stream import ReplyExtractor

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.
from backend.ai.sync_bridge import run_sync, stream_sync
from backend.config.settings import settings

logger = logging.getLogger(__name__)


# Per-turn budget defaults (plan 5.3.1); settings override each one.
_MAX_TOOL_CALLS = 5
_MAX_PLANNING_CALLS = 2
_MAX_TURN_TOKENS = 60_000
_MAX_TURN_SECONDS = 30.0
_CONTINUATION_MAX_TOKENS = 300


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(getattr(settings.ai, name, default)), high))
    except (TypeError, ValueError):
        return default


def _tool_call_limit() -> int:
    """Actions one turn may execute, the first included."""
    limit = _bounded_int("chat_max_tool_calls", _MAX_TOOL_CALLS, 1, 10)
    legacy = getattr(settings.ai, "chat_max_chain_steps", None)
    if isinstance(legacy, int) and legacy > 0:
        # Deprecated AI_CHAT_MAX_CHAIN_STEPS still caps an existing setup.
        limit = min(limit, legacy)
    return limit


def _planning_call_limit() -> int:
    """Follow-up model calls one turn may spend choosing the next step."""
    return _bounded_int("chat_max_planning_calls", _MAX_PLANNING_CALLS, 0, 8)


def _turn_token_budget() -> int:
    """Estimated tokens one turn may spend across all model calls."""
    return _bounded_int("chat_max_turn_tokens", _MAX_TURN_TOKENS, 4_000, 400_000)


def _estimate_tokens(*texts: str | None) -> int:
    """The same chars/4 estimate the prompt builder uses for its size guard."""
    return sum(len(text or "") for text in texts) // 4 + 1


def _turn_tokens_used(trace: list[dict] | None) -> int:
    return sum(
        int(item.get("estimated_tokens") or 0)
        for item in (trace or [])
        if item.get("kind") == "model_call"
    )


def _prompt_token_budget(system: str, max_output_tokens: int) -> int:
    """Prompt-size budget for one model call.

    Keeps the historical ``max_tokens - 500`` sizing, but never lets a single
    call's prompt, system text, and reply exceed the whole turn's budget.
    """
    historical = max(2000, ai_manager.settings.max_tokens - 500)
    remaining = _turn_token_budget() - _estimate_tokens(system) - max_output_tokens
    return max(1000, min(historical, remaining))


def _turn_budget_seconds() -> float:
    """Read the bounded wall-clock budget for chained planning safely."""
    configured = getattr(settings.ai, "chat_max_turn_seconds", _MAX_TURN_SECONDS)
    try:
        return max(1.0, min(float(configured), 120.0))
    except (TypeError, ValueError):
        return _MAX_TURN_SECONDS


# One extra attempt on a failed completion/parse before giving up (2026-
# 09-16) — this environment's local model has shown intermittent
# malformed-JSON replies where an immediate identical retry succeeds
# (observed live, not hypothetical), so trading a little latency for
# meaningfully fewer user-visible "I couldn't process that" replies is
# worth it. Total attempts = 1 + this.
_CHAT_PARSE_RETRIES = 1
CHAT_PARSE_MAX_ATTEMPTS = _CHAT_PARSE_RETRIES + 1


def _ai_enabled() -> bool:
    return bool(ai_manager.enabled)


def _ai_setting(name: str):
    """An AI setting, read through the one module that holds ``ai_manager``."""
    return getattr(ai_manager.settings, name)


def _chat_route_model(role: str, fallback: str | None = None) -> str | None:
    """Resolve a role-specific model, falling back to the legacy override."""
    configured = getattr(ai_manager.settings, f"chat_{role}_model", "")
    if isinstance(configured, str) and configured.strip():
        return configured
    legacy = getattr(ai_manager.settings, "chat_model", "")
    if isinstance(legacy, str) and legacy.strip():
        return legacy
    return fallback


def _complete_and_parse(
    prompt: str,
    system: str,
    max_tokens: int,
    model: str | None,
    repair_model: str | None = None,
    *,
    trace: list[dict] | None = None,
    role: str = "synthesis",
):
    """One or more attempts at an AI completion + ``ChatReplyResponse``
    parse, retrying ``_CHAT_PARSE_RETRIES`` more time(s) on either a raw
    provider failure or a malformed reply before giving up.

    Returns ``(parsed, failure_reason)`` — ``parsed`` is ``None`` on
    total failure, with ``failure_reason`` ("ai_error" or "parse_error",
    reflecting the LAST attempt) for the caller to pick a fallback
    message. Never raises.
    """
    failure_reason = "ai_error"
    for attempt in range(_CHAT_PARSE_RETRIES + 1):
        call_started = time.perf_counter()
        requested_model = repair_model if attempt else model
        try:
            resp = run_sync(
                ai_manager.complete(
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    model=requested_model,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat AI call raised (attempt %d): %s", attempt + 1, e)
            failure_reason = "ai_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_exception",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system),
                    "requested_model": requested_model,
                    "provider_request_count": 1,
                })
            continue
        provider = getattr(resp, "provider", None)
        response_model = getattr(resp, "model", None)
        attempted_providers = list(getattr(resp, "attempted_providers", None) or [])
        provider_request_count = len(attempted_providers) or 1
        if resp.text is None:
            logger.warning("Chat AI call returned no text (attempt %d)", attempt + 1)
            failure_reason = "ai_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_no_text",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system, resp.text),
                    "provider": provider,
                    "model": response_model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": attempted_providers,
                })
            continue
        try:
            parsed = parse_chat_reply(resp.text)
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": True,
                    "status": "parsed",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system, resp.text),
                    "provider": provider,
                    "model": response_model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": attempted_providers,
                })
            return parsed, None
        except Exception as e:  # noqa: BLE001
            logger.info("Chat reply failed to parse (attempt %d): %s", attempt + 1, e)
            failure_reason = "parse_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "parse_error",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system, resp.text),
                    "provider": provider,
                    "model": response_model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": attempted_providers,
                })
    return None, failure_reason


# The model's JSON is read while it streams. "action" usually comes after
# "reply", so these only catch an action the model writes first.
_STREAMED_ACTION_RE = re.compile(r'"action"\s*:\s*"(?P<name>[a-z_]+)"')
_STREAMED_REANALYSIS_RE = re.compile(r'"wants_reanalysis"\s*:\s*true')


def _stream_and_parse(
    prompt: str,
    model: str | None,
    repair_model: str | None,
    *,
    trace: list[dict] | None,
    hold: bool,
):
    """Stream the synthesis call, yielding the ``("delta", text)`` the gate allows.

    The streaming counterpart of :func:`_complete_and_parse`: same attempts,
    trace records and failure reasons, returned as ``(parsed,
    failure_reason)`` when the generator finishes. Deltas stop for the rest
    of an attempt once the JSON shows an action or a reanalysis, whose
    placeholder text the app replaces; ``hold`` stops them from the start.
    """
    failure_reason = "ai_error"
    for attempt in range(_CHAT_PARSE_RETRIES + 1):
        call_started = time.perf_counter()
        requested_model = repair_model if attempt else model
        raw = ""
        extractor = ReplyExtractor()
        attribution = StreamAttribution()
        blocked = hold
        try:
            for chunk in stream_sync(
                ai_manager.stream(
                    prompt,
                    system=CHAT_SYSTEM_PROMPT,
                    max_tokens=500,
                    model=requested_model,
                    attribution=attribution,
                )
            ):
                raw += chunk
                delta = extractor.feed(raw)
                if not blocked:
                    action = _STREAMED_ACTION_RE.search(raw)
                    blocked = bool(
                        (action is not None and action.group("name") != "none")
                        or _STREAMED_REANALYSIS_RE.search(raw)
                    )
                if delta and not blocked:
                    yield ("delta", delta)
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat streaming AI call raised (attempt %d): %s", attempt + 1, e)
            failure_reason = "ai_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": "synthesis",
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_exception",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, CHAT_SYSTEM_PROMPT),
                    "requested_model": requested_model,
                    "provider_request_count": 1,
                })
            continue

        record = {
            "kind": "model_call",
            "role": "synthesis",
            "attempt": attempt + 1,
            "prompt_chars": len(prompt),
            "estimated_tokens": _estimate_tokens(prompt, CHAT_SYSTEM_PROMPT, raw),
            "provider": attribution.provider,
            "model": attribution.model,
            "requested_model": requested_model,
            "provider_request_count": len(attribution.attempted_providers or []) or 1,
            "providers_tried": list(attribution.attempted_providers or []),
        }
        if not raw.strip():
            failure_reason = "ai_error"
            if trace is not None:
                trace.append({
                    **record,
                    "ok": False,
                    "failure_kind": "provider_no_text",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                })
            continue
        try:
            parsed = parse_chat_reply(raw)
        except Exception as e:  # noqa: BLE001
            logger.info("Chat reply failed to parse (attempt %d): %s", attempt + 1, e)
            failure_reason = "parse_error"
            if trace is not None:
                trace.append({
                    **record,
                    "ok": False,
                    "failure_kind": "parse_error",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                })
            continue
        if trace is not None:
            trace.append({
                **record,
                "ok": True,
                "status": "parsed",
                "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
            })
        return parsed, None
    return None, failure_reason


def _trace_model_route(trace: list[dict] | None, role: str, model: str | None) -> None:
    if trace is not None:
        trace.append({"kind": "model", "role": role, "model": model or "deterministic"})
