"""Phase 16 — API router tests for /api/ai/* endpoints."""

import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.ai.prompt import TradePlan
from backend.ai.verified_plan_store import VerifiedPlan
from backend.api.main import app
from backend.config.settings import settings

client = TestClient(app)


class TestAnalyzeEndpoint(unittest.TestCase):
    def test_no_template_id_keeps_the_builtin_prompt(self):
        from backend.api.ai.router import _sync_resolve_template

        db = MagicMock()
        self.assertEqual(_sync_resolve_template(db, None), (None, None))
        db.query.assert_not_called()

    @patch("backend.api.ai.router.analyze_symbol")
    def test_force_refresh_is_forwarded(self, mock_analyze):
        from backend.ai.prompt import AnalysisResponse

        mock_analyze.return_value = AnalysisResponse(
            summary="AAPL looks current and bullish.", trend="bullish", confidence=0.7
        )

        resp = client.post("/api/ai/analyze?symbol=AAPL&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_analyze.call_args.kwargs["force_refresh"])

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
            # The endpoint must report whoever actually answered
            # (analyze_symbol()'s own result), not the configured
            # primary — regression for a live bug found 2026-09-10
            # where a fallback-served analysis silently claimed to be
            # from the primary provider.
            provider="ollama",
            model="llama3.2",
            confidence_declared=0.9,
            confidence_sample_size=12,
            symbol="AAPL",
            timeframe="1d",
            price=201.25,
            source_timestamp="2026-09-24T15:30:00-04:00",
            data_age_seconds=12.5,
            data_status="LIVE",
            market_data_provider="webull",
            market_session="regular",
            cache_status="fresh",
            trade_plan=TradePlan(
                recommendation="buy",
                conviction="high",
                time_horizon="swing",
                entry_zone_low=200.0,
                entry_zone_high=202.0,
                stop_loss=194.0,
                targets=[208.0],
                risk_reward=1.5,
                thesis="Buy the pullback.",
                invalidation="Close below support.",
            ),
            trade_plan_validation={"status": "verified", "quote_price": 201.25},
        )

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
        self.assertEqual(data["confidence_declared"], 0.9)
        self.assertEqual(data["confidence_sample_size"], 12)
        self.assertEqual(data["symbol"], "AAPL")
        self.assertEqual(data["timeframe"], "1d")
        self.assertEqual(data["price"], 201.25)
        self.assertEqual(data["data_status"], "LIVE")
        self.assertEqual(data["market_data_provider"], "webull")
        self.assertEqual(data["market_session"], "regular")
        self.assertEqual(data["cache_status"], "fresh")
        self.assertEqual(data["trade_plan_validation"]["status"], "verified")
        self.assertIsInstance(data["verified_plan_id"], str)
        self.assertGreaterEqual(len(data["verified_plan_id"]), 16)
        self.assertFalse(data["is_uncertain"])

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_reports_actual_answering_provider_not_configured_primary(
        self, mock_ai_mgr, mock_analyze
    ):
        """Regression for a live bug (2026-09-10): the endpoint used to
        report ai_manager.settings.provider/model (the configured
        PRIMARY) regardless of which provider actually answered — so
        a fallback-served analysis silently claimed to be from the
        primary. The settings here deliberately say 'openrouter' is
        primary while the actual result says 'ollama' answered — the
        response must reflect the real answering provider."""
        from backend.ai.prompt import AnalysisResponse

        mock_analyze.return_value = AnalysisResponse(
            summary="AAPL looks bullish on the local fallback model.",
            trend="bullish",
            confidence=0.6,
            provider="ollama",
            model="llama3.2",
        )
        mock_ai_mgr.settings.provider = "openrouter"
        mock_ai_mgr.settings.model = "openrouter/free"

        resp = client.post("/api/ai/analyze?symbol=AAPL&timeframe=1d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["provider"], "ollama")
        self.assertEqual(data["model"], "llama3.2")

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_confidence_capped_at_95(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import AnalysisResponse

        mock_analyze.return_value = AnalysisResponse(
            summary="AAPL looks extremely bullish with high conviction here.",
            trend="bullish",
            confidence=1.0,  # overconfident AI reply → must be capped, not 1.0
            provider="ollama",
            model="llama3.2",
            market_regime={"regime": "risk_on"},
        )
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        resp = client.post("/api/ai/analyze?symbol=AAPL")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["confidence"], 0.95)

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_context_fields_surfaced(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import AnalysisResponse

        mock_analyze.return_value = AnalysisResponse(
            summary="AAPL looks bullish.",
            trend="bullish",
            confidence=0.8,
            supporting_factors=["Above SMA 50"],
            risk_factors=["RSI overbought"],
            key_levels=["$200"],
            provider="ollama",
            model="llama3.2",
            market_regime={"regime": "risk_on"},
            timeframe_scores={"1d": {"direction": "bullish", "strength": "strong"}},
            track_record={"sample_size": 12, "win_rate": 0.75},
            correlation_context={"peer_count": 2, "aligned": 1},
        )
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        resp = client.post("/api/ai/analyze?symbol=AAPL")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["market_regime"], {"regime": "risk_on"})
        self.assertEqual(
            data["timeframe_scores"],
            {"1d": {"direction": "bullish", "strength": "strong"}},
        )
        self.assertEqual(data["track_record"]["sample_size"], 12)
        self.assertEqual(data["correlation_context"]["peer_count"], 2)

    @patch("backend.api.ai.router.analyze_symbol")
    @patch("backend.api.ai.router.ai_manager")
    def test_returns_uncertainty_with_flag(self, mock_ai_mgr, mock_analyze):
        from backend.ai.prompt import UncertaintyResponse

        mock_analyze.return_value = UncertaintyResponse(
            summary="AI analysis is disabled (set AI_ENABLED=true to enable)",
            trend="uncertain",
            confidence=0.0,
            uncertainty_reason="disabled",
        )
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        resp = client.post("/api/ai/analyze?symbol=AAPL")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["trend"], "uncertain")
        self.assertEqual(data["confidence"], 0.0)
        self.assertTrue(data["is_uncertain"])
        # No quantitative context reaches the UI when we fell back to
        # uncertainty (no build_context() ran).
        self.assertEqual(data["market_regime"], {})
        self.assertEqual(data["correlation_context"], {})
        self.assertEqual(data["uncertainty_reason"], "disabled")

    @patch("backend.api.ai.router.analyze_symbol_stream")
    @patch("backend.api.ai.router.ai_manager")
    def test_analyze_stream_endpoint_emits_sse_frames(self, mock_ai_mgr, mock_stream):
        async def _gen():
            yield ("meta", {"symbol": "AAPL", "timeframe": "1d", "track_record": {}, "model": None})
            yield ("delta", "AAPL looks")
            yield ("delta", " bullish.")
            yield (
                "final",
                {
                    "summary": "AAPL looks bullish.",
                    "trend": "bullish",
                    "confidence": 0.9,
                    "supporting_factors": ["Above SMA 50"],
                    "risk_factors": ["RSI overbought"],
                    "key_levels": ["$200"],
                    "trade_plan": None,
                    "provider": "ollama",
                    "model": "llama3.2",
                    "is_uncertain": False,
                    "uncertainty_reason": "none",
                    "market_regime": {"regime": "risk_on"},
                    "timeframe_scores": {},
                    "track_record": {},
                    "correlation_context": {},
                },
            )

        mock_stream.return_value = _gen()
        mock_ai_mgr.settings.provider = "ollama"
        mock_ai_mgr.settings.model = "llama3.2"

        with client.stream("POST", "/api/ai/analyze/stream?symbol=AAPL&timeframe=1d") as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers["content-type"])
            resp.read()
            body = resp.text

        frames = [f for f in body.split("\n\n") if f.strip()]
        events = []
        for f in frames:
            lines = f.splitlines()
            ev = next((ln[len("event: ") :] for ln in lines if ln.startswith("event: ")), None)
            data_line = next((ln for ln in lines if ln.startswith("data: ")), None)
            if ev and data_line:
                events.append((ev, data_line[len("data: ") :]))
        kinds = [ev for ev, _ in events]
        self.assertEqual(kinds[0], "meta")
        self.assertEqual(kinds[-1], "final")
        self.assertEqual(kinds.count("delta"), 2)
        delta_text = "".join(json.loads(d)["text"] for ev, d in events if ev == "delta")
        self.assertEqual(delta_text, "AAPL looks bullish.")
        final = json.loads(next(d for ev, d in events if ev == "final"))
        self.assertEqual(final["trend"], "bullish")
        self.assertEqual(final["provider"], "ollama")
        self.assertEqual(final["model"], "llama3.2")
        self.assertEqual(final["market_regime"], {"regime": "risk_on"})
        self.assertEqual(final["uncertainty_reason"], "none")
        self.assertEqual(final["template_id"], None)
        self.assertEqual(final["template_name"], None)

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

        # status() is async now — the endpoint awaits it, so the stub
        # must be an AsyncMock (a plain MagicMock return_value makes the
        # endpoint choke on `await <list>`).
        mock_ai_mgr.status = AsyncMock(
            return_value=[
                ProviderStatus(name="ollama", healthy=True, is_primary=True),
                ProviderStatus(
                    name="anthropic", healthy=False, is_primary=False, error="connection refused"
                ),
            ]
        )

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

    @patch("backend.api.ai.router.ai_manager")
    def test_last_provider_and_model_default_to_null_when_absent(self, mock_ai_mgr):
        # safe_config() omitted these two keys entirely (e.g. an older
        # cached mock/fixture) — ConfigResponse must still validate,
        # defaulting both to null rather than 422ing.
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
        self.assertIsNone(data["last_provider"])
        self.assertIsNone(data["last_model"])

    @patch("backend.api.ai.router.ai_manager")
    def test_returns_the_resolved_last_provider_and_model(self, mock_ai_mgr):
        mock_ai_mgr.safe_config.return_value = {
            "enabled": True,
            "provider": "ollama",
            "fallback_providers": [],
            "model": "static-best-free",
            "last_provider": "ollama",
            "last_model": "openai/gpt-oss-120b",
            "base_url": "http://localhost:11434",
            "timeout": 30.0,
            "max_tokens": 1000,
            "temperature": 0.3,
            "api_key_set": False,
        }
        resp = client.get("/api/ai/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["model"], "static-best-free")
        self.assertEqual(data["last_model"], "openai/gpt-oss-120b")


class TestTrackTradePlanEndpoint(unittest.TestCase):
    @patch.object(settings.ai_trade_plan_tracking, "enabled", True)
    @patch("backend.api.ai.router.record_confirmed_trade_plan")
    @patch("backend.api.ai.router.build_context")
    @patch("backend.api.ai.router.get_verified_plan")
    def test_revalidates_and_tracks_explicitly_confirmed_plan(
        self, mock_verified, mock_context, mock_record
    ):
        mock_context.return_value = SimpleNamespace(
            data_status="LIVE",
            price=101.0,
            support_resistance={
                "supports": [{"price": 95.0}],
                "resistances": [{"price": 110.0}],
            },
        )
        plan = {
            "recommendation": "buy",
            "conviction": "high",
            "time_horizon": "swing",
            "entry_zone_low": 100.0,
            "entry_zone_high": 102.0,
            "stop_loss": 94.0,
            "targets": [108.0],
            "risk_reward": 1.5,
            "thesis": "Buy the pullback.",
            "invalidation": "Close below support.",
        }
        mock_verified.return_value = VerifiedPlan(
            id="verified-plan-123456",
            symbol="AAPL",
            timeframe="1d",
            provider="ollama",
            model="llama3.2",
            plan=TradePlan.model_validate(plan),
            issued_at=0.0,
        )
        mock_record.return_value = (SimpleNamespace(id=42), False)
        payload = {"verified_plan_id": "verified-plan-123456"}

        response = client.post("/api/ai/track-trade-plan", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome_id"], 42)
        self.assertFalse(response.json()["duplicate"])
        mock_context.assert_called_once_with("AAPL", "1d")
        mock_record.assert_called_once_with(
            "AAPL",
            TradePlan.model_validate(plan),
            provider="ollama",
            model="llama3.2",
            timeframe="1d",
            analysis_id="verified-plan-123456",
        )

    @patch.object(settings.ai_trade_plan_tracking, "enabled", True)
    @patch("backend.api.ai.router.get_verified_plan", return_value=None)
    def test_rejects_expired_or_unknown_verified_plan(self, mock_verified):
        response = client.post(
            "/api/ai/track-trade-plan", json={"verified_plan_id": "expired-plan-123456"}
        )

        self.assertEqual(response.status_code, 410)
        self.assertIn("no longer available", response.json()["detail"])
        mock_verified.assert_called_once_with("expired-plan-123456")


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
        # whether a real Ollama is running on localhost:11434. Both
        # _healthy and is_available are async now — bridge with
        # asyncio.run and stub _healthy with an AsyncMock.
        mgr._healthy = AsyncMock(return_value=False)
        self.assertFalse(asyncio.run(mgr.is_available()))
        mgr.set_enabled(True)
        # enabled=True but no providers are healthy -> still False.
        self.assertFalse(asyncio.run(mgr.is_available()))
        mgr._healthy = AsyncMock(return_value=True)
        self.assertTrue(asyncio.run(mgr.is_available()))
        mgr.set_enabled(False)
        self.assertFalse(asyncio.run(mgr.is_available()))


class TestCalculateEndpoint(unittest.TestCase):
    """HTTP-layer coverage for POST /api/ai/calculate."""

    def test_calculate_position_size_returns_200(self):
        resp = client.post(
            "/api/ai/calculate",
            json={
                "tool_name": "calculate",
                "arguments": {
                    "calculation": "position_size",
                    "entry_price": 220,
                    "stop_price": 212,
                    "account_value": 100_000,
                    "risk_percent": 1,
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["tool_name"], "calculate")
        self.assertEqual(data["data"]["values"]["shares"], 125)

    def test_calculate_unknown_tool_422(self):
        resp = client.post(
            "/api/ai/calculate",
            json={"tool_name": "no_such_tool", "arguments": {}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_calculate_rejects_bad_tool_name_pattern(self):
        resp = client.post(
            "/api/ai/calculate",
            json={"tool_name": "Bad-Tool!", "arguments": {}},
        )
        self.assertEqual(resp.status_code, 422)


class TestSymbolRegexValidation(unittest.TestCase):
    """The symbol regex rejects unsafe characters on both analyze endpoints."""

    def test_analyze_rejects_symbol_with_injection_chars(self):
        resp = client.post("/api/ai/analyze?symbol=A%3BCAT")  # A;CAT
        self.assertEqual(resp.status_code, 422)

    def test_analyze_stream_rejects_symbol_with_injection_chars(self):
        with client.stream("POST", "/api/ai/analyze/stream?symbol=A%3BCAT") as resp:
            self.assertEqual(resp.status_code, 422)

    def test_analyze_accepts_valid_ticker_with_dot(self):
        """BRK.B-style tickers must be allowed."""
        from backend.ai.prompt import UncertaintyResponse

        with patch("backend.api.ai.router.analyze_symbol") as mock_analyze:
            mock_analyze.return_value = UncertaintyResponse(summary="x")
            resp = client.post("/api/ai/analyze?symbol=BRK.B")
        self.assertEqual(resp.status_code, 200)


class TestStreamExtended(unittest.TestCase):
    """Additional stream coverage: template forwarding and force_refresh."""

    @patch("backend.api.ai.router.analyze_symbol_stream")
    @patch("backend.api.ai.router._sync_resolve_template")
    def test_stream_forwards_template_id(self, mock_resolve, mock_stream):
        from backend.ai.prompt import AnalysisResponse

        mock_resolve.return_value = (42, None)

        async def _gen():
            yield ("meta", {"symbol": "AAPL", "timeframe": "1d", "track_record": {}, "model": None})
            yield ("final", {
                "summary": "x", "trend": "bullish", "confidence": 0.8,
                "supporting_factors": [], "risk_factors": [], "key_levels": [],
                "trade_plan": None, "provider": "ollama", "model": "llama3.2",
                "is_uncertain": False, "uncertainty_reason": "none",
                "market_regime": {}, "timeframe_scores": {}, "track_record": {},
                "correlation_context": {},
            })

        mock_stream.return_value = _gen()

        with client.stream(
            "POST", "/api/ai/analyze/stream?symbol=AAPL&template_id=42"
        ) as resp:
            resp.read()

        # _sync_resolve_template must have been called with template_id=42.
        resolve_args = mock_resolve.call_args[0]  # (db, template_id)
        self.assertEqual(resolve_args[1], 42)

    @patch("backend.api.ai.router.analyze_symbol_stream")
    @patch("backend.api.ai.router._sync_resolve_template")
    def test_stream_forwards_force_refresh(self, mock_resolve, mock_stream):
        mock_resolve.return_value = (None, None)

        async def _gen():
            yield ("meta", {"symbol": "AAPL", "timeframe": "1d", "track_record": {}, "model": None})
            yield ("final", {
                "summary": "x", "trend": "bullish", "confidence": 0.8,
                "supporting_factors": [], "risk_factors": [], "key_levels": [],
                "trade_plan": None, "provider": "ollama", "model": "llama3.2",
                "is_uncertain": False, "uncertainty_reason": "none",
                "market_regime": {}, "timeframe_scores": {}, "track_record": {},
                "correlation_context": {},
            })

        mock_stream.return_value = _gen()

        with client.stream(
            "POST", "/api/ai/analyze/stream?symbol=AAPL&force_refresh=true"
        ) as resp:
            resp.read()

        call_kwargs = mock_stream.call_args.kwargs
        self.assertTrue(call_kwargs.get("force_refresh"))


if __name__ == "__main__":
    unittest.main()
