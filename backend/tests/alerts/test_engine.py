"""
Unit tests for AlertsEngine.
"""

import json
import os
import sys
import time
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from backend.alerts.engine import (
    AUX_CONDITIONS,
    BAR_CONDITIONS,
    DEDUP_WINDOW_SECONDS,
    PRICE_CONDITIONS,
    AlertsEngine,
)


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


class TestConditionGroupConstants(unittest.TestCase):
    """PRICE_CONDITIONS / BAR_CONDITIONS were hoisted to module level
    2026-09-11 after a bug where the same tuples, redefined locally in
    three methods, could (and once did — see register_for_alert's
    signal_equals comment) silently drift out of sync. Guard that every
    VALID_CONDITION_TYPES entry other than signal_equals lands in
    exactly one of these two groups."""

    def test_price_and_bar_conditions_are_disjoint(self):
        self.assertEqual(set(PRICE_CONDITIONS) & set(BAR_CONDITIONS), set())

    def test_every_non_signal_condition_type_is_covered(self):
        from backend.alerts.conditions import VALID_CONDITION_TYPES

        covered = (
            set(PRICE_CONDITIONS) | set(BAR_CONDITIONS) | set(AUX_CONDITIONS) | {"signal_equals"}
        )
        self.assertEqual(set(VALID_CONDITION_TYPES), covered)


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
        self._add_alert(
            _make_alert(
                condition_type="signal_equals",
                parameter="RSI_OVERSOLD",
            )
        )
        result = _make_result(signals=["RSI_OVERSOLD", "MACD_BULLISH"])

        with patch.object(self.engine, "_persist_trigger"):
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

    def test_signal_profile_fires_and_respects_snooze(self):
        profile = {
            "direction": "bullish",
            "min_score": 70,
            "min_strength": 0.7,
            "market_regime": "risk_on",
            "timeframe": "1d",
            "cooldown_minutes": 10,
        }
        alert = _make_alert(condition_type="signal_profile", parameter=json.dumps(profile))
        value = {
            "current": 80,
            "current_direction": "bullish",
            "strength": 0.8,
            "current_regime": "risk_on",
            "timeframe": "1d",
        }
        with patch.object(self.engine, "_persist_trigger"):
            self.assertTrue(self.engine._try_fire(alert, 150.0, extra_value=value))

        profile["snoozed_until"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        snoozed = _make_alert(condition_type="signal_profile", parameter=json.dumps(profile), id=2)
        self.assertFalse(self.engine._try_fire(snoozed, 150.0, extra_value=value))

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

        self.assertFalse(fired1)  # alert 1 deduped
        self.assertTrue(fired2)  # alert 2 is different

    def test_dedup_window_expired(self):
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        self.engine._fired_at[(1, "AAPL")] = time.time() - DEDUP_WINDOW_SECONDS - 1

        with patch.object(self.engine, "_persist_trigger"):
            fired = self.engine._try_fire(alert, 150.0, extra_value=["RSI_OVERSOLD"])

        self.assertTrue(fired)


class TestAuxiliaryAlertBaselines(unittest.TestCase):
    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True

    def test_news_baseline_then_new_headline(self):
        item = SimpleNamespace(
            timestamp=datetime(2026, 9, 20, tzinfo=UTC),
            headline="First headline",
            url="https://example.test/1",
            relevance=0.7,
        )
        newer = SimpleNamespace(
            timestamp=datetime(2026, 9, 21, tzinfo=UTC),
            headline="New headline",
            url="https://example.test/2",
            relevance=0.9,
        )
        with patch("backend.aux_data.services.manager.aux_data_manager") as manager:
            manager.get_news.side_effect = [
                SimpleNamespace(items=[item]),
                SimpleNamespace(items=[newer, item]),
            ]
            self.assertIsNone(self.engine._build_aux_payload("news_arrival", "AAPL"))
            payload = self.engine._build_aux_payload("news_arrival", "AAPL")
        self.assertEqual(payload["new_count"], 1)
        self.assertEqual(payload["headline"], "New headline")

    def test_insider_baseline_does_not_fire(self):
        rows = [
            SimpleNamespace(year=2026, month=8, sentiment=-0.1),
            SimpleNamespace(year=2026, month=9, sentiment=0.4),
        ]
        with patch("backend.market_data.services.finnhub_service.finnhub_service") as service:
            service.get_insider_sentiment.return_value = rows
            self.assertIsNone(self.engine._build_aux_payload("insider_sentiment_change", "AAPL"))


class TestAlertsEngineQuoteDispatchIntegration(unittest.TestCase):
    """End-to-end regression for the 2026-09-09 incident: EngineRegistry.
    dispatch_quote() never passed ``symbol`` through to callbacks, so
    AlertsEngine._on_quote(self, symbol, price, ...) crashed on every
    live quote tick with "missing 1 required positional argument:
    'symbol'" — silently swallowed by dispatch_quote's own except block,
    meaning every price-based alert (price_above/price_below/
    pct_change_above) never actually fired from live quotes.

    test_engine_seeding.py's TestEngineRegistry.test_callback_receives_symbol
    already regression-tests the dispatch layer in isolation (a generic
    ``**kw``-capturing callback). This test wires the REAL AlertsEngine
    callback through a REAL EngineRegistry and confirms an alert fires,
    closing the loop the review flagged as worth verifying end-to-end
    rather than trusting the dispatch-layer test alone.
    """

    def test_price_above_alert_fires_from_dispatched_quote(self):
        from backend.market_data.services.engine_seeder import EngineRegistry

        registry = EngineRegistry()
        engine = AlertsEngine()
        engine._started = True
        alert = _make_alert(id=1, condition_type="price_above", parameter="100.0")
        engine._alerts_cache = {1: alert}
        engine._price_alert_ids["AAPL"] = [1]

        registry.register("quote", "AAPL", engine._on_quote)

        with patch.object(engine, "_persist_trigger") as mock_persist:
            notified = registry.dispatch_quote(
                symbol="AAPL",
                price=150.0,
                volume=1000,
                timestamp=time.time(),
            )

        self.assertEqual(notified, 1, "AlertsEngine callback should have been invoked")
        mock_persist.assert_called_once()
        self.assertIn((1, "AAPL"), engine._fired_at)


class TestAlertsEngineStartup(unittest.TestCase):
    """Test _reload and callback registration."""

    def test_reload_registers_price_callbacks(self):
        engine = AlertsEngine()

        with (
            patch("backend.alerts.engine.SessionLocal") as mock_session_cls,
            patch("backend.alerts.engine.engine_registry") as mock_registry,
            patch.object(engine, "_alerts_cache", {}),
            patch.object(engine, "_price_alert_ids", {}),
        ):
            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = [
                _make_alert(id=1, condition_type="price_above", parameter="100"),
                _make_alert(id=2, condition_type="signal_equals", parameter="RSI_OVERSOLD"),
            ]

            engine._started = True
            engine._reload()

            # price_above → registered, signal_equals → not registered
            mock_registry.register.assert_called_once_with("quote", "AAPL", engine._on_quote)

    def test_reload_registers_bar_callbacks(self):
        engine = AlertsEngine()
        from collections import defaultdict

        engine._bar_alert_ids = defaultdict(list)

        with (
            patch("backend.alerts.engine.SessionLocal") as mock_session_cls,
            patch("backend.alerts.engine.engine_registry") as mock_registry,
        ):
            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = [
                _make_alert(id=1, condition_type="breakout", parameter="20"),
                _make_alert(id=2, condition_type="volume_expansion", parameter="2.0"),
            ]
            engine._started = True
            engine._reload()
            # Both AAPL alerts are bar-based — one register call is enough
            # because the symbol is the same. Confirm at least one bar
            # registration was made.
            bar_calls = [c for c in mock_registry.register.call_args_list if c[0][0] == "bar"]
            self.assertEqual(len(bar_calls), 1)
            self.assertEqual(bar_calls[0][0][1], "AAPL")
            self.assertEqual(bar_calls[0][0][2], engine._on_bar)

    def test_startup_idempotent(self):
        engine = AlertsEngine()

        with (
            patch("backend.alerts.engine.SessionLocal") as mock_session_cls,
            patch("backend.alerts.engine.engine_registry"),
        ):
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
            mock_registry.register.assert_called_once_with("quote", "AAPL", self.engine._on_quote)

    def test_register_for_alert_signal_equals_no_callback(self):
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.register_for_alert(alert)
            mock_registry.register.assert_not_called()

    def test_register_for_alert_signal_equals_added_to_cache(self):
        """Regression for a live bug (2026-09-09): signal_equals fell
        through both PRICE_CONDITIONS and BAR_CONDITIONS (it's in
        neither) and was silently never added to _alerts_cache here —
        evaluate_scan_result() iterates that cache directly, so a
        freshly-created signal_equals alert never actually evaluated
        until the next full _reload() (a server restart), despite this
        method's own docstring promising immediate evaluation."""
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        self.engine.register_for_alert(alert)
        self.assertIn(1, self.engine._alerts_cache)
        self.assertIs(self.engine._alerts_cache[1], alert)

    def test_unregister_for_alert_signal_equals_removes_from_cache(self):
        alert = _make_alert(id=1, condition_type="signal_equals", parameter="RSI_OVERSOLD")
        self.engine._alerts_cache[1] = alert
        self.engine.unregister_for_alert(alert)
        self.assertNotIn(1, self.engine._alerts_cache)

    def test_unregister_for_alert_removes_callback(self):
        alert = _make_alert(id=1, condition_type="price_above", parameter="100")
        self.engine._price_alert_ids["AAPL"].append(1)
        self.engine._alerts_cache[1] = alert
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.unregister_for_alert(alert)
            mock_registry.unregister.assert_called_once_with("quote", "AAPL", self.engine._on_quote)

    def test_register_for_alert_bar_condition_registers_callback(self):
        alert = _make_alert(id=1, condition_type="breakout", parameter="20")
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.register_for_alert(alert)
            mock_registry.register.assert_called_once_with("bar", "AAPL", self.engine._on_bar)

    def test_unregister_for_alert_bar_condition_removes_callback(self):
        alert = _make_alert(id=1, condition_type="breakout", parameter="20")
        from collections import defaultdict

        self.engine._bar_alert_ids = defaultdict(list)
        self.engine._bar_alert_ids["AAPL"].append(1)
        self.engine._alerts_cache[1] = alert
        with patch("backend.alerts.engine.engine_registry") as mock_registry:
            self.engine.unregister_for_alert(alert)
            mock_registry.unregister.assert_called_once_with("bar", "AAPL", self.engine._on_bar)


class TestAlertsEngineBarConditions(unittest.TestCase):
    """Test _on_bar dispatches to the right condition group."""

    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True
        self.engine._alerts_cache = {}
        from collections import defaultdict

        self.engine._bar_alert_ids = defaultdict(list)

    def _add_bar_alert(self, alert):
        self.engine._alerts_cache[alert.id] = alert
        self.engine._bar_alert_ids[alert.symbol.upper()].append(alert.id)

    def test_on_bar_breakout_calls_try_fire(self):
        alert = _make_alert(id=1, condition_type="breakout", parameter="20")
        self._add_bar_alert(alert)
        with (
            patch.object(self.engine, "_try_fire", return_value=True) as mock_fire,
            patch(
                "backend.alerts.engine.build_breakout_payload",
                return_value={"current_price": 155.0, "highest_high": 150.0, "symbol": "AAPL"},
            ),
        ):
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)
            self.assertTrue(mock_fire.called)
            self.assertEqual(mock_fire.call_args[0][0], alert)
            self.assertEqual(
                mock_fire.call_args[1]["extra_value"],
                {"current_price": 155.0, "highest_high": 150.0, "symbol": "AAPL"},
            )

    def test_on_bar_trend_conditions_use_trend_payload(self):
        alert = _make_alert(id=1, condition_type="trend_crosses_above_70", parameter="")
        self._add_bar_alert(alert)
        with (
            patch.object(self.engine, "_try_fire", return_value=True) as mock_fire,
            patch(
                "backend.alerts.engine.build_trend_payload",
                return_value={
                    "current": 75.0,
                    "previous": 65.0,
                    "current_direction": "bullish",
                    "previous_direction": "neutral",
                },
            ),
        ):
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)
            self.assertTrue(mock_fire.called)

    def test_on_bar_alignment_conditions_use_alignment_payload(self):
        alert = _make_alert(id=1, condition_type="full_timeframe_alignment", parameter="")
        self._add_bar_alert(alert)
        with (
            patch.object(self.engine, "_try_fire", return_value=True) as mock_fire,
            patch(
                "backend.alerts.engine.build_alignment_payload",
                return_value={"directions": ["bullish", "bullish", "bullish"], "symbol": "AAPL"},
            ),
        ):
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)
            self.assertTrue(mock_fire.called)

    def test_on_bar_unknown_condition_no_fire(self):
        alert = _make_alert(id=1, condition_type="unknown_bar_condition", parameter="")
        self._add_bar_alert(alert)
        with patch.object(self.engine, "_try_fire", return_value=False) as mock_fire:
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)
            mock_fire.assert_not_called()

    def test_on_bar_no_alerts_for_symbol_no_op(self):
        with patch.object(self.engine, "_try_fire") as mock_fire:
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)
            mock_fire.assert_not_called()

    def test_on_bar_divergence_uses_divergence_payload(self):
        alert = _make_alert(id=1, condition_type="divergence", parameter="negative")
        self._add_bar_alert(alert)
        with (
            patch.object(self.engine, "_try_fire", return_value=True) as mock_fire,
            patch(
                "backend.alerts.conditions.build_divergence_payload",
                return_value={"price_change_pct": 2.5, "rsi_like": 45.0},
            ),
        ):
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)
            self.assertTrue(mock_fire.called)
            self.assertEqual(
                mock_fire.call_args[1]["extra_value"], {"price_change_pct": 2.5, "rsi_like": 45.0}
            )

    def test_on_bar_builds_only_the_payload_the_registered_alert_needs(self):
        """2026-09-11 perf fix: a symbol with only a breakout alert must
        not pay for trend/alignment/volume/breakdown's DB round trips
        (alignment alone is 7 queries) on every bar tick."""
        alert = _make_alert(id=1, condition_type="breakout", parameter="20")
        self._add_bar_alert(alert)
        with (
            patch.object(self.engine, "_try_fire", return_value=True),
            patch(
                "backend.alerts.engine.build_breakout_payload",
                return_value={"current_price": 155.0, "highest_high": 150.0},
            ) as m_breakout,
            patch("backend.alerts.engine.build_trend_payload") as m_trend,
            patch("backend.alerts.engine.build_alignment_payload") as m_alignment,
            patch("backend.alerts.engine.build_volume_payload") as m_volume,
            patch("backend.alerts.engine.build_breakdown_payload") as m_breakdown,
        ):
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)

        m_breakout.assert_called_once()
        m_trend.assert_not_called()
        m_alignment.assert_not_called()
        m_volume.assert_not_called()
        m_breakdown.assert_not_called()

    def test_on_bar_builds_payloads_for_every_registered_condition_group(self):
        """Two alerts on the same symbol, two different condition
        groups -> both needed payloads are built, the other three
        (unused) groups are still skipped."""
        breakout_alert = _make_alert(id=1, condition_type="breakout", parameter="20")
        trend_alert = _make_alert(id=2, condition_type="trend_strengthens", parameter="")
        self._add_bar_alert(breakout_alert)
        self._add_bar_alert(trend_alert)
        with (
            patch.object(self.engine, "_try_fire", return_value=True),
            patch(
                "backend.alerts.engine.build_breakout_payload",
                return_value={"current_price": 155.0, "highest_high": 150.0},
            ) as m_breakout,
            patch(
                "backend.alerts.engine.build_trend_payload",
                return_value={
                    "current": 75.0,
                    "previous": 60.0,
                    "current_direction": "bullish",
                    "previous_direction": "bullish",
                },
            ) as m_trend,
            patch("backend.alerts.engine.build_alignment_payload") as m_alignment,
            patch("backend.alerts.engine.build_volume_payload") as m_volume,
            patch("backend.alerts.engine.build_breakdown_payload") as m_breakdown,
        ):
            self.engine._on_bar("AAPL", "1d", 155.0, volume=0, timestamp=None)

        m_breakout.assert_called_once()
        m_trend.assert_called_once()
        m_alignment.assert_not_called()
        m_volume.assert_not_called()
        m_breakdown.assert_not_called()


class TestEvaluateScanResult(unittest.TestCase):
    """Test the signal-based evaluation path."""

    def setUp(self):
        self.engine = AlertsEngine()
        self.engine._started = True
        self.engine._alerts_cache = {}
        self.engine._price_alert_ids = {}

    def test_evaluates_only_signal_equals_for_symbol(self):
        self.engine._alerts_cache[1] = _make_alert(
            id=1,
            symbol="AAPL",
            condition_type="signal_equals",
            parameter="RSI_OVERSOLD",
            is_enabled=True,
        )
        self.engine._alerts_cache[2] = _make_alert(
            id=2,
            symbol="TSLA",
            condition_type="signal_equals",
            parameter="RSI_OVERSOLD",
            is_enabled=True,
        )
        self.engine._alerts_cache[3] = _make_alert(
            id=3,
            symbol="AAPL",
            condition_type="price_above",
            parameter="100",
            is_enabled=True,
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
            id=1,
            symbol="AAPL",
            condition_type="signal_equals",
            parameter="RSI_OVERSOLD",
            is_enabled=False,
        )
        result = _make_result(symbol="AAPL", signals=["RSI_OVERSOLD"])
        with patch.object(self.engine, "_try_fire") as mock_fire:
            self.engine.evaluate_scan_result(result)
            mock_fire.assert_not_called()


class TestPersistTriggerAICommentary(unittest.TestCase):
    """Version 4, AI feature 3: _persist_trigger() enqueues AI
    commentary generation right after the trigger row commits.

    Mocks SessionLocal entirely (no real DB write) — same avoidance
    every other test of this class already uses by mocking
    _persist_trigger out wholesale; here it's the thing under test,
    so only the DB session itself is mocked."""

    def setUp(self):
        self.engine = AlertsEngine()

    @patch("backend.ai.background.enqueue_alert_commentary_job")
    @patch("backend.alerts.engine.SessionLocal")
    def test_enqueues_commentary_job_with_new_trigger_id(self, mock_session_cls, mock_enqueue):
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        def _fake_commit():
            # Simulate the DB assigning a PK on commit, same as a real
            # autoincrement insert would.
            trigger = mock_db.add.call_args[0][0]
            trigger.id = 42

        mock_db.commit.side_effect = _fake_commit

        alert = _make_alert()
        self.engine._persist_trigger(alert, price=150.0, extra_value=None)

        mock_enqueue.assert_called_once_with(42)

    @patch("backend.ai.background.enqueue_alert_commentary_job")
    @patch("backend.alerts.engine.SessionLocal")
    def test_enqueue_failure_does_not_prevent_trigger_commit(self, mock_session_cls, mock_enqueue):
        """A Redis hiccup enqueuing commentary must never turn an
        already-successful trigger-persist into a logged failure —
        it's caught by its own inner try/except, not the outer one
        that rolls back the trigger insert."""
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db
        mock_enqueue.side_effect = RuntimeError("redis down")

        alert = _make_alert()
        self.engine._persist_trigger(alert, price=150.0, extra_value=None)  # must not raise

        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()
        mock_db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
