"""The track_record section of build_context() — populates when
outcome tracking is enabled, degrades to {} otherwise (same pattern as
test_context_tape.py's tape section)."""
import unittest
from unittest.mock import MagicMock, patch

from backend.ai import context as ctx_mod
from backend.config.settings import settings


class TestContextTrackRecordSection(unittest.TestCase):
    def _min_scan(self):
        scan = MagicMock()
        q = MagicMock()
        q.price = 191.0
        q.timestamp = "2026-09-10T15:00:00"
        scan.quote = q
        scan.trend_signals = {}
        scan.scores = {}
        scan.signals = []
        scan.calculate_total_score.return_value = 0.0
        return scan

    def test_populates_when_enabled(self):
        record = {"sample_size": 4, "win_rate": 0.75, "avg_return_win": 0.04,
                  "avg_return_loss": -0.02, "open_count": 1}
        with patch.object(settings.ai_trade_plan_tracking, "enabled", True), \
             patch("backend.scanner.scanner.market_scanner.scan_symbol", return_value=self._min_scan()), \
             patch("backend.ai.trade_plan_tracker.get_track_record", return_value=record) as g:
            out = ctx_mod.build_context("AAPL", include_news=False,
                                        include_fundamentals=False, include_divergence=False)
        self.assertEqual(out.to_dict()["track_record"], record)
        g.assert_called_once_with("AAPL")

    def test_empty_when_disabled(self):
        with patch.object(settings.ai_trade_plan_tracking, "enabled", False), \
             patch("backend.scanner.scanner.market_scanner.scan_symbol", return_value=self._min_scan()), \
             patch("backend.ai.trade_plan_tracker.get_track_record") as g:
            out = ctx_mod.build_context("AAPL", include_news=False,
                                        include_fundamentals=False, include_divergence=False)
        self.assertEqual(out.to_dict()["track_record"], {})
        g.assert_not_called()

    def test_degrades_on_lookup_error(self):
        with patch.object(settings.ai_trade_plan_tracking, "enabled", True), \
             patch("backend.scanner.scanner.market_scanner.scan_symbol", return_value=self._min_scan()), \
             patch("backend.ai.trade_plan_tracker.get_track_record", side_effect=RuntimeError("db down")):
            out = ctx_mod.build_context("AAPL", include_news=False,
                                        include_fundamentals=False, include_divergence=False)
        self.assertEqual(out.to_dict()["track_record"], {})


if __name__ == "__main__":
    unittest.main()
