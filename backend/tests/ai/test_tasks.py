"""
Offline tests for backend.ai.tasks.

The task functions are thin orchestration over ``analyze_symbol``, the AI
template router, the DB, and the alert-commentary generator. All of those
are mocked so these tests exercise the task wiring (DB-status transitions,
direct-vs-job paths, serialization shape) without a Redis worker, a live
DB, or a real AI provider.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.ai import tasks


def _fake_analysis_result() -> MagicMock:
    """Build a stand-in for what ``analyze_symbol`` returns."""
    result = MagicMock()
    result.summary = "summary text"
    result.trend = "bullish"
    result.confidence = 0.7
    result.supporting_factors = ["a", "b"]
    result.risk_factors = ["c"]
    result.timeframe_conflicts = []
    result.key_levels = [{"price": 100.0}]
    result.trade_plan = None
    result.provider = "test-provider"
    result.model = "test-model"
    return result


@pytest.fixture(autouse=True)
def _no_real_db():
    """Stub the DB session the tasks open via SessionLocal."""
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    with patch("backend.ai.tasks.SessionLocal", return_value=fake_db):
        yield


class TestAnalyzeSymbolTask:
    def test_run_direct_path_without_job_id(self):
        """No job_id -> _run_direct, no DB status writes, returns dict."""
        with patch("backend.ai.tasks.analyze_symbol", return_value=_fake_analysis_result()):
            payload = tasks.analyze_symbol_task("aapl", "1d")
        assert payload["summary"] == "summary text"
        assert payload["trend"] == "bullish"
        assert payload["confidence"] == 0.7
        assert payload["supporting_factors"] == ["a", "b"]
        assert payload["key_levels"] == [{"price": 100.0}]
        assert payload["provider"] == "test-provider"
        assert payload["is_uncertain"] is False
        assert payload["template_id"] is None

    def test_run_direct_path_serializes_trade_plan_when_present(self):
        result = _fake_analysis_result()
        plan = MagicMock()
        plan.model_dump.return_value = {"entry": 100}
        result.trade_plan = plan
        with patch("backend.ai.tasks.analyze_symbol", return_value=result):
            payload = tasks.analyze_symbol_task("aapl", "1d")
        assert payload["trade_plan"] == {"entry": 100}

    def test_job_path_marks_started_then_finished(self):
        """With a job_id the task writes started -> finished status rows."""
        captured = {}

        def fake_update(job_id, status, result=None, error=None):
            captured.setdefault("statuses", []).append(status)
            captured["last_result"] = result

        with (
            patch("backend.ai.tasks.analyze_symbol", return_value=_fake_analysis_result()),
            patch("backend.ai.tasks._update_status", side_effect=fake_update),
        ):
            payload = tasks.analyze_symbol_task("aapl", "1d", job_id="job-1")
        assert captured["statuses"][0] == "started"
        assert captured["statuses"][-1] == "finished"
        assert payload["is_uncertain"] is False

    def test_template_resolution_failure_marks_failed_and_reraises(self):
        captured = {}

        def fake_update(job_id, status, result=None, error=None):
            captured[status] = error

        with (
            patch(
                "backend.ai.tasks.resolve_and_render",
                side_effect=ValueError("template missing"),
            ),
            patch("backend.ai.tasks._update_status", side_effect=fake_update),
            pytest.raises(ValueError),
        ):
            tasks.analyze_symbol_task("aapl", "1d", template_id=999, job_id="job-2")
        assert "template missing" in captured["failed"]

    def test_analysis_failure_marks_failed_and_reraises(self):
        captured = {}

        def fake_update(job_id, status, result=None, error=None):
            captured[status] = error

        with (
            patch(
                "backend.ai.tasks.analyze_symbol",
                side_effect=RuntimeError("provider down"),
            ),
            patch("backend.ai.tasks._update_status", side_effect=fake_update),
            pytest.raises(RuntimeError),
        ):
            tasks.analyze_symbol_task("aapl", "1d", job_id="job-3")
        assert "provider down" in captured["failed"]


class TestAlertCommentaryTask:
    def test_delegates_to_generate_commentary(self):
        with patch("backend.ai.alert_commentary.generate_commentary") as mock_gen:
            tasks.generate_alert_commentary_task(trigger_id=42)
        mock_gen.assert_called_once_with(42)


class TestUpdateStatus:
    def test_noop_when_record_missing(self):
        fake_db = MagicMock()
        fake_db.query.return_value.filter.return_value.first.return_value = None
        with patch("backend.ai.tasks.SessionLocal", return_value=fake_db):
            # Should not raise even though the record isn't found.
            tasks._update_status("missing-job", "finished", result={"a": 1})
        fake_db.commit.assert_not_called()
