"""
Offline tests for backend.ai.background.

Covers the seams that do NOT require a live Redis / RQ worker / DB:

  * ``_iso`` — pure ISO formatting helper.
  * The graceful-degradation contract: when Redis or background processing
    is disabled in settings, every enqueue/status helper short-circuits to
    ``None`` (or an empty result) instead of raising. The ``*_job`` helpers
    return ``None`` before any DB access, so these tests never touch SQLite.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.ai import background
from backend.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_background_cache():
    """Each test starts with a clean connection/queue cache."""
    background.reset_for_tests()
    yield
    background.reset_for_tests()


class TestIsoHelper:
    def test_none_returns_none(self):
        assert background._iso(None) is None

    def test_datetime_gets_z_suffix(self):
        dt = datetime(2026, 9, 1, 12, 30, 0, tzinfo=UTC)
        assert background._iso(dt) == "2026-09-01T12:30:00+00:00Z"


class TestGracefulDegradation:
    def test_enqueue_binds_rq_job_id_to_worker_status_updates(self):
        queue = MagicMock()
        queue.enqueue.return_value.id = "rq-id-is-overridden-by-requested-id"
        db = MagicMock()

        with (
            patch.object(background, "get_queue", return_value=queue),
            patch("backend.database.SessionLocal", return_value=db),
        ):
            job_id = background.enqueue_analyze_job("aapl", timeframe="4h")

        enqueue_kwargs = queue.enqueue.call_args.kwargs
        assert job_id == enqueue_kwargs["job_id"]
        assert enqueue_kwargs["kwargs"]["job_id"] == job_id
        record = db.add.call_args.args[0]
        assert record.job_id == job_id

    def test_get_redis_none_when_disabled(self):
        with patch.object(settings.redis, "enabled", False):
            assert background.get_redis() is None

    def test_get_queue_none_when_background_disabled(self):
        with patch.object(settings.background, "enabled", False):
            assert background.get_queue() is None

    def test_enqueue_analyze_job_none_when_queue_unavailable(self):
        # Queue is None (background processing disabled) -> no DB access
        # -> returns None.
        with patch.object(settings.background, "enabled", False):
            assert background.get_queue() is None
            assert background.enqueue_analyze_job("aapl") is None

    def test_enqueue_alert_commentary_job_none_when_queue_unavailable(self):
        with patch.object(settings.background, "enabled", False):
            assert background.get_queue() is None
            assert background.enqueue_alert_commentary_job(123) is None

    def test_safe_rq_status_none_when_redis_unavailable(self):
        # No Redis client cached, and settings.redis.enabled False so it
        # won't try to connect -> returns None rather than raising.
        with patch.object(settings.redis, "enabled", False):
            assert background._safe_rq_status("some-job-id") is None
