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

Why a single background loop instead of ``asyncio.run()`` per call
(rewritten 2026-09-14)
--------------------------------------------------
The providers hold **persistent** ``httpx.AsyncClient`` instances (the
O2 connection-pooling optimization). Pooled keep-alive connections are
bound to the event loop that opened them, so running coroutines on a
fresh loop per call while reusing one cached client is a correctness
bug, reproduced live against a local HTTP/1.1 server:

- sequential loops: the 2nd call through the same client raises
  ``RuntimeError: Event loop is closed`` (the pool hands back a
  connection whose transport is bound to the dead loop). In the digest
  path this is swallowed by ``_safe_call`` → silently-empty mover
  analyses, i.e. data loss rather than a crash.
- concurrent loops (multiple ``to_thread`` workers each calling
  ``run_sync``): hangs.

So every bridged coroutine now runs on ONE dedicated daemon-thread
event loop, shared process-wide. The client always executes on the loop
its connections were opened on, pooling actually works, and
cross-thread calls are serialized onto that single loop by
``run_coroutine_threadsafe`` (which is itself thread-safe). The caller
thread blocks for the duration of its own call; the slow part of an AI
call is the network await on the bridge loop, not caller-side compute,
and heavy sync work (``build_context``) runs on its own worker threads
inside the coroutine chain, so single-loop serialization costs little.

For callers that *do* have a running loop (FastAPI handlers awaiting
``ai_manager.complete()`` / ``status()`` directly), :func:`on_bridge`
is the async counterpart: it funnels the coroutine onto the same bridge
loop and awaits it without blocking the caller's loop. One process
therefore has exactly one loop ever touching a provider's httpx client
— two loops sharing one cached client is the same cross-loop bug, just
without the ``asyncio.run()`` teardown to make it crash loudly.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Iterator
from concurrent.futures import Future
from typing import Any, TypeVar

T = TypeVar("T")

__all__ = ["run_sync", "stream_sync", "on_bridge", "stop_bridge_loop"]

# --- The shared bridge loop ------------------------------------------

_bridge_loop: asyncio.AbstractEventLoop | None = None
_bridge_thread: threading.Thread | None = None
_bridge_lock = threading.Lock()


def _get_bridge_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide bridge loop, starting it on first use.

    Double-checked under ``_bridge_lock`` so concurrent first-callers
    (several to_thread workers starting at once) can't each spin up a
    loop and strand the losing one — only the loop stored in
    ``_bridge_loop`` is ever used.
    """
    global _bridge_loop, _bridge_thread
    loop = _bridge_loop
    if loop is not None and not loop.is_closed():
        return loop
    with _bridge_lock:
        loop = _bridge_loop
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _serve() -> None:
                asyncio.set_event_loop(loop)
                # Drain one loop iteration so the loop is verifiably
                # live before callers submit to it, then settle into
                # run_forever for its steady state.
                loop.run_until_complete(asyncio.sleep(0))
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    # Only reached via stop_bridge_loop(); cancel
                    # whatever is still pending so thread teardown is
                    # clean rather than leaving tasks on a dead loop.
                    for task in asyncio.all_tasks(loop):
                        task.cancel()
                    loop.close()

            thread = threading.Thread(
                target=_serve, name="ai-sync-bridge", daemon=True
            )
            _bridge_thread = thread
            thread.start()
            ready.wait(timeout=5.0)
            _bridge_loop = loop
        return loop


def _submit(awaitable: Awaitable[Any]) -> Future[Any]:
    """Schedule *awaitable* on the bridge loop from any (loop-less) thread."""
    return asyncio.run_coroutine_threadsafe(_wrap(awaitable), _get_bridge_loop())


async def _wrap(awaitable: Awaitable[Any]) -> Any:
    """run_coroutine_threadsafe wants a coroutine; __anext__()/aclose()
    technically return awaitables. Wrapping covers both."""
    return await awaitable


async def on_bridge[T](awaitable: Awaitable[T]) -> T:
    """Await *awaitable* with its work executed on the bridge loop.

    The async counterpart of :func:`run_sync`, for callers already
    inside an event loop (e.g. a FastAPI handler awaiting
    ``ai_manager.complete()``). The awaitable's I/O lands on the one
    loop the providers' persistent httpx clients belong to, while the
    caller's loop stays free to serve other requests.

    Code already running on the bridge loop awaits *awaitable*
    directly — submit-and-wait there would deadlock the loop, which is
    what the pass-through branch prevents. That also makes this helper
    a zero-cost wrapper on the ``run_sync`` path (worker → bridge →
    manager), where it just awaits through.
    """
    loop = _get_bridge_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:  # unreachable inside a coroutine; belt-and-braces
        running = None
    if running is loop:
        return await awaitable
    return await asyncio.wrap_future(_submit(awaitable))


def stop_bridge_loop() -> None:
    """Stop, drain and close the bridge loop.

    Safe to call twice, and safe when the bridge was never started.
    The loop thread is a daemon, so process exit alone never runs
    ``_serve``'s ``finally`` cleanup — this is the only orderly
    teardown path (test fixtures, graceful shutdown).
    """
    global _bridge_loop, _bridge_thread
    with _bridge_lock:
        loop, thread = _bridge_loop, _bridge_thread
        _bridge_loop = _bridge_thread = None
    if loop is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)


def run_sync(coro: Any) -> T:
    """Run *coro* to completion on the shared bridge loop, return its result.

    Use from sync code that has no running loop (RQ workers,
    ``to_thread``/``run_in_executor`` worker threads)::

        resp = run_sync(ai_manager.complete(prompt=p, system=s))

    Blocks the calling thread until the bridge loop finishes the
    coroutine; exceptions raised inside it propagate here unchanged
    (so ``ProviderUnavailable`` etc. behave exactly as when they were
    ``await``ed).

    Raises ``RuntimeError`` if called from inside a running loop —
    that call site should be ``await``-ing instead. (The bridge's own
    loop counts, with its own message: awaiting from code already
    running on the bridge loop via ``run_sync`` would deadlock it.)
    """
    if not asyncio.iscoroutine(coro) and not hasattr(coro, "__await__"):
        return coro  # type: ignore[return-value]

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        if hasattr(coro, "close"):
            coro.close()
        if running is _bridge_loop:
            raise RuntimeError(
                "run_sync() called from the bridge loop itself; "
                "await the coroutine directly instead."
            )
        raise RuntimeError(
            "run_sync() called from inside a running event loop; "
            "await the coroutine directly instead."
        )
    future = _submit(coro)
    try:
        return future.result()
    except BaseException:
        # If we're being interrupted (KeyboardInterrupt while blocked)
        # the coroutine may still be running on the bridge loop —
        # cancel it so it doesn't linger holding a provider connection.
        future.cancel()
        raise


def stream_sync[T](agen: AsyncIterator[T]) -> Iterator[T]:
    """Yield items from an async iterator in a sync ``for`` loop.

    Drives the async iterator one item at a time on the shared bridge
    loop, so streaming stays incremental (chunks surface as they
    arrive) instead of being collected up front::

        for chunk in stream_sync(ai_manager.stream(prompt=p, system=s)):
            sink.write(chunk)

    The async iterator is closed (``aclose()``) even if the consumer
    abandons the loop early (``GeneratorExit``/exception).

    Must share the bridge loop with :func:`run_sync` rather than own a
    private one (the pre-2026-09-14 behavior): the async generator's
    ``httpx`` stream is bound to whichever loop the provider's
    persistent client opened its connection on, so a per-stream loop
    would reintroduce exactly the cross-loop failure this module exists
    to prevent.
    """
    try:
        while True:
            try:
                item = _submit(agen.__anext__()).result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                _submit(aclose()).result(timeout=10)
            except BaseException:  # noqa: BLE001 — best-effort cleanup
                pass
