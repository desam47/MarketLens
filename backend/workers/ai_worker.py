"""
Background AI worker entry point (Phase 2.5).

Run with::

    rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-workers

``--worker-class rq.worker.SimpleWorker`` is NOT optional — see
``backend/workers/backfill_worker.py``'s module docstring for why: RQ's
default ``Worker`` forks a child process per job, and this project's
webull provider SDK (touched by AI analysis too, via market-data context)
reproducibly segfaults the forked child. ``SimpleWorker`` runs jobs in the
worker's own process instead, which sidesteps it.

Or, if you have the venv active and want a one-shot invocation::

    python -m backend.workers.ai_worker --once

For a long-lived worker process, the RQ CLI is preferred. This file is
mostly here so the project has a documented entry point.
"""

import argparse
import logging
import sys

from backend.ai.background import get_queue

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MarketLens AI background worker",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process a single job then exit (for tests).",
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Process all currently-queued jobs then exit.",
    )
    args = parser.parse_args()

    queue = get_queue()
    if queue is None:
        logger.error(
            "Redis is not available. Set REDIS_ENABLED=true and verify "
            "REDIS_URL points at a running Redis instance."
        )
        return 1

    # SimpleWorker (no fork-per-job) — see the module docstring for why
    # this is required, not just a style choice, in this repo.
    from rq import SimpleWorker

    if args.once:
        # Process a single job then exit. RQ's Queue has no
        # dequeue-and-run-one primitive in the installed version (2.x) —
        # the old `queue.dequeue()` here raised AttributeError on every
        # call (found 2026-09-08, alongside the identical bug in the
        # then-new backfill_worker.py, which copied this file as its
        # starting point). A Worker/SimpleWorker with max_jobs=1 in burst
        # mode is the real equivalent.
        worker = SimpleWorker([queue], connection=queue.connection)
        worker.work(burst=True, max_jobs=1)
        return 0

    if args.burst:
        worker = SimpleWorker([queue], connection=queue.connection)
        worker.work(burst=True)
        return 0

    # Default: print help — the actual worker should be started via
    # the RQ CLI, which gives access to all RQ features (signals, etc.).
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
