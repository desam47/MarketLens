"""
Tests for backend.ai.alert_commentary.generate_commentary — Version 4,
AI feature 3.

generate_commentary() must never raise: AI-off, a missing trigger/
alert row, or a malformed AI reply all degrade to returning None
(and leaving ai_commentary untouched) rather than propagating an
exception — the caller is an RQ worker task with no one watching for
a raised exception the way a request handler would.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.ai.alert_commentary import generate_commentary
from backend.ai.provider import AIResponse


def _mock_trigger(
    id=1,
    alert_id=1,
    symbol="AAPL",
    observed_value="150.5",
    message="AAPL: price above 150.00",
    ai_commentary=None,
):
    t = MagicMock()
    t.id = id
    t.alert_id = alert_id
    t.symbol = symbol
    t.observed_value = observed_value
    t.message = message
    t.triggered_at = None
    t.ai_commentary = ai_commentary
    return t


def _mock_alert(
    id=1, name="AAPL price alert", symbol="AAPL", condition_type="price_above", parameter="150.00"
):
    a = MagicMock()
    a.id = id
    a.name = name
    a.symbol = symbol
    a.condition_type = condition_type
    a.parameter = parameter
    return a


def _make_session(trigger, alert):
    """A MagicMock SessionLocal() whose .query(Model).filter(...).first()
    chain returns `trigger` for AlertTrigger queries and `alert` for
    Alert queries, keyed by which model class .query() was called with."""
    from backend.models import Alert, AlertTrigger

    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is AlertTrigger:
            q.filter.return_value.first.return_value = trigger
        elif model is Alert:
            q.filter.return_value.first.return_value = alert
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = _query
    return db


class TestGenerateCommentary(unittest.TestCase):
    @patch("backend.ai.alert_commentary.build_context")
    @patch("backend.ai.alert_commentary.ai_manager")
    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_success_writes_commentary_to_trigger(
        self, mock_session_cls, mock_ai, mock_build_context
    ):
        trigger = _mock_trigger()
        alert = _mock_alert()
        mock_session_cls.return_value = _make_session(trigger, alert)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_build_context.return_value.compact.return_value = {"price": 150.5}
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(
                text='```json\n{"commentary": "Price crossed above the 150 threshold."}\n```',
                provider="ollama",
                model="llama3.2",
            )
        )

        result = generate_commentary(1)

        self.assertEqual(result, "Price crossed above the 150 threshold.")
        self.assertEqual(trigger.ai_commentary, "Price crossed above the 150 threshold.")

    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_missing_trigger_returns_none(self, mock_session_cls):
        mock_session_cls.return_value = _make_session(None, None)
        result = generate_commentary(999)
        self.assertIsNone(result)

    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_missing_alert_returns_none(self, mock_session_cls):
        trigger = _mock_trigger()
        mock_session_cls.return_value = _make_session(trigger, None)
        result = generate_commentary(1)
        self.assertIsNone(result)

    @patch("backend.ai.alert_commentary.ai_manager")
    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_ai_unavailable_returns_none(self, mock_session_cls, mock_ai):
        trigger = _mock_trigger()
        alert = _mock_alert()
        mock_session_cls.return_value = _make_session(trigger, alert)
        mock_ai.is_available = AsyncMock(return_value=False)

        result = generate_commentary(1)

        self.assertIsNone(result)
        self.assertIsNone(trigger.ai_commentary)

    @patch("backend.ai.alert_commentary.build_context")
    @patch("backend.ai.alert_commentary.ai_manager")
    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_malformed_ai_reply_returns_none(self, mock_session_cls, mock_ai, mock_build_context):
        trigger = _mock_trigger()
        alert = _mock_alert()
        mock_session_cls.return_value = _make_session(trigger, alert)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_build_context.return_value.compact.return_value = {}
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(
                text="not json at all",
                provider="ollama",
                model="llama3.2",
            )
        )

        result = generate_commentary(1)

        self.assertIsNone(result)
        self.assertIsNone(trigger.ai_commentary)

    @patch("backend.ai.alert_commentary.build_context")
    @patch("backend.ai.alert_commentary.ai_manager")
    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_ai_call_exception_returns_none(self, mock_session_cls, mock_ai, mock_build_context):
        trigger = _mock_trigger()
        alert = _mock_alert()
        mock_session_cls.return_value = _make_session(trigger, alert)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_build_context.return_value.compact.return_value = {}
        mock_ai.complete = AsyncMock(side_effect=RuntimeError("provider down"))

        result = generate_commentary(1)

        self.assertIsNone(result)

    @patch("backend.ai.alert_commentary.ai_manager")
    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_build_context_failure_still_attempts_commentary(self, mock_session_cls, mock_ai):
        """InsufficientDataError (or any context-building failure)
        shouldn't give up entirely — fall back to alert/trigger facts
        alone rather than skipping the AI call."""
        trigger = _mock_trigger()
        alert = _mock_alert()
        mock_session_cls.return_value = _make_session(trigger, alert)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(
                text='```json\n{"commentary": "Fired on alert facts alone."}\n```',
                provider="ollama",
                model="llama3.2",
            )
        )

        with patch(
            "backend.ai.alert_commentary.build_context",
            side_effect=RuntimeError("no data"),
        ):
            result = generate_commentary(1)

        self.assertEqual(result, "Fired on alert facts alone.")

    @patch("backend.ai.alert_commentary.build_context")
    @patch("backend.ai.alert_commentary.ai_manager")
    @patch("backend.ai.alert_commentary.SessionLocal")
    def test_skips_news_and_fundamentals_for_speed(
        self, mock_session_cls, mock_ai, mock_build_context
    ):
        """Commentary explains *why this condition fired*, not a full
        research brief — news/fundamentals are the two extra-I/O
        sections build_context() can skip."""
        trigger = _mock_trigger()
        alert = _mock_alert()
        mock_session_cls.return_value = _make_session(trigger, alert)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_build_context.return_value.compact.return_value = {}
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(
                text='```json\n{"commentary": "ok"}\n```',
                provider="ollama",
                model="llama3.2",
            )
        )

        generate_commentary(1)

        mock_build_context.assert_called_once_with(
            "AAPL",
            include_news=False,
            include_fundamentals=False,
        )


if __name__ == "__main__":
    unittest.main()
