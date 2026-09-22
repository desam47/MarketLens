"""
System performance and metrics endpoints.

Exposes the metrics Phase 20 spec requires:
  - average processing latency  (scanner avg scan time)
  - peak memory                (RSS via ``resource``)
  - CPU utilization            (load average on darwin/Linux)
  - API requests               (HTTP request counter)
  - symbols processed          (scanner total scans)
  - timeframe update latency    (now - last ingested bar timestamp)

The actual counters live in ``backend/observability/metrics.py`` so the
scanner, ingestion, and bar repository can import them without pulling
in FastAPI (avoids circular imports).
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from ...observability import (
    get_snapshot,
    record_http_request,
)
from ...observability.metrics import (
    start_memory_profiling,
    stop_memory_profiling,
)
from ...observability.prometheus import render_prometheus_text

router = APIRouter(prefix="/api/system", tags=["system"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


class PerformanceResponse(BaseModel):
    timestamp: str
    uptime_seconds: float
    memory_rss_mb: float
    cpu_load: tuple[float, float, float]
    process_cpu_pct: float | None
    process_cpu_peak_pct: float
    scanner: dict
    ingestion: dict
    http_request_count: int
    memory_profiling_enabled: bool
    python_heap_current_mb: float | None
    python_heap_peak_mb: float | None
    python_heap_top_allocations: list | None
    cache: dict | None = None
    rate_limit: dict | None = None
    websocket: dict | None = None
    bars: dict | None = None  # Phase 3.1: bars_stored, bars_1m_only, bars_resampled
    # Phase 3.3.17: retention fields are nested in the bars dict above
    # (oldest_bar, newest_bar, distinct_symbols, retention_days).
    providers: dict | None = None  # Phase 3.6: per-provider health (incl. Alpaca WS status)
    provider_observability: dict | None = None

    class Config:
        arbitrary_types_allowed = True


class RequestCounterMiddleware:
    """Count every incoming HTTP request. Exposed via /api/system/performance.

    A plain ASGI middleware: as a BaseHTTPMiddleware this one-line counter cost ~180 us
    per request. WebSocket and lifespan scopes pass through uncounted, as before.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            record_http_request()
        await self.app(scope, receive, send)


def _safe_bar_counts() -> dict | None:
    """Query the bars table for storage metrics.

    Phase 3.1: all stored rows have timeframe='1m' and source='raw'.
    bars_resampled is always 0 because resampling is a read-time operation
    (no rows are persisted as resampled). These fields are useful for
    confirming the Phase 3.1 contract is honoured and for monitoring
    storage growth.

    Phase 3.3.17: also returns retention metrics — oldest/newest bar per
    symbol, distinct symbol count, and each timeframe's configured
    retention window (per-timeframe as of 2026-09-09, see RetentionSettings).

    Phase 3.9.4: 6 sequential count/min/max queries collapsed into one
    round-trip via a single SELECT with multiple aggregates.
    """
    try:
        from ...database import SessionLocal

        db = SessionLocal()
        try:
            # Single round-trip with all 6 aggregates (Phase 3.9.4).
            row = db.execute(
                text("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN timeframe = '1m' THEN 1 ELSE 0 END) AS bars_1m,
                    SUM(CASE WHEN source = 'resampled' THEN 1 ELSE 0 END) AS bars_resampled,
                    MIN(timestamp) AS oldest_bar,
                    MAX(timestamp) AS newest_bar,
                    COUNT(DISTINCT symbol) AS distinct_symbols
                FROM bars
            """)
            ).fetchone()
            if row is None:
                return None
            total, bars_1m, bars_resampled, oldest_bar, newest_bar, distinct_symbols = row
            try:
                from ...config.settings import settings as _s

                retention_by_tf = {
                    "1m": _s.retention.tf_1m_days,
                    "2m": _s.retention.tf_2m_days,
                    "3m": _s.retention.tf_3m_days,
                    "5m": _s.retention.tf_5m_days,
                    "15m": _s.retention.tf_15m_days,
                    "30m": _s.retention.tf_30m_days,
                    "1h": _s.retention.tf_1h_days,
                    "4h": _s.retention.tf_4h_days,
                    "1d": _s.retention.tf_1d_days,
                    "1wk": _s.retention.tf_1wk_days,
                }
            except Exception:
                retention_by_tf = {}

            def _iso(value) -> str | None:
                # SQLite's DBAPI only applies the DATETIME type converter to
                # values selected directly from a typed column — MIN()/MAX()
                # aggregate results come back as plain str, so `.isoformat()`
                # would raise AttributeError (previously swallowed by the
                # outer bare except, silently returning None for this whole
                # endpoint). Handle both shapes.
                if value is None:
                    return None
                if isinstance(value, str):
                    return value
                return value.isoformat()

            return {
                "bars_stored": int(total or 0),
                "bars_1m_only": int(bars_1m or 0),
                "bars_resampled": int(bars_resampled or 0),
                "oldest_bar": _iso(oldest_bar),
                "newest_bar": _iso(newest_bar),
                "distinct_symbols": int(distinct_symbols or 0),
                "retention_days_by_timeframe": retention_by_tf,
            }
        finally:
            db.close()
    except Exception:
        return None


def _safe_cache_stats() -> dict | None:
    """Pull Redis cache stats from MarketDataManager; return None on failure.

    Cache stats come from the same Manager instance the ingestion
    service uses, so the counters reflect real production traffic. If
    anything goes wrong (manager not importable, Redis constructor
    side-effects) we surface ``None`` instead of failing the endpoint.
    """
    try:
        from ...market_data.services.manager import market_data_manager

        return market_data_manager.get_cache_stats()
    except Exception:
        return None


def _safe_rate_limit_stats() -> dict | None:
    """Pull rate-limiter stats from the global _write_limiter.

    The limiter is created in ``api/main.py`` and lives there. We
    import lazily to avoid the circular ``api.main`` -> ``api.system``
    edge at import time.
    """
    try:
        from ..main import _write_limiter

        return _write_limiter.get_stats()
    except Exception:
        return None


def _safe_websocket_stats() -> dict | None:
    """Pull WebSocket connection + broadcast stats from both scanner and realtime streams."""
    try:
        from ..realtime.ws_router import broadcast_manager as realtime_bm
        from ..scanner.ws_router import broadcast_manager as scanner_bm

        return {
            "scanner": scanner_bm.get_stats(),
            "realtime": realtime_bm.get_stats(),
        }
    except Exception:
        return None


def _safe_provider_stats() -> dict | None:
    """Per-provider health snapshot, keyed by provider name.

    Returns ``{provider_name: {is_healthy, last_error, ws_status (alpaca only)}}``
    for every provider registered in the MarketDataManager. Returns ``None``
    on import failure so the endpoint stays available.

    Phase 3.6: includes Alpaca's WebSocket status string in the per-provider
    payload so the system health UI can surface live-stream health.
    """
    try:
        from ...market_data.services.manager import market_data_manager

        out: dict = {}
        for name, provider in market_data_manager.providers.items():
            try:
                status = provider.get_provider_status()
                entry = {
                    "is_healthy": status.is_healthy,
                    "last_error": status.error_message,
                    "circuit_breaker_state": status.circuit_breaker_state,
                    "consecutive_failures": status.consecutive_failures,
                    "total_successes": status.total_successes,
                    "total_failures": status.total_failures,
                }
                # Surface Alpaca's WebSocket status if available.
                ws_status = getattr(provider, "_ws_status", None)
                if ws_status is not None:
                    entry["ws_status"] = ws_status
                out[name] = entry
            except Exception as exc:
                out[name] = {"is_healthy": False, "last_error": str(exc)}
        return out
    except Exception:
        return None


def _safe_provider_observability() -> dict | None:
    """Return bounded provider activity history and configured feature coverage."""
    try:
        from ...config.settings import settings
        from ...observability.provider_history import provider_history_stats

        history = provider_history_stats()
        events = history["events"]
        successful_methods = {
            (event["provider"], event["method"])
            for event in events
            if event.get("outcome") in {"success", "fallback"}
        }

        def observed(provider: str, methods: set[str]) -> bool:
            return any((provider, method) in successful_methods for method in methods)

        webull_ready = bool(
            settings.webull.enabled and settings.webull.app_key and settings.webull.app_secret
        )
        stream_ready = bool(webull_ready and settings.webull.streaming_enabled)
        stream_connected = False
        if stream_ready:
            try:
                from ...market_data.streaming.webull_stream import get_webull_stream_client

                stream = get_webull_stream_client()
                stream_connected = bool(stream is not None and getattr(stream, "_connected", False))
            except Exception:
                stream_connected = False
        primary = settings.market_data.primary_provider
        return {
            "events": events,
            "failure_count": history["failure_count"],
            "success_count": history["success_count"],
            "entitlements": {
                "rest_quotes": {
                    "provider": settings.market_data.primary_provider,
                    "status": "verified"
                    if observed(primary, {"get_quote", "get_batch_quotes"})
                    else "configured"
                    if primary
                    else "unavailable",
                    "verification": "runtime_observed"
                    if observed(primary, {"get_quote", "get_batch_quotes"})
                    else "configuration_only",
                    "provider_reported": False,
                    "verification_note": "Webull entitlement metadata is not exposed by the installed SDK.",
                },
                "bars": {
                    "providers": [
                        settings.market_data.primary_provider,
                        *settings.market_data.fallback_providers,
                    ],
                    "timeframe_sources": {
                        timeframe: {
                            "primary": settings.backfill.get_primary_provider(timeframe),
                            "fallbacks": settings.backfill.get_fallback_providers(timeframe),
                            "observed": [
                                provider
                                for provider in {
                                    settings.backfill.get_primary_provider(timeframe),
                                    *settings.backfill.get_fallback_providers(timeframe),
                                }
                                if observed(provider, {"get_historical_bars", "get_latest_bar"})
                            ],
                        }
                        for timeframe in ("1m", "1h", "4h", "1d", "1wk")
                    },
                    "status": "verified"
                    if observed(primary, {"get_latest_bar", "get_historical_bars"})
                    else "configured"
                    if primary
                    else "unavailable",
                    "verification": "runtime_observed"
                    if observed(primary, {"get_latest_bar", "get_historical_bars"})
                    else "configuration_only",
                    "provider_reported": False,
                    "verification_note": "Provider subscription entitlement is not exposed by the installed SDK.",
                },
                "bbo": {
                    "provider": "webull",
                    "status": "verified"
                    if stream_connected
                    else "configured"
                    if stream_ready
                    else "disabled",
                    "verification": "runtime_observed"
                    if stream_connected
                    else "configuration_only",
                    "provider_reported": False,
                    "verification_note": "Webull stream connectivity is observable; subscription entitlement is not exposed.",
                },
                "time_and_sales": {
                    "provider": "webull",
                    "status": "verified"
                    if stream_connected
                    else "configured"
                    if stream_ready
                    else "disabled",
                    "verification": "runtime_observed"
                    if stream_connected
                    else "configuration_only",
                    "provider_reported": False,
                    "verification_note": "Webull stream connectivity is observable; subscription entitlement is not exposed.",
                },
                "options": {
                    "provider": "yahoo_finance",
                    "status": "delayed_or_estimated",
                    "verification": "provider_limited",
                    "provider_reported": False,
                    "verification_note": "Yahoo options data is delayed or estimated where available.",
                },
                "fundamentals": {
                    "provider": "configured_fallback_chain",
                    "status": "configured",
                    "verification": "provider_limited",
                    "provider_reported": False,
                    "verification_note": "Fundamentals availability is provider-limited.",
                },
            },
        }
    except Exception:
        return None


@router.get("/performance", response_model=PerformanceResponse)
def get_performance() -> PerformanceResponse:
    """Return live process and service metrics."""
    snap = get_snapshot()
    return PerformanceResponse(
        timestamp=_to_dashboard_tz(datetime.now(UTC)),
        uptime_seconds=snap["uptime_seconds"],
        memory_rss_mb=snap["memory_rss_mb"],
        cpu_load=snap["cpu_load"],
        process_cpu_pct=snap["process_cpu_pct"],
        process_cpu_peak_pct=snap["process_cpu_peak_pct"],
        scanner={
            "total_scans": snap["scanner_total_scans"],
            "avg_scan_ms": snap["scanner_avg_scan_ms"],
            "last_scan_time": snap["scanner_last_scan_time"],
        },
        ingestion={
            "is_running": snap["ingestion_is_running"],
            "total_bars_ingested": snap["ingestion_total_bars"],
            "last_bar_time": snap["ingestion_last_bar_time"],
            "tf_update_latency_seconds": snap["tf_update_latency_seconds"],
        },
        http_request_count=snap["http_request_count"],
        memory_profiling_enabled=snap["memory_profiling_enabled"],
        python_heap_current_mb=snap["python_heap_current_mb"],
        python_heap_peak_mb=snap["python_heap_peak_mb"],
        python_heap_top_allocations=snap["python_heap_top_allocations"],
        cache=_safe_cache_stats(),
        rate_limit=_safe_rate_limit_stats(),
        websocket=_safe_websocket_stats(),
        bars=_safe_bar_counts(),
        providers=_safe_provider_stats(),
        provider_observability=_safe_provider_observability(),
    )


class MemoryProfileToggle(BaseModel):
    enabled: bool


@router.post("/memory_profile")
async def toggle_memory_profiling(payload: MemoryProfileToggle) -> dict:
    """Turn tracemalloc heap profiling on/off without restarting the API."""
    if payload.enabled:
        start_memory_profiling()
    else:
        stop_memory_profiling()
    return {"memory_profiling_enabled": payload.enabled}


class LoopLagWatchdogToggle(BaseModel):
    enabled: bool
    threshold_ms: float = Field(50.0, ge=10.0, le=5000.0)


@router.post("/loop_lag_watchdog")
async def toggle_loop_lag_watchdog(payload: LoopLagWatchdogToggle) -> dict:
    """Turn the event-loop stall watchdog on/off without restarting the API.

    While on, any stretch longer than ``threshold_ms`` where the loop fails to
    tick is captured with every thread's stack and logged under
    ``marketlens.loop_lag``; the recent reports are returned here and by GET.
    Off by default. Runs on the API's own loop (this handler is async).
    """
    from ...observability import loop_lag_watchdog

    if payload.enabled:
        loop_lag_watchdog.enable(asyncio.get_running_loop(), payload.threshold_ms)
    else:
        loop_lag_watchdog.disable()
    return loop_lag_watchdog.status()


@router.get("/loop_lag_watchdog")
async def get_loop_lag_watchdog() -> dict:
    """State of the stall watchdog plus the most recent stall reports."""
    from ...observability import loop_lag_watchdog

    return loop_lag_watchdog.status()


@router.post("/restart")
async def restart_services() -> dict:
    """Restart the backend and frontend dev servers (System Health page).

    Spawns ``scripts/restart_dev.sh`` as a fully detached process (its own
    session — ``start_new_session=True`` — so it survives this backend
    process being killed a moment later) and returns immediately. The
    script itself sleeps briefly before killing anything, giving this
    response time to actually reach the client first.

    Local dev tool, matching the rest of this app: no auth gate, same as
    every other endpoint here.
    """
    import subprocess
    from pathlib import Path as _Path

    project_root = _Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "restart_dev.sh"
    subprocess.Popen(
        ["/bin/bash", str(script)],
        cwd=str(project_root),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "status": "restarting",
        "message": "Backend and frontend are restarting — this page will reconnect automatically.",
    }


@router.get("/metrics", response_class=Response)
def prometheus_metrics() -> Response:
    """Prometheus exposition endpoint (text format 0.0.4).

    Scraped by Prometheus / VictoriaMetrics. The text is generated
    on each call from the in-process counters — no background
    exporter thread is used.
    """
    text = render_prometheus_text()
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")


# ---------------------------------------------------------------------------
# Phase 3.3.3 — Backup / WAL health
# ---------------------------------------------------------------------------


def _safe_backup_status() -> dict | None:
    """Return WAL checkpoint + Litestream health snapshot.

    ``PRAGMA wal_checkpoint(PASSIVE)`` checkpoints as many frames as it
    can without waiting on any reader or writer, and reports how many it
    could not. It is deliberately NOT ``TRUNCATE``: that mode blocks
    writers (and waits on readers) until the WAL is empty — on a busy
    database that stalls ingestion for every status poll — and it also
    zeroes the very ``wal_size_bytes`` this endpoint exists to report, so
    a WAL that was growing unchecked could never show up here.

    Litestream exposes its health via HTTP GET to
    ``http://localhost:9090`` (default port when running
    ``litestream replicate``).  When Litestream is not running the
    request times out and we surface ``litestream_reachable: false``
    rather than raising.

    Returns ``None`` on any failure so the endpoint stays available
    even when the DB is in a bad state.
    """
    try:
        from ...database import engine

        if not str(engine.url).startswith("sqlite:"):
            return None

        db_path = Path(str(engine.url).replace("sqlite:///", ""))

        with engine.connect() as conn:
            # Checkpoint: returns (busy, wal_frames, checkpointed_frames)
            checkpoint_result = conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)")).fetchone()
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()

            # WAL file size (may be 0 if no writes have happened since last checkpoint)
            wal_path = db_path.with_suffix(".db-wal")
            wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0

            # SHM file size (always present when WAL is active)
            shm_path = db_path.with_suffix(".db-shm")
            shm_size_bytes = shm_path.stat().st_size if shm_path.exists() else 0

        # Litestream health (non-blocking; times out in 1s if not running)
        litestream_reachable = False
        litestream_generation = None
        litestream_dbs = None
        try:
            import urllib.request

            req = urllib.request.Request(
                "http://localhost:9090/health",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    import json

                    data = json.loads(resp.read())
                    litestream_reachable = True
                    litestream_generation = data.get("generation")
                    litestream_dbs = data.get("dbs")
        except Exception:
            pass  # Litestream not running — surface reachable=false

        return {
            "journal_mode": str(journal_mode),
            "wal_checkpoint_busy": bool(checkpoint_result[0]),  # pages still in WAL
            "wal_checkpoint_frames": int(checkpoint_result[1]),  # total WAL frames
            "wal_checkpoint_end": int(checkpoint_result[2]),  # WAL end page
            "wal_size_bytes": wal_size_bytes,
            "shm_size_bytes": shm_size_bytes,
            "litestream_reachable": litestream_reachable,
            "litestream_generation": litestream_generation,
            "litestream_dbs": litestream_dbs,
        }
    except Exception:
        return None


class BackupStatusResponse(BaseModel):
    """Response shape for GET /api/system/backup-status."""

    timestamp: str
    journal_mode: str
    wal_checkpoint_busy: bool
    wal_checkpoint_frames: int
    wal_checkpoint_end: int
    wal_size_bytes: int
    shm_size_bytes: int
    litestream_reachable: bool
    litestream_generation: str | None
    litestream_dbs: list | None


@router.get("/backup-status", response_model=BackupStatusResponse)
async def get_backup_status() -> BackupStatusResponse:
    """Return WAL + Litestream backup health.

    Phase 3.3.3: exposes checkpoint status, WAL file size, and Litestream
    streaming state so the SystemHealth DB tab can show at-a-glance backup
    health without requiring CLI access.
    """
    status = await asyncio.to_thread(_safe_backup_status)
    if status is None:
        # Degrade gracefully — return a known-unhealthy shape.
        return BackupStatusResponse(
            timestamp=_to_dashboard_tz(datetime.now(UTC)),
            journal_mode="unknown",
            wal_checkpoint_busy=False,
            wal_checkpoint_frames=0,
            wal_checkpoint_end=0,
            wal_size_bytes=0,
            shm_size_bytes=0,
            litestream_reachable=False,
            litestream_generation=None,
            litestream_dbs=None,
        )
    return BackupStatusResponse(timestamp=_to_dashboard_tz(datetime.now(UTC)), **status)


@router.get("/data-quality")
def get_data_quality() -> dict:
    """Audit for duplicate calendar-day bars (1d/1wk).

    2026-09-09: webull stamps 1d bars at 00:00, yahoo_finance at 09:30 —
    those conventions never collided on the DB's exact-timestamp unique
    key, so 63% of stored 1d rows ended up duplicated (with close prices
    differing by up to 2.4% between the two rows for the same day) before
    anyone noticed. The write-path bug is fixed (see _normalize_1d_bar in
    ingestion_service.py) and the existing duplicates were cleaned up, but
    nothing was watching for a regression of this specific failure mode —
    this endpoint is that watch. A non-empty ``duplicates`` list here
    means the write path let calendar-day duplicates back in.
    """
    from ...database import SessionLocal
    from ...repositories.bar_repository import find_duplicate_calendar_bars

    db = SessionLocal()
    try:
        duplicates: list[dict] = []
        for tf in ("1d", "1wk"):
            duplicates.extend(find_duplicate_calendar_bars(db, tf))
    finally:
        db.close()

    return {
        "healthy": len(duplicates) == 0,
        "duplicate_count": len(duplicates),
        "duplicates": duplicates,
    }
