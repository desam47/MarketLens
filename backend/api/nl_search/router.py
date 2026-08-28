"""
Phase 17 — API router for natural-language market search.

``POST /api/nl-search`` — takes a free-form English query, converts it
into a controlled scanner filter schema (AI-first, rule-based fallback),
executes the filter against the watchlist cache, and returns a ranked,
optionally AI-explained result list.

The endpoint never returns 500 for expected failures (AI off, empty
cache, invalid query, unparseable AI reply). The failure-mode contract
mirrors ``POST /api/ai/analyze``.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.ai.manager import ai_manager
from backend.ai.prompt import extract_json_object
from backend.api.dependencies import get_db
from backend.nl_search.executor import execute_query
from backend.nl_search.parser import parse_query
from backend.nl_search.prompt import NL_EXPLAIN_PROMPT, build_explain_prompt
from backend.nl_search.schema import NLFilters, NLSearchResponse, Ranking

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nl-search", tags=["nl-search"])


# --- Request / response models -----------------------------------------


class NLSearchRequest(BaseModel):
    """Body for ``POST /api/nl-search``."""

    query: str = Field(..., min_length=2, max_length=500)
    explain: bool = Field(default=True)
    top_n: int = Field(default=10, ge=1, le=50)
    watchlist_id: int | None = Field(default=None)
    scope: str = Field(default="watchlist")
    ranking: Ranking | None = Field(default=None)


# --- Explanation helper ----------------------------------------------


def _extract_explanation(text: str | None) -> str | None:
    """Pull the explanation string from an AI explanation reply.

    Accepts either:
    - A fenced JSON block containing ``{"explanation": "..."}``
    - Plain text (uses the first sentence, truncated to 500 chars)

    Returns ``None`` on failure.
    """
    if not text or not text.strip():
        return None

    try:
        candidate = extract_json_object(text)
        data = json.loads(candidate)
        if isinstance(data, dict) and "explanation" in data:
            return str(data["explanation"])[:500]
    except (ValueError, json.JSONDecodeError):
        pass

    # Fall back to the first sentence of plain text.
    first = text.strip().split(".")[0]
    return first[:500] if first else None


def _maybe_explain(
    query: str,
    schema: NLFilters,
    filter_description: str,
    entries: list[dict],
    explain: bool,
) -> tuple[str | None, bool]:
    """Attempt an AI explanation of the result list.

    Returns ``(explanation, used_ai)``. ``used_ai`` is ``False`` when
    AI was unavailable or the reply could not be parsed.
    """
    if not explain or not entries or not ai_manager.is_available():
        return None, False

    try:
        resp = ai_manager.complete(
            prompt=build_explain_prompt(
                query=query,
                filter_description=filter_description,
                entries=entries,
            ),
            system=NL_EXPLAIN_PROMPT,
            max_tokens=300,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning("AI explanation call raised: %s", e)
        return None, False

    if resp.text is None:
        return None, False

    explanation = _extract_explanation(resp.text)
    return explanation, True


# --- Endpoint --------------------------------------------------------


@router.post("", response_model=NLSearchResponse)
async def nl_search(
    body: NLSearchRequest,
    db: Session = Depends(get_db),
) -> NLSearchResponse:
    """
    Natural-language stock screening.

    Takes a free-form English query, converts it into a controlled
    scanner filter schema (AI-first, rule-based fallback), executes
    the filter against the scanner cache (warmly pre-scanned from the
    watchlist), and returns a ranked result list.

    Optionally asks the AI for a 1-2 sentence summary of the result
    list when ``explain=True`` (the default).
    """
    logger.info("NL search query=%r top_n=%r explain=%r",
                body.query, body.top_n, body.explain)

    # --- Step 1: derive the filter schema ---
    filters, extras, parser_used = parse_query(
        body.query,
        base={"scope": body.scope, "watchlist_id": body.watchlist_id},
    )

    # --- Step 2: caller overrides ---
    if body.ranking is not None:
        filters = filters.model_copy(update={"ranking": body.ranking})
    if body.top_n != 10:
        filters = filters.model_copy(update={"top_n": body.top_n})

    # --- Step 3: execute ---
    try:
        result = execute_query(
            filters,
            extras=extras,
            watchlist_id=body.watchlist_id,
            db=db,
        )
    except Exception as e:
        logger.exception("NL search executor raised unexpected error: %s", e)
        return NLSearchResponse(
            query=body.query,
            schema=filters,
            filter_description="(error during execution)",
            results=[],
            ranking=filters.ranking,
            ai_explanation_used=False,
            ai_translation_used=(parser_used == "ai"),
            reason=f"Internal error: {e}",
            parser_used=parser_used,
            timestamp=datetime.now(UTC).isoformat(),
        )

    # --- Step 4: AI explanation ---
    # Build a serialisable entry list for the explanation prompt.
    entry_dicts = [
        {
            "symbol": item.symbol,
            "score": item.total_score,
            "rank": item.rank,
            "metrics": {},
        }
        for item in result.top_n
    ]
    explanation, _ = _maybe_explain(
        query=body.query,
        schema=filters,
        filter_description=result.filter_description,
        entries=entry_dicts,
        explain=body.explain,
    )

    # --- Step 5: reason string ---
    reason: str | None = None
    if not result.top_n:
        if result.universe_size == 0:
            reason = "Watchlist is empty or scanner cache is empty."
        else:
            reason = f"No symbols matched the filter: {result.filter_description}."

    return NLSearchResponse(
        query=body.query,
        schema=filters,
        filter_description=result.filter_description,
        results=result.top_n,
        ranking=filters.ranking,
        explanation=explanation,
        ai_explanation_used=(explanation is not None),
        ai_translation_used=(parser_used == "ai"),
        reason=reason,
        parser_used=parser_used,
        timestamp=datetime.now(UTC).isoformat(),
    )


__all__ = ["router"]
