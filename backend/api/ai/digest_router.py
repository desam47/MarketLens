"""
Version 4, AI feature 2 — daily/session AI digest REST endpoints.

``GET /api/ai/digest/latest`` — the most recent digest for a session.
``GET /api/ai/digest/history`` — recent digests, newest first.
``POST /api/ai/digest/generate`` — manually generate + store a digest
right now (useful for testing, and a "Regenerate" button in the UI) —
does not wait for the scheduler's premarket/close time gate.

Digest generation is not user-triggered in normal operation — it's
schedule-only (``backend.ai.digest_service.DigestService``) — so the
read endpoints never enqueue anything; they just read the latest
``AIDigest`` row.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...repositories.ai_digest_repository import AIDigestRepository

router = APIRouter(prefix="/api/ai/digest", tags=["ai-digest"])


class DigestResponse(BaseModel):
    id: int
    session: str
    generated_at: str
    market_regime: str | None
    narrative: str | None
    payload: dict | None


def _to_response(row) -> DigestResponse:
    payload = None
    if row.payload:
        try:
            payload = json.loads(row.payload)
        except (ValueError, TypeError):
            payload = None
    return DigestResponse(
        id=row.id,
        session=row.session,
        generated_at=row.generated_at.isoformat(),
        market_regime=row.market_regime,
        narrative=row.narrative,
        payload=payload,
    )


@router.get("/latest", response_model=DigestResponse)
async def get_latest_digest(
    session: str = Query(default="close", pattern=r"^(premarket|close)$"),
):
    """Most recent digest for ``session`` ("premarket" or "close")."""
    repo = AIDigestRepository()
    try:
        row = await asyncio.to_thread(repo.get_latest, session)
    finally:
        repo.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No {session} digest generated yet")
    return _to_response(row)


@router.get("/history", response_model=list[DigestResponse])
async def get_digest_history(
    session: str | None = Query(default=None, pattern=r"^(premarket|close)$"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Recent digests, newest first. Omit ``session`` for both."""
    repo = AIDigestRepository()
    try:
        rows = await asyncio.to_thread(repo.get_history, session, limit)
    finally:
        repo.close()
    return [_to_response(r) for r in rows]


@router.post("/generate", response_model=DigestResponse)
async def generate_digest_now(
    session: str = Query(default="close", pattern=r"^(premarket|close)$"),
):
    """Generate and store a digest immediately, bypassing the
    scheduler's time gate. Same underlying generation code the
    scheduler uses — this is just an on-demand trigger for it."""
    from ...ai.digest import generate_and_store_digest

    result = await asyncio.to_thread(generate_and_store_digest, session)
    return DigestResponse(
        id=result["id"],
        session=result["session"],
        generated_at=result["generated_at"].isoformat(),
        market_regime=result["market_regime"],
        narrative=result["narrative"],
        payload=result["payload"],
    )


__all__ = ["router"]
