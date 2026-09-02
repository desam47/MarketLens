"""Lightweight in-process metrics.

Mutated by services (scanner, ingestion, middleware); read by the
``/api/system/performance`` endpoint. Kept in its own module so
services can import it without pulling in FastAPI (which would
create a circular import — the API routers import the services).
"""
from .metrics import (
    get_snapshot,
    record_bar,
    record_bars,
    record_http_request,
    record_scan,
    set_ingestion_running,
    start_memory_profiling,
    stop_memory_profiling,
)

__all__ = [
    "get_snapshot",
    "record_bar",
    "record_bars",
    "record_http_request",
    "record_scan",
    "set_ingestion_running",
    "start_memory_profiling",
    "stop_memory_profiling",
]
