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
