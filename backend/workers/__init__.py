"""
Background workers (Phase 2.5).

RQ-based workers for AI analysis jobs. Start with::

    rq worker --url redis://localhost:6379/0 marketlens-workers

See ``backend.workers.ai_worker`` for a small CLI wrapper.
"""
