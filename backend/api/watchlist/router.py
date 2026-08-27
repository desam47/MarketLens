"""
Watchlist API endpoints
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.repositories.watchlist_repository import WatchlistRepository

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

# Watchlist endpoints
@router.get("/", response_model=list[WatchlistResponse])
def get_watchlists(active_only: bool = True, db: Session = Depends(get_db)):
    """Get all watchlists"""
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
        description=watchlist.description
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
