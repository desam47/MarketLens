"""
Phase 22 — Tests for AuxDataManager (news / fundamentals / options).

Validates the four behaviours the manager promises:
  1. Disabled → empty response, provider="disabled"
  2. Enabled + first provider healthy → calls it
  3. Enabled + first provider fails → falls through to next
  4. All providers fail → empty response, provider="none"
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


class _FakeProvider:
    """Minimal provider stand-in that records calls and can be made to fail."""
    name = "fake"

    def __init__(self, *, fail: bool = False, return_value=None):
        self.fail = fail
        self.return_value = return_value
        self.calls = 0

    def get_news(self, symbol, limit=20):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return self.return_value

    def get_fundamentals(self, symbol):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return self.return_value

    def get_options(self, symbol, expiration=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return self.return_value

    def get_status(self):
        from backend.models.aux_data import AuxProviderStatus
        return AuxProviderStatus(
            name=self.name, healthy=not self.fail,
            last_error="provider down" if self.fail else None,
            last_checked=datetime.utcnow(),
        )


class TestAuxDataManagerDisabled(unittest.TestCase):
    """When the relevant aux category is disabled, all calls return empty."""

    def test_news_disabled(self):
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings

        with patch.object(settings.aux_data, "news", MagicMock(enabled=False)):
            mgr = AuxDataManager()
            resp = mgr.get_news("AAPL")
        self.assertEqual(resp.provider, "disabled")
        self.assertEqual(resp.items, [])

    def test_fundamentals_disabled(self):
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings

        with patch.object(settings.aux_data, "fundamentals", MagicMock(enabled=False)):
            mgr = AuxDataManager()
            resp = mgr.get_fundamentals("AAPL")
        self.assertEqual(resp.provider, "disabled")

    def test_options_disabled(self):
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings

        with patch.object(settings.aux_data, "options", MagicMock(enabled=False)):
            mgr = AuxDataManager()
            resp = mgr.get_options("AAPL")
        self.assertEqual(resp.provider, "disabled")


class TestAuxDataManagerDispatch(unittest.TestCase):
    """When enabled, dispatcher walks the provider chain."""

    def test_first_provider_succeeds(self):
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings
        from backend.models.aux_data import NewsResponse

        good = _FakeProvider(return_value=NewsResponse(
            symbol="AAPL", items=[], provider="fake", timestamp=datetime.utcnow(),
        ))
        with patch.object(settings.aux_data, "news", MagicMock(
            enabled=True, primary_provider="fake", fallback_providers=[],
            rate_limit_per_minute=0,
        )):
            with patch("backend.aux_data.services.manager._PROVIDER_CLASSES",
                       {"fake": lambda: good}):
                mgr = AuxDataManager()
                resp = mgr.get_news("AAPL")
        self.assertEqual(good.calls, 1)
        self.assertEqual(resp.provider, "fake")

    def test_falls_back_when_primary_fails(self):
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings
        from backend.models.aux_data import NewsResponse

        bad = _FakeProvider(fail=True)
        good = _FakeProvider(return_value=NewsResponse(
            symbol="AAPL", items=[], provider="fallback", timestamp=datetime.utcnow(),
        ))
        with patch.object(settings.aux_data, "news", MagicMock(
            enabled=True, primary_provider="primary", fallback_providers=["fallback"],
            rate_limit_per_minute=0,
        )):
            with patch("backend.aux_data.services.manager._PROVIDER_CLASSES", {
                "primary": lambda: bad,
                "fallback": lambda: good,
            }):
                mgr = AuxDataManager()
                resp = mgr.get_news("AAPL")
        self.assertEqual(bad.calls, 1)
        self.assertEqual(good.calls, 1)
        self.assertEqual(resp.provider, "fallback")

    def test_all_providers_fail_returns_empty(self):
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings

        bad1 = _FakeProvider(fail=True)
        bad2 = _FakeProvider(fail=True)
        with patch.object(settings.aux_data, "news", MagicMock(
            enabled=True, primary_provider="p1", fallback_providers=["p2"],
            rate_limit_per_minute=0,
        )):
            with patch("backend.aux_data.services.manager._PROVIDER_CLASSES", {
                "p1": lambda: bad1,
                "p2": lambda: bad2,
            }):
                mgr = AuxDataManager()
                resp = mgr.get_news("AAPL")
        self.assertEqual(bad1.calls, 1)
        self.assertEqual(bad2.calls, 1)
        self.assertEqual(resp.provider, "none")
        self.assertEqual(resp.items, [])

    def test_unknown_provider_skipped(self):
        """Providers not in the registry are logged and skipped, not raised."""
        from backend.aux_data.services.manager import AuxDataManager
        from backend.config.settings import settings
        from backend.models.aux_data import NewsResponse

        good = _FakeProvider(return_value=NewsResponse(
            symbol="AAPL", items=[], provider="real", timestamp=datetime.utcnow(),
        ))
        with patch.object(settings.aux_data, "news", MagicMock(
            enabled=True, primary_provider="missing", fallback_providers=["real"],
            rate_limit_per_minute=0,
        )):
            with patch("backend.aux_data.services.manager._PROVIDER_CLASSES",
                       {"real": lambda: good}):
                mgr = AuxDataManager()
                resp = mgr.get_news("AAPL")
        self.assertEqual(resp.provider, "real")
        self.assertEqual(good.calls, 1)


class TestAuxDataManagerStatuses(unittest.TestCase):

    def test_get_all_statuses_shape(self):
        from backend.aux_data.services.manager import AuxDataManager
        statuses = AuxDataManager().get_all_statuses()
        self.assertIn("news", statuses)
        self.assertIn("fundamentals", statuses)
        self.assertIn("options", statuses)
        for key in ("news", "fundamentals", "options"):
            self.assertIsInstance(statuses[key], list)


if __name__ == "__main__":
    unittest.main()
