"""The tape section of build_context() — populates when enabled, degrades to {}."""

import unittest
from unittest.mock import MagicMock, patch

from backend.ai import context as ctx_mod
from backend.config.settings import settings


class TestContextTapeSection(unittest.TestCase):
    def _min_scan(self):
        """A scan stub just complete enough for build_context to reach the tape section."""
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

    def test_tape_populates_when_enabled(self):
        snap = {
            "pressure": "heavy_buy",
            "signed_volume": 4200,
            "buy_ratio": 0.79,
            "tape_speed": 3.1,
            "tape_accel": 1.8,
            "block_count_5m": 2,
            "trade_count": 55,
        }
        with (
            patch.object(settings.tape, "enabled", True),
            patch(
                "backend.scanner.scanner.market_scanner.scan_symbol", return_value=self._min_scan()
            ),
            patch("backend.api.tape.registry.get_tape_engine") as g,
        ):
            g.return_value.get_snapshot.return_value = snap
            out = ctx_mod.build_context(
                "AAPL", include_news=False, include_fundamentals=False, include_divergence=False
            )
        tape = out.to_dict()["tape"]
        self.assertEqual(tape["pressure"], "heavy_buy")
        self.assertEqual(tape["signed_volume_1m"], 4200)
        self.assertEqual(tape["block_count_5m"], 2)

    def test_tape_empty_when_disabled(self):
        with (
            patch.object(settings.tape, "enabled", False),
            patch(
                "backend.scanner.scanner.market_scanner.scan_symbol", return_value=self._min_scan()
            ),
        ):
            out = ctx_mod.build_context(
                "AAPL", include_news=False, include_fundamentals=False, include_divergence=False
            )
        self.assertEqual(out.to_dict()["tape"], {})

    def test_tape_degrades_on_engine_error(self):
        with (
            patch.object(settings.tape, "enabled", True),
            patch(
                "backend.scanner.scanner.market_scanner.scan_symbol", return_value=self._min_scan()
            ),
            patch("backend.api.tape.registry.get_tape_engine", side_effect=RuntimeError("cold")),
        ):
            out = ctx_mod.build_context(
                "AAPL", include_news=False, include_fundamentals=False, include_divergence=False
            )
        self.assertEqual(out.to_dict()["tape"], {})


if __name__ == "__main__":
    unittest.main()
