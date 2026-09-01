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

    class Config:
        arbitrary_types_allowed = True


class RequestCounterMiddleware(BaseHTTPMiddleware):
    """Count every incoming request. Exposed via /api/system/performance."""

    async def dispatch(self, request: Request, call_next):
        record_http_request()
        return await call_next(request)


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
    """Pull WebSocket connection + broadcast stats."""
    try:
        from ..scanner.ws_router import broadcast_manager
        return broadcast_manager.get_stats()
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
