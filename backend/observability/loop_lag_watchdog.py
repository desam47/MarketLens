"""
Opt-in event-loop stall watchdog: names the code running when the API loop lags.

Why it exists: ``py-spy`` isn't available here, and a sleep-loop "heartbeat"
badly under-reports the stalls that matter (a bare ``asyncio.sleep`` loop showed
13 ms while real HTTP requests saw 187 ms, because a request needs dozens of GIL
handoffs and a heartbeat needs one). So instead of measuring lag, this watches
for the loop *failing to tick* and, while it is stalled, snapshots what every
thread is doing.

How it works
------------
* A tiny callback re-schedules itself on the event loop every ``tick_ms`` and
  records the time of its last run.
* A daemon thread wakes every ``sample_ms``. If the loop hasn't ticked for more
  than ``threshold_ms``, it starts sampling ``sys._current_frames()``; when the
  loop ticks again it aggregates the samples into one report and logs it under
  the ``marketlens.loop_lag`` logger.

Reading a report: a thread whose stack is identical in every sample is *blocked*
(sleeping, waiting on I/O or a lock); one whose innermost frame keeps changing is
*executing Python* — with a stalled loop, the likely GIL holder. The event-loop
thread itself is flagged ``is_event_loop``: if it is stuck in one frame, sync
code is running on the loop; if it is varying while the loop is stalled, another
thread is starving it of the GIL.

Off by default and zero-cost while off: nothing runs until ``enable()`` is called
(``POST /api/system/loop_lag_watchdog``).
"""
from __future__ import annotations

import asyncio
import collections
import logging
import sys
import threading
import time
import traceback
from datetime import UTC, datetime

logger = logging.getLogger("marketlens.loop_lag")

_PROJECT_MARKER = "/MarketLens/"
# Innermost frames that mean "parked", so a report can put busy threads first.
_IDLE_FRAMES = {
    ("selectors.py", "select"),
    ("threading.py", "wait"),
    ("threading.py", "_wait_for_tstate_lock"),
    ("threading.py", "join"),
    ("queue.py", "get"),
    ("base_events.py", "run_forever"),
    ("runners.py", "run"),
}


def _short(filename: str) -> str:
    if _PROJECT_MARKER in filename:
        return filename.split(_PROJECT_MARKER, 1)[1]
    if "site-packages/" in filename:
        return "site-packages/" + filename.split("site-packages/", 1)[1]
    return filename.rsplit("/", 1)[-1]


class LoopLagWatchdog:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        threshold_ms: float = 50.0,
        sample_ms: float = 5.0,
        tick_ms: float = 5.0,
        stack_depth: int = 8,
        max_samples_per_stall: int = 400,
        max_reports: int = 200,
    ) -> None:
        self._loop = loop
        self.threshold_ms = threshold_ms
        self._sample_s = sample_ms / 1000.0
        self._tick_s = tick_ms / 1000.0
        self._stack_depth = stack_depth
        self._max_samples = max_samples_per_stall
        self._max_reports = max_reports
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick = time.monotonic()
        self._loop_tid: int | None = None
        self.stall_count = 0
        self.reports: collections.deque[dict] = collections.deque(maxlen=50)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._last_tick = time.monotonic()
        self._loop.call_soon_threadsafe(self._tick)
        self._thread = threading.Thread(target=self._run, name="loop-lag-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --------------------------------------------------------------- the loop
    def _tick(self) -> None:  # runs ON the event loop
        self._last_tick = time.monotonic()
        self._loop_tid = threading.get_ident()
        if not self._stop.is_set():
            self._loop.call_later(self._tick_s, self._tick)

    # ------------------------------------------------------ the watcher thread
    def _run(self) -> None:
        me = threading.get_ident()
        stalled = False
        stall_from = 0.0
        samples: list[list[tuple[str, int, tuple[str, ...]]]] = []
        while not self._stop.wait(self._sample_s):
            if self._loop.is_closed():  # its loop is gone: nothing left to watch
                break
            last = self._last_tick
            lagging = (time.monotonic() - last) * 1000.0 > self.threshold_ms
            if lagging:
                if not stalled:
                    stalled, stall_from, samples = True, last, []
                if len(samples) < self._max_samples:
                    samples.append(self._snapshot(me))
            elif stalled:
                stalled = False
                self._report((self._last_tick - stall_from) * 1000.0, samples)
                samples = []

    def _snapshot(self, my_tid: int) -> list[tuple[str, int, tuple[str, ...]]]:
        names = {t.ident: t.name for t in threading.enumerate()}
        out = []
        for tid, frame in sys._current_frames().items():
            if tid == my_tid:
                continue
            stack = traceback.extract_stack(frame, limit=self._stack_depth)
            out.append((
                names.get(tid, str(tid)), tid,
                tuple(f"{_short(f.filename)}:{f.lineno} {f.name}" for f in stack),
            ))
        return out

    def _report(self, stall_ms: float, samples: list) -> None:
        if not samples or self.stall_count >= self._max_reports:  # bound log volume if it goes pathological
            return
        per_thread: dict[str, list[tuple[str, ...]]] = collections.defaultdict(list)
        tids: dict[str, int] = {}
        for snap in samples:
            for name, tid, stack in snap:
                per_thread[name].append(stack)
                tids[name] = tid
        threads = []
        for name, stacks in per_thread.items():
            innermost = collections.Counter(s[-1] if s else "?" for s in stacks)
            idle = all(
                s and (s[-1].split(":")[0].rsplit("/", 1)[-1], s[-1].rsplit(" ", 1)[-1]) in _IDLE_FRAMES
                for s in stacks
            )
            threads.append({
                "thread": name,
                "is_event_loop": tids[name] == self._loop_tid,
                "samples": len(stacks),
                "distinct_innermost_frames": len(innermost),
                "idle": idle,
                "top_stacks": [
                    {"count": n, "stack": list(st)}
                    for st, n in collections.Counter(stacks).most_common(2)
                ],
            })
        # Busy threads first: the ones whose innermost frame keeps changing are
        # executing Python (likely GIL holders); then non-idle ones stuck in place.
        threads.sort(key=lambda t: (t["idle"], -t["distinct_innermost_frames"], -t["samples"]))
        report = {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "stall_ms": round(stall_ms, 1),
            "samples": len(samples),
            "threads": threads,
        }
        self.stall_count += 1
        self.reports.append(report)
        busy = [t for t in threads if not t["idle"]][:3]
        logger.warning(
            "event loop stalled %.0f ms (%d stack samples); busiest: %s",
            stall_ms, len(samples),
            " | ".join(
                f"{t['thread']}{'[loop]' if t['is_event_loop'] else ''}"
                f" x{t['distinct_innermost_frames']}: "
                f"{' <- '.join(reversed(t['top_stacks'][0]['stack'][-3:])) if t['top_stacks'] else '?'}"
                for t in busy
            ) or "none",
        )


# ---------------------------------------------------------------- module API
_watchdog: LoopLagWatchdog | None = None
_lock = threading.Lock()


def enable(loop: asyncio.AbstractEventLoop, threshold_ms: float = 50.0) -> LoopLagWatchdog:
    """Start watching ``loop`` (idempotent: an existing watchdog just gets the new threshold)."""
    global _watchdog
    with _lock:
        if _watchdog is not None and _watchdog.running and _watchdog._loop is loop:
            _watchdog.threshold_ms = threshold_ms
            return _watchdog
        if _watchdog is not None:
            _watchdog.stop()
        _watchdog = LoopLagWatchdog(loop, threshold_ms=threshold_ms)
        _watchdog.start()
        return _watchdog


def disable() -> None:
    global _watchdog
    with _lock:
        if _watchdog is not None:
            _watchdog.stop()


def status() -> dict:
    wd = _watchdog
    return {
        "enabled": bool(wd and wd.running),
        "threshold_ms": wd.threshold_ms if wd else None,
        "stall_count": wd.stall_count if wd else 0,
        "recent_stalls": list(wd.reports) if wd else [],
    }
