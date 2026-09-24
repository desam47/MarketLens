"""Application-owned verification for persisted Chat answers.

The model is allowed to write prose, but it is not allowed to decide whether
that prose is grounded.  This module checks the small set of claims that can
be validated cheaply and safely from the bounded tool trace already produced
by Chat.  It intentionally returns uncertainty instead of trying to repair a
partially unverifiable answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.ai.calculator import CalculationRequest, calculate

VERIFIER_VERSION = "5.8.1"

_NUMBER_RE = re.compile(
    r"(?P<prefix>[$€£])?\s*(?P<number>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<unit>%|percent|pct|bps|[kKmMbB])?"
)
_TICKER_RE = re.compile(
    r"(?:\$([A-Z]{1,6})\b|\b(?:ticker|symbol)\s*(?:is|:)?\s*([A-Z]{1,6})\b|\b([A-Z]{2,6})\s+stock\b)"
)
_BARE_TICKER_RE = re.compile(r"\b[A-Z]{2,6}\b")
_NON_TICKER_CAPS = {
    "ADX", "AI", "API", "ATR", "BBO", "BUY", "CPI", "DMI", "EMA", "ETF",
    "FOMC", "GDP", "HOLD", "IV", "MACD", "NAV", "OHLC", "OI", "PE", "RSI",
    "SEC", "SELL", "SMA", "USD", "UTC", "VWAP",
}
_TIMEFRAME_RE = re.compile(r"\b(1m|2m|3m|5m|15m|30m|1h|4h|1d|1wk)\b", re.I)
_SESSION_RE = re.compile(r"\b(premarket|pre-market|regular|regular session|after[- ]hours?|extended)\b", re.I)
_LIVE_RE = re.compile(r"\b(current(?:ly)?|live|latest|today|now|real[- ]?time)\b", re.I)
_POSITIVE_DIRECTION_RE = re.compile(r"\b(up|higher|gain(?:s|ed)?|positive|bullish|rising|increase(?:d)?)\b", re.I)
_NEGATIVE_DIRECTION_RE = re.compile(r"\b(down|lower|loss(?:es|ed)?|negative|bearish|fall(?:s|en)?|decrease(?:d)?)\b", re.I)
# Wording that attributes a number to the trader's own plan/inputs rather
# than to observed market data.
_USER_INPUT_CONTEXT_RE = re.compile(r"\b(you|your|you're|you've|planned|stop|target|entry|limit|budget)\b", re.I)
# "follow up", "set up", "up to", "break down", ... are not price-direction
# claims; they are removed before direction checks.
_PHRASAL_DIRECTION_RE = re.compile(
    r"\b(?:follow|set|sign|look|pick|back|show|build|line|pull|bring|come|open|clean|call|speed|keep|catch|"
    r"break|narrow|drill|write|scale|slow|shut|track|wind|boil|cut|step|tone|settle)[- ]?(?:up|down)\b"
    r"|\b(?:up|down)\s+to\b|\bups and downs\b|\bstop[- ]loss(?:es)?\b",
    re.I,
)


@dataclass(frozen=True)
class AnswerVerification:
    """The server-authored verification result persisted in response blocks."""

    version: str
    status: Literal["verified", "degraded", "blocked"]
    issues: list[str]
    evidence_refs: list[str]
    claim_count: int
    numeric_claim_count: int
    unsupported_claim_count: int
    safe_content: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "issues": list(self.issues),
            "evidence_refs": list(self.evidence_refs),
            "claim_count": self.claim_count,
            "numeric_claim_count": self.numeric_claim_count,
            "unsupported_claim_count": self.unsupported_claim_count,
        }


def assign_evidence_ids(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable, per-answer IDs to read-only tool results.

    Action steps and model-route entries are deliberately excluded: an action
    receipt is not market evidence, and a provider/model name alone is not a
    source.  The function mutates the turn-local trace so the same IDs appear
    in the persisted planner trace and response blocks.
    """

    next_id = 1
    for item in trace:
        if not isinstance(item, dict) or item.get("kind") == "step" or not item.get("tool"):
            continue
        item.setdefault("evidence_id", f"ev-{next_id}")
        next_id += 1
    return trace


def _successful_evidence(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in trace
        if item.get("evidence_id") and item.get("ok") is True and item.get("kind") != "step"
    ]


def _flatten_numbers(value: Any, prefix: str = "", *, depth: int = 0) -> dict[str, float]:
    """Flatten a bounded JSON-like value into numeric leaves."""

    if depth > 4:
        return {}
    if isinstance(value, bool):
        return {}
    if isinstance(value, (int, float)):
        return {prefix or "value": float(value)}
    if isinstance(value, dict):
        output: dict[str, float] = {}
        for key, child in list(value.items())[:80]:
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            output.update(_flatten_numbers(child, child_prefix, depth=depth + 1))
        return output
    if isinstance(value, list):
        output = {}
        for index, child in enumerate(value[:80]):
            output.update(_flatten_numbers(child, f"{prefix}[{index}]", depth=depth + 1))
        return output
    return {}


def _evidence_numbers(item: dict[str, Any]) -> dict[str, float]:
    values = item.get("evidence_values")
    if isinstance(values, dict):
        output = {str(key): float(value) for key, value in values.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}
    else:
        output = {}
    output.update(_flatten_numbers(item.get("data"), "data"))
    return output


def _numbers_from_text(text: str) -> list[tuple[float, str | None, str | None]]:
    claims: list[tuple[float, str | None, str | None]] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group("number")
        start, end = match.span("number")
        if any(timeframe_match.start() <= start and end <= timeframe_match.end() for timeframe_match in _TIMEFRAME_RE.finditer(text)):
            continue
        before = text[max(0, start - 2):start].lower()
        after = text[end:min(len(text), end + 3)].lower()
        # Dates, ordinals, and timeframe tokens are not market-number claims.
        if re.search(r"\d[-/]\d|[-/]\d", text[max(0, start - 5):min(len(text), end + 5)]):
            continue
        if after.startswith(("m", "h", "d", "wk")):
            continue
        if before.endswith("#") or after.startswith(("st", "nd", "rd", "th")):
            continue
        unit = match.group("unit")
        if unit:
            unit = unit.lower()
        if match.group("prefix"):
            unit = "currency"
        snippet_start = max(0, start - 24)
        snippet_end = min(len(text), end + 24)
        snippet = text[snippet_start:snippet_end]
        relative_start = start - snippet_start
        relative_end = end - snippet_start
        context = snippet[:relative_start] + "__CLAIM__" + snippet[relative_end:]
        claims.append((float(raw.replace(",", "")), unit, context))
    return claims


def _unit_family(unit: str | None) -> str | None:
    if unit in {"%", "percent", "pct"}:
        return "percent"
    if unit == "bps":
        return "bps"
    if unit == "currency":
        return "currency"
    return None


def _evidence_unit_families(numbers: dict[str, float]) -> set[str]:
    families: set[str] = set()
    for key in numbers:
        normalized = key.lower().replace("_", " ")
        if any(token in normalized for token in ("percent", "pct", "rate", "return", "change", "yield", "volatility", "drawdown", "iv")):
            families.add("percent")
        if "bps" in normalized:
            families.add("bps")
        if any(token in normalized for token in ("price", "value", "cost", "premium", "strike", "close", "open", "high", "low")):
            families.add("currency")
    return families


def _safe_uncertainty(issues: list[str]) -> str:
    if "unknown_ticker" in issues:
        return "I couldn't verify that answer because it referenced unsupported market data. Please provide a supported ticker and retry."
    if "live_claim_without_freshness" in issues or "stale_live_claim" in issues:
        return "I couldn't verify that as current because the available market data is missing a trustworthy freshness timestamp."
    if "unsupported_numeric_claim" in issues or "unit_mismatch" in issues:
        return "I couldn't verify one or more numbers in that answer against the available source data."
    return "I couldn't verify that answer against the available evidence. Please retry with current data or ask for the source data."


def _claim_symbol(context: str | None, allowed: set[str]) -> str | None:
    if not context:
        return None
    marker = context.find("__CLAIM__")
    before = context[:marker] if marker >= 0 else context
    candidates = [match.group(0).upper() for match in _BARE_TICKER_RE.finditer(before)]
    scoped = [symbol for symbol in candidates if symbol in allowed]
    if scoped:
        return scoped[-1]
    after = context[marker + len("__CLAIM__"):] if marker >= 0 else ""
    after_candidates = [match.group(0).upper() for match in _BARE_TICKER_RE.finditer(after)]
    after_scoped = [symbol for symbol in after_candidates if symbol in allowed]
    return after_scoped[0] if after_scoped else None


def _evidence_by_symbol(successful: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = {}
    for item in successful:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        grouped.setdefault(symbol, {}).update(_evidence_numbers(item))
    return grouped


def _evidence_labels(successful: list[dict[str, Any]]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for item in successful:
        symbol = str(item.get("symbol") or "").upper() or "*"
        labels = item.get("evidence_labels")
        if not isinstance(labels, dict):
            continue
        grouped.setdefault(symbol, set()).update(
            str(value).lower().replace("_", " ")
            for value in labels.values()
            if isinstance(value, str) and value
        )
    return grouped


def _calculation_issues(trace: list[dict[str, Any]]) -> list[str]:
    """Recompute deterministic calculator results independently of the trace."""
    issues: list[str] = []
    for item in trace:
        if item.get("kind") != "calculation" or item.get("ok") is not True:
            continue
        request = item.get("request")
        actual = ((item.get("data") or {}).get("values") if isinstance(item.get("data"), dict) else None)
        if not isinstance(request, dict) or not isinstance(actual, dict):
            issues.append("calculation_unverifiable")
            continue
        try:
            expected = calculate(CalculationRequest.model_validate(request)).values
        except (TypeError, ValueError):
            issues.append("calculation_unverifiable")
            continue
        for key, expected_value in expected.items():
            actual_value = actual.get(key)
            if expected_value is None and actual_value is None:
                continue
            if not isinstance(expected_value, (int, float)) or not isinstance(actual_value, (int, float)):
                issues.append("calculation_mismatch")
                continue
            tolerance = max(0.0001, abs(float(expected_value)) * 0.0001)
            if abs(float(expected_value) - float(actual_value)) > tolerance:
                issues.append("calculation_mismatch")
    return list(dict.fromkeys(issues))


def _default_known_symbols() -> set[str]:
    """The tradable-symbol universe Chat itself resolves tickers against."""
    try:
        from backend.ai.chat_symbols import _known_symbols

        return {str(symbol).upper() for symbol in _known_symbols()}
    except Exception:  # noqa: BLE001 — verification must not depend on it
        return set()


def _evidence_symbols(successful: list[dict[str, Any]]) -> set[str]:
    symbols: set[str] = set()
    for item in successful:
        for value in (item.get("symbol"), (item.get("arguments") or {}).get("symbol") if isinstance(item.get("arguments"), dict) else None):
            if isinstance(value, str) and value:
                symbols.add(value.upper())
    return symbols


def verify_answer(
    content: str,
    trace: list[dict[str, Any]],
    *,
    allowed_symbols: list[str] | None = None,
    unavailable_symbols: list[str] | None = None,
    user_content: str = "",
    known_symbols: set[str] | None = None,
) -> AnswerVerification:
    """Validate claims in ``content`` against application-owned evidence.

    Explicit ticker forms (``$XYZ``, "ticker XYZ", "XYZ stock") must be a
    turn symbol. A bare all-caps word is only treated as a ticker when it is
    a real symbol from ``known_symbols`` (default: Chat's own resolver
    universe), so acronyms such as EPS, NYSE, or CEO are never ticker
    claims.
    """

    assign_evidence_ids(trace)
    successful = _successful_evidence(trace)
    evidence_refs = [str(item["evidence_id"]) for item in successful]
    calculation_issues = _calculation_issues(trace)
    if calculation_issues:
        return AnswerVerification(
            version=VERIFIER_VERSION,
            status="blocked",
            issues=calculation_issues,
            evidence_refs=evidence_refs,
            claim_count=0,
            numeric_claim_count=0,
            unsupported_claim_count=len(calculation_issues),
            safe_content=_safe_uncertainty(calculation_issues),
        )
    action_steps = [item for item in trace if item.get("kind") == "step"]
    trusted_server_reply = any(item.get("kind") == "server_reply" and item.get("trusted") is True for item in trace)
    if action_steps or trusted_server_reply:
        # Action receipts and confirmation prompts are server-authored.  They
        # may contain watchlist names, IDs, or counts that are not market
        # evidence; the separate action/confirmation contract remains the
        # source of truth for whether anything executed.
        completed = any(item.get("status") == "completed" for item in action_steps)
        incomplete = any(
            item.get("status") != "completed"
            for item in action_steps
        )
        return AnswerVerification(
            version=VERIFIER_VERSION,
            # A server-authored clarification is trustworthy prose, but it is
            # not market evidence. Keep it out of the VERIFIED label unless a
            # tool actually completed without an incomplete action step. A
            # failed scoped action must remain visibly limited even when the
            # turn also collected unrelated baseline evidence.
            status="degraded" if incomplete else "verified" if completed or successful else "degraded",
            issues=[],
            evidence_refs=evidence_refs,
            claim_count=0,
            numeric_claim_count=0,
            unsupported_claim_count=0,
        )
    allowed = {str(symbol).upper() for symbol in (allowed_symbols or [])} | _evidence_symbols(successful)
    unavailable = {str(symbol).upper() for symbol in (unavailable_symbols or [])}
    issues: list[str] = []
    numeric_claims = _numbers_from_text(content)
    claim_count = len(numeric_claims)
    unsupported = 0

    for match in _TICKER_RE.finditer(content):
        symbol = next((group for group in match.groups() if group), "").upper()
        if symbol and symbol not in allowed and symbol not in unavailable:
            issues.append("unknown_ticker")
            unsupported += 1
    universe = _default_known_symbols() if known_symbols is None else {str(symbol).upper() for symbol in known_symbols}
    for match in _BARE_TICKER_RE.finditer(content):
        symbol = match.group(0).upper()
        if symbol in _NON_TICKER_CAPS or symbol in allowed or symbol in unavailable:
            continue
        if symbol in universe:
            issues.append("unknown_ticker")
            unsupported += 1

    evidence_numbers: dict[str, float] = {}
    evidence_families: set[str] = set()
    for item in successful:
        item_numbers = _evidence_numbers(item)
        evidence_numbers.update(item_numbers)
        evidence_families.update(_evidence_unit_families(item_numbers))
    evidence_by_symbol = _evidence_by_symbol(successful)
    unscoped_numbers: dict[str, float] = {}
    for item in successful:
        if not str(item.get("symbol") or ""):
            unscoped_numbers.update(_evidence_numbers(item))
    evidence_labels = _evidence_labels(successful)
    user_numbers = {number for number, _, _ in _numbers_from_text(user_content)}
    direction_text = _PHRASAL_DIRECTION_RE.sub(" ", content)
    directional_claim = bool(_POSITIVE_DIRECTION_RE.search(direction_text) or _NEGATIVE_DIRECTION_RE.search(direction_text))

    for value, unit, context in numeric_claims:
        claim_symbol = _claim_symbol(context, allowed)
        symbol_numbers = evidence_by_symbol.get(claim_symbol) if claim_symbol else None
        scoped_numbers = {**unscoped_numbers, **symbol_numbers} if symbol_numbers else evidence_numbers
        matched_evidence = any(
            abs(value - candidate) <= max(0.02 if abs(candidate) < 10 else 0.01, abs(candidate) * 0.0005)
            for candidate in scoped_numbers.values()
        )
        if not matched_evidence and directional_claim:
            matched_evidence = any(
                abs(abs(value) - abs(candidate)) <= max(0.02 if abs(candidate) < 10 else 0.01, abs(candidate) * 0.0005)
                for candidate in scoped_numbers.values()
            )
        # Echoing the user's own inputs is fine, but a user-supplied number
        # never overrides server evidence for the symbol it is attributed
        # to ("is AAPL at 300?" must not verify "AAPL is at $300").
        if value in user_numbers and (not symbol_numbers or _USER_INPUT_CONTEXT_RE.search(context or "")):
            matched_evidence = True
        if not matched_evidence and successful:
            issues.append("unsupported_numeric_claim")
            unsupported += 1
        elif not matched_evidence and not successful:
            # A number supplied by the user is already accepted above; any
            # other number in an answer has no server-owned source.
            issues.append("unsupported_numeric_claim")
            unsupported += 1
        family = _unit_family(unit)
        if family and successful and evidence_families and family not in evidence_families:
            issues.append("unit_mismatch")
            unsupported += 1

    live_claim = bool(_LIVE_RE.search(content))
    if live_claim:
        fresh_evidence = [
            item for item in successful
            if isinstance(item.get("freshness_seconds"), (int, float))
        ]
        if not fresh_evidence:
            issues.append("live_claim_without_freshness")
            unsupported += 1
        elif all(
            item.get("fallback") or (
                isinstance(item.get("freshness_seconds"), (int, float))
                and item.get("freshness_seconds") > 900
            )
            for item in fresh_evidence
        ):
            issues.append("stale_live_claim")
            unsupported += 1

    evidence_timeframes = {str(item.get("timeframe")).lower() for item in successful if item.get("timeframe")}
    mentioned_timeframes = {match.group(1).lower() for match in _TIMEFRAME_RE.finditer(content)}
    if evidence_timeframes and mentioned_timeframes and not mentioned_timeframes.intersection(evidence_timeframes):
        issues.append("timeframe_mismatch")
        unsupported += 1

    evidence_sessions = {
        str(item.get("session")).lower().replace("-", "_").replace(" ", "_")
        for item in successful
        if item.get("session")
    }
    mentioned_sessions = {
        match.group(1).lower().replace("-", "_").replace(" ", "_")
        for match in _SESSION_RE.finditer(content)
    }
    if evidence_sessions and mentioned_sessions and not mentioned_sessions.intersection(evidence_sessions):
        issues.append("session_mismatch")
        unsupported += 1

    directional_values = [
        value
        for key, value in evidence_numbers.items()
        if any(token in key.lower() for token in ("change", "return", "delta", "move", "gain", "loss"))
        and value != 0
    ]
    if directional_values:
        if _POSITIVE_DIRECTION_RE.search(direction_text) and all(value < 0 for value in directional_values):
            issues.append("contradictory_evidence")
            unsupported += 1

    positive_claim = bool(_POSITIVE_DIRECTION_RE.search(direction_text))
    negative_claim = bool(_NEGATIVE_DIRECTION_RE.search(direction_text))
    sideways_claim = bool(re.search(r"\b(sideways|flat|range[- ]bound|mixed)\b", content, re.I))
    label_sets = list(evidence_labels.values())
    if label_sets:
        known_labels = {label for labels in label_sets for label in labels}
        positive_labels = {"bullish", "up", "rising", "strong", "positive"}
        negative_labels = {"bearish", "down", "falling", "weak", "negative"}
        sideways_labels = {"sideways", "flat", "mixed", "neutral"}
        if positive_claim and known_labels.intersection(negative_labels) and not known_labels.intersection(positive_labels):
            issues.append("contradictory_evidence")
            unsupported += 1
        elif negative_claim and known_labels.intersection(positive_labels) and not known_labels.intersection(negative_labels):
            issues.append("contradictory_evidence")
            unsupported += 1
        elif sideways_claim and known_labels.intersection(positive_labels | negative_labels) and not known_labels.intersection(sideways_labels):
            issues.append("contradictory_evidence")
            unsupported += 1
        elif negative_claim and directional_values and all(value > 0 for value in directional_values):
            issues.append("contradictory_evidence")
            unsupported += 1

    # Preserve order while avoiding repeated warnings for several bad values.
    issues = list(dict.fromkeys(issues))
    if issues:
        return AnswerVerification(
            version=VERIFIER_VERSION,
            status="blocked",
            issues=issues,
            evidence_refs=evidence_refs,
            claim_count=claim_count,
            numeric_claim_count=len(numeric_claims),
            unsupported_claim_count=unsupported,
            safe_content=_safe_uncertainty(issues),
        )

    status: Literal["verified", "degraded"] = "verified" if successful else "degraded"
    return AnswerVerification(
        version=VERIFIER_VERSION,
        status=status,
        issues=[],
        evidence_refs=evidence_refs,
        claim_count=claim_count,
        numeric_claim_count=len(numeric_claims),
        unsupported_claim_count=0,
    )


__all__ = ["AnswerVerification", "VERIFIER_VERSION", "assign_evidence_ids", "verify_answer"]
