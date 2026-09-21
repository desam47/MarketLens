"""
Garbage-collector tuning for the long-running API process.

Why: a full (generation-2) collection walks EVERY tracked object, never releases the
GIL, and runs in whichever thread happened to allocate. After startup this process
tracks ~540k long-lived objects (25 symbols x 10 timeframe engines with candle
history, caches, provider clients, loaded modules), so each full collection cost
~99 ms measured — and on the live server every one showed up as a 140-160 ms stall
of the whole API (2 in 6 minutes, each matched to the millisecond by the stall
watchdog's GC hook). Freezing that startup heap moves it into a permanent
generation the collector no longer visits: the same collection then takes ~0 ms.

Trade-off: frozen objects are never traversed again, so garbage that is part of a
reference CYCLE among them would never be reclaimed. Reference counting still frees
every acyclic object immediately (frozen or not), and what gets frozen here is
process-lifetime state (engines, caches, modules), so that is the right side of the
trade. Anything allocated after startup is collected normally.
"""

from __future__ import annotations

import gc
import logging
import time

logger = logging.getLogger(__name__)


def freeze_startup_heap() -> int:
    """Collect startup garbage once, then freeze what is still alive.

    Returns the number of objects in the permanent generation afterwards.
    """
    started = time.perf_counter()
    gc.collect()  # free startup garbage first, so it is not frozen along with the live heap
    gc.freeze()
    frozen = gc.get_freeze_count()
    logger.info(
        "GC: froze %d startup objects so full collections skip them (collect+freeze %.0f ms)",
        frozen,
        (time.perf_counter() - started) * 1000.0,
    )
    return frozen
