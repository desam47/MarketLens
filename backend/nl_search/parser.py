"""
Phase 17 — NL query → ``NLFilters`` translation.

Two paths, sharing one orchestrator:

- :func:`parse_query_rule_based` — pure-Python regex/keyword parser.
  Handles the six canonical example phrases from the spec, plus a
  handful of obvious variants. Deterministic, no AI cost.
- :func:`parse_query_with_ai` — Phase 15 AI manager, asked to emit a
  single ``NLFilters`` JSON. Used as the primary path when the AI is
  available and the rule-based parser found nothing.

The orchestrator :func:`parse_query` tries rule-based first (cheap,
deterministic) and only escalates to AI when the rules return
``None``. Either way, a usable ``NLFilters`` is returned — the
endpoint never sees ``None`` unless the orchestrator itself fell
back to a ``match_all=True`` default.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from backend.ai.manager import ai_manager
from backend.ai.prompt import extract_json_object
from backend.ai.sync_bridge import run_sync

from .prompt import NL_TRANSLATION_PROMPT, build_translation_prompt
from .schema import NLFilters

logger = logging.getLogger(__name__)


# --- Rule-based parser ----------------------------------------------


# A rule is (compiled regex, partial-dict-or-callable). Each is tried
# against the lower-cased query; later rules do NOT overwrite a key
# already set by an earlier rule.
_RULES: list[tuple[re.Pattern, dict | Callable[[re.Match[str]], dict]]] = [
    # Rankings
    (
        re.compile(r"\bstrongest\s+bullish\b"),
        {"ranking": "strongest_bullish", "direction": "bullish"},
    ),
    (
        re.compile(r"\bstrongest\s+bearish\b"),
        {"ranking": "strongest_bearish", "direction": "bearish"},
    ),
    (re.compile(r"\bstrongest\s+momentum\b"), {"ranking": "strongest_momentum"}),
    (
        re.compile(r"\bbest\s+mtf\b|\bbest\s+multi[- ]timeframe\b"),
        {"ranking": "best_mtf_alignment"},
    ),
    (re.compile(r"\bbiggest\s+improvement\b"), {"ranking": "biggest_improvement"}),
    (re.compile(r"\bbiggest\s+deterioration\b"), {"ranking": "biggest_deterioration"}),
    (re.compile(r"\bstrongest\s+relative\b"), {"ranking": "strongest_relative_strength"}),
    # Direction + timeframe
    (
        re.compile(r"\bbullish\s+daily\b|\bdaily\s+bullish\b"),
        {"timeframe": "1d", "direction": "bullish"},
    ),
    (
        re.compile(r"\bbearish\s+daily\b|\bdaily\s+bearish\b"),
        {"timeframe": "1d", "direction": "bearish"},
    ),
    (
        re.compile(r"\bbullish\s+(\d{1,2})\s*h(?:our)?\b"),
        lambda m: {"timeframe": f"{m.group(1)}h", "direction": "bullish"},
    ),
    (
        re.compile(r"\bbullish\s+(\d{1,2})\s*m(?:in)?\b"),
        lambda m: {"timeframe": f"{m.group(1)}m", "direction": "bullish"},
    ),
    (
        re.compile(r"\bbearish\s+(\d{1,2})\s*h(?:our)?\b"),
        lambda m: {"timeframe": f"{m.group(1)}h", "direction": "bearish"},
    ),
    (
        re.compile(r"\bbearish\s+(\d{1,2})\s*m(?:in)?\b"),
        lambda m: {"timeframe": f"{m.group(1)}m", "direction": "bearish"},
    ),
    # Transitions
    (
        re.compile(r"\b(just|recently)\s+(turned|transitioned|became)\s+bullish\b"),
        {"transition": "just_became_bullish"},
    ),
    (
        re.compile(r"\b(just|recently)\s+(turned|transitioned|became)\s+bearish\b"),
        {"transition": "just_became_bearish"},
    ),
    # Standalone "uptrend yet bearish 1h" pattern
    (re.compile(r"\buptrend\b"), {"transition": "just_became_bullish"}),
    (re.compile(r"\bdowntrend\b"), {"transition": "just_became_bearish"}),
    # Outperformance & SPY (case-insensitive on the benchmark symbol)
    (
        re.compile(r"\boutperform(?:ing|ers?)?\s+([a-z]{1,5})\b"),
        lambda m: {
            "outperforms": m.group(1).upper(),
            "ranking": "strongest_relative_strength",
        },
    ),
    # SPY: only match the *left* half of a while-split. The full "bullish
    # while SPY is bearish" pattern is split by _split_clauses first, so
    # the left half becomes just "bullish" which doesn't tell us anything
    # SPY-specific. Detect the SPY token on the right half in
    # _apply_conflict_rules. But for queries like "bullish while SPY is
    # bearish" that don't get split, this rule still fires.
    (
        re.compile(r"\bbullish\s+vs\s+bearish\s+spy\b"),
        {"spy_bearish_while_stock_bullish": True, "direction": "bullish"},
    ),
    # Also match when the right (conflict) half of a while-split mentions SPY.
    # _apply_rules detects SPY in the conflict clause and sets
    # spy_bearish_while_stock_bullish there.
    (
        re.compile(r"\bbullish\s+while\s+.*spy\b"),
        {"spy_bearish_while_stock_bullish": True, "direction": "bullish"},
    ),
    # Strength / volume
    (
        re.compile(r"\bstrong\s+trend\b|\btrending\s+strong\b|\bstrong\s+trends?\b"),
        {"adx_strong_above": 25.0},
    ),
    (re.compile(r"\b(?:strong|high)\s+volume\b"), {"signals": ["HIGH_VOLUME"]}),
    # Numeric thresholds
    (
        re.compile(r"\btrend\s+(?:score\s+)?(?:over|above|>=?|min)\s*(\d{1,3})\b"),
        lambda m: {"trend_min": float(m.group(1))},
    ),
    (
        re.compile(r"\btrend\s+(?:score\s+)?(?:under|below|<=?|max)\s*(\d{1,3})\b"),
        lambda m: {"trend_max": float(m.group(1))},
    ),
    (re.compile(r"\brsi\s+oversold\b|\boversold\b"), {"rsi_oversold_below": 30.0}),
    (re.compile(r"\brsi\s+overbought\b|\boverbought\b"), {"rsi_overbought_above": 70.0}),
    # MTF agreement
    (
        re.compile(r"\b(?:all|every)\s+timeframes?\s+(?:aligned|agree|bullish)\b"),
        {"mtf_alignment": True, "min_bullish_timeframes": 3},
    ),
    # Bare direction words with no timeframe/ranking qualifier — catches
    # the very common "bullish stocks" / "bearish stocks" / "show me
    # bearish" phrasing that no rule above matches, without paying an
    # AI round-trip (~1-1.5s) for something this simple. Placed LAST so
    # every more specific rule above (ranking, timeframe+direction,
    # transition, SPY) wins via setdefault — this is a pure fallback.
    # Also sets `ranking` to match direction (AI already does this;
    # otherwise NLFilters' schema default of "strongest_bullish" would
    # silently rank a bearish query wrong-way).
    (re.compile(r"\bbullish\b"), {"direction": "bullish", "ranking": "strongest_bullish"}),
    (re.compile(r"\bbearish\b"), {"direction": "bearish", "ranking": "strongest_bearish"}),
]


def _split_clauses(q: str) -> tuple[str, str | None]:
    """Split ``"bullish daily but bearish 5m"`` → ``("bullish daily",
    "bearish 5m")``.

    Returns ``(whole, None)`` if no separator is found.
    """
    lower = q.lower()
    for sep in (" but ", " yet ", " while "):
        if sep in lower:
            return q.split(sep, 1)[0], q.split(sep, 1)[1]
    return q, None


def _apply_rules(query: str) -> tuple[dict, dict | None]:
    """Run every rule against ``query`` and merge into a single dict.

    Returns ``(merged, conflict_clause)`` where ``conflict_clause`` is
    the right-hand side of a ``but``/``yet``/``while`` split, if any.
    The two halves are then re-merged in :func:`parse_query_rule_based`.
    """
    primary, conflict = _split_clauses(query)
    merged: dict[str, Any] = {}
    for pattern, payload in _RULES:
        m = pattern.search(primary.lower())
        if not m:
            continue
        if callable(payload):
            payload = payload(m)
        for k, v in payload.items():
            # First match wins per key.
            merged.setdefault(k, v)
    # If the conflict half mentions SPY, set the SPY-bearish filter.
    # Also look for "spy" anywhere in the original query (for the "bullish
    # while SPY is bearish" form where the full pattern is matched).
    if conflict and re.search(r"\bspy\b", conflict.lower()):
        merged.setdefault("spy_bearish_while_stock_bullish", True)
    # Also match the SPY keyword anywhere in the original query.
    if re.search(r"\bspy\b", query.lower()):
        merged.setdefault("spy_bearish_while_stock_bullish", True)
        merged.setdefault("direction", "bullish")
    # Mark cross-TF conflict whenever a but/yet/while clause is present.
    if conflict:
        merged.setdefault("mtf_conflict", True)
    return merged, (conflict.lower() if conflict else None)


def _apply_conflict_rules(conflict_clause: str, primary_direction: str = "bullish") -> dict:
    """Apply the *direction-only* rules to a conflict clause.

    ``primary_direction`` is used when the conflict clause is a bare timeframe
    (e.g. "5m") with no explicit direction word.

    Only timeframe + direction patterns are considered — a conflict
    clause is "bearish 5m", "bearish weekly", etc. The result is
    returned as a dict the executor can fold into the filter.
    """
    out: dict[str, Any] = {}
    lower = conflict_clause.lower().strip()

    # SPY-specific: "spy is bearish" in the conflict clause means the user
    # wants the stock bullish while SPY is bearish.
    if re.search(r"\bspy\b", lower):
        out["spy_bearish_while_stock_bullish"] = True
        return out

    # Map common timeframe words to Timeframe literals.
    TF_WORDS = {
        "daily": "1d",
        "day": "1d",
        "1d": "1d",
        "hourly": "1h",
        "1h": "1h",
        "hour": "1h",
        "hours": "1h",
        "weekly": "1w",
        "week": "1w",
        "1w": "1w",
    }

    # If the conflict clause starts with a direction word (e.g. "bearish 5m"),
    # the direction is that word; extract just the timeframe part.
    dir_word, _, tf_part = lower.partition(" ")
    if dir_word in ("bullish", "bearish"):
        direction = dir_word
        # Try word-form timeframe first, then digit form.
        tf_key = tf_part.strip()
        if tf_key in TF_WORDS:
            out["timeframe"] = TF_WORDS[tf_key]
            out["direction"] = direction
        else:
            m = re.search(r"(\d+[hm])", tf_part)
            if m:
                out["timeframe"] = m.group(1)
                out["direction"] = direction
        return out

    # Bare timeframe word (e.g. "daily") — use primary_direction.
    if lower in TF_WORDS:
        out["timeframe"] = TF_WORDS[lower]
        out["direction"] = primary_direction
        return out

    # Bare digit timeframe (e.g. "5m", "1h") — use primary_direction.
    m = re.search(r"(\d+[hm])", lower)
    if m:
        out["timeframe"] = m.group(1)
        out["direction"] = primary_direction
    return out


def parse_query_rule_based(
    query: str,
    *,
    base: dict | None = None,
) -> tuple[NLFilters, dict | None] | None:
    """Return ``(NLFilters, extras)`` if at least one rule fired.

    ``extras`` carries a parsed ``conflict`` dict (e.g.
    ``{"timeframe":"5m","direction":"bearish"}``) when the query
    contained a ``but``/``yet``/``while`` split; otherwise ``None``.
    """
    merged, conflict_clause = _apply_rules(query)
    # Only apply base params when at least one rule fired (or a conflict clause exists).
    if (merged or conflict_clause is not None) and base:
        for k, v in base.items():
            merged.setdefault(k, v)

    if not merged and conflict_clause is None:
        return None

    try:
        f = NLFilters(**merged)
    except ValidationError as e:
        logger.info("Rule-based parse produced invalid NLFilters: %s", e)
        return None

    extras: dict | None = None
    if conflict_clause is not None:
        primary_direction = f.direction if f.direction in ("bullish", "bearish") else "bullish"
        conflict_dict = _apply_conflict_rules(conflict_clause, primary_direction=primary_direction)
        if conflict_dict:
            extras = {"conflict": conflict_dict}

    return f, extras


# --- AI parser ------------------------------------------------------


# Translation uses the configured AI_TEMPERATURE like every other AI
# call in the app (no per-call override — see parse_query_with_ai
# below) and isn't purely deterministic. But unlike the AI
# *explanation* call (which depends on the current, live result set
# and must not be cached this loosely), a translation depends only on
# the query text, not on market data — so caching still trades a
# little per-call sampling diversity for a real win: a repeated query
# (re-running the same search, clicking the same example pill twice)
# gets a consistent interpretation and skips the ~1-1.5s AI
# round-trip entirely on a cache hit.
#
# Only successful parses are cached. Failures (AI off, bad JSON, a
# schema-rejected reply) are deliberately NOT cached, so a transient
# provider hiccup doesn't permanently force one literal query string
# into the fallback path until the process restarts.
_MAX_TRANSLATION_CACHE = 256
_translation_cache: OrderedDict[str, NLFilters] = OrderedDict()


def _translation_cache_get(key: str) -> NLFilters | None:
    cached = _translation_cache.get(key)
    if cached is not None:
        _translation_cache.move_to_end(key)
        return cached.model_copy(deep=True)
    return None


def _translation_cache_put(key: str, value: NLFilters) -> None:
    _translation_cache[key] = value.model_copy(deep=True)
    _translation_cache.move_to_end(key)
    while len(_translation_cache) > _MAX_TRANSLATION_CACHE:
        _translation_cache.popitem(last=False)


def parse_query_with_ai(query: str) -> NLFilters | None:
    """Ask the AI to translate ``query`` → ``NLFilters``.

    Returns ``None`` for any failure mode (AI off, gibberish reply,
    bad schema, missing JSON). The caller is expected to fall back
    to the rule-based parser or to a ``match_all=True`` default.
    """
    cache_key = query.strip().lower()
    cached = _translation_cache_get(cache_key)
    if cached is not None:
        return cached

    # ``parse_query_with_ai`` runs in a worker thread (the router pushes
    # ``parse_query`` through ``asyncio.to_thread``), so it has no event
    # loop — bridge the async manager calls instead of awaiting.
    if not run_sync(ai_manager.is_available()):
        return None

    try:
        # No per-call temperature override — uses the configured
        # AI_TEMPERATURE like every other AI call in the app, rather
        # than special-casing this one as a "must be deterministic"
        # task.
        resp = run_sync(
            ai_manager.complete(
                prompt=build_translation_prompt(query),
                system=NL_TRANSLATION_PROMPT,
                max_tokens=400,
            )
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("AI translation call raised: %s", e)
        return None

    if resp.text is None:
        return None

    try:
        candidate = extract_json_object(resp.text)
        data = json.loads(candidate)
    except (ValueError, json.JSONDecodeError) as e:
        logger.info("AI translation reply had no JSON object: %s", e)
        return None

    # The schema allows a top-level "_conflict" extension used only
    # for cross-timeframe queries (see NL_TRANSLATION_PROMPT rule 6).
    if isinstance(data, dict):
        data.pop("_conflict", None)  # consumed but not propagated in v1

    try:
        result = NLFilters.model_validate(data)
    except ValidationError as e:
        logger.info("AI translation reply failed schema validation: %s", e)
        return None

    _translation_cache_put(cache_key, result)
    return result


# --- Orchestrator ---------------------------------------------------


def parse_query(
    query: str,
    *,
    base: dict | None = None,
) -> tuple[NLFilters, dict | None, str]:
    """Top-level entry: rule-based first, AI second.

    Returns ``(filters, extras, parser_used)`` where ``parser_used``
    is one of ``"rules"``, ``"ai"``, or ``"default"``.

    Never raises. Falls back to a ``match_all=True`` ``NLFilters`` so
    the endpoint always has something to execute.
    """
    filters, extras, parser_used = _parse_query_inner(query, base=base)

    # ``scope`` (the Watchlist/Market dropdown) is a REQUEST-level
    # parameter, not something to infer from query text — it must win
    # regardless of which path produced ``filters``. Found live
    # 2026-09-09: selecting "Market" in the dropdown still searched the
    # watchlist. Root cause: `base`'s scope only ever reached the
    # rule-based path (parse_query_rule_based), and even there only via
    # `setdefault` when at least one keyword rule fired — any query
    # that fell through to the AI parser (which never received `base`
    # at all) or the graceful "no rule fired" default silently reverted
    # to NLFilters' own schema default ("watchlist"), no matter what
    # the dropdown said. Applying it once here, unconditionally, after
    # parsing — rather than threading it through every parse path —
    # means it can never be dropped again by a future path added here.
    if base and "scope" in base and filters.scope != base["scope"]:
        filters = filters.model_copy(update={"scope": base["scope"]})

    return filters, extras, parser_used


def _parse_query_inner(
    query: str,
    *,
    base: dict | None = None,
) -> tuple[NLFilters, dict | None, str]:
    rule_result = parse_query_rule_based(query, base=base)
    if rule_result is not None:
        return rule_result[0], rule_result[1], "rules"

    ai_result = parse_query_with_ai(query)
    if ai_result is not None:
        # When the AI path is used, the rule-based parser did not fire
        # but the AI's reply may still contain a `_conflict` hint. We
        # don't have that info here (parse_query_with_ai discarded it
        # after stripping); re-extract via a single round-trip.
        # Practically the rule-based path covers the cross-TF case for
        # the spec's example; if AI surfaces a fresh conflict we
        # surface it in the response's filter_description. For v1 we
        # keep it simple: re-derive the conflict from the raw query.
        _, conflict_clause = _split_clauses(query)
        extras: dict | None = None
        if conflict_clause is not None:
            cd = _apply_conflict_rules(conflict_clause.lower())
            if cd:
                extras = {"conflict": cd}
        return ai_result, extras, "ai"

    # Graceful default
    return NLFilters(match_all=True), None, "default"


__all__ = [
    "parse_query",
    "parse_query_rule_based",
    "parse_query_with_ai",
]
