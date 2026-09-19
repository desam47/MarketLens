"""
Main API application for MarketLens
"""
import asyncio
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.ai.digest_service import digest_service
from backend.alerts.engine import alerts_engine
from backend.config.settings import _PROJECT_ROOT, settings
from backend.observability.correlation_id import CorrelationIdMiddleware
from backend.observability.logging_enhanced import get_correlation_id
from backend.observability.tracing import initialize_tracing, shutdown_tracing
from backend.api import analysis, market_context, multitimeframe, regime, strategy, trend
from backend.api.ai.router import router as ai_router
from backend.api.alerts.router import router as alerts_router
from backend.api.aux_data.router import router as aux_data_router
from backend.api.backtest.router import router as backtest_router
from backend.api.finnhub.router import router as finnhub_router
from backend.api.cache import CacheMiddleware
from backend.api.market_data_routes import router as market_data_routes_router
from backend.api.nl_search.router import router as nl_search_router
from backend.api.rate_limit import RedisRateLimiter, RateLimitMiddleware
from backend.api.realtime import router as realtime_router
from backend.version import get_version
from backend.api.security_headers import SecurityHeadersMiddleware
from backend.api.scanner.router import router as scanner_router
from backend.api.scanner.ws_router import router as scanner_ws_router
from backend.api.tape.router import router as tape_router
from backend.api.signals.router import router as signals_router
from backend.api.strategy_lab.router import router as strategy_lab_router
from backend.api.structured_logging import configure_logging
from backend.api.system.router import RequestCounterMiddleware
from backend.api.system.router import router as system_router
from backend.api.watchlist.router import router as watchlist_router
from backend.api.custom_indicators.router import router as custom_indicators_router
from backend.api.drawing_tools.router import router as drawing_tools_router
from backend.api.ai_templates.router import router as ai_templates_router
from backend.api.ai.chat_router import router as ai_chat_router
from backend.api.ai.digest_router import router as ai_digest_router
from backend.api.ai.jobs import router as ai_jobs_router

# Configure structured JSON logging
configure_logging(
    debug=settings.debug,
    log_level=settings.log_level,
)
logger = logging.getLogger(__name__)

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)

# Create FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    """One-time startup: load enabled alerts, start memory profiling, initialize
    tracing, and pre-warm trend engines so ingestion can dispatch bars immediately."""

    # Run Alembic migrations at startup so schema is always current.
    # Alembic reads the DB URL directly from ``backend.config.settings``,
    # which hard-codes the path to ``<project_root>/marketlens.db``.
    #
    # Run it as ``sys.executable -m alembic`` against an explicit config and
    # cwd rather than a bare ``alembic`` on PATH: a bare call silently
    # no-ops (just a warning below) whenever the server is started from
    # another directory or an interpreter whose shims aren't on PATH,
    # leaving the schema behind the code.
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "-c", str(_PROJECT_ROOT / "alembic.ini"),
             "upgrade", "head"],
            capture_output=True, text=True, cwd=_PROJECT_ROOT, timeout=300,
        )
        if completed.returncode == 0:
            for line in (completed.stdout + completed.stderr).strip().splitlines():
                if line.strip() and "Running upgrade" in line:
                    logger.info("[alembic] %s", line.strip())
        else:
            logger.warning("[alembic] upgrade failed: %s", completed.stderr.strip())
    except Exception:
        logger.warning("Alembic migration failed; continuing", exc_info=True)

    # Clear the bar/quote CACHE on startup to ensure fresh data.
    #
    # This used to be an unconditional r.flushall() — wiping the ENTIRE
    # Redis logical DB, not just the cache. That was survivable while
    # Redis only held ephemeral cache + rate-limit-window data (worst
    # case: a slightly cold cache, a reset rate-limit window). It stopped
    # being survivable once Redis also started holding a durable job
    # queue (backend/market_data/services/backfill_queue.py /
    # backend/ai/background.py): every backend restart — the single most
    # common action in this project's own dev workflow — would silently
    # discard every queued-but-not-yet-processed RQ job (backfill AND AI
    # analysis), leaving their BackfillJob/AIAnalysisJob DB rows stuck at
    # status="queued" forever with nothing left in Redis to explain why
    # (found live, 2026-09-08: repeated FastAPI TestClient startups during
    # this session's own test runs — TestClient triggers this same
    # lifespan hook — were observed silently vanishing several just-queued
    # real BackfillJob rows' underlying RQ jobs within seconds). Scoped to
    # the cache layer's own key prefix (see
    # backend/market_data/services/cache.py's _make_bar_key/_make_quote_key/
    # _make_latest_bar_key) instead — RQ's "rq:*" keys, the rate limiter's
    # "rate_limit:*" keys, and the backfill enqueue lock's
    # "backfill:enqueue-lock:*" keys (self-expiring in 10s regardless) are
    # untouched.
    try:
        import redis
        from backend.config.settings import settings as _s
        redis_url = _s.redis.url
        r = redis.from_url(redis_url)
        cleared = 0
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="marketlens:*", count=500)
            if keys:
                r.delete(*keys)
                cleared += len(keys)
            if cursor == 0:
                break
        logger.info(f"Redis bar/quote cache cleared on startup ({cleared} keys)")
    except Exception:
        logger.warning("Redis cache clear failed; continuing", exc_info=True)

    alerts_engine.startup()
    digest_service.start()
    if settings.ai_nudges.enabled:
        from backend.ai.nudges import nudge_service
        nudge_service.start()
    # start_memory_profiling() removed — tracemalloc is expensive and
    # grows with uptime.  Heap profiling is opt-in via the toggle
    # endpoint POST /api/system/memory_profile {"enabled": true}.
    initialize_tracing()

    # Start the market data ingestion service FIRST so it loads its
    # symbol list from the active watchlist before the trend warmup
    # iterates over those symbols.
    try:
        from backend.market_data.services.ingestion_service import ingestion_service
        if not ingestion_service.is_running:
            ingestion_service.start()
            logger.info(
                f"Market data ingestion started for {len(ingestion_service.symbols)} "
                f"symbols: {ingestion_service.symbols}"
            )
        else:
            logger.info("Market data ingestion already running")
    except Exception as e:
        logger.warning(f"Ingestion service startup failed: {e}")

    # Webull MQTT streaming (2026-09-10) — push L1 snapshots + trade ticks.
    # Off unless WEBULL_STREAMING_ENABLED=true. When live, the polled quote
    # loop backs off to a stale-fallback for covered symbols.
    try:
        if settings.webull.streaming_enabled:
            from backend.market_data.services.ingestion_service import ingestion_service
            from backend.market_data.streaming.bridge import (
                on_stream_snapshot,
                on_stream_trade,
            )
            from backend.market_data.streaming.webull_stream import get_webull_stream_client

            _stream = get_webull_stream_client()
            if _stream is not None:
                _stream.on_snapshot = on_stream_snapshot
                _stream.on_trade = on_stream_trade
                _stream.subscribe(ingestion_service.symbols)
                _stream.start()
                app.state.webull_stream = _stream
                logger.info(
                    "Webull stream started for %d symbols", len(ingestion_service.symbols)
                )
    except Exception as e:
        logger.warning(f"Webull stream startup failed: {e}")

    # Pre-register trend engines for all ingested symbols so bars dispatched
    # by the ingestion service have listeners from the first tick.
    try:
        from backend.api.trend.registry import warmup_engines
        warmed = warmup_engines()
        for sym, count in warmed.items():
            logger.info(f"Trend engine warmup: {sym} ({count} bars)")
    except Exception as e:
        logger.warning(f"Trend engine warmup failed: {e}")

    # Tape (Time & Sales) analytics — pre-warm per-symbol engines + start
    # the 1-second-bar persistence flusher. Off unless TAPE_ENABLED=true.
    try:
        if settings.tape.enabled:
            from backend.api.tape.registry import warmup_tape_engines
            warmed_tape = warmup_tape_engines()
            logger.info("Tape engine warmup: %d symbols", len(warmed_tape))
    except Exception as e:
        logger.warning(f"Tape engine warmup failed: {e}")

    # AI trade-plan outcome tracking — background grading thread. Off
    # unless AI_TRADE_PLAN_TRACKING_ENABLED=true; capture itself
    # (analyze_symbol's choke point) is gated independently and doesn't
    # need anything started here.
    try:
        if settings.ai_trade_plan_tracking.enabled:
            from backend.ai.trade_plan_tracker import start_trade_plan_tracker
            start_trade_plan_tracker()
            logger.info("Trade plan grading thread started")
    except Exception as e:
        logger.warning(f"Trade plan tracker startup failed: {e}")

    # Phase 3.6.2: pre-warm the market-context engine so the first
    # /api/market-context/current request hits a fully-seeded aggregate
    # (SPY/QQQ/IWM/VIX sub-regimes warm from 1d history + live-tick
    # registration), not a cold singleton that has to seed on the request
    # path. Mirrors the trend warmup pattern above.
    try:
        from backend.api.market_context.router import get_engine
        mc_engine = get_engine()
        seeded = sum(
            1 for sym in mc_engine._cfg.indices
            if mc_engine.sub_engines[sym].get_current_regime() is not None
        )
        logger.info(
            f"Market-context warmup: {seeded}/{len(mc_engine._cfg.indices)} "
            f"sub-engines warmed"
        )
    except Exception as e:
        logger.warning(f"Market-context warmup failed: {e}")

    # Signal hygiene: fill gaps and enforce retention caps on every restart so
    # any ticker added before these fixes get patched automatically.
    try:
        from backend.api.main_helpers import run_signal_hygiene
        gaps = run_signal_hygiene()
        for sym, tfs in gaps.items():
            for tf, filled in tfs.items():
                if filled:
                    logger.info(f"Signal hygiene: {sym}/{tf} — filled {filled} missing signals")
    except Exception as e:
        logger.warning(f"Signal hygiene check failed: {e}")

    # Data-quality audit: duplicate calendar-day bars (1d/1wk). See
    # /api/system/data-quality and find_duplicate_calendar_bars' docstring
    # for the 2026-09-09 incident this guards against — logged here too so
    # a regression is visible in the startup log, not just on-demand.
    try:
        from backend.database import SessionLocal as _SessionLocal
        from backend.repositories.bar_repository import find_duplicate_calendar_bars
        _db = _SessionLocal()
        try:
            dupes = []
            for _tf in ("1d", "1wk"):
                dupes.extend(find_duplicate_calendar_bars(_db, _tf))
        finally:
            _db.close()
        if dupes:
            logger.warning(
                f"Data-quality audit: {len(dupes)} duplicated calendar-day bars "
                f"found (see /api/system/data-quality for detail)"
            )
        else:
            logger.info("Data-quality audit: no duplicate calendar-day bars found")
    except Exception as e:
        logger.warning(f"Data-quality audit failed: {e}")

    # Take the ~540k long-lived objects built during startup (engines + candle
    # history, caches, provider clients) out of the cyclic GC's view. A full
    # collection over them cost ~100-160 ms and, holding the GIL the whole time,
    # stalled every API request once every few minutes. See backend/utils/gc_tuning.py.
    try:
        from backend.utils.gc_tuning import freeze_startup_heap
        freeze_startup_heap()
    except Exception as e:
        logger.warning(f"GC freeze failed: {e}")

    yield

    # Stop the background services startup launched. The threads are daemons
    # so the process exits regardless, but stopping them lets in-flight DB
    # writes finish instead of being cut off mid-transaction. Each stop is
    # isolated so one failure can't skip the rest. ``stop()`` joins its
    # thread, so run it off the event loop.
    try:
        from backend.market_data.services.ingestion_service import ingestion_service
        await asyncio.to_thread(ingestion_service.stop)
    except Exception as e:
        logger.warning(f"Ingestion shutdown failed: {e}")
    try:
        digest_service.stop()
    except Exception as e:
        logger.warning(f"Digest service shutdown failed: {e}")
    if settings.ai_nudges.enabled:
        try:
            from backend.ai.nudges import nudge_service
            nudge_service.stop()
        except Exception as e:
            logger.warning(f"Nudge service shutdown failed: {e}")

    _stream = getattr(app.state, "webull_stream", None)
    if _stream is not None:
        try:
            _stream.stop()
        except Exception as e:
            logger.warning(f"Webull stream shutdown failed: {e}")
    if settings.tape.enabled:
        try:
            from backend.api.tape.registry import stop_tape_flusher
            stop_tape_flusher()
        except Exception as e:
            logger.warning(f"Tape flusher shutdown failed: {e}")
    if settings.ai_trade_plan_tracking.enabled:
        try:
            from backend.ai.trade_plan_tracker import stop_trade_plan_tracker
            stop_trade_plan_tracker()
        except Exception as e:
            logger.warning(f"Trade plan tracker shutdown failed: {e}")
    shutdown_tracing()
    try:
        from backend.ai.manager import ai_manager
        from backend.ai.sync_bridge import stop_bridge_loop

        await ai_manager.shutdown()
        stop_bridge_loop()
    except Exception as e:
        logger.warning(f"AI manager shutdown failed: {e}")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Market Intelligence and Quantitative Research Platform",
    lifespan=lifespan,
)


# ── Exception handlers ────────────────────────────────────────────────────────

def _get_correlation_id(request: Request) -> str | None:
    """Read correlation ID from request state or context variable.

    The CorrelationIdMiddleware sets ``request.state.correlation_id`` before
    passing to the next handler. If an exception fires *before* that middleware
    runs (e.g. during routing), the ID may still be in the context variable.
    """
    # 1. Prefer the state set by CorrelationIdMiddleware.
    corr_id = getattr(request.state, "correlation_id", None)
    if corr_id:
        return corr_id
    # 2. Fall back to the context variable (handles pre-dispatch errors).
    return get_correlation_id()


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Attach X-Correlation-ID to HTTP exceptions (400, 404, 422, etc.)."""
    corr_id = _get_correlation_id(request)
    headers = {}
    if corr_id:
        headers["X-Correlation-ID"] = corr_id
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )


@app.exception_handler(Exception)
async def _generic_exception_handler(request: Request, exc: Exception):
    """Attach X-Correlation-ID to uncaught exceptions (500 errors).

    The correlation ID lets operators search logs for the request even when
    Starlette's error middleware swallows the exception before the
    CorrelationIdMiddleware can write the header normally.
    """
    corr_id = _get_correlation_id(request)
    logger.error("Unhandled exception", exc_info=exc)
    headers = {}
    if corr_id:
        headers["X-Correlation-ID"] = corr_id
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
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

# Correlation ID middleware runs first so every subsequent middleware and
# endpoint can include the ID in logs and traces.
app.add_middleware(CorrelationIdMiddleware)

# Security headers: applied to every response, including error
# responses from inner middlewares (rate-limit 429, cache 304).
# Must run *outside* the rate-limit middleware so 429s still get the
# headers — middleware ordering in Starlette is LIFO for dispatch.
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting on write/mutation endpoints. Uses Redis-backed implementation
# for distributed rate limiting across multiple instances, with fallback to
# in-memory limiter if Redis is unavailable.  ``name="global"`` namespaces
# its Redis key so the per-endpoint limiters (AI, alerts, backtest) do not
# share a counter with the global cap — otherwise each write would
# increment the same key twice and the tighter cap would fire at half
# its real budget.
_write_limiter = RedisRateLimiter(
    max_requests=settings.rate_limit.max_requests_per_window,
    window_seconds=settings.rate_limit.window_seconds,
    name="global",
)
app.add_middleware(RateLimitMiddleware, limiter=_write_limiter)
app.add_middleware(CacheMiddleware)
app.add_middleware(RequestCounterMiddleware)

# CORS goes on LAST so it is the outermost layer (Starlette wraps in reverse
# registration order). Registered first, it sat innermost, so the 429s the
# rate limiter short-circuits never received Access-Control-Allow-Origin and
# the browser reported an opaque network error instead of a readable 429.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Include API routers.
#
# ORDER IS MATCH ORDER: Starlette scans the top-level routers linearly on every
# request (~3.4 us per router scanned, measured), so the busiest go first. The
# ranking is each router's share of real frontend traffic in the Sep 17-18 access
# logs (16k requests): trend 21%, analysis 17%, regime 15%, scanner 7%,
# multitimeframe 7%, strategy 6%, market-context 6%, market-data 5%, tape 3%,
# watchlists 3%, ai 3%. Traffic-weighted routing cost went ~38 -> ~26 us/request
# (analysis alone was 17th: ~57 us on 17% of all requests).
#
# Reordering is only safe while no two routers can match the same request (the
# first match wins). None do today, and tests/api/test_route_order.py enforces it,
# so a future overlap fails a test instead of silently changing which handler runs.
app.include_router(trend.router)
app.include_router(analysis.router)
app.include_router(regime.router)
app.include_router(scanner_router)
app.include_router(multitimeframe.router)
app.include_router(strategy.router)
app.include_router(market_context.router)
app.include_router(market_data_routes_router)
app.include_router(tape_router)
app.include_router(watchlist_router)
app.include_router(ai_router)
# Less frequently hit, in their original relative order:
app.include_router(alerts_router)
app.include_router(backtest_router)
app.include_router(finnhub_router)
app.include_router(scanner_ws_router)
app.include_router(realtime_router)
app.include_router(signals_router)
app.include_router(nl_search_router)
app.include_router(aux_data_router)
app.include_router(strategy_lab_router)
app.include_router(custom_indicators_router)
app.include_router(drawing_tools_router)
app.include_router(ai_templates_router)
app.include_router(ai_jobs_router)
app.include_router(ai_digest_router)
app.include_router(ai_chat_router)
app.include_router(system_router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": get_version()
    }

@app.get("/api/system/status")
async def system_status():
    """System status endpoint"""
    from datetime import datetime
    return {
        "service": settings.app_name,
        "version": get_version(),
        "debug": settings.debug,
        "market_data_provider": settings.market_data.primary_provider,
        "market_data_fallback_providers": settings.market_data.fallback_providers,
        "ai_enabled": settings.ai.enabled,
        "timestamp": _to_dashboard_tz(datetime.now(UTC))
    }


@app.get("/api/system/config")
async def system_config():
    """Live system configuration read directly from the .env file.

    Returns current environment values so operators can see what the system
    is actually configured with, including any changes made to .env that
    haven't triggered a server restart yet. Falls back to cached settings
    for fields that can't be read from the env file.
    """
    from pathlib import Path
    from datetime import datetime

    # Read primary and fallback providers directly from .env file.
    env_path = Path(__file__).parent.parent.parent / ".env"
    env_primary = None
    env_fallbacks = None
    if env_path.exists():
        env_vars = {}
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

        env_primary = env_vars.get("MARKET_DATA_PRIMARY_PROVIDER")
        # fallback is stored as a JSON list string, e.g. '["yahoo_finance"]'
        import json as _json
        raw_fallback = env_vars.get("MARKET_DATA_FALLBACK_PROVIDERS", "[]")
        try:
            env_fallbacks = _json.loads(raw_fallback)
        except Exception:
            env_fallbacks = []

    return {
        "service": settings.app_name,
        "version": get_version(),
        "market_data_primary_provider": env_primary or settings.market_data.primary_provider,
        "market_data_fallback_providers": env_fallbacks or settings.market_data.fallback_providers,
        "ai_enabled": settings.ai.enabled,
        "config_source": "live",
        "timestamp": _to_dashboard_tz(datetime.now(UTC)),
    }


# CacheMiddleware and RateLimitMiddleware are registered via
# `app.add_middleware(...)` (the LIFO chain). They are BaseHTTPMiddleware
# subclasses that short-circuit on 304 / 429 responses — and FastAPI's
# `ExceptionMiddleware` sits outside the BaseHTTPMiddleware chain, so the
# short-circuit Response does not reach outer BaseHTTPMiddleware instances
# (SecurityHeadersMiddleware). To keep a single source of truth for the
# security-header baseline, both middlewares inline the same headers via
# `SecurityHeadersMiddleware._static_headers()` in their short-circuit
# paths. See `docs/Version_2/phase_audit_v2.md` for the full rationale.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
