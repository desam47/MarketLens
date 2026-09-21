"""
Background symbol-history backfill worker entry point.

Mirrors ``backend/workers/ai_worker.py`` for the separate backfill queue —
run TWO worker processes to reproduce the old ``asyncio.Semaphore(2)``
concurrency cap (see ``backend/market_data/services/backfill_service.py``'s
module docstring for why that semaphore no longer lives in-process).

Run with::

    rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-backfill
    rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-backfill   # a 2nd instance

``--worker-class rq.worker.SimpleWorker`` is NOT optional here — RQ's
default ``Worker`` forks a child process per job, and this project's
webull provider SDK (``webull_trade_sdk``, imported transitively by the
backfill pipeline for its default primary/fallback provider) does
non-trivial work at import/client-init time (token management, HTTP
client setup) that reproducibly segfaults the forked child (confirmed
live, 2026-09-08: ``Work-horse terminated unexpectedly; waitpid returned
11 (signal 11)`` on the very first real job, immediately, before any of
this module's own code even ran) — almost certainly a fork-safety issue
in a native/threading component of that SDK, not a bug in this pipeline
(the identical job runs to completion without any issue called directly
in-process, no fork involved). ``SimpleWorker`` runs jobs in the worker's
own process instead of forking, which sidesteps it entirely — verified
live as the fix (same job, same worker code, no crash). This same
exposure applies to ``ai_worker.py`` too since AI analysis also touches
market-data providers; both worker entry points and every documented
``rq worker`` invocation in this repo use ``--worker-class`` /
``SimpleWorker`` for this reason.

Or, if you have the venv active and want a one-shot invocation::

    python -m backend.workers.backfill_worker --once

For a long-lived worker process, the RQ CLI is preferred. This file is
mostly here so the project has a documented entry point.
"""

import argparse
import logging
import sys

from backend.market_data.services.backfill_queue import get_backfill_queue

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MarketLens symbol-history backfill worker",
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

    queue = get_backfill_queue()
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
        # a Worker with max_jobs=1 in burst mode is the equivalent.
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
