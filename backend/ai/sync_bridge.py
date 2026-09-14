"""Sync → async bridges for callers that have no event loop.

The AI stack (:mod:`backend.ai.manager` and the analysis entry points in
:mod:`backend.ai.analyze`) is async. Several callers run in contexts
with **no running event loop**:

- RQ worker tasks (:mod:`backend.ai.tasks`) — plain threads, no loop.
- FastAPI routes that push work through ``asyncio.to_thread`` /
  ``loop.run_in_executor`` (chat, digest, NL search) — worker threads,
  no loop.

These helpers let that sync code drive coroutine calls without
restructuring the whole call chain. Rule of thumb for call sites:

- Inside an ``async def`` → just ``await``. Never use these helpers.
- Inside a sync function that provably runs in a loop-less thread
  (RQ / to_thread / run_in_executor) → ``run_sync(...)`` for
  coroutines, ``stream_sync(...)`` for async generators.

``run_sync`` raises if a loop is already running in the current thread,
so misuse inside async code fails loudly instead of deadlocking.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

__all__ = ["run_sync", "stream_sync"]


def run_sync(coro: "Coroutine[Any, Any, T]") -> T:
    """Run *coro* to completion on a private event loop and return its result.

    Use from sync code that has no running loop (RQ workers,
    ``to_thread``/``run_in_executor`` worker threads)::

        resp = run_sync(ai_manager.complete(prompt=p, system=s))

    Raises ``RuntimeError`` if called from inside a running loop —
    that call site should be ``await``-ing instead.
    """
    if not asyncio.iscoroutine(coro) and not hasattr(coro, "__await__"):
        return coro  # type: ignore[return-value]

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # No loop running here — safe to create one.
    else:
        coro.close() if hasattr(coro, "close") else None
        raise RuntimeError(
            "run_sync() called from inside a running event loop; "
            "await the coroutine directly instead."
        )
    return asyncio.run(coro)  # type: ignore[arg-type,return-value]


def stream_sync(agen: AsyncIterator[T]) -> Iterator[T]:
    """Yield items from an async iterator in a sync ``for`` loop.

    Drives the async iterator one item at a time on a private event
    loop, so streaming stays incremental (chunks surface as they
    arrive) instead of being collected up front::

        for chunk in stream_sync(ai_manager.stream(prompt=p, system=s)):
            sink.write(chunk)

    The async iterator is closed (``aclose()``) even if the consumer
    abandons the loop early (``GeneratorExit``/exception).
    """
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                item = loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                return
            yield item
    finally:
        try:
            loop.run_until_complete(agen.aclose())
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        loop.close()
