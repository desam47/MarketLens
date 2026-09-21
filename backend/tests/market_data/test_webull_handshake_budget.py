"""
Importing the app must authenticate Webull exactly ONCE.

Two managers (the global one and a private one inside IngestionService) meant
two signed handshakes per process start; every dev-server reload is a start, and
bursts of them drew 429s from Webull that dropped it from the provider list.
Runs in a fresh interpreter so it sees the real import-time behaviour.
"""
import os
import subprocess
import sys
import textwrap
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_SCRIPT = textwrap.dedent("""
    import webull.core.client as wb
    def _offline(self, r):
        raise ConnectionError("offline")
    wb.ApiClient.get_response = _offline          # never reach the real API
    from backend.market_data.providers import webull_provider as W
    calls = []
    orig = W.WebullProvider.__init__
    def counting(self, *a, **k):
        calls.append(1)
        return orig(self, *a, **k)
    W.WebullProvider.__init__ = counting
    import backend.api.main                        # import only: no lifespan / ingestion thread
    print("WEBULL_CONSTRUCTIONS=%d" % len(calls))
""")


class TestWebullHandshakeBudget(unittest.TestCase):
    def test_app_import_constructs_webull_exactly_once(self):
        # Force Webull to be registered AND selected as primary with fake
        # credentials, so the probe exercises real construction on any machine
        # (a developer .env, CI with no .env, ...). The offline guard in the
        # script means nothing reaches the real API.
        env = dict(
            os.environ, PYTHONPATH=_ROOT,
            WEBULL_ENABLED="true", WEBULL_APP_KEY="test-key", WEBULL_APP_SECRET="test-secret",
            MARKET_DATA_PRIMARY_PROVIDER="webull",
            MARKET_DATA_FALLBACK_PROVIDERS='["yahoo_finance"]',
        )
        proc = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=_ROOT, env=env,
                              capture_output=True, text=True, timeout=120)
        out = [line for line in proc.stdout.splitlines() if line.startswith("WEBULL_CONSTRUCTIONS=")]
        self.assertTrue(out, f"probe did not run:\n{proc.stderr[-800:]}")
        # == 1 (not <= 1): the probe must actually construct Webull, otherwise a
        # misconfigured run would pass vacuously.
        self.assertEqual(int(out[0].split("=")[1]), 1)


if __name__ == "__main__":
    unittest.main()
