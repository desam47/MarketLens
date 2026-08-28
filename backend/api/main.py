"""
Main API application for MarketLens
"""
import logging
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.alerts.engine import alerts_engine
from backend.observability.metrics import start_memory_profiling

from ..config.settings import settings
from . import analysis, market_context, multitimeframe, regime, strategy, trend
from .ai.router import router as ai_router
from .alerts.router import router as alerts_router
from .aux_data.router import router as aux_data_router
from .backtest.router import router as backtest_router
from .market_data_routes import router as market_data_routes_router
from .nl_search.router import router as nl_search_router
from .rate_limit import InMemoryRateLimiter, RateLimitMiddleware
from .scanner.router import router as scanner_router
from .scanner.ws_router import router as scanner_ws_router
from .signals.router import router as signals_router
from .strategy_lab.router import router as strategy_lab_router
from .structured_logging import configure_logging
from .system.router import RequestCounterMiddleware
from .system.router import router as system_router
from .watchlist.router import router as watchlist_router

# Configure structured JSON logging
configure_logging(settings.debug)
logger = logging.getLogger(__name__)

# Create FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    """One-time startup: load enabled alerts and start memory profiling."""
    alerts_engine.startup()
    start_memory_profiling()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Market Intelligence and Quantitative Research Platform",
    lifespan=lifespan,
)

# CORS: use a configurable allowlist instead of "*" so the API is safe to
# expose to non-localhost clients. Default allows the local dev server
# (React on :3000) and the API itself. Override with the CORS_ALLOWED_ORIGINS
# env var (comma-separated).
allow_origins = settings.cors.allowed_origins
if "*" in allow_origins:
    # `*` plus `allow_credentials=True` is a CORS spec violation; browsers
    # will silently drop the Access-Control-Allow-Credentials header. Log
    # it loudly so a misconfigured prod deploy is obvious in the logs.
    logger.warning(
        "CORS allowed_origins includes '*'. Browser clients will not be able "
        "to send credentials. Set CORS_ALLOWED_ORIGINS to an explicit list "
        "(e.g. https://app.example.com) before deploying."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Rate limiting on write/mutation endpoints. Uses an in-process token bucket
# keyed by client IP — sufficient for a single-instance deployment. For
# multi-instance production, swap in a Redis-backed implementation behind
# the same interface.
_write_limiter = InMemoryRateLimiter(
    max_requests=settings.rate_limit.max_requests_per_window,
    window_seconds=settings.rate_limit.window_seconds,
)
app.add_middleware(RateLimitMiddleware, limiter=_write_limiter)
app.add_middleware(RequestCounterMiddleware)

# Include API routers
# Additional API routers will be included here as phases progress
app.include_router(regime.router)
app.include_router(trend.router)
app.include_router(multitimeframe.router)
app.include_router(strategy.router)
app.include_router(market_data_routes_router)
app.include_router(watchlist_router)
app.include_router(alerts_router)
app.include_router(backtest_router)
app.include_router(scanner_router)
app.include_router(scanner_ws_router)
app.include_router(analysis.router)
app.include_router(market_context.router)
app.include_router(signals_router)
app.include_router(ai_router)
app.include_router(nl_search_router)
app.include_router(aux_data_router)
app.include_router(strategy_lab_router)
app.include_router(system_router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }

@app.get("/api/system/status")
async def system_status():
    """System status endpoint"""
    from datetime import datetime
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "debug": settings.debug,
        "market_data_provider": settings.market_data.primary_provider,
        "ai_enabled": settings.ai.enabled,
        "timestamp": datetime.now(UTC).isoformat()
    }

# Additional API routers will be included here as phases progress
# from . import market_data, indicators, etc.
# app.include_router(market_data.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
