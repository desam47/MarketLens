"""
Watchlist API endpoints
"""

import logging
from datetime import datetime
from io import StringIO
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.config.settings import settings as _settings
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.services.purge_service import purge_symbol_from_database_safe
from backend.symbols.validator import validate_symbol
from backend.utils.timezone import format_edt_iso

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


def _start_symbol_tracking_and_backfill(symbol: str) -> str | None:
    """Register a freshly-added symbol for live tracking and enqueue its
    historical backfill. Returns the RQ job id, or None if no job was
    enqueued (backfill_on_add is off, Redis/RQ unavailable, or a backfill
    for this symbol is already in flight).

    Two independent, decoupled steps — neither blocks on provider I/O, so
    the caller (an ``add``/``import`` endpoint) returns immediately no
    matter how long the actual backfill takes:

      1. ``ingestion_service.register_symbol`` — synchronous, in-process,
         no I/O. The symbol starts getting quotes/1m bars from the next
         loop tick regardless of the backfill's outcome.
      2. ``backfill_queue.enqueue_backfill`` — a Redis/RQ enqueue call
         (also fast; the actual provider fetching happens later, in a
         worker process).

    Replaces the old ``_trigger_backfill_for_symbol``, which ran
    ``backfill_symbol_history`` directly on the calling thread — its
    sync-context fallback did ``future.result(timeout=600)``, blocking a
    FastAPI worker thread for up to 10 minutes per add. See
    ``backend/market_data/services/backfill_service.py``'s module
    docstring for the full rationale behind this split.
    """
    symbol = symbol.upper()
    from backend.market_data.services.ingestion_service import ingestion_service

    ingestion_service.register_symbol(symbol)

    if not _settings.market_data.backfill_on_add:
        return None
    from backend.market_data.services.backfill_queue import enqueue_backfill

    return enqueue_backfill(symbol)


# Pydantic models for request/response
class WatchlistBase(BaseModel):
    name: str
    description: str | None = None


class WatchlistCreate(WatchlistBase):
    pass


class WatchlistUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class WatchlistResponse(WatchlistBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    symbol_count: int = 0

    @field_serializer("created_at", "updated_at")
    def _serialize_tz(self, value: datetime | None) -> str | None:
        # Naive datetimes are NY local time (project convention since
        # 2026-09-02). format_edt_iso attaches the explicit EDT/EST
        # offset so the browser parses the value as NY local time.
        return format_edt_iso(value)


class WatchlistSymbolBase(BaseModel):
    symbol: str
    is_enabled: bool = True
    entity_type: Literal["stock", "etf"] | None = None


class WatchlistSymbolCreate(WatchlistSymbolBase):
    pass


class WatchlistSymbolResponse(WatchlistSymbolBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    watchlist_id: int
    added_at: datetime
    position: int
    notes: str | None = None

    @field_serializer("added_at")
    def _serialize_tz(self, value: datetime | None) -> str | None:
        return format_edt_iso(value)


class WatchlistSymbolUpdate(BaseModel):
    """Body for PATCH /api/watchlists/{id}/symbols/{symbol}."""

    notes: str | None = None
    is_enabled: bool | None = None
    entity_type: Literal["stock", "etf"] | None = None


class ImportRequest(BaseModel):
    """Body for POST /api/watchlists/{id}/import."""

    symbols: list[str]


class ImportResponse(BaseModel):
    """Result of an import: symbols split into imported / skipped / errors.

    - ``imported``: tickers successfully added to the watchlist.
    - ``skipped``: tickers that were already present (no change made).
    - ``errors``: tickers that failed validation, with the reason.
    """

    imported: list[str]
    skipped: list[str]
    errors: list[str]


# Watchlist endpoints
@router.get("/", response_model=list[WatchlistResponse])
def get_watchlists(active_only: bool = False, db: Session = Depends(get_db)):
    """Get all watchlists.

    Defaults to ``active_only=False`` so the user can see and re-enable
    watchlists they previously disabled. Set ``?active_only=true`` to hide
    disabled ones (used by the market-data ingestion service).

    ``symbol_count`` is populated via a single subquery — no N+1 round-trips
    to count symbols per watchlist.
    """
    from backend.models.watchlist import WatchlistSymbol

    repo = WatchlistRepository(db)
    watchlists = repo.get_watchlists(active_only=active_only)

    # Batch-count enabled symbols for all watchlists in one query.
    watchlist_ids = [wl.id for wl in watchlists]
    if watchlist_ids:
        counts = (
            db.query(WatchlistSymbol.watchlist_id, func.count(WatchlistSymbol.id))
            .filter(
                WatchlistSymbol.watchlist_id.in_(watchlist_ids),
                WatchlistSymbol.is_enabled.is_(True),
            )
            .group_by(WatchlistSymbol.watchlist_id)
            .all()
        )
        count_map = {wl_id: cnt for wl_id, cnt in counts}
    else:
        count_map = {}

    # Build response dicts manually so we can inject symbol_count without
    # modifying the ORM model.
    result = []
    for wl in watchlists:
        data = WatchlistResponse.model_validate(wl)
        data.symbol_count = count_map.get(wl.id, 0)
        result.append(data)
    return result


@router.post("/", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
def create_watchlist(watchlist: WatchlistCreate, db: Session = Depends(get_db)):
    """Create a new watchlist"""
    repo = WatchlistRepository(db)
    return repo.create_watchlist(name=watchlist.name, description=watchlist.description)


@router.get("/{watchlist_id}", response_model=WatchlistResponse)
def get_watchlist(watchlist_id: int, db: Session = Depends(get_db)):
    """Get a specific watchlist"""
    repo = WatchlistRepository(db)
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return watchlist


@router.put("/{watchlist_id}", response_model=WatchlistResponse)
def update_watchlist(watchlist_id: int, watchlist: WatchlistUpdate, db: Session = Depends(get_db)):
    """Update a watchlist"""
    repo = WatchlistRepository(db)
    updated_watchlist = repo.update_watchlist(
        watchlist_id=watchlist_id,
        name=watchlist.name,
        description=watchlist.description,
        is_active=watchlist.is_active,
    )
    if updated_watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return updated_watchlist


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(watchlist_id: int, db: Session = Depends(get_db)):
    """Delete a watchlist and purge per-symbol data for any symbol that
    no longer appears in any remaining watchlist.

    Without this cascade, deleting a watchlist leaves orphaned bars,
    signals, quotes, and market_status rows in the DB for symbols that
    were only present in that watchlist. Mirrors the cleanup done by
    ``remove_symbol_from_watchlist``.
    """
    repo = WatchlistRepository(db)
    # Capture the symbols that are about to be removed with the watchlist
    # so we can decide which need per-symbol purge afterwards.
    symbols_in_watchlist = [
        s.symbol.upper() for s in repo.get_watchlist_symbols(watchlist_id, enabled_only=False)
    ]
    success = repo.delete_watchlist(watchlist_id)
    if not success:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    # Phase 3.8.6+: notify ingestion service BEFORE purge so it stops fetching
    # deleted symbols immediately — prevents the race where purge deletes bars
    # but the next ingestion tick re-ingests them with the stale symbol list.
    try:
        from backend.market_data.services.ingestion_service import ingestion_service

        ingestion_service.refresh_symbols_from_watchlist()
    except Exception as e:
        logger.debug(f"ingestion refresh before watchlist delete failed: {e}")
    # Cascade: for any symbol that no longer appears in any watchlist,
    # purge ALL per-symbol data (bars, signals, quotes, market_status,
    # alerts, alert_triggers, ai_analysis_jobs, backtest_runs, backtest_trades,
    # drawing_tools). Single transaction via purge_service.
    for symbol_upper in symbols_in_watchlist:
        if repo.symbol_exists_in_any_watchlist(symbol_upper):
            continue  # still watched elsewhere — leave its data alone
        result = purge_symbol_from_database_safe(symbol_upper)
        if result["total"] > 0:
            logger.info(
                f"purged {result['total']} rows for {symbol_upper} "
                f"(watchlist {watchlist_id} deleted, no remaining watchlist): "
                f"bars={result['bars']}, signals={result['signals']}, "
                f"quotes={result['quotes']}, alerts={result['alerts']}, "
                f"ai_jobs={result['ai_analysis_jobs']}, "
                f"backfill_jobs={result['backfill_jobs']}, "
                f"backtest_runs={result['backtest_runs']}, "
                f"drawings={result['drawing_tools']}"
            )


# Watchlist symbol endpoints
@router.get("/{watchlist_id}/symbols", response_model=list[WatchlistSymbolResponse])
def get_watchlist_symbols(
    watchlist_id: int, enabled_only: bool = True, db: Session = Depends(get_db)
):
    """Get all symbols in a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    symbols = repo.get_watchlist_symbols(watchlist_id, enabled_only=enabled_only)
    return symbols


@router.post(
    "/{watchlist_id}/symbols",
    response_model=WatchlistSymbolResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_symbol_to_watchlist(
    watchlist_id: int, symbol: WatchlistSymbolCreate, db: Session = Depends(get_db)
):
    """Add a symbol to a watchlist.

    If the symbol is newly added OR re-enabled from a disabled row, it
    starts live-tracking immediately and a background backfill of bar
    history is enqueued — see ``_start_symbol_tracking_and_backfill``. A
    re-enable needs the same wake-up as a new row: disabling a symbol
    doesn't currently tear down its live tracking/stream subscription, but
    treating "new" and "re-enabled" identically here is what keeps this
    endpoint correct if that ever changes, and it's also what makes a
    symbol added to a second watchlist (arriving here as a no-op "already
    exists, enabled" case) distinct from one that actually needs
    (re-)starting. Neither step does provider I/O on this request thread,
    so this endpoint returns as soon as the DB row is written.

    Enforces the same two checks ``import_watchlist_symbols`` already does
    for a bulk import: ticker validation via the market data provider and
    the ``max_symbols_per_watchlist`` cap. This is the endpoint the
    frontend's everyday "add a symbol" UI action actually calls — without
    these checks here, a typo'd/nonexistent ticker was silently accepted
    (and started live tracking + a backfill job that could never resolve
    against any provider) and a watchlist could grow past the configured
    cap through this path even though the bulk-import path enforced it.
    Only applied when the symbol isn't already present-and-enabled, so a
    duplicate add to an existing/enabled symbol stays a cheap, provider-
    I/O-free no-op — matching the import endpoint's own skip logic.
    """
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    sym = symbol.symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty")

    existing = repo.get_watchlist_symbol(watchlist_id, sym)
    if not (existing and existing.is_enabled):
        max_symbols = _settings.watchlist.max_symbols_per_watchlist
        current_count = repo.get_watchlist_symbol_count(watchlist_id, enabled_only=True)
        if current_count >= max_symbols:
            raise HTTPException(
                status_code=400,
                detail=f"Watchlist full (max {max_symbols} symbols)",
            )
        result = validate_symbol(sym)
        if not result.valid:
            raise HTTPException(
                status_code=400,
                detail=f"{sym}: {result.error or 'invalid symbol'}",
            )

    watchlist_symbol, is_new_row, did_reenable = repo.add_symbol_to_watchlist(
        watchlist_id=watchlist_id,
        symbol=sym,
        entity_type=symbol.entity_type or "stock",
    )
    if is_new_row or did_reenable:
        _start_symbol_tracking_and_backfill(sym)
    return watchlist_symbol


@router.delete("/{watchlist_id}/symbols/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def remove_symbol_from_watchlist(watchlist_id: int, symbol: str, db: Session = Depends(get_db)):
    """Remove a symbol from a watchlist.

    Cancels any pending/in-flight backfill job for the symbol and, if the
    symbol is no longer present in any other watchlist, purges its bars
    from the database.
    """
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    success = repo.remove_symbol_from_watchlist(watchlist_id=watchlist_id, symbol=symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    symbol_upper = symbol.upper()
    try:
        from backend.market_data.services.backfill_queue import cancel_backfill

        if cancel_backfill(symbol_upper):
            logger.debug(f"cancelled in-flight backfill job for {symbol_upper}")
    except Exception as e:
        logger.debug(f"cancel_backfill failed for {symbol_upper}: {e}")
    # Drop the dedup cache so a re-added symbol isn't suppressed as a
    # "duplicate" of its now-deleted historical signals.
    try:
        from backend.services.signal_recorder import signal_recorder

        signal_recorder._last_recorded = {
            (s, tf, ts): ts for (s, tf, ts) in signal_recorder._last_recorded if s != symbol_upper
        }
    except Exception as e:
        logger.debug(f"signal_recorder cache cleanup skipped: {e}")
    # Phase 3.8.6+: notify ingestion service BEFORE purge so it stops fetching
    # deleted symbols immediately — prevents the race where purge deletes bars
    # but the next ingestion tick re-ingests them with the stale symbol list.
    try:
        from backend.market_data.services.ingestion_service import ingestion_service

        ingestion_service.refresh_symbols_from_watchlist()
    except Exception as e:
        logger.debug(f"ingestion refresh before purge failed: {e}")
    # Phase 3.3.15 / Phase 3.x: purge ALL per-symbol data if the symbol
    # is no longer in any watchlist. Covers bars, signals, quotes,
    # market_status, alerts, alert_triggers, ai_analysis_jobs,
    # backtest_runs/trades, and drawing_tools.
    if not repo.symbol_exists_in_any_watchlist(symbol_upper):
        result = purge_symbol_from_database_safe(symbol_upper)
        if result["total"] > 0:
            logger.info(
                f"purged {result['total']} rows for {symbol_upper} "
                f"(no longer in any watchlist): "
                f"bars={result['bars']}, signals={result['signals']}, "
                f"quotes={result['quotes']}, alerts={result['alerts']}, "
                f"ai_jobs={result['ai_analysis_jobs']}, "
                f"backfill_jobs={result['backfill_jobs']}, "
                f"backtest_runs={result['backtest_runs']}, "
                f"drawings={result['drawing_tools']}"
            )


class BackfillStatusResponse(BaseModel):
    """Latest backfill job status for a symbol — see
    ``backend.market_data.services.backfill_queue.get_backfill_job_status``.
    """

    symbol: str
    job_id: str
    status: str
    tier1_written: int
    tier2_written: int
    tier3_written: int
    gaps_found: int
    gaps_filled: int
    result: dict | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@router.get("/symbols/{symbol}/backfill-status", response_model=BackfillStatusResponse)
def get_symbol_backfill_status(symbol: str):
    """Latest backfill job status for ``symbol`` — lets the frontend show
    backfill progress after an add instead of nothing. 404 if the symbol
    has never had a backfill job (e.g. added before this endpoint existed,
    or ``backfill_on_add`` is off).
    """
    from backend.market_data.services.backfill_queue import get_backfill_job_status

    status_dict = get_backfill_job_status(symbol.upper())
    if status_dict is None:
        raise HTTPException(status_code=404, detail="No backfill job found for this symbol")
    return status_dict


@router.put("/{watchlist_id}/symbols/{symbol}/enable", response_model=WatchlistSymbolResponse)
def enable_symbol_in_watchlist(watchlist_id: int, symbol: str, db: Session = Depends(get_db)):
    """Enable a symbol in a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    success = repo.enable_symbol_in_watchlist(watchlist_id=watchlist_id, symbol=symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    # Return the updated symbol
    watchlist_symbol = repo.get_watchlist_symbol(watchlist_id, symbol)
    return watchlist_symbol


@router.put("/{watchlist_id}/symbols/{symbol}/disable", response_model=WatchlistSymbolResponse)
def disable_symbol_in_watchlist(watchlist_id: int, symbol: str, db: Session = Depends(get_db)):
    """Disable a symbol in a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    success = repo.disable_symbol_in_watchlist(watchlist_id=watchlist_id, symbol=symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    # Return the updated symbol
    watchlist_symbol = repo.get_watchlist_symbol(watchlist_id, symbol)
    return watchlist_symbol


@router.patch("/{watchlist_id}/symbols/{symbol}", response_model=WatchlistSymbolResponse)
def update_watchlist_symbol(
    watchlist_id: int,
    symbol: str,
    payload: WatchlistSymbolUpdate,
    db: Session = Depends(get_db),
):
    """Update symbol metadata (notes, enabled state).

    Lets the UI show an Edit dialog without round-tripping through the
    separate enable/disable endpoints.
    """
    repo = WatchlistRepository(db)
    if repo.get_watchlist(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    updated = repo.update_symbol_in_watchlist(
        watchlist_id=watchlist_id,
        symbol=symbol,
        notes=payload.notes,
        is_enabled=payload.is_enabled,
        entity_type=payload.entity_type,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    return updated


@router.put("/{watchlist_id}/symbols/reorder", response_model=list[WatchlistSymbolResponse])
def reorder_watchlist_symbols(
    watchlist_id: int, symbol_order: list[str], db: Session = Depends(get_db)
):
    """Reorder symbols in a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    success = repo.reorder_watchlist_symbols(watchlist_id=watchlist_id, symbol_order=symbol_order)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to reorder symbols")
    # Return the updated symbols
    symbols = repo.get_watchlist_symbols(watchlist_id)
    return symbols


# ----------------------------------------------------------------------
# Phase 3 closure endpoints — search, import, export.
# ----------------------------------------------------------------------


@router.get("/{watchlist_id}/symbols/search", response_model=list[WatchlistSymbolResponse])
def search_watchlist_symbols(
    watchlist_id: int,
    q: str = Query(..., min_length=1, description="Substring to match against ticker symbols"),
    db: Session = Depends(get_db),
):
    """Search symbols in a watchlist by substring (case-insensitive).

    Includes disabled symbols so the user can find and re-enable them.
    """
    repo = WatchlistRepository(db)
    if repo.get_watchlist(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    needle = q.upper()
    matches = [
        s
        for s in repo.get_all_watchlist_symbols(watchlist_id, include_disabled=True)
        if needle in s.symbol.upper()
    ]
    return matches


@router.post("/{watchlist_id}/import", response_model=ImportResponse)
def import_watchlist_symbols(watchlist_id: int, body: ImportRequest, db: Session = Depends(get_db)):
    """Bulk import symbols into a watchlist.

    Each input symbol is uppercased and trimmed. Symbols that are already
    enabled in the watchlist go to ``skipped``. Symbols that fail
    validation (provider returns no quote, or max-symbols cap is hit) go
    to ``errors``. Successfully imported symbols are returned in
    ``imported``.

    The max-symbols cap is ``WatchlistSettings.max_symbols_per_watchlist``
    (default 50), checked against the current enabled count.
    """
    repo = WatchlistRepository(db)
    if repo.get_watchlist(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    max_symbols = _settings.watchlist.max_symbols_per_watchlist
    current_count = repo.get_watchlist_symbol_count(watchlist_id, enabled_only=True)
    slots_left = max(0, max_symbols - current_count)

    imported: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for raw in body.symbols:
        symbol = raw.upper().strip()
        if not symbol:
            continue
        # Skip if already present (treat disabled rows as not present so
        # an import can "re-add" a previously disabled symbol — the repo's
        # add method re-enables existing rows for us).
        existing = repo.get_watchlist_symbol(watchlist_id, symbol)
        if existing and existing.is_enabled:
            skipped.append(symbol)
            continue
        # Enforce max-symbols.
        if slots_left <= 0:
            errors.append(f"{symbol}: watchlist full (max {max_symbols})")
            continue
        # Validate the ticker via the market data provider.
        result = validate_symbol(symbol)
        if not result.valid:
            errors.append(f"{symbol}: {result.error or 'invalid'}")
            continue
        _, is_new_row, did_reenable = repo.add_symbol_to_watchlist(watchlist_id, symbol)
        if is_new_row or did_reenable:
            _start_symbol_tracking_and_backfill(symbol)
        imported.append(symbol)
        slots_left -= 1

    return ImportResponse(imported=imported, skipped=skipped, errors=errors)


@router.get("/{watchlist_id}/export")
def export_watchlist(
    watchlist_id: int,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
):
    """Export all symbols in a watchlist as JSON or CSV.

    JSON returns the full ``WatchlistSymbolResponse`` list. CSV returns
    ``symbol,is_enabled,position`` rows with a
    ``Content-Disposition: attachment`` header so browsers download it.
    """
    repo = WatchlistRepository(db)
    if repo.get_watchlist(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    symbols = repo.get_all_watchlist_symbols(watchlist_id, include_disabled=True)

    if format == "csv":
        buf = StringIO()
        buf.write("symbol,is_enabled,position\n")
        for s in symbols:
            buf.write(f"{s.symbol},{int(s.is_enabled)},{s.position}\n")
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=watchlist_{watchlist_id}.csv"},
        )

    # JSON path: reuse the response model to keep the shape consistent.
    return [WatchlistSymbolResponse.model_validate(s) for s in symbols]


class BackfillResponse(BaseModel):
    """Result of a manual backfill trigger."""

    symbols: list[str]
    status: str


@router.post("/{watchlist_id}/backfill", response_model=BackfillResponse)
def trigger_watchlist_backfill(watchlist_id: int, db: Session = Depends(get_db)):
    """Manually trigger a full backfill for all symbols in a watchlist.

    Backfill is already wired into the per-symbol add path
    (``add_symbol_to_watchlist`` and ``import_watchlist_symbols``), but
    symbols added before that existed, or whose earlier backfill failed,
    never got one. This endpoint lets the user retroactively (re-)fill
    historical bars for every symbol in the watchlist — each gets its own
    queued RQ job (single-flight still applies: a symbol already mid-backfill
    is skipped, not duplicated).
    """
    repo = WatchlistRepository(db)
    if repo.get_watchlist(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    symbols = repo.get_watchlist_symbols(watchlist_id, enabled_only=False)
    triggered = []
    for ws in symbols:
        _start_symbol_tracking_and_backfill(ws.symbol.upper())
        triggered.append(ws.symbol.upper())
    return BackfillResponse(symbols=triggered, status="backfill triggered")
