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
SYSTEM_PROMPT_CONFLUENCE = """This is the system prompt for confluence analysis. It instructs the AI to consider quantitative trend, sentiment, and fundamentals, and output a JSON with score (0‑100), alignment (\"strong\", \"moderate\", \"weak\", \"conflicting\"), reasoning, and primary_catalyst. The AI must output only the JSON block and nothing else."""
class ConfluenceResponse(BaseModel):
    score: float = Field(..., ge=0.0, le=100.0)
    alignment: Literal["strong", "moderate", "weak", "conflicting"]
    reasoning: str = Field(..., min_length=1, max_length=500)
    primary_catalyst: str = Field(..., min_length=1, max_length=200)

from backend.alerts.conditions.evaluators import (
    VALID_CONDITION_TYPES as _VALID_ALERT_CONDITION_TYPES,
)

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

# Common key names a model uses for a level's price/number and its
# label, across whatever shape it picks for a "key level" object.
_LEVEL_VALUE_KEYS = ("price", "level", "value")
_LEVEL_LABEL_KEYS = ("type", "label", "side", "kind")


def _stringify_list_item(item: Any) -> str:
    """A free-text list item ("supporting_factors", "key_levels", ...)
    coerced to a readable string.

    A bare string/number passes through. A dict — a model sometimes
    sends key_levels as ``{"price": 242.76, "type": "support"}`` instead
    of a string — is rendered as "242.76 support" rather than Python's
    ``str(dict)`` repr (``"{'price': 242.76, 'type': 'support'}"``),
    which is exactly what the UI would otherwise print verbatim.
    """
    if isinstance(item, dict):
        value = next((item[k] for k in _LEVEL_VALUE_KEYS if item.get(k) is not None), None)
        label = next((item[k] for k in _LEVEL_LABEL_KEYS if item.get(k) is not None), None)
        if value is not None and label is not None:
            return f"{value} {label}".strip()
        if value is not None or label is not None:
            return str(value if value is not None else label).strip()
        # Unrecognized shape — still avoid Python's dict repr.
        return ", ".join(f"{k}: {v}" for k, v in item.items()).strip()
    return str(item).strip()


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
        # rather than strings ("242.76 support"), or as an object
        # ({"price": 242.76, "type": "support"}) — coerce so a near-miss
        # reply isn't thrown away over a type, and so an object doesn't
        # render as a raw Python repr ("{'price': ...}") in the UI.
        # ``null`` becomes an empty list; any other non-list is left for
        # the strict check.
        if v is None:
            return []
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for item in v:
            s = _stringify_list_item(item)
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


def parse_ai_reply(text: str | None, structured: bool = False) -> AnalysisResponse:
    """Extract a structured ``AnalysisResponse`` from an AI reply.

    Strategy:
    1. When ``structured`` is True (the provider guaranteed JSON output),
       try ``json.loads`` directly on the text — skip regex extraction.
    2. Otherwise (or if direct parse fails), delegate JSON extraction to
       :func:`extract_json_object`.
    3. ``json.loads`` and validate against the model.

    Raises ``ValueError`` when the reply is empty, doesn't contain
    JSON, or fails schema validation. The caller should fall back
    to an ``UncertaintyResponse`` in that case — the spec says we
    must never trust a malformed AI reply.
    """
    if structured:
        text_stripped = (text or "").strip()
        if text_stripped.startswith("```json"):
            text_stripped = text_stripped.removeprefix("```json")
        if text_stripped.endswith("```"):
            text_stripped = text_stripped.removesuffix("```")
        text_stripped = text_stripped.strip()
        try:
            data = json.loads(text_stripped)
            return AnalysisResponse.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            pass
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
4. The context may include "news", "fundamentals", "divergence", \
   "tape", and "track_record" sections. Treat these as supporting \
   evidence only — they may inform "supporting_factors" or \
   "risk_factors", but they must NEVER override the quant-derived \
   "trend" field. An empty or missing section just means that data \
   wasn't available — its absence is not evidence of anything. The \
   "tape" section is raw recent order-flow aggregation (buy vs sell \
   pressure, block prints, tape speed) — you MAY quote its values \
   directly, framed as "recent tape". The "track_record" section, \
   when present, is YOUR OWN past buy/sell calls on this ticker \
   graded against what happened (win_rate, sample_size, ...) — frame \
   it honestly, never as a guarantee, and say so explicitly when \
   sample_size is small (under ~5).
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
   (array, max 10, key support/resistance prices as short strings, \
   e.g. "756.64 resistance" — always name which one, never a bare \
   number), and "trade_plan" (object, see rule 7).
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


# I1: Confidence calibration — injects the AI's own track-record win-rate
# into the system prompt so the model can self-calibrate its confidence.
_CONFIDENCE_CALIBRATION = """\
Your historical accuracy on this ticker is {win_rate}% over {n} resolved \
calls. Calibrate your confidence accordingly — if your past calls on this \
ticker have been wrong more often than right, lower your confidence."""

# I2: Regime-aware prompt tuning — adds a risk-first framing clause when
# the market regime signals high volatility or crisis conditions.
_RISK_FIRST_CLAUSE = """\
IMPORTANT — you are currently in a {regime} market regime. In this \
environment, prioritize capital preservation over profit capture. \
Weight risk_factors more heavily than supporting_factors: a setup that \
looks attractive in a calm regime may be a trap in high volatility. \
Reduce position-size reasoning accordingly (smaller, tighter, and \
more conservative)."""

# O10: Cross-ticker correlation summary — injects the portfolio/sector
# peer trend directions so the model can reason about confluence and
# divergence instead of analyzing each ticker in isolation.
_CORRELATION_CLAUSE = """\
Portfolio/sector peers ({peer_count} scanned): {aligned} bullish, \
{opposed} bearish. Primary sector: {sector}. When the ticker you are \
analyzing is moving WITH the sector/peer group, the trend is more \
likely to continue; when it is diverging (moving against the group), \
treat that as a warning sign and weight risk_factors accordingly. \
A lone bullish signal in a sea of bearish peers is a divergence, not \
a buying opportunity."""


def _format_win_rate(win_rate: float) -> str:
    """Format a 0.0-1.0 win rate as a percentage string."""
    return f"{int(win_rate * 100)}%"


def render_system_prompt(
    base_prompt: str,
    *,
    track_record: dict[str, Any],
    market_regime: dict[str, Any],
    correlation_context: dict[str, Any] | None = None,
) -> str:
    """Render a system prompt with I1/I2 dynamic injections.

    I1 — Confidence calibration: when ``track_record`` has a ``win_rate``
    and ``sample_size``, appends a calibration clause so the model knows
    its own historical accuracy on this ticker.

    I2 — Regime-aware tuning: when ``market_regime`` signals
    ``high_volatility`` or ``crisis``, appends a risk-first framing
    clause and lowers the effective guidance.

    O10 — Multi-symbol correlation: when ``correlation_context`` has
    peer data, appends a cross-ticker confluence summary so the model
    can reason about portfolio/sector alignment instead of analyzing
    in isolation.

    Args:
        base_prompt: The base system prompt (``SYSTEM_PROMPT`` or
            ``SYSTEM_PROMPT_ANALYST_ONLY``).
        track_record: The ``AnalysisContext.track_record`` dict.
        market_regime: The ``AnalysisContext.market_regime`` dict.
        correlation_context: The ``AnalysisContext.correlation_context``
            dict (optional).
    """
    additions: list[str] = []

    # --- I1: confidence calibration ---
    win_rate = track_record.get("win_rate")
    sample_size = track_record.get("sample_size", 0)
    if win_rate is not None and sample_size:
        additions.append(
            _CONFIDENCE_CALIBRATION.format(
                win_rate=_format_win_rate(win_rate), n=sample_size,
            )
        )

    # --- I2: regime-aware risk-first framing ---
    regime_str = market_regime.get("regime", "")
    if regime_str in ("high_volatility", "crisis"):
        additions.append(_RISK_FIRST_CLAUSE.format(regime=regime_str))

    # --- O10: cross-ticker correlation summary ---
    if correlation_context and correlation_context.get("peer_count"):
        peer_count = correlation_context["peer_count"]
        aligned = correlation_context.get("aligned", 0)
        opposed = correlation_context.get("opposed", 0)
        sector = correlation_context.get("primary_sector", "")
        additions.append(
            _CORRELATION_CLAUSE.format(
                peer_count=peer_count,
                aligned=aligned,
                opposed=opposed,
                sector=sector or "Unknown",
            )
        )

    if not additions:
        return base_prompt
    return base_prompt + "\n\n" + "\n\n".join(additions)


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

    # Six more tools (2026-09-11): the chat can create/delete an alert
    # and add/remove a watchlist ticker or watchlist itself. Flat
    # fields, not a nested object — this codebase's chat schema stays
    # flat deliberately; a weak local model mangles nested JSON far more
    # often than it mangles an extra top-level key (see the null-list
    # and dict-repr fixes this session). "none" (the default) means no
    # action this turn. The *_delete_* / remove_* / delete_* actions are
    # destructive: backend.ai.chat._finalize_parsed refuses to execute
    # them without action_confirmed=True regardless of what the prompt
    # says — see CHAT_SYSTEM_PROMPT rule 10 for when the model may set
    # them.
    action: Literal[
        "none", "create_alert", "modify_alert", "delete_alert",
        "add_to_watchlist", "remove_from_watchlist",
        "create_watchlist", "delete_watchlist",
        "run_backtest", "set_entity_type", "run_screen",
    ] = "none"
    action_symbol: str | None = Field(default=None, max_length=20)
    action_watchlist: str | None = Field(default=None, max_length=120)
    action_condition_type: str | None = Field(default=None, max_length=40)
    action_parameter: str | None = Field(default=None, max_length=120)
    action_label: str | None = Field(default=None, max_length=120)
    action_target_id: int | None = None
    # set_entity_type only: "stock" or "etf" to relabel action_symbol as.
    action_entity_type: Literal["stock", "etf"] | None = None
    # run_screen only: the trader's free-text screening criteria (e.g.
    # "oversold with rising volume"), handed to the existing
    # backend.nl_search parser/executor — the same engine behind
    # POST /api/nl-search — rather than the model inventing filter logic
    # itself.
    action_query: str | None = Field(default=None, max_length=300)
    action_confirmed: bool = False

    @field_validator("action_condition_type")
    @classmethod
    def _validate_condition_type(cls, v: str | None) -> str | None:
        # An invalid condition type degrades to "couldn't set that" in
        # _run_action rather than failing the whole reply's parse — same
        # tolerance policy as the other near-miss coercions in this file.
        if v is not None and v not in _VALID_ALERT_CONDITION_TYPES:
            return None
        return v


# Shared by CHAT_SYSTEM_PROMPT (rule 10) and CHAT_CONTINUATION_SYSTEM_PROMPT
# — the per-tool field/behavior rules a completion needs to DECIDE an
# action are identical whether it's the turn's first decision or a
# multi-step continuation (rule 12); only the surrounding framing (when
# to just answer instead, how "reply" is used, the watchlist-CONTENTS-
# is-not-an-action carve-out) differs between the two, so only that
# framing is duplicated, not these ~10 tool-behavior bullets. Kept
# deliberately terse (2026-09-16 trim) — this text is resent on every
# single completion call, including every chained continuation one.
_ACTION_TOOL_DOCS = """\
    - create_alert / modify_alert / add_to_watchlist / create_watchlist / \
      run_backtest / set_entity_type / run_screen fire on the FIRST \
      clear request — no confirmation needed. Fill the matching \
      action_* fields.
    - delete_alert / remove_from_watchlist / delete_watchlist are \
      DESTRUCTIVE (modify_alert is NOT — it fires immediately like \
      create_alert). Set "action" and its action_* fields as soon as \
      it's clear what's being removed, but leave action_confirmed=false \
      unless the trader's OWN message right now clearly confirms \
      (yes / confirm / do it / go ahead) a destructive action you \
      already proposed. The app asks the confirmation question itself \
      when action_confirmed is false — never compose that wording \
      yourself.
    - delete_alert / modify_alert need action_target_id, the numeric \
      "id" from active_alerts in the <market> block. If you can't find \
      a matching alert there, say so instead of guessing an id.
    - create_alert needs action_symbol, action_condition_type (one of: \
      """ + ", ".join(_VALID_ALERT_CONDITION_TYPES) + """), and \
      action_parameter (the threshold, e.g. "220" for a price level or \
      "5" for a percent). action_label is an optional short name.
    - modify_alert changes an EXISTING alert (found via active_alerts) \
      in place instead of delete-then-recreate — e.g. "change my NVDA \
      alert to 230". Set action_target_id plus only the fields \
      actually changing; leave the rest null to keep their current \
      value.
    - add_to_watchlist / remove_from_watchlist / create_watchlist / \
      delete_watchlist use action_symbol and/or action_watchlist (the \
      watchlist name). The <market> block's "watchlists" array has \
      every real name + size (not contents) — use real names in \
      conversational "reply" text, but for the action itself only set \
      action_watchlist when the trader named one; otherwise leave it \
      unset and let the app resolve it (it will ask by name if \
      genuinely ambiguous) — never guess which watchlist an unnamed \
      "my watchlist" means yourself.
    - run_backtest needs action_symbol — runs a fresh 6-month daily \
      backtest of the engine's own signals with a real win rate / avg \
      return. Use for "how has this performed historically", not "is \
      it moving right now" (that's the tape section already in \
      context).
    - run_screen needs action_query, the trader's screening criteria \
      verbatim or lightly cleaned (e.g. "oversold with rising \
      volume") — a real screener over their watchlist, not answered \
      from your context. action_watchlist is optional; their default \
      list otherwise. Use for any find/screen/scan request or "which \
      of my names look weak" — never answer those from the <market> \
      block (market-wide only, no per-name results).
    - set_entity_type needs action_symbol and action_entity_type \
      ("stock" or "etf") — use when a ticker is mislabeled or the \
      trader asks to reclassify it. A real, changeable per-watchlist \
      label — never say you can't relabel a ticker, set this action \
      instead.
"""

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
3. For market-wide questions ("what's the market doing", "how's \
   sentiment") answer from the <market> block — regime_live is \
   current; the digest is from its generated_at timestamp, so hedge if \
   it looks stale. Name only symbols that actually appear in the \
   block. If <market> is empty, say the market read isn't available \
   and set "grounded" to false. For "which of my names look weak/strong" \
   or any request to find/rank/screen names by a description, use the \
   run_screen tool (rule 10) instead — the <market> block has no \
   per-name breakdown to answer that from.
4. Prior turns are provided for conversational continuity, but the \
   <context>/<market> blocks are always the current live truth — if an \
   earlier turn discussed older data, prefer the blocks over your own \
   past replies. A "tape" section, when present, is raw recent \
   order-flow aggregation (buy/sell pressure, block prints, tape \
   speed); you may quote it directly as "recent tape". A \
   "track_record" section, when present, is YOUR OWN past buy/sell \
   calls on that ticker graded against what happened — frame it \
   honestly, never as a guarantee, and flag it when sample_size is \
   small (under ~5).
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
10. You have TEN more tools, via "action": create_alert, modify_alert, \
    delete_alert, add_to_watchlist, remove_from_watchlist, \
    create_watchlist, delete_watchlist, run_backtest, set_entity_type, \
    run_screen.
""" + _ACTION_TOOL_DOCS + """    - Asking about a watchlist's CONTENTS or asking to ANALYZE one \
      ("what's in my X watchlist", "analyze my X watchlist") is never \
      an "action" — the app resolves the name itself and gives you a \
      normal <context> block per member ticker (same shape as any \
      other named ticker) BEFORE you reply; just analyze those blocks \
      normally, exactly as any other multi-ticker turn (rule 1 still \
      applies: don't cross-reference numbers between tickers). Only \
      say you lack visibility into a watchlist's contents when NO \
      such block was provided this turn (an unresolved name) — not \
      that watchlist data is categorically unavailable to you.
    - When "action" is set to anything but "none", "reply" is ignored \
      (a short placeholder is fine) — the app executes the action and \
      replies with its own result instead, same as wants_reanalysis.
11. NEVER state or imply in "reply" that you added, removed, deleted, \
    created, or changed anything (an alert, a watchlist, a ticker) — \
    you cannot perform any of those yourself; only the app can, and \
    only when "action" is set to the matching tool this turn. If \
    "action" is "none", nothing was changed, no matter what the \
    trader asked for — say what you need (a confirmation, a missing \
    ticker, a clarification) instead of claiming it's done.
12. Still exactly one "action" per reply — never invent a way to set \
    more than one, even when a message asks for several things \
    ("create a watchlist called Tech and add NVDA to it"). Set the \
    first one now; the app decides on its own whether anything from \
    the message still needs doing after that runs.
"""

# A separate, deliberately lean system prompt for a multi-step
# continuation call (backend.ai.chat._run_turn_actions, 2026-09-16) —
# deciding "what's the next step of a compound request already in
# progress" needs the tool rules (_ACTION_TOOL_DOCS) but none of
# CHAT_SYSTEM_PROMPT's rules 1-9 about grounding a conversational
# answer in <context>/<market> data, since a continuation call never
# produces one ("reply" is always discarded here — see _run_turn_actions).
# This prompt is resent on every chained step, so trimming it matters
# more than trimming the main prompt: a 3-step compound turn used to
# resend the FULL ~2.4k-token CHAT_SYSTEM_PROMPT three times; the 2nd
# and 3rd calls now send this instead.
CHAT_CONTINUATION_SYSTEM_PROMPT = """\
You already ran the first step of a multi-part trader request; the \
message below has the ORIGINAL request plus what's already been done. \
Decide: is anything from the original request still undone?

Your output is a single JSON object with EXACTLY these fields: \
"reply" (string — always discarded here, a short placeholder is \
fine), "grounded" (boolean, true), "action" (see tools below, or \
"none" if everything requested is already done), plus whichever \
action_* fields that tool needs (null for the rest). Wrap it in a \
single ```json ... ``` block, no prose outside it. Still exactly one \
"action" — never invent a way to set more than one, even if more \
than one thing is still left; you'll be asked again after this one \
runs.

Tools, via "action": create_alert, modify_alert, delete_alert, \
add_to_watchlist, remove_from_watchlist, create_watchlist, \
delete_watchlist, run_backtest, set_entity_type, run_screen.
""" + _ACTION_TOOL_DOCS

# Rough token estimate for the assembled prompt's size guard.
def _approx_tokens(s: str) -> int:
    return len(s) // 4


_MAX_NEWS_ITEMS = 3
_MAX_NEWS_TEXT_CHARS = 120
_MAX_SIGNALS = 5
_MAX_TIMEFRAME_SCORES = 2
_MAX_FUNDAMENTALS_TEXT_CHARS = 60
_DEFAULT_ANALYSIS_TOKEN_BUDGET = 4000


def summarize_context(
    context_dict: dict[str, Any],
    *,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Compress large list/string fields so the serialized context fits within *token_budget*.

    Only safe-to-lose verbose fields (``news``, ``market_structure.signals``,
    non-primary ``timeframe_scores``, ``fundamentals`` text) are touched.
    All scalar and core quant fields are preserved so a schema-valid
    ``AnalysisResponse`` can still be produced.  A ``"context_summarized"``
    flag is set on the returned dict when truncation occurs.
    """
    budget = token_budget if token_budget is not None else _DEFAULT_ANALYSIS_TOKEN_BUDGET
    body = json.dumps(context_dict, default=str)
    if _approx_tokens(body) <= budget:
        return context_dict

    out = dict(context_dict)
    out["context_summarized"] = True

    news = out.get("news")
    if isinstance(news, list) and len(news) > _MAX_NEWS_ITEMS:
        out["news"] = _truncate_news(news[:_MAX_NEWS_ITEMS])

    ms = out.get("market_structure")
    if isinstance(ms, dict) and isinstance(ms.get("signals"), list):
        ms = dict(ms)
        ms["signals"] = ms["signals"][:_MAX_SIGNALS]
        out["market_structure"] = ms

    tf_scores = out.get("timeframe_scores")
    if isinstance(tf_scores, dict) and len(tf_scores) > _MAX_TIMEFRAME_SCORES:
        primary = out.get("timeframe", "1d").upper()
        keys = sorted(tf_scores, key=lambda k: k == primary, reverse=True)
        out["timeframe_scores"] = {k: tf_scores[k] for k in keys[:_MAX_TIMEFRAME_SCORES]}

    funda = out.get("fundamentals")
    if isinstance(funda, dict):
        out["fundamentals"] = {
            k: v[:_MAX_FUNDAMENTALS_TEXT_CHARS]
            if isinstance(v, str) and len(v) > _MAX_FUNDAMENTALS_TEXT_CHARS
            else v
            for k, v in funda.items()
        }

    if _approx_tokens(json.dumps(out, default=str)) > budget:
        out.pop("news", None)

    return out


def _truncate_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    capped: list[dict[str, Any]] = []
    for item in items[:_MAX_NEWS_ITEMS]:
        if isinstance(item, dict):
            capped.append(
                {
                    k: v[:_MAX_NEWS_TEXT_CHARS]
                    if isinstance(v, str) and len(v) > _MAX_NEWS_TEXT_CHARS
                    else v
                    for k, v in item.items()
                }
            )
        else:
            capped.append(item)
    return capped


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


# --- O11: Structured output schema -----------------------------------

# When the provider supports structured output, the manager passes this
# response_format dict so the model is forced to return a single JSON
# object matching AnalysisResponse. On OpenAI-compatible endpoints this
# becomes ``response_format={"type": "json_object"}``; on Anthropic it
# becomes a single tool_use declaration whose input_schema is the
# schema below.
ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "name": "analysis_json_output",
    "description": "Structured JSON conforming to AnalysisResponse.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 10, "maxLength": 2000},
            "trend": {
                "type": "string",
                "enum": ["bullish", "bearish", "neutral", "mixed", "uncertain"],
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "supporting_factors": {
                "type": "array", "items": {"type": "string"}, "maxItems": 10,
            },
            "risk_factors": {
                "type": "array", "items": {"type": "string"}, "maxItems": 10,
            },
            "timeframe_conflicts": {
                "type": "array", "items": {"type": "string"}, "maxItems": 10,
            },
            "key_levels": {
                "type": "array", "items": {"type": "string"}, "maxItems": 10,
            },
        },
        "required": ["summary", "trend", "confidence"],
    },
}


def make_analysis_response_format() -> dict[str, Any]:
    """Return the ``response_format`` dict for the OpenAI-compatible channel.

    OpenAI-compatible endpoints expect ``response_format={"type": "json_object"}``
    to enable JSON mode. We embed the ANALYSIS_JSON_SCHEMA inside the
    ``json_schema`` key so providers that support schema validation use it;
    the Anthropic adapter ignores the wrapper and reads from ``json_schema``.
    """
    return {
        "type": "json_object",
        "json_schema": ANALYSIS_JSON_SCHEMA,
    }
