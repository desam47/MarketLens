"""
Unit tests for AlertsEngine.
"""
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from backend.alerts.engine import AlertsEngine, DEDUP_WINDOW_SECONDS


def _make_alert(
    id: int = 1,
    name: str = "Test Alert",
    symbol: str = "AAPL",
    condition_type: str = "signal_equals",
    parameter: str = "RSI_OVERSOLD",
    is_enabled: bool = True,
):
    alert = MagicMock()
    alert.id = id
    alert.name = name
    alert.symbol = symbol
    alert.condition_type = condition_type
    alert.parameter = parameter
    alert.is_enabled = is_enabled
    return alert


def _make_result(symbol: str = "AAPL", signals: list[str] | None = None, price: float = 150.0):
    result = MagicMock()
    result.symbol = symbol
    result.signals = signals or []
    result.price = price
    return result


class TestAlertsEngineConditions(unittest.TestCase):
    """Test _try_fire through signal_equals path."""

    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True
        self.engine._alerts_cache = {}

    def _add_alert(self, alert):
        self.engine._alerts_cache[alert.id] = alert

    def test_signal_equals_fires(self):
        """Alert fires when its signal is in the result."""
        self._add_alert(_make_alert(
            condition_type="signal_equals",
            parameter="RSI_OVERSOLD",
        ))
        result = _make_result(signals=["RSI_OVERSOLD", "MACD_BULLISH"])

        with patch.object(self.engine, "_persist_trigger") as mock_persist:
            fired = self.engine._try_fire(
                _make_alert(condition_type="signal_equals", parameter="RSI_OVERSOLD"),
                price=150.0,
                extra_value=result.signals,
            )
        self.assertTrue(fired)

    def test_signal_equals_no_fire_wrong_signal(self):
        alert = _make_alert(condition_type="signal_equals", parameter="RSI_OVERBOUGHT")
        result = _make_result(signals=["RSI_OVERSOLD"])
        fired = self.engine._try_fire(alert, 150.0, extra_value=result.signals)
        self.assertFalse(fired)

    def test_price_above_fires(self):
        alert = _make_alert(condition_type="price_above", parameter="100.0")
        fired = self.engine._try_fire(alert, 150.0, extra_value=150.0)
        self.assertTrue(fired)

    def test_price_below_fires(self):
        alert = _make_alert(condition_type="price_below", parameter="200.0")
        fired = self.engine._try_fire(alert, 150.0, extra_value=150.0)
        self.assertTrue(fired)

    def test_pct_change_above_fires(self):
        alert = _make_alert(condition_type="pct_change_above", parameter="3.0")
        fired = self.engine._try_fire(alert, 5.0, extra_value=5.0)
        self.assertTrue(fired)

    def test_unknown_condition_type_no_crash(self):
        alert = _make_alert(condition_type="unknown_condition", parameter="foo")
        # Should not raise, just return False
        fired = self.engine._try_fire(alert, 100.0, extra_value=100.0)
        self.assertFalse(fired)


class TestAlertsEngineDedup(unittest.TestCase):
    """Test the 1-hour dedup window."""

    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True

    def test_same_alert_fires_once_then_suppressed(self):
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        self.engine._alerts_cache[1] = alert
        self.engine._fired_at[(1, "AAPL")] = time.time()  # fired just now

        fired = self.engine._try_fire(alert, 150.0, extra_value=["RSI_OVERSOLD"])
        self.assertFalse(fired)

    def test_different_alert_bypasses_dedup(self):
        alert1 = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        alert2 = _make_alert(id=2, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        self.engine._fired_at[(1, "AAPL")] = time.time()  # alert 1 recently fired

        with patch.object(self.engine, "_persist_trigger"):
            fired1 = self.engine._try_fire(alert1, 150.0, extra_value=["RSI_OVERSOLD"])
            fired2 = self.engine._try_fire(alert2, 150.0, extra_value=["RSI_OVERSOLD"])

        self.assertFalse(fired1)   # alert 1 deduped
        self.assertTrue(fired2)    # alert 2 is different

    def test_dedup_window_expired(self):
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        self.engine._fired_at[(1, "AAPL")] = time.time() - DEDUP_WINDOW_SECONDS - 1

        with patch.object(self.engine, "_persist_trigger"):
            fired = self.engine._try_fire(alert, 150.0, extra_value=["RSI_OVERSOLD"])

        self.assertTrue(fired)


class TestAlertsEngineStartup(unittest.TestCase):
    """Test _reload and callback registration."""

    def test_reload_registers_price_callbacks(self):
        engine = AlertsEngine()

        with patch("backend.alerts.engine.SessionLocal") as mock_session_cls, \
             patch("backend.alerts.engine.engine_registry") as mock_registry, \
             patch.object(engine, "_alerts_cache", {}), \
             patch.object(engine, "_price_alert_ids", {}):

            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = [
                _make_alert(id=1, condition_type="price_above", parameter="100"),
                _make_alert(id=2, condition_type="signal_equals", parameter="RSI_OVERSOLD"),
            ]

            engine._started = True
            engine._reload()

            # price_above → registered, signal_equals → not registered
            mock_registry.register.assert_called_once_with(
                "quote", "AAPL", engine._on_quote
            )

    def test_startup_idempotent(self):
        engine = AlertsEngine()

        with patch("backend.alerts.engine.SessionLocal") as mock_session_cls, \
             patch("backend.alerts.engine.engine_registry"):

            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []

            engine.startup()  # first call
            engine.startup()  # second call — should just reload, not crash
            engine._started = True
            engine._reload()  # explicit reload while already started


class TestAlertsEngineRegisterUnregister(unittest.TestCase):
    """Test dynamic registration/unregistration of alerts."""

    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True

    def test_register_for_alert_price_registers_callback(self):
        alert = _make_alert(id=1, condition_type="price_above", parameter="100")
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.register_for_alert(alert)
            mock_registry.register.assert_called_once_with(
                "quote", "AAPL", self.engine._on_quote
            )

    def test_register_for_alert_signal_equals_no_callback(self):
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.register_for_alert(alert)
            mock_registry.register.assert_not_called()

    def test_unregister_for_alert_removes_callback(self):
        alert = _make_alert(id=1, condition_type="price_above", parameter="100")
        self.engine._price_alert_ids["AAPL"].append(1)
        self.engine._alerts_cache[1] = alert
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.unregister_for_alert(alert)
            mock_registry.unregister.assert_called_once_with(
                "quote", "AAPL", self.engine._on_quote
            )


class TestEvaluateScanResult(unittest.TestCase):
    """Test the signal-based evaluation path."""

    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True
        self.engine._alerts_cache = {}
        self.engine._price_alert_ids = {}

    def test_evaluates_only_signal_equals_for_symbol(self):
        self.engine._alerts_cache[1] = _make_alert(
            id=1, symbol="AAPL", condition_type="signal_equals",
            parameter="RSI_OVERSOLD", is_enabled=True,
        )
        self.engine._alerts_cache[2] = _make_alert(
            id=2, symbol="TSLA", condition_type="signal_equals",
            parameter="RSI_OVERSOLD", is_enabled=True,
        )
        self.engine._alerts_cache[3] = _make_alert(
            id=3, symbol="AAPL", condition_type="price_above",
            parameter="100", is_enabled=True,
        )

        result = _make_result(symbol="AAPL", signals=["RSI_OVERSOLD", "MACD_BULLISH"])

        with patch.object(self.engine, "_try_fire") as mock_fire:
            self.engine.evaluate_scan_result(result)
            # Only alert 1 (AAPL signal_equals) should be checked
            fired_alert_ids = [call[0][0].id for call in mock_fire.call_args_list]
            self.assertIn(1, fired_alert_ids)
            self.assertNotIn(2, fired_alert_ids)
            self.assertNotIn(3, fired_alert_ids)

    def test_disabled_alert_not_evaluated(self):
        self.engine._alerts_cache[1] = _make_alert(
            id=1, symbol="AAPL", condition_type="signal_equals",
            parameter="RSI_OVERSOLD", is_enabled=False,
        )
        result = _make_result(symbol="AAPL", signals=["RSI_OVERSOLD"])
        with patch.object(self.engine, "_try_fire") as mock_fire:
            self.engine.evaluate_scan_result(result)
            mock_fire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
