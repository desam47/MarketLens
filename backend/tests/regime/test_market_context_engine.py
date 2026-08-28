"""
Tests for MarketContextEngine — Phase 8 spec §1.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.regime.market_context_engine import MarketContextEngine, MarketContextSignal
from backend.regime.market_regime_engine import MarketRegime


class TestMarketContextEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketContextEngine()

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def test_engine_initialization(self):
        """Engine initializes with 4 sub-engines (SPY, QQQ, IWM, ^VIX)."""
        self.assertEqual(len(self.engine.sub_engines), 4)
        for sym in ("SPY", "QQQ", "IWM", "^VIX"):
            self.assertIn(sym, self.engine.sub_engines)

    # ------------------------------------------------------------------
    # Sub-regime collection
    # ------------------------------------------------------------------

    def test_collect_sub_regimes_no_data(self):
        """With no data, _collect_sub_regimes returns empty dict."""
        result = self.engine._collect_sub_regimes()
        self.assertEqual(result, {})

    def test_collect_sub_regimes_all_cold_start(self):
        """After enough ticks, sub-engines produce a regime each."""
        # Feed enough data to all 4 sub-engines
        now = datetime.now()
        for sym in ("SPY", "QQQ", "IWM", "^VIX"):
            for i in range(20):
                self.engine.update(
                    price=100.0 + i * 0.1,
                    volume=1000,
                    timestamp=now + timedelta(minutes=i),
                    symbol=sym,
                )
        result = self.engine._collect_sub_regimes()
        # All 4 sub-engines should now have a regime signal
        self.assertEqual(len(result), 4)

    # ------------------------------------------------------------------
    # Aggregation — consensus rules
    # ------------------------------------------------------------------

    def test_aggregate_risk_on_3_of_4(self):
        """3+ RISK_ON sub-regimes → RISK_ON (threshold=3)."""
        # Inject pre-built RegimeSignals into sub-engines via a helper
        self._inject_regimes({
            "SPY": MarketRegime.RISK_ON,
            "QQQ": MarketRegime.RISK_ON,
            "IWM": MarketRegime.RISK_ON,
            "^VIX": MarketRegime.NEUTRAL,
        })
        regime, confidence, factors = self.engine._aggregate(self.engine._collect_sub_regimes())
        self.assertEqual(regime, "risk_on")
        self.assertGreater(confidence, 0)

    def test_aggregate_risk_off_3_of_4(self):
        """3+ RISK_OFF sub-regimes → RISK_OFF."""
        self._inject_regimes({
            "SPY": MarketRegime.RISK_OFF,
            "QQQ": MarketRegime.RISK_OFF,
            "IWM": MarketRegime.NEUTRAL,
            "^VIX": MarketRegime.RISK_OFF,
        })
        regime, confidence, factors = self.engine._aggregate(self.engine._collect_sub_regimes())
        self.assertEqual(regime, "risk_off")

    def test_aggregate_neutral_3_of_4(self):
        """3+ NEUTRAL sub-regimes → NEUTRAL."""
        self._inject_regimes({
            "SPY": MarketRegime.NEUTRAL,
            "QQQ": MarketRegime.NEUTRAL,
            "IWM": MarketRegime.NEUTRAL,
            "^VIX": MarketRegime.RISK_ON,
        })
        regime, confidence, factors = self.engine._aggregate(self.engine._collect_sub_regimes())
        self.assertEqual(regime, "neutral")

    def test_aggregate_mixed_goes_transition(self):
        """Mixed sub-regimes → TRANSITION."""
        self._inject_regimes({
            "SPY": MarketRegime.RISK_ON,
            "QQQ": MarketRegime.RISK_OFF,
            "IWM": MarketRegime.NEUTRAL,
            "^VIX": MarketRegime.RISK_ON,
        })
        regime, confidence, factors = self.engine._aggregate(self.engine._collect_sub_regimes())
        self.assertEqual(regime, "transition")

    def test_aggregate_2_and_2_goes_transition(self):
        """2 RISK_ON + 2 RISK_OFF → TRANSITION (no consensus)."""
        self._inject_regimes({
            "SPY": MarketRegime.RISK_ON,
            "QQQ": MarketRegime.RISK_ON,
            "IWM": MarketRegime.RISK_OFF,
            "^VIX": MarketRegime.RISK_OFF,
        })
        regime, confidence, factors = self.engine._aggregate(self.engine._collect_sub_regimes())
        self.assertEqual(regime, "transition")

    # ------------------------------------------------------------------
    # End-to-end
    # ------------------------------------------------------------------

    def test_get_current_context_no_data(self):
        """No data → None."""
        self.assertIsNone(self.engine.get_current_context())

    def test_history_limit(self):
        """History respects the limit parameter."""
        # Manually append some signals
        for _i in range(5):
            self.engine._signals.append(
                MarketContextSignal(
                    regime="neutral",
                    confidence=0.5,
                    trend_strength=0.5,
                    momentum=0.0,
                    volatility_state="normal",
                    timestamp=datetime.now(),
                )
            )
        history = self.engine.get_history(limit=3)
        self.assertEqual(len(history), 3)

    def test_history_no_limit(self):
        """No limit → full copy."""
        for _i in range(3):
            self.engine._signals.append(
                MarketContextSignal(
                    regime="risk_on",
                    confidence=0.7,
                    trend_strength=0.6,
                    momentum=0.5,
                    volatility_state="normal",
                    timestamp=datetime.now(),
                )
            )
        history = self.engine.get_history()
        self.assertEqual(len(history), 3)

    def test_signal_to_dict(self):
        """to_dict() produces a serialisable dict."""
        signal = MarketContextSignal(
            regime="risk_on",
            confidence=0.75,
            trend_strength=0.65,
            momentum=0.4,
            volatility_state="normal",
            sub_regimes={"SPY": "risk_on", "QQQ": "risk_on", "IWM": "neutral", "^VIX": "risk_off"},
            contributing_factors={"primary_reason": "consensus_risk_on"},
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
        )
        d = signal.to_dict()
        self.assertEqual(d["regime"], "risk_on")
        self.assertIn("timestamp", d)
        self.assertIn("sub_regimes", d)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _inject_regimes(self, mapping: dict[str, MarketRegime]) -> None:
        """Override the regime_history of each sub-engine to short-circuit
        _collect_sub_regimes for deterministic aggregation tests."""
        from backend.regime.market_regime_engine import RegimeSignal
        ts = datetime.now()
        for sym, reg in mapping.items():
            sig = RegimeSignal(
                symbol=sym,
                regime=reg,
                confidence=0.8,
                strength=0.7,
                supporting_factors={},
                timestamp=ts,
            )
            # Replace cold history with one known entry
            self.engine.sub_engines[sym].regime_history = [sig]


if __name__ == '__main__':
    unittest.main()
