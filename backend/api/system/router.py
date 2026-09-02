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
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
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
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(_DASHBOARD_TZ).isoformat()


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
    """
    try:
        from sqlalchemy import func
        from ...database import SessionLocal
        from ...models.market_data_sql import BarModel
        db = SessionLocal()
        try:
            total = db.query(func.count(BarModel.id)).scalar() or 0
            bars_1m = (
                db.query(func.count(BarModel.id))
                .filter(BarModel.timeframe == "1m")
                .scalar()
                or 0
            )
            bars_resampled = (
                db.query(func.count(BarModel.id))
                .filter(BarModel.source == "resampled")
                .scalar()
                or 0
            )
            return {
                "bars_stored": total,
                "bars_1m_only": bars_1m,
                "bars_resampled": bars_resampled,
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
