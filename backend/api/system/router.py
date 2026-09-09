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
from datetime import UTC, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

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

    class Config:
        arbitrary_types_allowed = True


class RequestCounterMiddleware(BaseHTTPMiddleware):
    """Count every incoming request. Exposed via /api/system/performance."""

    async def dispatch(self, request: Request, call_next):
        record_http_request()
        return await call_next(request)


def _safe_bar_counts() -> dict | None:
    """Query the bars table for storage metrics.

    Phase 3.1: all stored rows have timeframe='1m' and source='raw'.
    bars_resampled is always 0 because resampling is a read-time operation
    (no rows are persisted as resampled). These fields are useful for
    confirming the Phase 3.1 contract is honoured and for monitoring
    storage growth.

    Phase 3.3.17: also returns retention metrics — oldest/newest bar per
    symbol, distinct symbol count, and the configured retention window.

    Phase 3.9.4: 6 sequential count/min/max queries collapsed into one
    round-trip via a single SELECT with multiple aggregates.
    """
    try:
        from ...database import SessionLocal
        db = SessionLocal()
        try:
            # Single round-trip with all 6 aggregates (Phase 3.9.4).
            row = db.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN timeframe = '1m' THEN 1 ELSE 0 END) AS bars_1m,
                    SUM(CASE WHEN source = 'resampled' THEN 1 ELSE 0 END) AS bars_resampled,
                    MIN(timestamp) AS oldest_bar,
                    MAX(timestamp) AS newest_bar,
                    COUNT(DISTINCT symbol) AS distinct_symbols
                FROM bars
            """)).fetchone()
            if row is None:
                return None
            total, bars_1m, bars_resampled, oldest_bar, newest_bar, distinct_symbols = row
            try:
                from ...config.settings import settings as _s
                retention_days = _s.market_data.bar_retention_days
            except Exception:
                retention_days = 1000

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
                "retention_days": int(retention_days),
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
        from ...market_data.services.manager import MarketDataManager

        manager = MarketDataManager()
        return manager.get_cache_stats()
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
        from ..scanner.ws_router import broadcast_manager as scanner_bm
        from ..realtime.ws_router import broadcast_manager as realtime_bm
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
        from ...market_data.services.manager import MarketDataManager

        manager = MarketDataManager()
        out: dict = {}
        for name, provider in manager.providers.items():
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


@router.get("/performance", response_model=PerformanceResponse)
async def get_performance() -> PerformanceResponse:
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


@router.get("/metrics", response_class=Response)
async def prometheus_metrics() -> Response:
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

BackupStatusResponse: type = None  # defined after the helper


def _safe_backup_status() -> dict | None:
    """Return WAL checkpoint + Litestream health snapshot.

    ``PRAGMA wal_checkpoint(TRUNCATE)`` does a passive checkpoint then
    truncates the WAL file to zero bytes if all frames were committed —
    it is always safe to call and never blocks readers or writers.

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
            # Checkpoint: returns (checkpointed_pages, wal_frames, end_page)
            checkpoint_result = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
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
            "wal_checkpoint_busy": bool(checkpoint_result[0]),   # pages still in WAL
            "wal_checkpoint_frames": int(checkpoint_result[1]),   # total WAL frames
            "wal_checkpoint_end": int(checkpoint_result[2]),      # WAL end page
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
    status = _safe_backup_status()
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
