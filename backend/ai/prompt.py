"""
Phase 16 — Prompt template + response validation for AI analysis.

The system prompt tells the model:

1. Its role (analyst, not trader)
2. What structured input it will receive
3. What structured output it must return
4. That it must NEVER claim to compute indicators itself
5. That its output is for UI display only — quantitative truth is
   computed by the engine, not by the AI

The validator (Pydantic model ``AnalysisResponse``) parses the
model's reply, rejects malformed answers, and clamps numerical
fields to a known range so the UI never receives a nonsense
``confidence: 1.5`` or ``trend: "sideways-and-up"``.

The validator's strictness is the safety boundary: the AI may
say anything it likes, but only fields we explicitly model survive
the parse. This means a confident-but-wrong AI reply can't
overwrite the quantitative engine's score.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --- Response model -------------------------------------------------


TrendLabel = Literal[
    "bullish",
    "bearish",
    "neutral",
    "mixed",
    "uncertain",
]


# Found live 2026-09-09: llama3.2 (Ollama fallback) correctly followed
# rule 3 ("your trend should agree with the engine's trend_state.direction
# unless...") right down to copying the ENGINE'S OWN vocabulary
# ("downtrend") instead of translating it to the requested output
# vocabulary ("bearish") — the context dict it's shown uses TrendDirection/
# TrendClassification values (uptrend/downtrend/sideways/unknown,
# strong_bullish/weak_bearish/no_signal/...), so a near-miss reply that
# echoes what it just read is a predictable failure mode, not gibberish.
# Rejecting a reply we can confidently interpret just to enforce a literal
# string match would throw away a good answer — normalize known synonyms
# before validating; anything NOT in this map still hits the strict
# Literal check unchanged (extract_json_object's parse rejects true
# gibberish long before this point).
_TREND_SYNONYMS: dict[str, str] = {
    "uptrend": "bullish", "up": "bullish", "upward": "bullish",
    "strong_uptrend": "bullish", "strong_bullish": "bullish",
    "weak_bullish": "bullish",
    "downtrend": "bearish", "down": "bearish", "downward": "bearish",
    "strong_downtrend": "bearish", "strong_bearish": "bearish",
    "weak_bearish": "bearish",
    "sideways": "neutral", "flat": "neutral", "range": "neutral",
    "ranging": "neutral", "choppy": "neutral",
    "conflicting": "mixed", "conflict": "mixed",
    "unknown": "uncertain", "unclear": "uncertain", "no_signal": "uncertain",
    "n/a": "uncertain", "na": "uncertain", "none": "uncertain",
}


class AnalysisResponse(BaseModel):
    """Strict schema for the AI's reply.

    The model is intentionally narrow: extra fields from the AI are
    dropped, missing required fields cause a validation error, and
    the ``trend`` value is restricted to a fixed vocabulary (after
    ``_TREND_SYNONYMS`` normalization — see its comment). The
    quantitative engine's score is NOT part of this model — the
    spec is explicit that AI must never overwrite quant truth.
    """

    summary: str = Field(..., min_length=10, max_length=2000)
    trend: TrendLabel
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_factors: list[str] = Field(default_factory=list, max_length=10)
    risk_factors: list[str] = Field(default_factory=list, max_length=10)
    timeframe_conflicts: list[str] = Field(default_factory=list, max_length=10)
    key_levels: list[str] = Field(default_factory=list, max_length=10)
    # Which provider/model actually answered this call — set by
    # analyze_symbol() AFTER parsing/validation, never trusted from the
    # AI's own raw JSON (harmless either way since it's always
    # overwritten before the response is returned, but not documented
    # in the system prompt as a field the AI should fill in). Found
    # live 2026-09-10: every caller of analyze_symbol() (the /analyze
    # endpoint, both background job paths) was reporting
    # ai_manager.settings.provider/model instead — the configured
    # PRIMARY, not whoever actually served the request — so a
    # fallback-served analysis silently claimed to be from the primary.
    provider: str = "unknown"
    model: str = "unknown"

    @field_validator("trend", mode="before")
    @classmethod
    def _normalize_trend_synonyms(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        key = v.strip().lower().replace(" ", "_").replace("-", "_")
        return _TREND_SYNONYMS.get(key, v)

    @field_validator("supporting_factors", "risk_factors", "timeframe_conflicts", "key_levels")
    @classmethod
    def _strip_strings(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


class UncertaintyResponse(BaseModel):
    """The "I don't know" response.

    Returned when the quant engine doesn't have enough data to ask
    the AI a question, or when AI is disabled / unavailable. The
    UI can render this as a neutral info card with no actionable
    claim.
    """

    summary: str
    trend: Literal["uncertain"] = "uncertain"
    confidence: float = 0.0
    supporting_factors: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    timeframe_conflicts: list[str] = Field(default_factory=list)
    key_levels: list[str] = Field(default_factory=list)
    # Same provider/model attribution as AnalysisResponse — see its
    # comment. Reflects whichever provider was actually tried (e.g.
    # the one whose malformed reply produced this uncertainty), not
    # necessarily the configured primary.
    provider: str = "none"
    model: str = "unknown"


# --- Parsing --------------------------------------------------------


# Match a ```json ... ``` block (preferred) OR a balanced { ... } block.
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str | None) -> str:
    """Extract the first JSON object substring from an AI reply.

    Strategy:
    1. Look for a fenced ```json ... ``` block.
    2. Fall back to the first balanced ``{...}`` substring.

    Returns the candidate string. Raises ``ValueError`` when the reply
    is empty or doesn't contain a JSON object. Used by both
    :func:`parse_ai_reply` (Phase 16) and the NL search translator
    (Phase 17) so the two layers share one extraction path.
    """
    if not text or not text.strip():
        raise ValueError("empty AI reply")

    m = _JSON_FENCE.search(text)
    if m:
        return m.group(1)

    # Find the first balanced { ... } in the reply
    start = text.find("{")
    while start != -1:
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : end + 1]
        start = text.find("{", start + 1)

    raise ValueError("no JSON object found in AI reply")


def parse_ai_reply(text: str | None) -> AnalysisResponse:
    """Extract a structured ``AnalysisResponse`` from an AI reply.

    Strategy:
    1. Delegate JSON extraction to :func:`extract_json_object`.
    2. ``json.loads`` and validate against the model.

    Raises ``ValueError`` when the reply is empty, doesn't contain
    JSON, or fails schema validation. The caller should fall back
    to an ``UncertaintyResponse`` in that case — the spec says we
    must never trust a malformed AI reply.
    """
    candidate = extract_json_object(text)
    data = json.loads(candidate)
    return AnalysisResponse.model_validate(data)


# --- Prompt template ------------------------------------------------


SYSTEM_PROMPT = """\
You are MarketLens Analyst, a quant-augmented research assistant. \
Your job is to summarize the structured quantitative context below \
for a human trader, not to issue trade orders or override the \
engine's own calculations.

Rules you must follow:
1. Use only the numbers in the JSON context below. NEVER compute \
   indicators, prices, or percentages yourself. If a field is null \
   or missing, say so — do not invent a value.
2. Your output is a single JSON object with EXACTLY these fields: \
   "summary" (string, 1-3 sentences), "trend" (one of bullish, \
   bearish, neutral, mixed, uncertain — NOT the context's own \
   "uptrend"/"downtrend"/"sideways" labels; translate those to \
   bullish/bearish/neutral respectively), "confidence" (number 0.0-1.0), \
   "supporting_factors" (array of short strings, max 10), \
   "risk_factors" (array, max 10), "timeframe_conflicts" (array, \
   max 10, list timeframes that disagree with the primary trend), \
   "key_levels" (array, max 10, key support/resistance prices as \
   short strings).
3. The quantitative engine has already computed trend, score, \
   regime, and relative strength. Your "trend" field should agree \
   with the engine's "trend_state.direction" unless the cross-\
   timeframe evidence clearly contradicts it. If so, set trend to \
   "mixed" and explain in timeframe_conflicts.
4. "confidence" is YOUR estimate (0.0-1.0) of how much weight a \
   trader should give this analysis. High values require multiple \
   confirming signals; low values indicate conflicting or sparse data.
5. NEVER recommend buying, selling, or holding. NEVER mention target \
   prices or stop losses. You are summarizing, not advising.
6. Wrap the JSON in a single ```json ... ``` block. No prose outside \
   the block.
7. The context may include "news", "fundamentals", and "divergence" \
   sections. Treat these as supporting evidence only — they may \
   inform "supporting_factors" or "risk_factors" (e.g. a bearish \
   headline, a stretched valuation, a bullish RSI divergence), but \
   they must NEVER override the quant-derived "trend" field itself. \
   If a section is empty or missing, it simply means that data \
   wasn't available — do not treat its absence as evidence of \
   anything.
"""


def build_user_prompt(context_dict: dict[str, Any]) -> str:
    """Render the structured context into a user message.

    The context dict is dumped as pretty-printed JSON inside a
    ``<context>...</context>`` fence so the model can parse it
    unambiguously.
    """
    body = json.dumps(context_dict, indent=2, default=str)
    return (
        "Analyze the following MarketLens symbol context. "
        "Respond with a single JSON object as specified.\n\n"
        f"<context>\n{body}\n</context>"
    )


# --- Digest (Version 4, AI feature 2) -------------------------------

class DigestNarrative(BaseModel):
    """AI's short natural-language summary of a digest payload.

    Deliberately not the same schema as AnalysisResponse — a digest
    is a market-wide summary, not a per-symbol trend call, so there's
    no "trend"/"confidence" field to protect here. The closed-vocabulary
    strictness that matters for AnalysisResponse doesn't apply the same
    way; this is just a bounded free-text field.
    """

    narrative: str = Field(..., min_length=1, max_length=1000)
    headline_movers: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("headline_movers", mode="before")
    @classmethod
    def _strip_empties(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [s for s in v if isinstance(s, str) and s.strip()]
        return v


DIGEST_SYSTEM_PROMPT = """\
You are MarketLens Analyst, summarizing a watchlist-wide digest for \
a human trader — not issuing trade orders or overriding the engine's \
own calculations.

Rules you must follow:
1. Use only the numbers and symbols in the JSON payload below. NEVER \
   invent a symbol, price, or statistic that isn't there.
2. Your output is a single JSON object with EXACTLY these fields: \
   "narrative" (string, 2-4 sentences covering the overall market \
   regime and the most notable movers) and "headline_movers" (array \
   of up to 5 ticker symbols worth calling out, drawn only from the \
   payload's movers lists).
3. NEVER recommend buying, selling, or holding. NEVER mention target \
   prices or stop losses. You are summarizing, not advising.
4. Wrap the JSON in a single ```json ... ``` block. No prose outside \
   the block.
"""


def build_digest_user_prompt(payload: dict[str, Any]) -> str:
    """Render a digest payload into a user message, same fenced-JSON
    convention as :func:`build_user_prompt`."""
    body = json.dumps(payload, indent=2, default=str)
    return (
        "Summarize the following MarketLens watchlist digest. "
        "Respond with a single JSON object as specified.\n\n"
        f"<digest>\n{body}\n</digest>"
    )


def parse_digest_reply(text: str | None) -> DigestNarrative:
    """Extract a structured ``DigestNarrative`` from an AI reply.

    Same extract-then-validate strategy as :func:`parse_ai_reply`.
    Raises ``ValueError`` on an empty/non-JSON/schema-invalid reply —
    the caller (``backend.ai.digest.narrate_digest``) falls back to a
    plain, non-AI narrative in that case.
    """
    candidate = extract_json_object(text)
    data = json.loads(candidate)
    return DigestNarrative.model_validate(data)


# --- Alert commentary (Version 4, AI feature 3) ---------------------

class AlertCommentaryResponse(BaseModel):
    """AI's short note explaining why a specific alert fired.

    Deliberately minimal — one bounded free-text field. This is an
    enrichment on an already-fired, already-recorded event (the
    AlertTrigger row exists and is meaningful with or without this),
    not a decision the app acts on, so it doesn't need
    AnalysisResponse's closed-vocabulary/confidence machinery.
    """

    commentary: str = Field(..., min_length=1, max_length=500)


ALERT_COMMENTARY_SYSTEM_PROMPT = """\
You are MarketLens Analyst, briefly explaining why one alert just \
fired for a human trader — not issuing trade orders or overriding \
the engine's own calculations.

Rules you must follow:
1. Use only the facts in the JSON payload below (the alert's own \
   condition/parameter, what triggered it, and the symbol's current \
   quant context). NEVER invent a number or fact that isn't there.
2. Your output is a single JSON object with EXACTLY one field: \
   "commentary" (string, 1-2 sentences explaining why this condition \
   fired right now, in plain language a trader would find useful).
3. NEVER recommend buying, selling, or holding. NEVER mention target \
   prices or stop losses. You are explaining an event, not advising.
4. Wrap the JSON in a single ```json ... ``` block. No prose outside \
   the block.
"""


def build_alert_commentary_prompt(payload: dict[str, Any]) -> str:
    """Render an alert-trigger payload into a user message, same
    fenced-JSON convention as :func:`build_user_prompt`."""
    body = json.dumps(payload, indent=2, default=str)
    return (
        "Explain the following MarketLens alert trigger. "
        "Respond with a single JSON object as specified.\n\n"
        f"<alert_trigger>\n{body}\n</alert_trigger>"
    )


def parse_alert_commentary_reply(text: str | None) -> AlertCommentaryResponse:
    """Extract a structured ``AlertCommentaryResponse`` from an AI reply.

    Same extract-then-validate strategy as :func:`parse_ai_reply`.
    Raises ``ValueError`` on an empty/non-JSON/schema-invalid reply —
    the caller (``backend.ai.alert_commentary.generate_commentary``)
    leaves the trigger's ``ai_commentary`` as ``None`` in that case.
    """
    candidate = extract_json_object(text)
    data = json.loads(candidate)
    return AlertCommentaryResponse.model_validate(data)


# --- Chat (Version 4, AI feature 4) ----------------------------------

class ChatReplyResponse(BaseModel):
    """AI's reply to one chat turn.

    ``grounded`` lets the caller distinguish "I don't have enough
    data to answer that" from a normal answer without a separate
    error path — same uncertainty-first spirit as ``UncertaintyResponse``,
    just folded into one small schema instead of two response shapes,
    since a chat reply is always some text either way.
    """

    reply: str = Field(..., min_length=1, max_length=2000)
    grounded: bool = True
    # The chat's one tool call (Version 4, follow-up scope decision):
    # when true, the backend discards `reply` and instead runs a real
    # analyze_symbol() call — the same function AIAnalysisPanel's
    # "Re-run" button uses — and replies with its result. Deliberately
    # the only action the AI can trigger; see backend.ai.chat's module
    # docstring for the narrower alternatives considered and why this
    # one was picked.
    wants_reanalysis: bool = False


CHAT_SYSTEM_PROMPT = """\
You are MarketLens Analyst, having a back-and-forth conversation with \
a human trader about one symbol — not issuing trade orders or \
overriding the engine's own calculations.

Rules you must follow:
1. Use only the numbers in the JSON context below. NEVER compute \
   indicators, prices, or percentages yourself. If the context can't \
   answer the question, say so plainly and set "grounded" to false —
   do not invent a value to fill the gap.
2. Prior turns are provided for conversational continuity, but the \
   <context> block is always the current live truth — if an earlier \
   turn discussed older data, prefer the context over your own past \
   replies.
3. Your output is a single JSON object with EXACTLY these fields: \
   "reply" (string, 1-4 sentences, conversational), "grounded" \
   (boolean — true if you had enough context to answer, false if \
   you're saying you don't have enough data), and "wants_reanalysis" \
   (boolean, default false).
4. NEVER recommend buying, selling, or holding. NEVER mention target \
   prices or stop losses. You are discussing, not advising.
5. Wrap the JSON in a single ```json ... ``` block. No prose outside \
   the block.
6. Set "wants_reanalysis" to true ONLY when the trader explicitly asks \
   for a fresh, official, or full analysis run (e.g. "re-run the \
   analysis", "give me the full read", "check it again officially") — \
   not for ordinary questions. You already have live context above for \
   ordinary questions ("what's the trend", "why did this alert fire"); \
   reanalysis is for when they specifically want the real analysis \
   engine to run again, not just your conversational answer. When true, \
   "reply" is ignored, so it can be a short placeholder like "Let me \
   check." — the app runs the real analysis and replies with that \
   instead.
"""


def build_chat_prompt(
    context_dict: dict[str, Any],
    transcript: list[tuple[str, str]],
    new_message: str,
    alert_context: dict[str, Any] | None = None,
) -> str:
    """Render one chat turn into a user message.

    ``transcript`` is a list of ``(role, content)`` pairs for prior
    turns in this session (oldest first) — rendered as plain text,
    not re-sent as separate messages, since there's no multi-message
    conversation API here (one ai_manager.complete() call per turn,
    same as every other AI call site in this app).
    ``alert_context``, when present, is the alert/trigger this chat
    was opened from (see backend.ai.chat.answer_chat_message) — folded
    in as an extra section so "explain this alert" style questions
    have something concrete to reference.
    """
    context_body = json.dumps(context_dict, indent=2, default=str)
    parts = [
        "Here is the current context for this symbol:",
        f"<context>\n{context_body}\n</context>",
    ]
    if alert_context:
        alert_body = json.dumps(alert_context, indent=2, default=str)
        parts.append(
            "This chat was opened from a specific alert trigger:\n"
            f"<alert_trigger>\n{alert_body}\n</alert_trigger>"
        )
    if transcript:
        lines = [f"{role}: {content}" for role, content in transcript]
        parts.append("Prior conversation (oldest first):\n" + "\n".join(lines))
    parts.append(
        "Respond to the trader's new message with a single JSON object "
        "as specified.\n\n"
        f"New message: {new_message}"
    )
    return "\n\n".join(parts)


def parse_chat_reply(text: str | None) -> ChatReplyResponse:
    """Extract a structured ``ChatReplyResponse`` from an AI reply.

    Same extract-then-validate strategy as :func:`parse_ai_reply`.
    Raises ``ValueError`` on an empty/non-JSON/schema-invalid reply —
    the caller (``backend.ai.chat.answer_chat_message``) stores a
    plain "I couldn't process that" assistant message in that case,
    never an HTTP error.
    """
    candidate = extract_json_object(text)
    data = json.loads(candidate)
    return ChatReplyResponse.model_validate(data)


# --- Template rendering (Phase 2.4.5) -----------------------------------

# Matches ``{{variable}}`` tokens where ``variable`` is one-or-more
# word characters (letters, digits, underscore). Whitespace inside the
# braces is tolerated so ``{{ symbol }}`` and ``{{symbol}}`` both work.
_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_template(template: str, variables: dict[str, Any]) -> str:
    """Substitute ``{{variable}}`` tokens in ``template`` with values.

    Unknown variables (declared in the template but not in
    ``variables``) are replaced with an empty string. Non-string
    values are coerced via ``str()``. Numeric values are rendered
    with their default ``str()`` representation so the template
    author can pick formatting by passing a stringified value.

    >>> render_template("Analyze {{symbol}} on {{timeframe}}", {"symbol": "AAPL", "timeframe": "1d"})
    'Analyze AAPL on 1d'
    >>> render_template("Hello {{name}}", {})
    'Hello '
    """
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = variables.get(name, "")
        return str(value) if value is not None else ""
    return _TEMPLATE_VAR_RE.sub(_replace, template)
