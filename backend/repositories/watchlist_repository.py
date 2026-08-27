"""
Watchlist repository for data access operations
"""

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from backend.models.watchlist import Watchlist, WatchlistSymbol


class WatchlistRepository:
    """Repository for watchlist operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    # Watchlist operations
    def get_watchlists(self, active_only: bool = True) -> list[Watchlist]:
        """Get all watchlists"""
        query = self.db.query(Watchlist)
        if active_only:
            query = query.filter(Watchlist.is_active == True)
        return query.order_by(Watchlist.created_at.desc()).all()
    
    def get_watchlist(self, watchlist_id: int) -> Watchlist | None:
        """Get a specific watchlist by ID"""
        return self.db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    
    def get_watchlist_by_name(self, name: str) -> Watchlist | None:
        """Get a watchlist by name"""
        return self.db.query(Watchlist).filter(
            and_(Watchlist.name == name, Watchlist.is_active == True)
        ).first()
    
    def create_watchlist(self, name: str, description: str | None = None) -> Watchlist:
        """Create a new watchlist"""
        watchlist = Watchlist(name=name, description=description)
        self.db.add(watchlist)
        self.db.commit()
        self.db.refresh(watchlist)
        return watchlist
    
    def update_watchlist(self, watchlist_id: int, name: str | None = None, 
                        description: str | None = None) -> Watchlist | None:
        """Update an existing watchlist"""
        watchlist = self.get_watchlist(watchlist_id)
        if watchlist:
            if name is not None:
                watchlist.name = name
            if description is not None:
                watchlist.description = description
            self.db.commit()
            self.db.refresh(watchlist)
        return watchlist
    
    def delete_watchlist(self, watchlist_id: int) -> bool:
        """Delete a watchlist"""
        watchlist = self.get_watchlist(watchlist_id)
        if watchlist:
            watchlist.is_active = False  # Soft delete
            self.db.commit()
            return True
        return False
    
    # Watchlist symbol operations
    def get_watchlist_symbols(self, watchlist_id: int, enabled_only: bool = True) -> list[WatchlistSymbol]:
        """Get all symbols in a watchlist"""
        query = self.db.query(WatchlistSymbol).filter(WatchlistSymbol.watchlist_id == watchlist_id)
        if enabled_only:
            query = query.filter(WatchlistSymbol.is_enabled == True)
        return query.order_by(WatchlistSymbol.position).all()
    
    def get_watchlist_symbol(self, watchlist_id: int, symbol: str) -> WatchlistSymbol | None:
        """Get a specific symbol in a watchlist"""
        return self.db.query(WatchlistSymbol).filter(
            and_(
                WatchlistSymbol.watchlist_id == watchlist_id,
                WatchlistSymbol.symbol == symbol.upper()
            )
        ).first()
    
    def add_symbol_to_watchlist(self, watchlist_id: int, symbol: str, 
                               position: int | None = None) -> WatchlistSymbol:
        """Add a symbol to a watchlist"""
        symbol = symbol.upper()
        
        # Check if symbol already exists in watchlist
        existing = self.get_watchlist_symbol(watchlist_id, symbol)
        if existing:
            # If exists but disabled, re-enable it
            if not existing.is_enabled:
                existing.is_enabled = True
                self.db.commit()
                self.db.refresh(existing)
            return existing
        
        # Determine position if not provided
        if position is None:
            # Get the highest position and add 1
            max_position = self.db.query(
                func.max(WatchlistSymbol.position)
            ).filter(WatchlistSymbol.watchlist_id == watchlist_id).scalar() or -1
            position = max_position + 1
        
        watchlist_symbol = WatchlistSymbol(
            watchlist_id=watchlist_id,
            symbol=symbol,
            position=position
        )
        self.db.add(watchlist_symbol)
        self.db.commit()
        self.db.refresh(watchlist_symbol)
        return watchlist_symbol
    
    def remove_symbol_from_watchlist(self, watchlist_id: int, symbol: str) -> bool:
        """Remove a symbol from a watchlist"""
        watchlist_symbol = self.get_watchlist_symbol(watchlist_id, symbol)
        if watchlist_symbol:
            watchlist_symbol.is_enabled = False  # Soft delete
            self.db.commit()
            return True
        return False
    
    def enable_symbol_in_watchlist(self, watchlist_id: int, symbol: str) -> bool:
        """Enable a symbol in a watchlist"""
        watchlist_symbol = self.get_watchlist_symbol(watchlist_id, symbol)
        if watchlist_symbol:
            watchlist_symbol.is_enabled = True
            self.db.commit()
            return True
        return False
    
    def disable_symbol_in_watchlist(self, watchlist_id: int, symbol: str) -> bool:
        """Disable a symbol in a watchlist"""
        watchlist_symbol = self.get_watchlist_symbol(watchlist_id, symbol)
        if watchlist_symbol:
            watchlist_symbol.is_enabled = False
            self.db.commit()
            return True
        return False
    
    def reorder_watchlist_symbols(self, watchlist_id: int, symbol_order: list[str]) -> bool:
        """Reorder symbols in a watchlist based on provided order"""
        try:
            for index, symbol in enumerate(symbol_order):
                watchlist_symbol = self.get_watchlist_symbol(watchlist_id, symbol)
                if watchlist_symbol:
                    watchlist_symbol.position = index
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            return False
    
    def get_next_position(self, watchlist_id: int) -> int:
        """Get the next available position for a watchlist"""
        max_position = self.db.query(
            func.max(WatchlistSymbol.position)
        ).filter(WatchlistSymbol.watchlist_id == watchlist_id).scalar() or -1
        return max_position + 1
