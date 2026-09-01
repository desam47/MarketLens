"""
Watchlist API endpoints
"""
from datetime import datetime
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.config.settings import settings as _settings
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.symbols.validator import validate_symbol

from ..dependencies import get_db

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])

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

class WatchlistSymbolBase(BaseModel):
    symbol: str
    is_enabled: bool = True

class WatchlistSymbolCreate(WatchlistSymbolBase):
    pass

class WatchlistSymbolResponse(WatchlistSymbolBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    watchlist_id: int
    added_at: datetime
    position: int
    notes: str | None = None


class WatchlistSymbolUpdate(BaseModel):
    """Body for PATCH /api/watchlists/{id}/symbols/{symbol}."""

    notes: str | None = None
    is_enabled: bool | None = None


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
    """
    repo = WatchlistRepository(db)
    watchlists = repo.get_watchlists(active_only=active_only)
    return watchlists

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
    """Delete a watchlist"""
    repo = WatchlistRepository(db)
    success = repo.delete_watchlist(watchlist_id)
    if not success:
        raise HTTPException(status_code=404, detail="Watchlist not found")

# Watchlist symbol endpoints
@router.get("/{watchlist_id}/symbols", response_model=list[WatchlistSymbolResponse])
def get_watchlist_symbols(watchlist_id: int, enabled_only: bool = True, db: Session = Depends(get_db)):
    """Get all symbols in a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    symbols = repo.get_watchlist_symbols(watchlist_id, enabled_only=enabled_only)
    return symbols

@router.post("/{watchlist_id}/symbols", response_model=WatchlistSymbolResponse, status_code=status.HTTP_201_CREATED)
def add_symbol_to_watchlist(watchlist_id: int, symbol: WatchlistSymbolCreate, db: Session = Depends(get_db)):
    """Add a symbol to a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    watchlist_symbol = repo.add_symbol_to_watchlist(
        watchlist_id=watchlist_id,
        symbol=symbol.symbol
    )
    return watchlist_symbol

@router.delete("/{watchlist_id}/symbols/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def remove_symbol_from_watchlist(watchlist_id: int, symbol: str, db: Session = Depends(get_db)):
    """Remove a symbol from a watchlist"""
    repo = WatchlistRepository(db)
    # First check if watchlist exists
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    success = repo.remove_symbol_from_watchlist(watchlist_id=watchlist_id, symbol=symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")

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
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    return updated

@router.put("/{watchlist_id}/symbols/reorder", response_model=list[WatchlistSymbolResponse])
def reorder_watchlist_symbols(watchlist_id: int, symbol_order: list[str], db: Session = Depends(get_db)):
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
        s for s in repo.get_all_watchlist_symbols(watchlist_id, include_disabled=True)
        if needle in s.symbol.upper()
    ]
    return matches


@router.post("/{watchlist_id}/import", response_model=ImportResponse)
def import_watchlist_symbols(
    watchlist_id: int, body: ImportRequest, db: Session = Depends(get_db)
):
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
        repo.add_symbol_to_watchlist(watchlist_id, symbol)
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
            headers={
                "Content-Disposition": f"attachment; filename=watchlist_{watchlist_id}.csv"
            },
        )

    # JSON path: reuse the response model to keep the shape consistent.
    return [WatchlistSymbolResponse.model_validate(s) for s in symbols]
