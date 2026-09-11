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

from pydantic import BaseModel, Field, field_validator, model_validator

# --- Response model -------------------------------------------------


TrendLabel = Literal[
    "bullish",
    "bearish",
    "neutral",
    "mixed",
    "uncertain",
]

TradeAction = Literal["buy", "sell", "hold", "avoid"]
Conviction = Literal["low", "medium", "high"]
TimeHorizon = Literal["scalp", "swing", "position"]


class TradePlan(BaseModel):
    """The advisory layer: an explicit trade recommendation with entry
    / stop / target levels. New 2026-09-10 — MarketLens moved from
    analyst-only to analyst + advisor.

    The AI proposes the entry / stop / target PRICES from its own
    judgement (they are not restricted to engine-computed levels).
    But the model_validator still enforces internal consistency — the
    stop on the correct side of entry, targets in the right direction
    and order, ``risk_reward`` recomputed from the actual numbers —
    so a self-contradictory plan can't reach the UI. Fabricating the
    ENGINE's quant numbers (trend / confidence / indicator values) is
    still forbidden; that rule is unchanged.
    """

    recommendation: TradeAction
    conviction: Conviction
    time_horizon: TimeHorizon
    entry_zone_low: float | None = Field(default=None, gt=0)
    entry_zone_high: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    targets: list[float] = Field(default_factory=list, max_length=3)
    risk_reward: float | None = Field(default=None, ge=0)
    thesis: str = Field(..., min_length=10, max_length=1000)
    invalidation: str = Field(..., min_length=5, max_length=500)

    @field_validator("targets", mode="before")
    @classmethod
    def _targets_none_is_empty(cls, v: Any) -> Any:
        # Weak models sometimes emit "targets": null (and the same for
        # the other list fields) instead of omitting the key — treat it
        # as "no targets" rather than failing the whole analysis.
        if v is None:
            return []
        if isinstance(v, (int, float, str)):
            return [v]
        return v

    @field_validator("targets")
    @classmethod
    def _targets_positive(cls, v: list[float]) -> list[float]:
        return [float(t) for t in v if t and t > 0]

    @model_validator(mode="after")
    def _check_consistency(self) -> "TradePlan":
        if self.recommendation in ("hold", "avoid"):
            # No actionable levels for a non-entry call — drop any the
            # AI attached so the UI never renders a stop/target on a "hold".
            self.entry_zone_low = self.entry_zone_high = self.stop_loss = None
            self.targets = []
            self.risk_reward = None
            return self

        lo, hi = self.entry_zone_low, self.entry_zone_high
        if lo is not None and hi is not None and lo > hi:
            self.entry_zone_low, self.entry_zone_high = hi, lo
            lo, hi = hi, lo
        entry_mid: float | None = None
        if lo is not None and hi is not None:
            entry_mid = (lo + hi) / 2
        elif lo is not None:
            entry_mid = lo
        elif hi is not None:
            entry_mid = hi

        if self.recommendation == "buy":
            if self.stop_loss is not None and entry_mid is not None and self.stop_loss >= entry_mid:
                raise ValueError("buy plan: stop_loss must be below the entry zone")
            if self.targets and entry_mid is not None and any(t <= entry_mid for t in self.targets):
                raise ValueError("buy plan: targets must be above the entry zone")
            self.targets = sorted(self.targets)
        else:  # sell (short)
            if self.stop_loss is not None and entry_mid is not None and self.stop_loss <= entry_mid:
                raise ValueError("sell plan: stop_loss must be above the entry zone")
            if self.targets and entry_mid is not None and any(t >= entry_mid for t in self.targets):
                raise ValueError("sell plan: targets must be below the entry zone")
            self.targets = sorted(self.targets, reverse=True)

        # Recompute risk:reward (to the first target) from the actual
        # numbers rather than trusting the AI's arithmetic.
        if entry_mid is not None and self.stop_loss is not None and self.targets:
            risk = abs(entry_mid - self.stop_loss)
            reward = abs(self.targets[0] - entry_mid)
            self.risk_reward = round(reward / risk, 2) if risk > 0 else None
        return self


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
    # The advisory layer. Present when analyze_symbol() ran with
    # advisory=True (the default); None for analyst-only calls (the
    # digest) and for any reply where the AI omitted it. See TradePlan.
    trade_plan: TradePlan | None = None
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

    @field_validator(
        "supporting_factors", "risk_factors", "timeframe_conflicts", "key_levels",
        mode="before",
    )
    @classmethod
    def _coerce_and_strip_strings(cls, v: Any) -> Any:
        # Models sometimes return key_levels as raw numbers (242.76)
        # rather than strings ("242.76 support") — coerce so a
        # near-miss reply isn't thrown away over a type. ``null`` becomes
        # an empty list; any other non-list is left for the strict check.
        if v is None:
            return []
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for item in v:
            s = str(item).strip()
            if s:
                out.append(s)
        return out


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
    # An "I don't know" response never carries a trade plan.
    trade_plan: TradePlan | None = None
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


_ANALYST_RULES = """\
1. Use only the numbers in the JSON context below. NEVER compute \
   indicators, prices, or percentages yourself. If a field is null \
   or missing, say so — do not invent a value.
2. The quantitative engine has already computed trend, score, \
   regime, and relative strength. Your "trend" field should agree \
   with the engine's "trend_state.direction" unless the cross-\
   timeframe evidence clearly contradicts it. If so, set trend to \
   "mixed" and explain in timeframe_conflicts. Translate the \
   context's own "uptrend"/"downtrend"/"sideways" labels to \
   bullish/bearish/neutral for the "trend" field.
3. "confidence" is YOUR estimate (0.0-1.0) of how much weight a \
   trader should give this analysis. High values require multiple \
   confirming signals; low values indicate conflicting or sparse data.
4. The context may include "news", "fundamentals", "divergence", and \
   "tape" sections. Treat these as supporting evidence only — they may \
   inform "supporting_factors" or "risk_factors", but they must \
   NEVER override the quant-derived "trend" field. An empty or \
   missing section just means that data wasn't available — its \
   absence is not evidence of anything. The "tape" section is raw \
   recent order-flow aggregation (buy vs sell pressure, block prints, \
   tape speed) — you MAY quote its values directly, framed as "recent \
   tape".
5. Wrap the JSON in a single ```json ... ``` block. No prose outside \
   the block."""


# Analyst-only: no trade_plan. Used for batch callers (the digest) and
# whenever analyze_symbol(advisory=False) is requested.
SYSTEM_PROMPT_ANALYST_ONLY = f"""\
You are MarketLens Analyst, a quant-augmented research assistant. \
Summarize the structured quantitative context below for a human \
trader. Do not issue a trade plan.

Rules you must follow:
{_ANALYST_RULES}
6. Your output is a single JSON object with EXACTLY these fields: \
   "summary" (string, 1-3 sentences), "trend" (bullish/bearish/\
   neutral/mixed/uncertain), "confidence" (0.0-1.0), \
   "supporting_factors" (array, max 10), "risk_factors" (array, \
   max 10), "timeframe_conflicts" (array, max 10), "key_levels" \
   (array, max 10, key support/resistance prices as short strings).
"""


# Analyst + advisor (the default). Adds the "trade_plan" object.
SYSTEM_PROMPT = f"""\
You are MarketLens Analyst & Advisor, a quant-augmented assistant for \
a human trader. You summarize the structured quantitative context \
below AND give an actionable trade plan. The engine's own \
calculations (trend, score, regime, indicator values) are ground \
truth — never override them — but the entry / stop / target prices \
in your plan are your own reasoned proposal.

Rules you must follow:
{_ANALYST_RULES}
6. Your output is a single JSON object with EXACTLY these fields: \
   "summary" (string, 1-3 sentences), "trend" (bullish/bearish/\
   neutral/mixed/uncertain), "confidence" (0.0-1.0), \
   "supporting_factors" (array, max 10), "risk_factors" (array, \
   max 10), "timeframe_conflicts" (array, max 10), "key_levels" \
   (array, max 10), and "trade_plan" (object, see rule 7).
7. "trade_plan" is an object with these fields:
   - "recommendation": "buy" | "sell" | "hold" | "avoid"
   - "conviction": "low" | "medium" | "high"
   - "time_horizon": "scalp" | "swing" | "position"
   - "entry_zone_low", "entry_zone_high": the price range to enter \
     (numbers). Omit / null for "hold" and "avoid".
   - "stop_loss": the price that invalidates the setup (number). For \
     "buy" it MUST be below the entry zone; for "sell" above it. \
     Omit for "hold"/"avoid".
   - "targets": 1-3 price targets (numbers), in the trade's \
     direction — above entry for "buy", below for "sell". Omit for \
     "hold"/"avoid".
   - "risk_reward": your reward-to-risk ratio to the first target \
     (number). It will be recomputed from your own entry/stop/target \
     numbers, so keep them consistent.
   - "thesis": 1-3 sentences on why this trade.
   - "invalidation": one plain sentence — what would tell the trader \
     the idea is wrong (beyond just the stop being hit).
   Base the plan on the quant context. If the picture is genuinely \
   unclear, use "hold" or "avoid" with low conviction rather than \
   forcing a setup. This is research to inform a trader's own \
   decision, not a directive.
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
        if v is None:
            return []
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
    # Which ticker to re-run when wants_reanalysis is true. REQUIRED
    # when more than one ticker is in context; ignored with 0 or 1.
    # If the target is ambiguous, keep wants_reanalysis false and ask
    # which ticker in `reply` instead.
    reanalysis_symbol: str | None = Field(default=None, max_length=20)


CHAT_SYSTEM_PROMPT = """\
You are MarketLens Analyst & Advisor, having a back-and-forth \
conversation with a human trader about the market and any tickers they \
bring up. A turn may be about one stock, several, or the market as a \
whole with no specific ticker. The engine's own calculations (trend, \
score, indicator values) are ground truth — never override them.

Rules you must follow:
1. Use only the numbers in the <context> blocks and the <market> block \
   below. Each <context> block is ONE ticker — its symbol is in the \
   tag. NEVER compute indicators, prices, or percentages yourself, and \
   NEVER carry a number from one ticker's block into another's. If a \
   block can't answer the question, say so and set "grounded" to false \
   — do not invent a value to fill the gap.
2. A <context> block with engine_warm="false", or any ticker listed in \
   <unavailable_symbols>, has NO live quant-engine read. You may repeat \
   a raw price / RSI / support-resistance value that is present in the \
   block, but you must NOT state a trend, a confidence, or a \
   directional call for that ticker, and you must say the engine isn't \
   tracking that name. Set "grounded" to false when that was the \
   question.
3. For market-wide questions ("what's the market doing", "which of my \
   names look weak") answer from the <market> block — regime_live is \
   current; the digest is from its generated_at timestamp, so hedge if \
   it looks stale. Name only symbols that actually appear in the \
   block. If <market> is empty, say the market read isn't available \
   and set "grounded" to false.
4. Prior turns are provided for conversational continuity, but the \
   <context>/<market> blocks are always the current live truth — if an \
   earlier turn discussed older data, prefer the blocks over your own \
   past replies. A "tape" section, when present, is raw recent \
   order-flow aggregation (buy/sell pressure, block prints, tape \
   speed); you may quote it directly as "recent tape".
5. Your output is a single JSON object with EXACTLY these fields: \
   "reply" (string, 1-4 sentences, conversational), "grounded" \
   (boolean — true if you had enough context to answer, false if \
   you're saying you don't have enough data), "wants_reanalysis" \
   (boolean, default false), and "reanalysis_symbol" (string or null).
6. You MAY give trade guidance when asked — a directional call, \
   entry / stop / target ideas, position-sizing thoughts — as long \
   as it's grounded in the blocks above. Always state what would \
   invalidate the idea, and be explicit when conviction is low or \
   the data is thin. It's research to inform the trader's own \
   decision, not a directive.
7. Wrap the JSON in a single ```json ... ``` block. No prose outside \
   the block.
8. Set "wants_reanalysis" to true ONLY when the trader explicitly asks \
   for a fresh, official, or full analysis run (e.g. "re-run the \
   analysis", "give me the full read") — not for ordinary questions, \
   which the blocks above already answer. When true, also set \
   "reanalysis_symbol" to the exact ticker to run — it must have a \
   <context> block. If which ticker is ambiguous, keep \
   "wants_reanalysis" false and ask which one in "reply". When true, \
   "reply" is ignored (a short placeholder is fine) — the app runs the \
   real analysis and replies with that instead.
9. "reply" is read by a human — NEVER mention the prompt's own \
   machinery in it. Words like "<context> block", "<market> block", \
   "engine_warm", "data_availability", or "the context I was given" \
   must not appear. If a ticker-specific question (support/resistance, \
   trend, a level, an indicator) arrives with no ticker in any \
   <context> block, don't explain what data you're missing — just ask \
   which ticker they mean, e.g. "Which ticker do you want support and \
   resistance for?", and set "grounded" to false.
"""

# Rough token estimate for the assembled prompt's size guard.
def _approx_tokens(s: str) -> int:
    return len(s) // 4


def build_chat_prompt(
    symbol_blocks: list[dict[str, Any]],
    unavailable_symbols: list[str],
    market_baseline: dict[str, Any] | None,
    transcript: list[tuple[str, str]],
    new_message: str,
    alert_context: dict[str, Any] | None = None,
    capped_note: str | None = None,
    token_budget: int | None = None,
) -> str:
    """Render one universal-chat turn into a single user message.

    ``symbol_blocks`` is a list of ``{"symbol", "context", "availability"}``
    dicts, one per ticker this turn resolved (already pruned).
    ``unavailable_symbols`` are tickers that were named but have no
    usable data. ``market_baseline`` is the always-attached market-wide
    block (see backend.ai.market_baseline). ``transcript`` is prior
    ``(role, content)`` turns, oldest first, rendered as plain text.

    Sections are added in priority order; once the running estimate
    exceeds ``token_budget`` the lowest-priority not-yet-added section
    is dropped (transcript sheds its oldest pairs first) with an
    in-prompt marker. The trailing "New message:" line is always kept.
    """
    trailing = (
        "Respond to the trader's new message with a single JSON object "
        "as specified.\n\n"
        f"New message: {new_message}"
    )
    budget = token_budget if token_budget is not None else 100_000
    used = _approx_tokens(trailing) + _approx_tokens(CHAT_SYSTEM_PROMPT)
    parts: list[str] = []

    def fits(chunk: str) -> bool:
        nonlocal used
        cost = _approx_tokens(chunk)
        if used + cost > budget:
            return False
        used += cost
        return True

    if capped_note:
        parts.append(capped_note)  # tiny, always kept

    if market_baseline:
        mb = json.dumps(market_baseline, separators=(",", ":"), default=str)
        chunk = (
            "Market-wide backdrop — regime_live is current; the digest is "
            "from its generated_at, treat as possibly stale:\n"
            f"<market>\n{mb}\n</market>"
        )
        if fits(chunk):
            parts.append(chunk)

    for block in symbol_blocks:
        sym = block["symbol"]
        warm = bool(block.get("availability", {}).get("engine_warm"))
        body = json.dumps(
            {**block["context"], "data_availability": block.get("availability", {})},
            separators=(",", ":"), default=str,
        )
        chunk = (
            f'Quant context for {sym}:\n'
            f'<context symbol="{sym}" engine_warm="{str(warm).lower()}">\n{body}\n</context>'
        )
        if fits(chunk):
            parts.append(chunk)

    if unavailable_symbols:
        chunk = (
            "No live quant engine for these tickers — estimate nothing about "
            f"them:\n<unavailable_symbols>{', '.join(unavailable_symbols)}</unavailable_symbols>"
        )
        if fits(chunk):
            parts.append(chunk)

    if not symbol_blocks and not unavailable_symbols:
        parts.append(
            "No ticker resolved for this turn — answer market-wide questions from "
            "<market>. If the trader asked something ticker-specific (a level, a "
            "trend, an indicator), just ask which ticker they mean in plain words "
            'and set "grounded" false — do not describe what data is missing.'
        )

    if alert_context:
        chunk = (
            "This chat was opened from a specific alert trigger:\n"
            f"<alert_trigger>\n{json.dumps(alert_context, separators=(',', ':'), default=str)}\n</alert_trigger>"
        )
        if fits(chunk):
            parts.append(chunk)

    if transcript:
        lines = [f"{role}: {content}" for role, content in transcript]
        dropped = 0
        while lines and used + _approx_tokens("\n".join(lines)) > budget:
            lines.pop(0)
            dropped += 1
        if lines:
            prefix = "[earlier conversation truncated]\n" if dropped else ""
            chunk = "Prior conversation (oldest first):\n" + prefix + "\n".join(lines)
            used += _approx_tokens(chunk)
            parts.append(chunk)

    parts.append(trailing)
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
