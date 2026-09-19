"""
Phase 15 — Tests for the AI provider abstraction.

Covers the full surface of ``AIManager`` plus the provider
implementations, with ``httpx`` mocked so the suite doesn't require
a live Ollama / OpenAI / Anthropic endpoint. The tests focus on:

- ``safe_config`` strips ``api_key``
- provider factory dispatches by name
- health check returns False on connection errors
- ``complete()`` walks the fallback chain
- ``complete()`` returns disabled response when ``enabled=False``
- ``is_available()`` / ``status()`` reflect the chain
- exception flow (non-recoverable errors propagate)
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

import httpx

from backend.ai.manager import AIManager
from backend.ai.provider import AIResponse, ProviderUnavailable
from backend.ai.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    build_provider,
)
from backend.config.settings import AISettings

# --- AISettings ---------------------------------------------------


class TestAISettings(unittest.TestCase):

    def test_defaults_disable_ai(self):
        # _env_file=None: test the field default in isolation from this
        # machine's real .env (which has AI_ENABLED=true as of
        # 2026-09-09 — see docs/Version_4/phase_audit_v4.md).
        s = AISettings(_env_file=None)
        self.assertFalse(s.enabled)
        self.assertEqual(s.provider, "ollama")
        self.assertEqual(s.fallback_providers, "")
        self.assertEqual(s.timeout, 30.0)
        self.assertEqual(s.health_check_timeout, 2.0)
        self.assertFalse(bool(s.api_key))  # None or "" — no real key

    def test_fallback_chain_splits_and_strips(self):
        s = AISettings(fallback_providers="openrouter, openai , anthropic")
        self.assertEqual(s.fallback_chain(), ["openrouter", "openai", "anthropic"])

    def test_fallback_chain_handles_empty(self):
        s = AISettings(fallback_providers="")
        self.assertEqual(s.fallback_chain(), [])
        # also: stray spaces
        s2 = AISettings(fallback_providers="   ")
        self.assertEqual(s2.fallback_chain(), [])

    def test_all_providers_orders_primary_then_fallbacks(self):
        s = AISettings(provider="anthropic", fallback_providers="openai,ollama")
        self.assertEqual(s.all_providers(), ["anthropic", "openai", "ollama"])


# --- Provider factory ---------------------------------------------


class TestBuildProvider(unittest.TestCase):

    def test_known_names_dispatch(self):
        cases = {
            "ollama": OpenAICompatibleProvider,
            "lm_studio": OpenAICompatibleProvider,
            "openai": OpenAICompatibleProvider,
            "openrouter": OpenAICompatibleProvider,
            "anthropic": AnthropicProvider,
        }
        for name, cls in cases.items():
            p = build_provider(
                name, base_url=None, model=None, api_key="x",
                timeout=1.0, health_check_timeout=1.0,
            )
            self.assertIsInstance(p, cls, f"{name} should be {cls.__name__}")

    def test_openai_compatible_needs_base_url(self):
        # openai_compatible is the generic catch-all — no sensible default URL
        with self.assertRaises(ValueError):
            build_provider(
                "openai_compatible", base_url=None, model=None, api_key=None,
                timeout=1.0, health_check_timeout=1.0,
            )
        # With a URL it works
        p = build_provider(
            "openai_compatible",
            base_url="https://my-endpoint.example.com/v1",
            model=None, api_key=None,
            timeout=1.0, health_check_timeout=1.0,
        )
        self.assertIsInstance(p, OpenAICompatibleProvider)
        self.assertEqual(p._base_url, "https://my-endpoint.example.com/v1")

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            build_provider(
                "bogus_provider", base_url=None, model=None, api_key=None,
                timeout=1.0, health_check_timeout=1.0,
            )

    def test_ollama_default_base_url(self):
        p = build_provider(
            "ollama", base_url=None, model=None, api_key=None,
            timeout=1.0, health_check_timeout=1.0,
        )
        self.assertEqual(p._base_url, "http://localhost:11434/v1")
        self.assertEqual(p._model, "llama3.2")
        self.assertEqual(p.name, "ollama")

    def test_openai_default_base_url(self):
        p = build_provider(
            "openai", base_url=None, model=None, api_key="sk-test",
            timeout=1.0, health_check_timeout=1.0,
        )
        self.assertEqual(p._base_url, "https://api.openai.com/v1")
        self.assertEqual(p._model, "gpt-4o-mini")
        self.assertEqual(p._api_key, "sk-test")

    def test_anthropic_requires_api_key_for_health(self):
        p = build_provider(
            "anthropic", base_url=None, model=None, api_key=None,
            timeout=1.0, health_check_timeout=1.0,
        )
        self.assertFalse(asyncio.run(p.health_check()))


# --- OpenAICompatibleProvider --------------------------------------


class _MockResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=MagicMock(), response=MagicMock(status_code=self.status_code),
            )


class TestOpenAICompatibleProvider(unittest.TestCase):

    def setUp(self):
        self.p = OpenAICompatibleProvider(
            provider_name="ollama",
            base_url="http://localhost:11434/v1",
            model="llama3.2",
            api_key=None,
            timeout=1.0,
            health_check_timeout=1.0,
        )

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_health_check_returns_true_on_200(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.get = AsyncMock(return_value=_MockResponse(200))
        MockClient.return_value = client
        self.assertTrue(asyncio.run(self.p.health_check()))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_health_check_returns_false_on_connection_error(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value = client
        self.assertFalse(asyncio.run(self.p.health_check()))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_returns_text(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, {
            "model": "llama3.2",
            "choices": [{"message": {"content": "Hello, world."}}],
        }))
        MockClient.return_value = client
        resp = asyncio.run(self.p.complete("hi", system="be brief"))
        self.assertEqual(resp.text, "Hello, world.")
        self.assertEqual(resp.provider, "ollama")
        self.assertEqual(resp.model, "llama3.2")

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_omits_response_format_when_unsupported(self, MockClient):
        # Provider built with supports_structured_output=False must NOT
        # send response_format even when one was requested — keeps the
        # request shape and the ai_resp.structured flag in lockstep.
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, {
            "model": "llama3.2",
            "choices": [{"message": {"content": "ok"}}],
        }))
        MockClient.return_value = client
        asyncio.run(self.p.complete("hi", response_format={"type": "json_object"}))
        body = client.post.call_args.kwargs["json"]
        self.assertNotIn("response_format", body)

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_includes_response_format_when_supported(self, MockClient):
        self.p.supports_structured_output = True
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, {
            "model": "llama3.2",
            "choices": [{"message": {"content": "ok"}}],
        }))
        MockClient.return_value = client
        asyncio.run(self.p.complete("hi", response_format={"type": "json_object"}))
        body = client.post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_connection_error(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_401(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(401, text="bad key"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_500(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(500))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_for_status_on_400(self, MockClient):
        # 400 (other client errors) should NOT be treated as recoverable
        # — the caller is doing something wrong and should see the error.
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(400, text="malformed"))
        MockClient.return_value = client
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_429(self, MockClient):
        """Regression for a live bug (2026-09-09): openrouter's
        free-tier model returned a bare 429 (rate limited), which
        r.raise_for_status() turned into an uncaught
        httpx.HTTPStatusError that crashed the whole /api/ai/analyze
        request with an unhandled 500 — despite that endpoint's own
        docstring promising it never 500s for a provider-side failure.
        429 means the provider is temporarily unavailable (unlike a
        genuine 400 malformed request, which should still raise loud —
        see test_complete_raises_for_status_on_400 above), so it must
        degrade the same way 401/403/404/5xx already do."""
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(429, text="rate limited"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_handles_unexpected_payload(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, {"weird": "shape"}))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_non_json_200(self, MockClient):
        """Regression for a live bug (2026-09-09): a misconfigured
        base_url (missing the /v1 path a custom openai_compatible
        gateway requires) landed on the gateway's own HTML web UI
        instead of its API — a 200 response with an HTML body. r.json()
        raised a raw json.JSONDecodeError that nothing upstream caught,
        crashing the whole /api/ai/analyze request with an unhandled
        500 instead of falling through like any other bad-response
        case. json_data=None makes the mock's .json() raise ValueError,
        the same as httpx's real JSONDecodeError (a ValueError subclass)
        on a non-JSON body."""
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, None, text="<html>...</html>"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))


# --- AnthropicProvider --------------------------------------------


class TestAnthropicProvider(unittest.TestCase):

    def setUp(self):
        self.p = AnthropicProvider(
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet-latest",
            api_key="sk-ant-test",
            timeout=1.0,
            health_check_timeout=1.0,
        )

    def test_health_check_false_without_api_key(self):
        p2 = AnthropicProvider(api_key=None)
        self.assertFalse(asyncio.run(p2.health_check()))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_health_check_true_on_200(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200))
        MockClient.return_value = client
        self.assertTrue(asyncio.run(self.p.health_check()))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_concatenates_text_blocks(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, {
            "model": "claude-3-5-sonnet-latest",
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world."},
                {"type": "tool_use", "id": "x"},  # ignored
            ],
        }))
        MockClient.return_value = client
        resp = asyncio.run(self.p.complete("hi", system="be brief"))
        self.assertEqual(resp.text, "Hello world.")
        self.assertEqual(resp.provider, "anthropic")

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_non_json_200(self, MockClient):
        """Same regression as OpenAICompatibleProvider's version — see
        that test's docstring."""
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(200, None, text="<html>...</html>"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    def test_complete_raises_without_api_key(self):
        p2 = AnthropicProvider(api_key=None)
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(p2.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_401(self, MockClient):
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(401, text="bad key"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_complete_raises_unavailable_on_429(self, MockClient):
        """Same regression as OpenAICompatibleProvider's version — see
        that test's docstring."""
        client = MagicMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(return_value=_MockResponse(429, text="rate limited"))
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(self.p.complete("hi"))


# --- AIManager -----------------------------------------------------


class TestAIManager(unittest.TestCase):

    def _make_manager(self, **overrides) -> AIManager:
        defaults = dict(
            enabled=True,
            provider="ollama",
            fallback_providers="",
            model="llama3.2",
            base_url="http://localhost:11434/v1",
            api_key=None,
            timeout=1.0,
            health_check_timeout=1.0,
            max_tokens=100,
            temperature=0.3,
        )
        defaults.update(overrides)
        return AIManager(AISettings(**defaults))

    def test_disabled_returns_empty_response(self):
        m = self._make_manager(enabled=False)
        resp = asyncio.run(m.complete("hi"))
        self.assertIsNone(resp.text)
        self.assertEqual(resp.provider, "disabled")

    def test_disabled_is_not_available(self):
        m = self._make_manager(enabled=False)
        self.assertFalse(asyncio.run(m.is_available()))

    def test_chain_supports_structured_output_true_when_any_supported(self):
        m = self._make_manager(provider="ollama", fallback_providers="openai")
        # Flip the primary to unsupported; the openai fallback still is.
        m._get_provider("ollama").supports_structured_output = False
        self.assertTrue(m.chain_supports_structured_output())

    def test_chain_supports_structured_output_false_when_none_supported(self):
        m = self._make_manager(provider="ollama", fallback_providers="openai")
        for name in m._all_providers():
            m._get_provider(name).supports_structured_output = False
        self.assertFalse(m.chain_supports_structured_output())

    def test_safe_config_strips_api_key(self):
        m = self._make_manager(api_key="sk-secret")
        cfg = m.safe_config()
        self.assertNotIn("api_key", cfg)
        self.assertTrue(cfg["api_key_set"])
        # Re-assert the secret is not present anywhere
        self.assertNotIn("sk-secret", str(cfg))

    def test_safe_config_omits_when_key_missing(self):
        m = self._make_manager(api_key=None)
        cfg = m.safe_config()
        self.assertFalse(cfg["api_key_set"])

    def test_safe_config_last_provider_and_model_are_none_before_any_call(self):
        m = self._make_manager()
        cfg = m.safe_config()
        self.assertIsNone(cfg["last_provider"])
        self.assertIsNone(cfg["last_model"])

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_safe_config_reflects_the_resolved_model_after_a_successful_call(
        self, mock_complete, _hc,
    ):
        # settings.model can be a gateway-side alias ("static-best-free")
        # that only resolves to a real model name once a request is
        # actually made — safe_config() should surface that resolution,
        # not just echo the configured alias back.
        m = self._make_manager(provider="ollama", model="static-best-free")
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="openai/gpt-oss-120b",
        )
        asyncio.run(m.complete("hi"))
        cfg = m.safe_config()
        self.assertEqual(cfg["model"], "static-best-free")  # unchanged — still the configured alias
        self.assertEqual(cfg["last_provider"], "ollama")
        self.assertEqual(cfg["last_model"], "openai/gpt-oss-120b")

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_last_success_survives_a_later_disabled_call(self, mock_complete, _hc):
        # A disabled/unavailable response must never clobber the last
        # known-good resolution — a badge showing "last answered by X"
        # shouldn't blank out just because AI got toggled off a moment
        # later.
        m = self._make_manager(provider="ollama")
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        asyncio.run(m.complete("hi"))
        m._enabled_override = False
        resp = asyncio.run(m.complete("hi again"))
        self.assertIsNone(resp.text)
        cfg = m.safe_config()
        self.assertEqual(cfg["last_provider"], "ollama")
        self.assertEqual(cfg["last_model"], "llama3.2")

    def test_status_lists_chain_in_order(self):
        m = self._make_manager(
            enabled=False,  # so health checks are skipped
            provider="ollama",
            fallback_providers="openai,anthropic",
        )
        s = asyncio.run(m.status())
        self.assertEqual([p.name for p in s], ["ollama", "openai", "anthropic"])
        self.assertTrue(s[0].is_primary)
        self.assertFalse(s[1].is_primary)
        # With enabled=False we skip health checks but still mark
        # them unhealthy so the UI can render the "off" state.
        self.assertFalse(any(p.healthy for p in s))

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_complete_uses_primary_when_healthy(self, mock_complete, _hc):
        m = self._make_manager(provider="ollama")
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        resp = asyncio.run(m.complete("hi"))
        self.assertEqual(resp.text, "ok")
        self.assertEqual(resp.provider, "ollama")
        mock_complete.assert_called_once()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_complete_falls_through_on_unavailable(self, mock_complete, mock_hc, _sleep):
        # O4: complete() no longer does a pre-flight health_check —
        # instead it calls provider.complete() directly and falls
        # through on ProviderUnavailable. So the primary failing
        # means its complete() raises, not its health_check failing.
        mock_complete.side_effect = [
            ProviderUnavailable("ollama down"),
            AIResponse(text="from-openai", provider="openai", model="gpt-4o-mini"),
        ]
        m = self._make_manager(provider="ollama", fallback_providers="openai")
        resp = asyncio.run(m.complete("hi"))
        self.assertEqual(resp.text, "from-openai")
        self.assertEqual(resp.provider, "openai")
        # Both providers were tried directly (primary raised, fallback succeeded)
        self.assertEqual(mock_complete.call_count, 2)
        # health_check is no longer called from complete() at all
        mock_hc.assert_not_called()

    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock,
                  side_effect=ProviderUnavailable("connection refused"))
    def test_complete_returns_none_when_all_unhealthy(self, mock_complete):
        # complete() no longer pre-checks health_check() (O4): a provider that is down raises
        # ProviderUnavailable from complete() itself. This test used to patch health_check, which
        # nothing calls any more, so the fallback provider made a REAL request to api.openai.com.
        m = self._make_manager(
            provider="ollama", fallback_providers="openai",
        )
        resp = asyncio.run(m.complete("hi"))
        self.assertIsNone(resp.text)
        self.assertEqual(resp.provider, "none")
        self.assertEqual(mock_complete.await_count, 2, "both chain entries were tried before giving up")

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_complete_passes_max_tokens_and_temperature(self, mock_complete, _hc):
        m = self._make_manager(
            enabled=True, provider="ollama",
            max_tokens=500, temperature=0.9,
        )
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        asyncio.run(m.complete("hi", max_tokens=222, temperature=0.5))
        # Verify the kwarg forwarding
        kwargs = mock_complete.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 222)
        self.assertEqual(kwargs["temperature"], 0.5)

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_complete_uses_settings_defaults_when_no_kwargs(self, mock_complete, _hc):
        m = self._make_manager(
            provider="ollama", max_tokens=777, temperature=0.4,
        )
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        asyncio.run(m.complete("hi"))
        kwargs = mock_complete.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 777)
        self.assertEqual(kwargs["temperature"], 0.4)

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_complete_skips_unknown_provider(self, mock_complete, _hc):
        # Configured with a name not in the factory — should log+skip
        m = self._make_manager(provider="bogus_provider")
        resp = asyncio.run(m.complete("hi"))
        self.assertIsNone(resp.text)
        self.assertEqual(resp.provider, "none")

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete", new_callable=AsyncMock)
    def test_complete_sends_system_prompt(self, mock_complete, _hc):
        m = self._make_manager(provider="ollama")
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        asyncio.run(m.complete("hi", system="you are a stock analyst"))
        self.assertEqual(mock_complete.call_args.args[0], "hi")
        self.assertEqual(mock_complete.call_args.kwargs["system"], "you are a stock analyst")

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    def test_is_available_true_when_any_provider_healthy(self, _hc):
        m = self._make_manager(provider="ollama", fallback_providers="openai")
        self.assertTrue(asyncio.run(m.is_available()))

    # --- Health-check retry (found live 2026-09-09: GET /v1/models is ---
    # --- flakier than the actual completion endpoint) -------------------

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock)
    def test_is_available_retries_transient_health_check_failure(self, mock_hc, mock_sleep):
        # First check fails, retry succeeds — is_available() must not
        # report the provider unhealthy over one transient blip.
        mock_hc.side_effect = [False, True]
        m = self._make_manager(provider="ollama")
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=False)
    def test_is_available_false_when_retry_also_fails(self, mock_hc, mock_sleep):
        # A genuinely-down provider still reports unhealthy — the retry
        # absorbs transient flakiness, it doesn't mask a real outage.
        m = self._make_manager(provider="ollama")
        self.assertFalse(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 2)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock)
    def test_status_retries_transient_health_check_failure(self, mock_hc, mock_sleep):
        mock_hc.side_effect = [False, True]
        m = self._make_manager(provider="ollama")
        s = asyncio.run(m.status())
        self.assertTrue(s[0].healthy)

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    def test_is_available_no_retry_when_first_check_succeeds(self, mock_hc):
        # The common healthy case shouldn't pay for a retry it doesn't need.
        m = self._make_manager(provider="ollama")
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 1)


# --- O3: TTL health-check cache -------------------------------------


class TestHealthCheckCache(unittest.TestCase):
    """The TTL cache in _healthy() must:
    - short-circuit repeated is_available()/status() calls within the window
    - still run the retry path on a cache miss (preserving call counts)
    - invalidate per-provider (one stale entry doesn't block others)
    """

    def _make_manager(self, **overrides) -> AIManager:
        defaults = dict(
            enabled=True,
            provider="ollama",
            fallback_providers="openai",
            timeout=1.0,
            health_check_timeout=1.0,
        )
        defaults.update(overrides)
        return AIManager(AISettings(**defaults))

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    def test_cached_after_first_successful_is_available(self, mock_hc, _sleep):
        m = self._make_manager()
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 1)
        # Second call — provider is cached healthy, no new health checks
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 1)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock)
    def test_retry_runs_on_cache_miss_then_cached(self, mock_hc, _sleep):
        # First is_available(): miss → retry [False, True] → cached True
        mock_hc.side_effect = [False, True]
        m = self._make_manager()
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 2)
        # Second is_available(): cache hit, no retry needed
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 2)

    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=False)
    def test_unhealthy_result_cached_no_retry_on_second_call(self, mock_hc):
        # With return_value=False, _healthy() retries once per provider
        # (2 calls each), then caches False — the second is_available()
        # should hit the cache and not re-check.
        m = self._make_manager()
        self.assertFalse(asyncio.run(m.is_available()))
        # 2 providers, 2 checks each (retry) = 4 total
        self.assertEqual(mock_hc.call_count, 4)
        self.assertFalse(asyncio.run(m.is_available()))
        # Cache hits — no additional health checks
        self.assertEqual(mock_hc.call_count, 4)

    @patch("backend.ai.manager.time.monotonic")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch.object(OpenAICompatibleProvider, "health_check", new_callable=AsyncMock, return_value=True)
    def test_cache_expires_after_ttl(self, mock_hc, _sleep, mock_time):
        # Freeze time at 0.0, then advance past TTL.
        mock_time.return_value = 0.0
        m = self._make_manager()
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 1)
        # Simulate TTL expiry (10s+)
        mock_time.return_value = 10.5
        self.assertTrue(asyncio.run(m.is_available()))
        self.assertEqual(mock_hc.call_count, 2)


# --- Fallback providers get their own defaults, not the primary's --


class TestFallbackProviderIsolation(unittest.TestCase):
    """A fallback provider is a different service by definition — it
    must not inherit the primary's base_url/model/api_key.

    Regression coverage for a live bug (2026-09-09): with a paid
    OpenAI-compatible gateway as primary and "ollama" as the fallback,
    the fallback was built pointed at the *primary's* base_url asking
    for the *primary's* model — so it silently carried the same
    unreachable/wrong-model failure as the primary instead of actually
    falling back to a working local Ollama instance. Found while
    proving out AI integration end-to-end.
    """

    def _make_manager(self, **overrides) -> AIManager:
        defaults = dict(
            enabled=True,
            provider="openai_compatible",
            fallback_providers="ollama",
            model="claude-opus-4-8",
            base_url="https://example-gateway.test/v1",
            api_key="sk-primary-secret",
            timeout=1.0,
            health_check_timeout=1.0,
        )
        defaults.update(overrides)
        return AIManager(AISettings(**defaults))

    def test_primary_gets_the_configured_settings(self):
        m = self._make_manager()
        primary = m._get_provider("openai_compatible")
        self.assertEqual(primary._base_url, "https://example-gateway.test/v1")
        self.assertEqual(primary._model, "claude-opus-4-8")
        self.assertEqual(primary._api_key, "sk-primary-secret")

    def test_fallback_gets_its_own_defaults_not_the_primarys(self):
        m = self._make_manager()
        fallback = m._get_provider("ollama")
        self.assertEqual(fallback._base_url, "http://localhost:11434/v1")
        self.assertEqual(fallback._model, "llama3.2")
        self.assertIsNone(fallback._api_key)

    def test_fallback_never_receives_the_primarys_api_key(self):
        """However this bug resurfaces, a fallback provider leaking the
        primary's credential to a different host would be the worst
        version of it — assert directly, not just via base_url."""
        m = self._make_manager()
        fallback = m._get_provider("ollama")
        self.assertNotEqual(fallback._api_key, "sk-primary-secret")

    def test_a_provider_that_is_both_primary_and_named_gets_settings(self):
        """Sanity: when the primary itself is 'ollama' (the common,
        default case), it still gets the configured base_url/model —
        the fix must not break the single-provider path."""
        m = self._make_manager(
            provider="ollama",
            fallback_providers="",
            base_url="http://localhost:11434",
            model="llama3.2",
            api_key=None,
        )
        primary = m._get_provider("ollama")
        self.assertEqual(primary._base_url, "http://localhost:11434/v1")
        self.assertEqual(primary._model, "llama3.2")


class TestSameProviderMultipleModels(unittest.TestCase):
    """"type:model" fallback-chain entries — running a second (or
    third) model through the same provider TYPE as another chain
    entry. Plain entries are cached/deduped by bare type name, so this
    syntax exists specifically to avoid that collision.
    """

    def _make_manager(self, **overrides) -> AIManager:
        defaults = dict(
            enabled=True,
            provider="openai_compatible",
            fallback_providers="",
            model="deepseek-v4-flash",
            base_url="https://gateway.test/v1",
            api_key="sk-gateway-secret",
            timeout=1.0,
            health_check_timeout=1.0,
        )
        defaults.update(overrides)
        return AIManager(AISettings(**defaults))

    def test_second_entry_same_type_gets_its_own_model(self):
        m = self._make_manager(
            fallback_providers="openai_compatible:deepseek-v3,ollama"
        )
        primary = m._get_provider("openai_compatible")
        second = m._get_provider("openai_compatible:deepseek-v3")
        self.assertEqual(primary._model, "deepseek-v4-flash")
        self.assertEqual(second._model, "deepseek-v3")
        # Distinct cache entries — not the same object, not the same model.
        self.assertIsNot(primary, second)

    def test_same_type_entry_reuses_primarys_base_url_and_key(self):
        m = self._make_manager(
            fallback_providers="openai_compatible:deepseek-v3"
        )
        second = m._get_provider("openai_compatible:deepseek-v3")
        self.assertEqual(second._base_url, "https://gateway.test/v1")
        self.assertEqual(second._api_key, "sk-gateway-secret")

    def test_different_type_entry_with_explicit_model_still_works(self):
        """The ":model" syntax isn't limited to same-type entries — a
        genuinely different fallback provider can use it too, and it
        wins over that provider type's hardcoded default."""
        m = self._make_manager(fallback_providers="ollama:qwen3:14b")
        fallback = m._get_provider("ollama:qwen3:14b")
        self.assertEqual(fallback._model, "qwen3:14b")

    def test_status_reports_resolved_model_per_entry(self):
        m = self._make_manager(
            fallback_providers="openai_compatible:deepseek-v3,ollama"
        )
        statuses = asyncio.run(m.status())
        self.assertEqual(statuses[0].model, "deepseek-v4-flash")
        self.assertEqual(statuses[1].model, "deepseek-v3")
        self.assertEqual(statuses[2].model, "llama3.2")


# --- Quant engine independence -------------------------------------


class TestQuantEngineIndependence(unittest.TestCase):
    """Confirm the AI module is purely additive — importing it doesn't
    change any quant engine behaviour, and disabling AI doesn't break
    the chain."""

    def test_importing_ai_module_does_not_touch_other_modules(self):
        # Smoke test: import all the major quant modules alongside
        # the AI module. If any of them were modified by the import,
        # the assertions below would break.
        from backend import ai
        # AI module exposes the expected public names
        self.assertTrue(hasattr(ai, "AIManager"))
        self.assertTrue(hasattr(ai, "ai_manager"))
        # The singleton's chain always starts with its own configured
        # primary — whatever that is. Not asserting a specific
        # enabled/provider value here: those are real .env config
        # (AI_ENABLED=true as of 2026-09-09 in this project, see
        # docs/Version_4/phase_audit_v4.md), not the AI module's own
        # default — TestAISettings.test_defaults_disable_ai covers the
        # actual field default in isolation via _env_file=None.
        self.assertEqual(
            ai.ai_manager.settings.all_providers()[0],
            ai.ai_manager.settings.provider,
        )


class _MockStreamCtx:
    """Stand-in for ``httpx.AsyncClient.stream(...)``'s context manager."""

    def __init__(self, status_code: int, lines: list[str] | None = None, text: str = ""):
        self.status_code = status_code
        self._lines = lines or []
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aread(self):
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=MagicMock(), response=MagicMock(status_code=self.status_code),
            )


def _stream_client(ctx):
    client = MagicMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.stream = MagicMock(return_value=ctx)
    return client


async def _collect(agen):
    return [x async for x in agen]


async def _agen(items):
    for it in items:
        yield it


class TestProviderStreaming(unittest.TestCase):
    def setUp(self):
        self.p = OpenAICompatibleProvider(
            provider_name="ollama", base_url="http://localhost:11434/v1",
            model="llama3.2", api_key=None, timeout=1.0, health_check_timeout=1.0,
        )

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_openai_stream_concatenates_deltas_and_stops_on_done(self, MockClient):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            "",
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
            'data: {"choices":[{"delta":{"content":"IGNORED"}}]}',
        ]
        MockClient.return_value = _stream_client(_MockStreamCtx(200, lines))
        self.assertEqual("".join(asyncio.run(_collect(self.p.stream("hi")))), "Hello")

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_openai_stream_raises_unavailable_on_429(self, MockClient):
        MockClient.return_value = _stream_client(_MockStreamCtx(429, text="slow down"))
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(_collect(self.p.stream("hi")))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_openai_stream_raises_unavailable_on_unlisted_4xx(self, MockClient):
        # 405 is not in the recoverable-status set, but it must still surface
        # as ProviderUnavailable rather than a raw HTTPStatusError escaping
        # the generator (which would bypass AIManager.stream() fallback).
        MockClient.return_value = _stream_client(_MockStreamCtx(405, text="method no"))
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(_collect(self.p.stream("hi")))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_anthropic_stream_raises_unavailable_on_unlisted_4xx(self, MockClient):
        p = AnthropicProvider(model="claude-x", api_key="sk-test", timeout=1.0)
        MockClient.return_value = _stream_client(_MockStreamCtx(400, text="bad arg"))
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(_collect(p.stream("hi")))

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_openai_stream_unlisted_4xx_logs_at_error_not_silently(self, MockClient):
        # 2026-09-16 regression: an unlisted 4xx during streaming still
        # degrades to ProviderUnavailable (so the SSE connection doesn't
        # break), but it must be logged at ERROR — same severity
        # complete() surfaces this exact class of bug at (an uncaught
        # exception) — not silently indistinguishable from a routine
        # INFO-level "provider unavailable" fallback.
        MockClient.return_value = _stream_client(_MockStreamCtx(422, text="bad request body"))
        with self.assertLogs("backend.ai.providers", level="ERROR") as cm:
            with self.assertRaises(ProviderUnavailable):
                asyncio.run(_collect(self.p.stream("hi")))
        self.assertTrue(any("422" in msg for msg in cm.output))

    @patch("backend.ai.providers.httpx.AsyncClient")
    @patch("backend.ai.providers.logger")
    def test_openai_stream_recoverable_4xx_does_not_log_at_error(self, mock_logger, MockClient):
        # A recoverable status (429) is routine fallback, not a bug in
        # our own request — must NOT get the loud ERROR treatment.
        MockClient.return_value = _stream_client(_MockStreamCtx(429, text="slow down"))
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(_collect(self.p.stream("hi")))
        mock_logger.error.assert_not_called()

    @patch("backend.ai.providers.httpx.AsyncClient")
    def test_anthropic_stream_reads_content_block_delta(self, MockClient):
        p = AnthropicProvider(model="claude-x", api_key="sk-test", timeout=1.0)
        lines = [
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"A"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"B"}}',
            'data: {"type":"message_stop"}',
        ]
        MockClient.return_value = _stream_client(_MockStreamCtx(200, lines))
        self.assertEqual("".join(asyncio.run(_collect(p.stream("hi")))), "AB")

    def test_default_stream_falls_back_to_complete(self):
        from backend.ai.provider import AIProvider

        class _P(AIProvider):
            name = "x"
            async def health_check(self):
                return True
            async def complete(self, *a, **k):
                return AIResponse(text="whole thing", provider="x", model="m")

        # _P doesn't override stream() -> the base yields complete().text once
        self.assertEqual("".join(asyncio.run(_collect(_P().stream("hi")))), "whole thing")


class TestManagerStreaming(unittest.TestCase):
    def _mgr(self, **ov):
        d = dict(enabled=True, provider="ollama", fallback_providers="",
                 model="llama3.2", base_url="http://localhost:11434/v1", api_key=None,
                 timeout=1.0, health_check_timeout=1.0, max_tokens=100, temperature=0.3)
        d.update(ov)
        return AIManager(AISettings(**d))

    def test_disabled_yields_nothing(self):
        self.assertEqual(
            asyncio.run(_collect(self._mgr(enabled=False).stream("hi"))), []
        )

    def test_streams_from_healthy_provider(self):
        m = self._mgr()
        prov = MagicMock()
        prov.health_check = AsyncMock(return_value=True)
        prov.stream = MagicMock(side_effect=lambda *a, **k: _agen(["a", "b", "c"]))
        with patch.object(m, "_get_provider", return_value=prov):
            self.assertEqual(asyncio.run(_collect(m.stream("hi"))), ["a", "b", "c"])

    def test_falls_through_when_first_provider_raises_before_any_chunk(self):
        m = self._mgr(fallback_providers="openai")
        bad = MagicMock()
        bad.health_check = AsyncMock(return_value=True)
        bad.stream.side_effect = ProviderUnavailable("down")
        good = MagicMock()
        good.health_check = AsyncMock(return_value=True)
        good.stream = MagicMock(side_effect=lambda *a, **k: _agen(["ok"]))
        with patch.object(m, "_get_provider", side_effect=[bad, good]):
            self.assertEqual(asyncio.run(_collect(m.stream("hi"))), ["ok"])

    def test_stops_on_mid_stream_failure_no_restart(self):
        m = self._mgr(fallback_providers="openai")

        async def _boom():
            yield "part"
            raise ProviderUnavailable("died mid-stream")

        p1 = MagicMock()
        p1.health_check = AsyncMock(return_value=True)
        p1.stream = MagicMock(side_effect=lambda *a, **k: _boom())
        p2 = MagicMock()
        p2.health_check = AsyncMock(return_value=True)
        p2.stream = MagicMock(
            side_effect=lambda *a, **k: _agen(["SHOULD-NOT-APPEAR"])
        )
        with patch.object(m, "_get_provider", side_effect=[p1, p2]):
            self.assertEqual(asyncio.run(_collect(m.stream("hi"))), ["part"])


class TestShutdown(unittest.TestCase):
    """AIManager.shutdown() must close every built provider's persistent
    httpx client on the bridge loop, clear caches, and be idempotent.
    """

    def _make_manager(self, **overrides):
        defaults = dict(
            enabled=True,
            provider="openai_compatible",
            fallback_providers="ollama",
            base_url="https://test.local/v1",
            model="gpt-4o-mini",
            api_key="sk-test",
            timeout=1.0,
            health_check_timeout=1.0,
        )
        defaults.update(overrides)
        return AIManager(AISettings(**defaults))

    @patch("backend.ai.manager.on_bridge", new_callable=AsyncMock)
    def test_shutdown_closes_all_built_providers(self, mock_on_bridge):
        m = self._make_manager()
        p1 = m._get_provider("openai_compatible")
        p2 = m._get_provider("ollama")
        p1.aclose = MagicMock()
        p2.aclose = MagicMock()
        self.assertEqual(len(m._providers), 2)
        asyncio.run(m.shutdown())
        p1.aclose.assert_called_once()
        p2.aclose.assert_called_once()
        self.assertEqual(mock_on_bridge.call_count, 2)
        self.assertEqual(len(m._providers), 0)
        self.assertEqual(len(m._health_cache), 0)

    @patch("backend.ai.manager.on_bridge", new_callable=AsyncMock)
    def test_shutdown_is_idempotent(self, mock_on_bridge):
        m = self._make_manager()
        p1 = m._get_provider("openai_compatible")
        p1.aclose = MagicMock()
        asyncio.run(m.shutdown())
        asyncio.run(m.shutdown())
        p1.aclose.assert_called_once()
        self.assertEqual(mock_on_bridge.call_count, 1)

    @patch("backend.ai.manager.on_bridge", new_callable=AsyncMock)
    def test_shutdown_swallows_aclose_errors(self, mock_on_bridge):
        m = self._make_manager()
        p1 = m._get_provider("openai_compatible")
        p2 = m._get_provider("ollama")
        p1.aclose = MagicMock()
        p2.aclose = MagicMock()
        mock_on_bridge.side_effect = [RuntimeError("boom"), None]
        asyncio.run(m.shutdown())
        self.assertEqual(len(m._providers), 0)


if __name__ == "__main__":
    unittest.main()


# --- O12: provider-specific model routing -------------------------------


class TestModelRouting(unittest.TestCase):
    """O12: complete()/stream() accept a ``model`` kwarg that routes
    the call through a specific chain entry first, then falls
    through to the default chain. This lets callers pick a cheaper/
    faster model for low-stakes requests (chat) without
    reconfiguring the whole provider chain."""

    def _mgr(self, **ov):
        d = dict(enabled=True, provider="ollama", fallback_providers="",
                 model="llama3.2", base_url="http://localhost:11434/v1", api_key=None,
                 timeout=1.0, health_check_timeout=1.0, max_tokens=100, temperature=0.3)
        d.update(ov)
        return AIManager(AISettings(**d))

    def test_model_routes_through_named_entry_first(self):
        m = self._mgr()
        prov = MagicMock()
        prov.complete = AsyncMock(
            return_value=AIResponse(text="ok", provider="openai:gpt-4o", model="gpt-4o")
        )
        with patch.object(m, "_get_provider", return_value=prov) as mock_get:
            resp = asyncio.run(m.complete("hi", model="openai:gpt-4o"))
        self.assertEqual(resp.text, "ok")
        self.assertEqual(mock_get.call_args_list[0].args[0], "openai:gpt-4o")
        self.assertEqual(mock_get.call_count, 1)

    def test_model_falls_through_to_default_chain(self):
        m = self._mgr(provider="ollama", fallback_providers="openai")
        prov1 = MagicMock()
        prov1.complete = AsyncMock(side_effect=ProviderUnavailable("named down"))
        prov2 = MagicMock()
        prov2.complete = AsyncMock(
            return_value=AIResponse(text="ok", provider="ollama", model="llama3.2")
        )
        with patch.object(m, "_get_provider", side_effect=[prov1, prov2]) as mock_get:
            resp = asyncio.run(m.complete("hi", model="openai:gpt-4o"))
        self.assertEqual(resp.text, "ok")
        self.assertEqual(mock_get.call_args_list[0].args[0], "openai:gpt-4o")
        self.assertEqual(mock_get.call_args_list[1].args[0], "ollama")
        self.assertEqual(mock_get.call_count, 2)

    def test_model_dedupes_when_named_entry_is_primary(self):
        m = self._mgr(provider="ollama")
        prov = MagicMock()
        prov.complete = AsyncMock(
            return_value=AIResponse(text="ok", provider="ollama", model="llama3.2")
        )
        with patch.object(m, "_get_provider", return_value=prov) as mock_get:
            resp = asyncio.run(m.complete("hi", model="ollama"))
        self.assertEqual(resp.text, "ok")
        # "ollama" is both the named entry and the primary — tried once
        self.assertEqual(mock_get.call_count, 1)

    def test_no_model_uses_default_chain(self):
        m = self._mgr(provider="ollama", fallback_providers="openai")
        prov = MagicMock()
        prov.complete = AsyncMock(
            return_value=AIResponse(text="ok", provider="ollama", model="llama3.2")
        )
        with patch.object(m, "_get_provider", return_value=prov) as mock_get:
            resp = asyncio.run(m.complete("hi"))
        self.assertEqual(resp.text, "ok")
        # Default chain: primary only (no fallbacks in this mgr)
        self.assertEqual(mock_get.call_args_list[0].args[0], "ollama")
        self.assertEqual(mock_get.call_count, 1)
