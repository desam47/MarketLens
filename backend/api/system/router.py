"""
System performance endpoint.

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
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from backend.observability import (
    get_snapshot,
    record_http_request,
)
from backend.observability.metrics import (
    start_memory_profiling,
    stop_memory_profiling,
)

router = APIRouter(prefix="/api/system", tags=["system"])


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

    class Config:
        arbitrary_types_allowed = True


class RequestCounterMiddleware(BaseHTTPMiddleware):
    """Count every incoming request. Exposed via /api/system/performance."""

    async def dispatch(self, request: Request, call_next):
        record_http_request()
        return await call_next(request)


@router.get("/performance", response_model=PerformanceResponse)
async def get_performance() -> PerformanceResponse:
    """Return live process and service metrics."""
    snap = get_snapshot()
    return PerformanceResponse(
        timestamp=datetime.now(UTC).isoformat(),
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
