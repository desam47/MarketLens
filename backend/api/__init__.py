"""
API package for MarketLens
"""

from .market_data_routes import router as market_data_routes_router

__all__ = [
    "market_data_routes_router",
]