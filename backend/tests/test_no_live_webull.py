"""
The test suite must never reach the real Webull API (see the guard in
``backend/tests/conftest.py``): doing so burns Webull's rate limit and, through
the shared app key, knocks the Webull provider out of the live dev server.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MARKETLENS_TEST_ALLOW_NETWORK") == "1",
    reason="live network explicitly allowed",
)


class TestWebullNetworkBlocked(unittest.TestCase):
    def test_sdk_http_funnel_is_offline(self):
        import webull.core.client as wb

        client = wb.ApiClient("k", "s", "us")
        with self.assertRaises(ConnectionError) as ctx:
            client.get_response(MagicMock())
        self.assertIn("disabled under pytest", str(ctx.exception))

    def test_provider_construction_fails_fast_without_network(self):
        from backend.market_data.providers import webull_provider as mod

        fake = MagicMock(app_key="test_key", app_secret="test_secret", use_sandbox=False)
        with patch.object(mod, "_settings", MagicMock(webull=fake)):
            with self.assertRaises(ConnectionError):
                mod.WebullProvider()


if __name__ == "__main__":
    unittest.main()
