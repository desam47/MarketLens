"""Sanitized, bounded Chat observability for Phase 5.8.

This module produces metadata suitable for the persisted planner trace.  It
never receives or stores the full prompt, reply, journal text, or credentials.
"""

from __future__ import annotations

import re
import time
from typing import Any

OBSERVABILITY_VERSION = "5.8.0"
MAX_ARGUMENT_KEYS = 24
MAX_ARGUMENT_TEXT = 160

# Explicit budgets are intentionally conservative and are a release target,
# not a promise that a provider or a cold database can always meet them.
PERFORMANCE_TARGETS_MS: dict[str, float] = {
    "calculation_only": 500.0,
    "cached_data": 1500.0,
    "database": 3000.0,
    "provider_backed": 15000.0,
}

_SECRET_KEY = re.compile(r"(?:api.?key|secret|token|password|credential|authorization|cookie)", re.I)
_PRIVATE_KEY = re.compile(r"(?:journal|private|prompt|transcript|content|message|thesis|invalidation)", re.I)
_PRIVATE_EXACT_KEYS = {"entry", "notes", "note", "comment", "comments", "description", "text"}
_RAW_ERROR_MARKER = re.compile(
    r"(?:traceback|jsondecodeerror|httpx\.|requests\.|response\s+body|status_code|"
    r"api[\s_-]?key|authorization|bearer\s+|cookie|password|secret|token\s*=|"
    r"\bat\s+0x[0-9a-f]+|\bfile\s+['\"])",
    re.I,
)

_SAFE_ERROR_BY_KIND = {
    "timeout": "That data request timed out. Please retry.",
    "tool_timeout": "That data request timed out. Please retry.",
    "provider_exception": "The AI provider is unavailable right now. Please retry.",
    "provider_no_text": "The AI provider returned no usable answer. Please retry.",
    "parse_error": "The AI provider returned an unusable answer. Please retry.",
    "action_exception": "The requested operation failed safely. Please retry.",
    "calculation_error": "I couldn't complete that calculation safely.",
    "tool_error": "I couldn't retrieve that data safely.",
}


def sanitize_arguments(value: Any, *, depth: int = 0) -> Any:
    """Return a small JSON-safe argument shape with secrets/private prose removed."""
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= MAX_ARGUMENT_KEYS:
                output["[truncated_keys]"] = True
                break
            key_text = str(key)
            if _SECRET_KEY.search(key_text):
                output[key_text] = "[redacted]"
            elif key_text.lower() in _PRIVATE_EXACT_KEYS or _PRIVATE_KEY.search(key_text):
                output[key_text] = "[private omitted]"
            else:
                output[key_text] = sanitize_arguments(child, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [sanitize_arguments(item, depth=depth + 1) for item in list(value)[:MAX_ARGUMENT_KEYS]]
    if isinstance(value, str):
        return value[:MAX_ARGUMENT_TEXT] + ("…" if len(value) > MAX_ARGUMENT_TEXT else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_ARGUMENT_TEXT]


def sanitize_error_message(
    error: Any,
    *,
    failure_kind: str | None = None,
    default: str = "I couldn't retrieve that data safely.",
) -> str:
    """Return a bounded, non-sensitive error suitable for Chat or traces.

    Provider exceptions often contain response bodies, URLs, credentials, or
    stack frames. Those details are useful in server logs but must not cross
    into the visible reply, response blocks, API trace, or persisted message.
    Short domain errors such as ``Watchlist not found`` remain useful.
    """
    mapped = _SAFE_ERROR_BY_KIND.get(str(failure_kind or "").lower())
    if mapped:
        return mapped
    if error is None:
        return default
    text = str(error).strip()
    if not text:
        return default
    first_line = text.splitlines()[0].strip()
    if not first_line or len(first_line) > 240 or _RAW_ERROR_MARKER.search(first_line):
        return default
    # JSON and list payloads are overwhelmingly provider/error-body material;
    # never copy them into Chat even when their text looks innocuous.
    if first_line.startswith(("{", "[")) or ("{" in first_line and "}" in first_line):
        return default
    return first_line


def sanitize_warnings(warnings: Any) -> list[str]:
    """Keep data-quality warnings bounded before they enter Chat blocks."""
    if not isinstance(warnings, (list, tuple)):
        return []
    return [
        sanitize_error_message(warning, default="The tool returned a data-quality warning.")
        for warning in list(warnings)[:8]
    ]


def _target_class(trace: list[dict[str, Any]]) -> str:
    model_calls = [item for item in trace if item.get("kind") == "model_call"]
    if model_calls or any(item.get("provider_request_count", 0) for item in trace):
        return "provider_backed"
    tools = [item for item in trace if item.get("tool") and item.get("kind") != "step"]
    if any(item.get("tool") == "calculate" for item in tools):
        return "calculation_only"
    if tools:
        return "database"
    return "cached_data"


def build_turn_observability(
    trace: list[dict[str, Any]],
    *,
    started_at: float,
    prompt_chars: int = 0,
) -> dict[str, Any]:
    """Summarize one turn without copying sensitive request/response text."""
    tool_calls = [item for item in trace if item.get("tool") and item.get("kind") != "step"]
    model_calls = [item for item in trace if item.get("kind") == "model_call"]
    failed = [
        item.get("failure_kind") or item.get("status") or "tool_failure"
        for item in [*tool_calls, *model_calls]
        if item.get("ok") is False
    ]
    total_ms = round((time.perf_counter() - started_at) * 1000, 3)
    target_class = _target_class(trace)
    target_ms = PERFORMANCE_TARGETS_MS[target_class]
    return {
        "kind": "observability",
        "version": OBSERVABILITY_VERSION,
        "turn_duration_ms": total_ms,
        "target_class": target_class,
        "target_ms": target_ms,
        "within_target": total_ms <= target_ms,
        "prompt_chars": min(max(int(prompt_chars), 0), 50_000),
        "tool_calls": len(tool_calls),
        "model_calls": len(model_calls),
        "provider_requests": sum(int(item.get("provider_request_count", 0) or 0) for item in trace),
        "cache_hits": sum(1 for item in trace if item.get("cache_hit") is True),
        "retry_count": sum(max(0, int(item.get("attempt", 1)) - 1) for item in model_calls),
        "failure_kinds": list(dict.fromkeys(str(item) for item in failed)),
        "evidence_refs": [str(item["evidence_id"]) for item in trace if item.get("evidence_id")],
    }


__all__ = [
    "OBSERVABILITY_VERSION",
    "PERFORMANCE_TARGETS_MS",
    "build_turn_observability",
    "sanitize_arguments",
    "sanitize_error_message",
    "sanitize_warnings",
]
