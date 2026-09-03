"""
In-process metrics counters. Module-level state on purpose — these
are read and written from many places (scanner, ingestion service,
bar repository, HTTP middleware) and consolidating them here keeps
the cross-cutting state out of any one subsystem.

This is the single source of truth for the values exposed by
``GET /api/system/performance``. The endpoint reads ``get_snapshot()``
to get a consistent view.
"""
import os
import resource
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import datetime

_start_time = time.time()
_tracemalloc_started = False
_tracemalloc_lock = threading.Lock()

# Limit how many top frames tracemalloc returns on /performance. Snapshot
# diffs are cheap; producing 100+ frames per request is not.
_TRACEMALLOC_TOP_FRAMES = 25

_scanner_total_scans: int = 0
_scanner_total_scan_time_ms: float = 0.0
_scanner_last_scan_time: datetime | None = None

_ingestion_is_running: bool = False
_ingestion_last_bar_time: datetime | None = None
_ingestion_total_bars: int = 0

_http_request_count: int = 0

# Per-process CPU tracking. We sample the process's cumulative user+system
# CPU time on each get_snapshot() call and report utilization as a fraction
# of wall-clock elapsed since the previous sample. This gives a number
# close to what `ps`/`top` would show for *this* Python process specifically,
# which is what the perf endpoint spec asks for ("CPU utilization") and is
# more actionable than the system-wide os.getloadavg() number.
_cpu_lock = threading.Lock()
_cpu_prev_sample_time: float | None = None
_cpu_prev_user_s: float = 0.0
_cpu_prev_sys_s: float = 0.0
_cpu_current_pct: float | None = None
_cpu_peak_pct: float = 0.0


# ── tracemalloc (Python heap profiling) ────────────────────────────────────

def start_memory_profiling() -> None:
    """Start tracemalloc if not already running. Idempotent."""
    global _tracemalloc_started
    with _tracemalloc_lock:
        if not _tracemalloc_started:
            tracemalloc.start()
            _tracemalloc_started = True


def stop_memory_profiling() -> None:
    """Stop tracemalloc if running. Idempotent."""
    global _tracemalloc_started
    with _tracemalloc_lock:
        if _tracemalloc_started:
            tracemalloc.stop()
            _tracemalloc_started = False


def get_tracemalloc_snapshot() -> dict:
    """
    Return a JSON-serializable snapshot of Python heap memory.

    Returns ``None`` if profiling has not been started.
    Keys added by this function:
      - current_mb      — current memory use in MB
      - peak_mb         — peak memory use in MB
      - top_allocations — list of top ``_TRACEMALLOC_TOP_FRAMES`` frames
                          each as ``{"frame": "...", "size_kb": float}``
    """
    if not _tracemalloc_started:
        return None

    current, peak = tracemalloc.get_traced_memory()
    top = tracemalloc.take_snapshot().statistics("lineno")
    top_frames = [
        {
            "frame": str(stat.traceback),
            "size_kb": round(stat.size / 1024, 1),
        }
        for stat in sorted(top, key=lambda s: s.size, reverse=True)[
            :_TRACEMALLOC_TOP_FRAMES
        ]
    ]

    return {
        "current_mb": round(current / (1024 * 1024), 2),
        "peak_mb": round(peak / (1024 * 1024), 2),
        "top_allocations": top_frames,
    }


def record_scan(duration_ms: float) -> None:
    """Called by the scanner after each scan completes.

    ``duration_ms`` is the total wall-clock time, used to compute
    the average processing latency exposed by the perf endpoint.
    """
    global _scanner_total_scans, _scanner_total_scan_time_ms, _scanner_last_scan_time
    _scanner_total_scans += 1
    _scanner_total_scan_time_ms += duration_ms
    _scanner_last_scan_time = datetime.now()


def record_bar() -> None:
    """Called by the bar repository on every successful upsert of one bar."""
    global _ingestion_last_bar_time, _ingestion_total_bars
    _ingestion_last_bar_time = datetime.now()
    _ingestion_total_bars += 1


def record_bars(n: int) -> None:
    """Called by the bar repository on every successful bulk upsert.

    More efficient than calling record_bar() in a loop because it updates
    the timestamp once instead of N times, and increments the counter by N
    instead of N separate +1 operations.
    """
    global _ingestion_last_bar_time, _ingestion_total_bars
    _ingestion_last_bar_time = datetime.now()
    _ingestion_total_bars += n


def set_ingestion_running(running: bool) -> None:
    """Called by the ingestion service on start/stop."""
    global _ingestion_is_running
    _ingestion_is_running = running


def record_http_request() -> None:
    """Called by middleware on every API request."""
    global _http_request_count
    _http_request_count += 1


@dataclass
class MetricsSnapshot:
    uptime_seconds: float
    memory_rss_mb: float
    cpu_load: tuple[float, float, float]
    process_cpu_pct: float | None  # this process's CPU % (0–100×n_cores)
    process_cpu_peak_pct: float
    scanner_total_scans: int
    scanner_avg_scan_ms: float
    scanner_last_scan_time: str | None
    ingestion_is_running: bool
    ingestion_total_bars: int
    ingestion_last_bar_time: str | None
    tf_update_latency_seconds: float | None
    http_request_count: int
    # tracemalloc heap profile — None until start_memory_profiling() is called
    memory_profiling_enabled: bool
    python_heap_current_mb: float | None
    python_heap_peak_mb: float | None
    python_heap_top_allocations: list | None


def get_system_load() -> tuple[float, float, float]:
    """Return (1m, 5m, 15m) system load average. darwin and Linux both expose
    ``os.getloadavg``; on other platforms, return zeros."""
    if hasattr(os, "getloadavg"):
        try:
            return os.getloadavg()
        except OSError:
            return (0.0, 0.0, 0.0)
    return (0.0, 0.0, 0.0)


def _sample_process_cpu() -> float | None:
    """Sample cumulative user+system CPU time and compute utilization since
    the last call. Returns the per-interval CPU fraction (0–1 per core) or
    None on the very first call (no baseline yet). Thread-safe."""
    global _cpu_prev_sample_time, _cpu_prev_user_s, _cpu_prev_sys_s, _cpu_current_pct, _cpu_peak_pct

    now = time.monotonic()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    user_s = usage.ru_utime
    sys_s = usage.ru_stime

    with _cpu_lock:
        if _cpu_prev_sample_time is None:
            _cpu_prev_sample_time = now
            _cpu_prev_user_s = user_s
            _cpu_prev_sys_s = sys_s
            return None

        wall_s = now - _cpu_prev_sample_time
        if wall_s <= 0:
            return _cpu_current_pct

        cpu_s = (user_s - _cpu_prev_user_s) + (sys_s - _cpu_prev_sys_s)
        pct = (cpu_s / wall_s) * 100.0
        if pct > _cpu_peak_pct:
            _cpu_peak_pct = pct
        _cpu_current_pct = pct
        _cpu_prev_sample_time = now
        _cpu_prev_user_s = user_s
        _cpu_prev_sys_s = sys_s
        return pct


def _get_memory_mb() -> float:
    """Return peak RSS in MB. ``resource.getrusage`` returns KB on
    Linux and bytes on macOS, so normalize."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = usage.ru_maxrss
    if rss > 10 * 1024 * 1024:
        # Looks like bytes (macOS).
        return rss / (1024 * 1024)
    return rss / 1024


def get_snapshot() -> dict:
    """Return a JSON-serializable snapshot of the current metrics."""
    now = datetime.now()
    avg_scan_ms = (
        _scanner_total_scan_time_ms / _scanner_total_scans
        if _scanner_total_scans > 0
        else 0.0
    )
    tf_latency_s: float | None = None
    if _ingestion_last_bar_time is not None:
        tf_latency_s = (now - _ingestion_last_bar_time).total_seconds()

    heap = get_tracemalloc_snapshot()
    proc_cpu = _sample_process_cpu()

    snap = MetricsSnapshot(
        uptime_seconds=time.time() - _start_time,
        memory_rss_mb=round(_get_memory_mb(), 2),
        cpu_load=get_system_load(),
        process_cpu_pct=(round(proc_cpu, 2) if proc_cpu is not None else None),
        process_cpu_peak_pct=round(_cpu_peak_pct, 2),
        scanner_total_scans=_scanner_total_scans,
        scanner_avg_scan_ms=round(avg_scan_ms, 2),
        scanner_last_scan_time=(
            _scanner_last_scan_time.isoformat() if _scanner_last_scan_time else None
        ),
        ingestion_is_running=_ingestion_is_running,
        ingestion_total_bars=_ingestion_total_bars,
        ingestion_last_bar_time=(
            _ingestion_last_bar_time.isoformat() if _ingestion_last_bar_time else None
        ),
        tf_update_latency_seconds=(
            round(tf_latency_s, 2) if tf_latency_s is not None else None
        ),
        http_request_count=_http_request_count,
        memory_profiling_enabled=(heap is not None),
        python_heap_current_mb=(heap["current_mb"] if heap else None),
        python_heap_peak_mb=(heap["peak_mb"] if heap else None),
        python_heap_top_allocations=(heap["top_allocations"] if heap else None),
    )
    return asdict(snap)
