"""
Tests for the Alerts API endpoints.
"""
import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_alert(**kwargs):
    """Factory for a mock Alert model."""
    defaults = dict(
        id=1, name="Test Alert", symbol="AAPL",
        condition_type="signal_equals", parameter="RSI_OVERSOLD",
        is_enabled=True,
        created_at=datetime(2025, 1, 1, 12, 0, 0),
        updated_at=datetime(2025, 1, 1, 12, 0, 0),
    )
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _mock_trigger(**kwargs):
    defaults = dict(
        id=1, alert_id=1, symbol="AAPL",
        observed_value="['RSI_OVERSOLD']",
        message="AAPL: RSI_OVERSOLD signal detected",
        triggered_at=datetime(2025, 1, 1, 12, 0, 0),
        # Version 4 AI feature 3 — must default to a real None, not an
        # auto-generated MagicMock attribute (which fails
        # AlertTriggerResponse's `str | None` validation).
        ai_commentary=None,
    )
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


class TestAlertsAPI(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        # Prevent engine startup from hitting the DB.
        self.engine_startup_patch = patch(
            "backend.alerts.engine.AlertsEngine.startup",
            return_value=None,
        )
        self.engine_startup_patch.start()
        self.addCleanup(self.engine_startup_patch.stop)

        # Patch the engine instance used in the router.
        self.engine_patch = patch("backend.api.alerts.router.alerts_engine")
        self.mock_engine = self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)

    # --- GET /api/alerts/ -----------------------------------------------

    def test_list_alerts_empty(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_all.return_value = []
            response = self.client.get("/api/alerts/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_list_alerts_returns_all(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_all.return_value = [
                _mock_alert(id=1, name="AAPL Alert"),
                _mock_alert(id=2, name="TSLA Alert"),
            ]
            response = self.client.get("/api/alerts/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["name"], "AAPL Alert")

    # --- POST /api/alerts/ ----------------------------------------------

    def test_create_alert_success(self):
        created = _mock_alert(id=1, name="New Alert", symbol="TSLA",
                              condition_type="price_above", parameter="200.0")
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.create.return_value = created
            response = self.client.post("/api/alerts/", json={
                "name": "New Alert",
                "symbol": "TSLA",
                "condition_type": "price_above",
                "parameter": "200.0",
            })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "New Alert")
        self.mock_engine.register_for_alert.assert_called_once()

    def test_create_alert_invalid_condition_type(self):
        response = self.client.post("/api/alerts/", json={
            "name": "Bad",
            "symbol": "AAPL",
            "condition_type": "not_a_real_condition",
            "parameter": "100",
        })
        self.assertEqual(response.status_code, 422)

    # --- GET /api/alerts/active -----------------------------------------

    def test_active_triggers(self):
        now = datetime.now(UTC)
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_recent_triggers.return_value = [
                _mock_trigger(id=1, symbol="AAPL", triggered_at=now),
                _mock_trigger(id=2, symbol="TSLA", triggered_at=now),
            ]
            response = self.client.get("/api/alerts/active")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["symbol"], "AAPL")

    def test_active_triggers_serializes_ai_commentary(self):
        """Version 4, AI feature 3: ai_commentary rides along on the
        existing trigger response — None when not yet generated,
        the real string once the async job has written it."""
        now = datetime.now(UTC)
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_recent_triggers.return_value = [
                _mock_trigger(id=1, symbol="AAPL", triggered_at=now, ai_commentary=None),
                _mock_trigger(
                    id=2, symbol="TSLA", triggered_at=now,
                    ai_commentary="TSLA crossed above its 50-day average on rising volume.",
                ),
            ]
            response = self.client.get("/api/alerts/active")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data[0]["ai_commentary"])
        self.assertEqual(
            data[1]["ai_commentary"],
            "TSLA crossed above its 50-day average on rising volume.",
        )

    # --- GET /api/alerts/{id} -------------------------------------------

    def test_get_alert_found(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id.return_value = _mock_alert(
                id=5, name="Found It"
            )
            response = self.client.get("/api/alerts/5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Found It")

    def test_get_alert_not_found(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id.return_value = None
            response = self.client.get("/api/alerts/999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Alert not found")

    # --- PUT /api/alerts/{id} -------------------------------------------

    def test_update_alert_enable_to_disable_triggers_unregister(self):
        existing = _mock_alert(id=1, is_enabled=True,
                              condition_type="price_above", parameter="100")
        updated = _mock_alert(id=1, is_enabled=False,
                              condition_type="price_above", parameter="100")
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.get_by_id.return_value = existing
            instance.update.return_value = updated
            response = self.client.put("/api/alerts/1", json={"is_enabled": False})
        self.assertEqual(response.status_code, 200)
        self.mock_engine.unregister_for_alert.assert_called_once()
        self.mock_engine.register_for_alert.assert_not_called()

    def test_update_alert_disable_to_enable_triggers_register(self):
        existing = _mock_alert(id=1, is_enabled=False,
                              condition_type="price_above", parameter="100")
        updated = _mock_alert(id=1, is_enabled=True,
                              condition_type="price_above", parameter="100")
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.get_by_id.return_value = existing
            instance.update.return_value = updated
            response = self.client.put("/api/alerts/1", json={"is_enabled": True})
        self.assertEqual(response.status_code, 200)
        self.mock_engine.register_for_alert.assert_called_once()
        self.mock_engine.unregister_for_alert.assert_not_called()

    def test_update_alert_not_found(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id.return_value = None
            response = self.client.put("/api/alerts/999", json={"name": "New Name"})
        self.assertEqual(response.status_code, 404)

    # --- DELETE /api/alerts/{id} -----------------------------------------

    def test_delete_alert(self):
        existing = _mock_alert(id=3, condition_type="price_above")
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id.return_value = existing
            MockRepo.return_value.delete.return_value = True
            response = self.client.delete("/api/alerts/3")
        self.assertEqual(response.status_code, 204)
        self.mock_engine.unregister_for_alert.assert_called_once()

    def test_delete_alert_not_found(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id.return_value = None
            response = self.client.delete("/api/alerts/999")
        self.assertEqual(response.status_code, 404)

    # --- DELETE /api/alerts/triggers --------------------------------------

    def test_clear_triggers(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.delete_all_triggers.return_value = 3
            response = self.client.delete("/api/alerts/triggers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 3})

    def test_clear_triggers_when_none_exist(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.delete_all_triggers.return_value = 0
            response = self.client.delete("/api/alerts/triggers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 0})

    # --- GET /api/alerts/{id}/triggers -----------------------------------

    def test_get_alert_triggers(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.get_by_id.return_value = _mock_alert(id=1)
            instance.get_triggers.return_value = [
                _mock_trigger(id=1),
                _mock_trigger(id=2),
            ]
            response = self.client.get("/api/alerts/1/triggers")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)

    def test_get_alert_triggers_alert_not_found(self):
        with patch("backend.api.alerts.router.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id.return_value = None
            response = self.client.get("/api/alerts/999/triggers")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
