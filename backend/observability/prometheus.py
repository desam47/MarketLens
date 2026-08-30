"""
Prometheus exposition format for MarketLens.

This module produces text-format metrics in the Prometheus exposition
format. We do not depend on the official ``prometheus_client`` library
to keep the install footprint small — the format is straightforward
enough to implement directly.

Format reference: https://prometheus.io/docs/instrumenting/exposition_formats/

Output looks like:
    # HELP marketlens_uptime_seconds Process uptime in seconds
    # TYPE marketlens_uptime_seconds gauge
    marketlens_uptime_seconds 1234.56
    # HELP marketlens_http_requests_total Total HTTP requests received
    # TYPE marketlens_http_requests_total counter
    marketlens_http_requests_total 5678

The exporter is read-only — it pulls from the in-process counters in
``backend/observability/metrics.py`` plus a few other sources
(Redis cache, rate limiter, WebSocket subscriptions) when those
are available.

Why not ``prometheus_client``? It's a fine library, but it pulls in
its own multiprocess and HTTP-server machinery that we don't need —
we already have FastAPI serving the endpoint. The minimal emitter
below adds zero dependencies and zero threads.

Cardinality guidelines
----------------------
High-cardinality label values (symbol, client IP, user ID) cause
Prometheus to create a new time series per unique label value. With
thousands of symbols or IPs this exhausts Prometheus memory.

**Do** use cardinality-bounded labels:
  - Endpoint name (e.g. ``/api/health``, ``/api/market-data``) — bounded
  - HTTP method (GET, POST, etc.) — fixed set
  - Status code bucket (2xx, 4xx, 5xx) — fixed set

**Do not** use unbounded labels without a sampling or bucketing strategy:
  - Per-symbol metrics (1000s of symbols → 1000s of series)
  - Per-client-IP metrics (unlimited clients)
  - Per-request-ID or per-session-ID

When per-symbol granularity is required, prefer:
  - A separate metrics endpoint that samples one symbol at a time, or
  - OpenTelemetry native metrics (which handle high-cardinality natively),
    and scrape those from Jaeger or a dedicated Otel collector.
"""
from typing import Iterable

from ..api.scanner.ws_router import broadcast_manager
from ..api.rate_limit import RedisRateLimiter
from .metrics import get_snapshot


# Counter increments are pulled live from each subsystem. The Prometheus
# exposition reads them on every scrape; values are therefore always
# current. The exporter never mutates state.


def _format_line(
    name: str,
    help_text: str,
    metric_type: str,
    value: float,
    labels: dict[str, str] | None = None,
) -> str:
    """Render one Prometheus metric (with optional labels)."""
    if labels:
        # Quote label values; escape internal backslashes and quotes.
        rendered_labels = ",".join(
            f'{k}="{_escape(v)}"' for k, v in sorted(labels.items())
        )
        metric_line = f"{name}{{{rendered_labels}}} {_format_value(value)}"
    else:
        metric_line = f"{name} {_format_value(value)}"
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} {metric_type}\n"
        f"{metric_line}\n"
    )


def _format_value(v: float) -> str:
    """Format a float for the Prometheus text format.

    NaN / Inf are special-cased per the spec — they're not valid JSON
    numbers but Prometheus does accept them as literal strings.
    """
    if v != v:  # NaN
        return "NaN"
    if v == float("inf"):
        return "+Inf"
    if v == float("-inf"):
        return "-Inf"
    # Int values render cleanly; floats are kept at full precision.
    if isinstance(v, int) or v.is_integer():
        return str(int(v))
    return repr(float(v))


def _escape(value: str) -> str:
    """Escape a label value per the Prometheus spec."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_lines(blocks: Iterable[str]) -> str:
    """Join blocks with a trailing newline so the final response ends cleanly."""
    out = "".join(blocks)
    if not out.endswith("\n"):
        out += "\n"
    return out


def _process_metrics() -> list[str]:
    """Process-level gauges: CPU, memory, uptime."""
    snap = get_snapshot()
    return [
        _format_line(
            "marketlens_uptime_seconds",
            "Process uptime in seconds.",
            "gauge",
            snap["uptime_seconds"],
        ),
        _format_line(
            "marketlens_memory_rss_mb",
            "Resident set size peak in MB.",
            "gauge",
            snap["memory_rss_mb"],
        ),
        _format_line(
            "marketlens_process_cpu_pct",
            "Per-process CPU utilization (0-100 * n_cores).",
            "gauge",
            snap["process_cpu_pct"] or 0.0,
        ),
        _format_line(
            "marketlens_process_cpu_peak_pct",
            "Peak per-process CPU since process start.",
            "gauge",
            snap["process_cpu_peak_pct"],
        ),
    ]


def _scanner_metrics() -> list[str]:
    """Scanner counters."""
    snap = get_snapshot()
    return [
        _format_line(
            "marketlens_scanner_scans_total",
            "Total scanner runs since process start.",
            "counter",
            snap["scanner_total_scans"],
        ),
        _format_line(
            "marketlens_scanner_avg_scan_ms",
            "Average scanner run duration in milliseconds.",
            "gauge",
            snap["scanner_avg_scan_ms"],
        ),
    ]


def _ingestion_metrics() -> list[str]:
    """Ingestion counters."""
    snap = get_snapshot()
    return [
        _format_line(
            "marketlens_ingestion_bars_total",
            "Total bars ingested since process start.",
            "counter",
            snap["ingestion_total_bars"],
        ),
        _format_line(
            "marketlens_ingestion_running",
            "1 if ingestion is running, 0 otherwise.",
            "gauge",
            1.0 if snap["ingestion_is_running"] else 0.0,
        ),
        _format_line(
            "marketlens_ingestion_tf_update_latency_seconds",
            "Seconds since the last bar was ingested.",
            "gauge",
            snap["tf_update_latency_seconds"] or 0.0,
        ),
    ]


def _http_metrics() -> list[str]:
    """HTTP request counter."""
    snap = get_snapshot()
    return [
        _format_line(
            "marketlens_http_requests_total",
            "Total HTTP requests received since process start.",
            "counter",
            snap["http_request_count"],
        ),
    ]


def _cache_metrics() -> list[str]:
    """Redis cache hit/miss counters from MarketDataManager.

    Best-effort: if the manager isn't importable (e.g. during a test
    where it's been mocked) we emit a 0 for each gauge rather than
    failing the scrape.
    """
    try:
        from ..market_data.services.manager import MarketDataManager
    except Exception:
        return []

    try:
        manager = MarketDataManager()
    except Exception:
        # Manager may have side effects in its constructor (Redis
        # connection). Don't fail the scrape over it.
        return []

    try:
        stats = manager.get_cache_stats()
    except Exception:
        return []

    lines = [
        _format_line(
            "marketlens_cache_bar_hits_total",
            "Total Redis bar cache hits.",
            "counter",
            stats.get("bar_hits", 0),
        ),
        _format_line(
            "marketlens_cache_bar_misses_total",
            "Total Redis bar cache misses.",
            "counter",
            stats.get("bar_misses", 0),
        ),
        _format_line(
            "marketlens_cache_quote_hits_total",
            "Total Redis quote cache hits.",
            "counter",
            stats.get("quote_hits", 0),
        ),
        _format_line(
            "marketlens_cache_quote_misses_total",
            "Total Redis quote cache misses.",
            "counter",
            stats.get("quote_misses", 0),
        ),
        _format_line(
            "marketlens_cache_bar_hit_rate",
            "Redis bar cache hit rate (0..1).",
            "gauge",
            stats.get("bar_hit_rate", 0.0),
        ),
        _format_line(
            "marketlens_cache_quote_hit_rate",
            "Redis quote cache hit rate (0..1).",
            "gauge",
            stats.get("quote_hit_rate", 0.0),
        ),
    ]

    # Add Redis-server-side stats if available.
    redis_info = stats.get("redis")
    if isinstance(redis_info, dict):
        for key in ("used_memory", "connected_clients", "ops_per_sec"):
            if key in redis_info:
                lines.append(
                    _format_line(
                        f"marketlens_cache_redis_{key}",
                        f"Redis server stat: {key}.",
                        "gauge",
                        float(redis_info[key] or 0),
                    )
                )

    return lines


def _rate_limit_metrics(rate_limiter: RedisRateLimiter | None = None) -> list[str]:
    """Rate-limiter counters."""
    if rate_limiter is None:
        # Try to get the global instance from main. Lazy import to
        # avoid circular imports at module load time.
        try:
            from ..main import _write_limiter
            rate_limiter = _write_limiter
        except Exception:
            return []

    try:
        stats = rate_limiter.get_stats()
    except Exception:
        return []

    fallback = stats.get("fallback", {})
    return [
        _format_line(
            "marketlens_rate_limit_backend",
            "Rate limiter backend (0=in-memory, 1=redis).",
            "gauge",
            1.0 if stats.get("backend") == "redis" else 0.0,
        ),
        _format_line(
            "marketlens_rate_limit_total_allowed",
            "Total requests allowed by the rate limiter.",
            "counter",
            fallback.get("total_allowed", 0),
        ),
        _format_line(
            "marketlens_rate_limit_total_rejected",
            "Total requests rejected (429) by the rate limiter.",
            "counter",
            fallback.get("total_rejected", 0),
        ),
        _format_line(
            "marketlens_rate_limit_reject_rate",
            "Rate limiter reject rate (0..1).",
            "gauge",
            fallback.get("reject_rate", 0.0),
        ),
        _format_line(
            "marketlens_rate_limit_tracked_ips",
            "Number of distinct IPs tracked by the in-memory limiter.",
            "gauge",
            fallback.get("tracked_ips", 0),
        ),
    ]


def _websocket_metrics() -> list[str]:
    """Scanner WebSocket connection and broadcast counters."""
    try:
        stats = broadcast_manager.get_stats()
    except Exception:
        return []
    return [
        _format_line(
            "marketlens_ws_active_connections",
            "Currently open scanner WebSocket connections.",
            "gauge",
            stats.get("active_connections", 0),
        ),
        _format_line(
            "marketlens_ws_subscribed_symbols",
            "Distinct symbols with at least one subscriber.",
            "gauge",
            stats.get("subscribed_symbols", 0),
        ),
        _format_line(
            "marketlens_ws_total_subscriptions",
            "Total symbol subscriptions across all connections.",
            "gauge",
            stats.get("total_subscriptions", 0),
        ),
        _format_line(
            "marketlens_ws_connections_total",
            "Lifetime count of WebSocket connections opened.",
            "counter",
            stats.get("connections_total", 0),
        ),
        _format_line(
            "marketlens_ws_disconnections_total",
            "Lifetime count of WebSocket disconnections.",
            "counter",
            stats.get("disconnections_total", 0),
        ),
        _format_line(
            "marketlens_ws_messages_sent_total",
            "Lifetime count of messages sent on the WebSocket.",
            "counter",
            stats.get("messages_sent_total", 0),
        ),
        _format_line(
            "marketlens_ws_broadcasts_total",
            "Lifetime count of broadcast operations.",
            "counter",
            stats.get("broadcasts_total", 0),
        ),
    ]


# Map circuit-breaker states to numeric values for Prometheus gauges.
# (Numeric rather than string because Prometheus prefers numeric gauges;
# a string label would create one time-series per state per provider.)
_CIRCUIT_STATE_VALUE: dict[str, int] = {
    "CLOSED": 0,
    "HALF_OPEN": 1,
    "OPEN": 2,
}


def _provider_metrics() -> list[str]:
    """Per-provider circuit breaker and rate limiter metrics (v2.2).

    Cardinality: bounded by the number of registered providers (yahoo_finance,
    webull, ...), so this is safe. Symbols are NOT included as labels.

    If the manager is not importable or no providers are registered, we
    emit nothing rather than failing the scrape — same fallback pattern
    used elsewhere in this file.
    """
    try:
        from ..market_data.services.manager import (
            MarketDataManager,
            _circuit_breakers,
            _rate_limiter,
        )
    except Exception:
        return []

    try:
        manager = MarketDataManager()
    except Exception:
        return []

    lines: list[str] = []

    # Circuit breaker state + consecutive failure count per provider.
    for provider_name, breaker in _circuit_breakers.items():
        try:
            state = breaker.get_state()
        except Exception:
            state = "CLOSED"
        try:
            consecutive_failures = breaker.stats().consecutive_failures
        except Exception:
            consecutive_failures = 0

        lines.append(
            _format_line(
                "marketlens_provider_circuit_breaker_state",
                "Circuit breaker state per provider (0=CLOSED, 1=HALF_OPEN, 2=OPEN).",
                "gauge",
                float(_CIRCUIT_STATE_VALUE.get(str(state), 0)),
                labels={"provider": provider_name},
            )
        )
        lines.append(
            _format_line(
                "marketlens_provider_consecutive_failures",
                "Current consecutive failure count per provider (resets on success).",
                "gauge",
                float(consecutive_failures),
                labels={"provider": provider_name},
            )
        )

    # Per-provider rate-limit throttle counters.
    try:
        throttle_stats = _rate_limiter.stats()
    except Exception:
        throttle_stats = {}

    # Emit a series for every provider the manager knows about, even
    # if it has 0 throttled calls, so dashboards can render zero-baselines.
    known_providers: set[str] = set(manager.providers.keys()) | set(throttle_stats.keys())
    for provider_name in sorted(known_providers):
        lines.append(
            _format_line(
                "marketlens_provider_rate_limit_hits_total",
                "Total calls throttled by the per-provider rate limiter.",
                "counter",
                float(throttle_stats.get(provider_name, 0)),
                labels={"provider": provider_name},
            )
        )

    return lines


def render_prometheus_text(
    rate_limiter: RedisRateLimiter | None = None,
) -> str:
    """Render the full Prometheus exposition text.

    Each block is appended to the response. The format is the
    well-known 0.0.4 text format that Prometheus scrapes by default.
    """
    blocks: list[str] = []
    blocks.extend(_process_metrics())
    blocks.extend(_scanner_metrics())
    blocks.extend(_ingestion_metrics())
    blocks.extend(_http_metrics())
    blocks.extend(_cache_metrics())
    blocks.extend(_rate_limit_metrics(rate_limiter))
    blocks.extend(_websocket_metrics())
    blocks.extend(_provider_metrics())
    return _format_lines(blocks)
