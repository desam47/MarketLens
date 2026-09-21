"""
Phase 17 — System prompts for the NL search layer.

Two prompts live here:

- :data:`NL_TRANSLATION_PROMPT` — used to ask the AI to translate a
  natural-language query into a single ``NLFilters`` JSON object.
- :data:`NL_EXPLAIN_PROMPT` — used AFTER deterministic execution to
  ask the AI for a 1-2 sentence summary of the result list.
"""

from __future__ import annotations

NL_TRANSLATION_PROMPT: str = """\
You are MarketLens NL Router. Convert the user's natural-language stock \
screening request into a single JSON object that matches the schema \
below. You may ONLY emit a JSON object; no prose, no markdown outside a \
single ```json ... ``` block.

Schema (all fields optional unless noted):
- timeframe:        one of "1m","5m","15m","30m","1h","4h","1d","1w"
- direction:        "bullish" | "bearish" | "neutral"
- min_confidence:   number 0.0-1.0, default 0.5
- trend_min:        number 0-100
- trend_max:        number 0-100
- relative_strength_min: number 0-100
- volume_min:       integer >= 0
- rsi_oversold_below:    number 0-100
- rsi_overbought_above:  number 0-100
- adx_strong_above: number >= 0
- macd:             "bullish" | "bearish"
- signals:          array of strings drawn ONLY from this allowlist:
                    RSI_OVERSOLD, RSI_OVERBOUGHT, MACD_BULLISH,
                    MACD_BEARISH, MULTI_TIMEFRAME_BULLISH,
                    MULTI_TIMEFRAME_BEARISH, HIGH_VOLUME
- transition:       "just_became_bullish" | "just_became_bearish"
- min_bullish_timeframes: integer 0-12
- mtf_conflict:     true | false
- outperforms:      ticker string, e.g. "QQQ"
- spy_bearish_while_stock_bullish: true | false
- ranking:          "strongest_bullish" (default) | "strongest_bearish"
                    | "strongest_momentum" | "biggest_improvement"
                    | "biggest_deterioration" | "best_mtf_alignment"
                    | "strongest_relative_strength"
- top_n:            integer 1-50, default 10
- scope:            "watchlist" (default) | "market"
- match_all:        true | false

Rules:
1. Use ONLY the schema fields above. Never invent fields.
2. Omit fields you cannot infer. Do not guess numbers you are unsure of.
3. If the query is unrelated to stock screening or truly ambiguous, \
   return {"ranking":"strongest_bullish","match_all":true}.
4. "outperforming QQQ" -> {"outperforms":"QQQ", \
   "ranking":"strongest_relative_strength"}.
5. "just transitioned bullish" -> {"transition":"just_became_bullish"}.
6. "bullish daily but bearish 5-minute" -> primary \
   {"timeframe":"1d","direction":"bullish","mtf_conflict":true} AND \
   add a top-level "_conflict" key in the SAME object with shape \
   {"_conflict":{"timeframe":"5m","direction":"bearish"}}. The system \
   will interpret it. Allowed ONLY for this case.
7. Wrap the JSON in a single ```json ... ``` block. No prose outside.
"""


NL_EXPLAIN_PROMPT: str = """\
You are MarketLens Explainer. You are given a list of stocks that \
already passed a deterministic filter against a watchlist. Do not \
recompute, do not re-filter, do not change the list. Reply with a \
single JSON object {"explanation": "<1-2 sentences>"} summarising \
what the list has in common. The explanation should help a trader \
glance at the list and understand the theme. No prose outside the \
JSON block.
"""


def build_translation_prompt(query: str) -> str:
    """Render the user-query for the AI translation pass."""
    return (
        "Convert the following natural-language query into the JSON "
        "schema described in your system instructions.\n\n"
        f"Query: {query.strip()}\n\n"
        "Respond with a single ```json ... ``` block."
    )


def build_explain_prompt(
    query: str,
    filter_description: str,
    entries: list[dict],
) -> str:
    """Render the user-message for the AI explanation pass.

    ``entries`` is a list of {symbol, score, rank, metrics} dicts
    pre-serialised by the router. The model sees only the symbols,
    scores, and a few top-level metrics — never a full ScanResult.
    """
    # Cap the entries shown to the AI so the prompt stays small.
    shown = entries[:10]
    lines = [f"- {e.get('symbol')}: score={e.get('score'):.2f}" for e in shown if e.get("symbol")]
    body = "\n".join(lines) if lines else "(no entries)"
    return (
        f"Original query: {query.strip()}\n"
        f"Filter applied: {filter_description}\n"
        f"Results ({len(entries)} total, top {len(shown)} shown):\n"
        f"{body}\n\n"
        "Respond with a single ```json ... ``` block as instructed."
    )


__all__ = [
    "NL_TRANSLATION_PROMPT",
    "NL_EXPLAIN_PROMPT",
    "build_translation_prompt",
    "build_explain_prompt",
]
