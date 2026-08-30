"""
Background AI worker entry point (Phase 2.5).

Run with::

    rq worker --url redis://localhost:6379/0 marketlens-workers

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

    if args.once:
        # Process a single job synchronously.
        job = queue.dequeue()
        if job is None:
            logger.info("No jobs in queue")
            return 0
        job.perform()
        return 0

    if args.burst:
        from rq import Worker
        worker = Worker([queue], connection=queue.connection)
        worker.work(burst=True)
        return 0

    # Default: print help — the actual worker should be started via
    # the RQ CLI, which gives access to all RQ features (signals, etc.).
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
