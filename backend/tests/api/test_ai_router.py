"""Phase 16 — API router tests for /api/ai/* endpoints."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


class TestAnalyzeEndpoint(unittest.TestCase):

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_returns_ai_analysis(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import AnalysisResponse
        mock_analyze.return_value = AnalysisResponse(
            summary="AAPL looks bullish.",
            trend="bullish",
            confidence=0.82,
            supporting_factors=["Above SMA 50"],
            risk_factors=["RSI overbought"],
            timeframe_conflicts=[],
            key_levels=["$200"],
        )
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        resp = client.post("/api/ai/analyze?symbol=AAPL&timeframe=1d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["summary"], "AAPL looks bullish.")
        self.assertEqual(data["trend"], "bullish")
        self.assertAlmostEqual(data["confidence"], 0.82)
        self.assertEqual(data["supporting_factors"], ["Above SMA 50"])
        self.assertEqual(data["risk_factors"], ["RSI overbought"])
        self.assertEqual(data["key_levels"], ["$200"])
        self.assertEqual(data["provider"], "ollama")
        self.assertEqual(data["model"], "llama3.2")
        self.assertFalse(data["is_uncertain"])

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_returns_uncertainty_with_flag(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import UncertaintyResponse
        mock_analyze.return_value = UncertaintyResponse(
            summary="AI analysis is disabled (set AI_ENABLED=true to enable)",
            trend="uncertain",
            confidence=0.0,
        )
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        resp = client.post("/api/ai/analyze?symbol=AAPL")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["trend"], "uncertain")
        self.assertEqual(data["confidence"], 0.0)
        self.assertTrue(data["is_uncertain"])

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_symbol_uppercased(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import UncertaintyResponse
        mock_analyze.return_value = UncertaintyResponse(summary="x")
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        client.post("/api/ai/analyze?symbol=aapl")
        mock_analyze.assert_called_once()
        self.assertEqual(mock_analyze.call_args[1]["symbol"], "AAPL")

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_forwards_max_tokens_and_temperature(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import UncertaintyResponse
        mock_analyze.return_value = UncertaintyResponse(summary="x")
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        client.post("/api/ai/analyze?symbol=AAPL&max_tokens=500&temperature=0.7")
        mock_analyze.assert_called_once()
        self.assertEqual(mock_analyze.call_args[1]["max_tokens"], 500)
        self.assertEqual(mock_analyze.call_args[1]["temperature"], 0.7)

    def test_rejects_missing_symbol(self):
        resp = client.post("/api/ai/analyze")
        self.assertEqual(resp.status_code, 422)

    def test_rejects_invalid_timeframe(self):
        resp = client.post("/api/ai/analyze?symbol=AAPL&timeframe=999")
        self.assertEqual(resp.status_code, 422)

    def test_rejects_symbol_too_long(self):
        # max_length=10, so 11 chars should fail
        resp = client.post("/api/ai/analyze?symbol=TOOLONGTOKXX")
        self.assertEqual(resp.status_code, 422)


class TestAIStatusEndpoint(unittest.TestCase):

    @patch("backend.api.ai.router.ai_manager")
    def test_returns_provider_statuses(self, mock_ai_mgr):
        from backend.ai.manager import ProviderStatus
        mock_ai_mgr.status.return_value = [
            ProviderStatus(name="ollama", healthy=True, is_primary=True),
            ProviderStatus(name="anthropic", healthy=False, is_primary=False,
                          error="connection refused"),
        ]

        resp = client.get("/api/ai/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["name"], "ollama")
        self.assertTrue(data[0]["healthy"])
        self.assertTrue(data[0]["is_primary"])
        self.assertEqual(data[1]["name"], "anthropic")
        self.assertFalse(data[1]["healthy"])
        self.assertEqual(data[1]["error"], "connection refused")


class TestAIConfigEndpoint(unittest.TestCase):

    @patch("backend.api.ai.router.ai_manager")
    def test_returns_safe_config(self, mock_ai_mgr):
        mock_ai_mgr.safe_config.return_value = {
            "enabled": True,
            "provider": "ollama",
            "fallback_providers": [],
            "model": "llama3.2",
            "base_url": "http://localhost:11434",
            "timeout": 30.0,
            "max_tokens": 1000,
            "temperature": 0.3,
            "api_key_set": False,
        }

        resp = client.get("/api/ai/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["provider"], "ollama")
        self.assertEqual(data["model"], "llama3.2")
        # api_key must not appear in the response
        self.assertNotIn("api_key", data)


class TestAIConfigPatchEndpoint(unittest.TestCase):
    """Phase 17+: PATCH /api/ai/config flips the enabled flag at runtime."""

    @patch("backend.api.ai.router.ai_manager")
    def test_set_enabled_true(self, mock_ai_mgr):
        mock_ai_mgr.safe_config.return_value = {
            "enabled": True,
            "provider": "ollama",
            "fallback_providers": [],
            "model": "llama3.2",
            "base_url": "http://localhost:11434",
            "timeout": 30.0,
            "max_tokens": 1000,
            "temperature": 0.3,
            "api_key_set": False,
        }

        resp = client.patch("/api/ai/config", json={"enabled": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["enabled"])
        mock_ai_mgr.set_enabled.assert_called_once_with(True)

    @patch("backend.api.ai.router.ai_manager")
    def test_set_enabled_false(self, mock_ai_mgr):
        mock_ai_mgr.safe_config.return_value = {
            "enabled": False,
            "provider": "ollama",
            "fallback_providers": [],
            "model": "llama3.2",
            "base_url": "http://localhost:11434",
            "timeout": 30.0,
            "max_tokens": 1000,
            "temperature": 0.3,
            "api_key_set": False,
        }

        resp = client.patch("/api/ai/config", json={"enabled": False})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["enabled"])
        mock_ai_mgr.set_enabled.assert_called_once_with(False)

    @patch("backend.api.ai.router.ai_manager")
    def test_empty_patch_returns_current_config(self, mock_ai_mgr):
        mock_ai_mgr.safe_config.return_value = {
            "enabled": True,
            "provider": "ollama",
            "fallback_providers": [],
            "model": "llama3.2",
            "base_url": "http://localhost:11434",
            "timeout": 30.0,
            "max_tokens": 1000,
            "temperature": 0.3,
            "api_key_set": False,
        }

        resp = client.patch("/api/ai/config", json={})
        self.assertEqual(resp.status_code, 200)
        # set_enabled should NOT have been called when no fields are passed.
        mock_ai_mgr.set_enabled.assert_not_called()


class TestAIManagerRuntimeToggle(unittest.TestCase):
    """The runtime override must shadow settings.enabled."""

    def test_default_uses_settings(self):
        from backend.ai.manager import AIManager
        from backend.config.settings import AISettings

        mgr = AIManager(AISettings(enabled=False))
        self.assertFalse(mgr.enabled)
        mgr2 = AIManager(AISettings(enabled=True))
        self.assertTrue(mgr2.enabled)

    def test_set_enabled_overrides_settings(self):
        from backend.ai.manager import AIManager
        from backend.config.settings import AISettings

        mgr = AIManager(AISettings(enabled=False))
        mgr.set_enabled(True)
        self.assertTrue(mgr.enabled)
        mgr.set_enabled(False)
        self.assertFalse(mgr.enabled)

    def test_is_available_reflects_override(self):
        from backend.ai.manager import AIManager
        from backend.config.settings import AISettings

        mgr = AIManager(AISettings(enabled=False))
        # Override _healthy so the test is deterministic regardless of
        # whether a real Ollama is running on localhost:11434.
        mgr._healthy = lambda name: False
        self.assertFalse(mgr.is_available())
        mgr.set_enabled(True)
        # enabled=True but no providers are healthy -> still False.
        self.assertFalse(mgr.is_available())
        mgr._healthy = lambda name: True
        self.assertTrue(mgr.is_available())
        mgr.set_enabled(False)
        self.assertFalse(mgr.is_available())


if __name__ == "__main__":
    unittest.main()
