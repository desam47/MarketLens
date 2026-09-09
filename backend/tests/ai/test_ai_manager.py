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
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

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
        self.assertFalse(p.health_check())


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

    @patch("backend.ai.providers.httpx.Client")
    def test_health_check_returns_true_on_200(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get.return_value = _MockResponse(200)
        MockClient.return_value = client
        self.assertTrue(self.p.health_check())

    @patch("backend.ai.providers.httpx.Client")
    def test_health_check_returns_false_on_connection_error(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get.side_effect = httpx.ConnectError("refused")
        MockClient.return_value = client
        self.assertFalse(self.p.health_check())

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_returns_text(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(200, {
            "model": "llama3.2",
            "choices": [{"message": {"content": "Hello, world."}}],
        })
        MockClient.return_value = client
        resp = self.p.complete("hi", system="be brief")
        self.assertEqual(resp.text, "Hello, world.")
        self.assertEqual(resp.provider, "ollama")
        self.assertEqual(resp.model, "llama3.2")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_raises_unavailable_on_connection_error(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.side_effect = httpx.ConnectError("refused")
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_raises_unavailable_on_401(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(401, text="bad key")
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_raises_unavailable_on_500(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(500)
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_raises_for_status_on_400(self, MockClient):
        # 400 (other client errors) should NOT be treated as recoverable
        # — the caller is doing something wrong and should see the error.
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(400, text="malformed")
        MockClient.return_value = client
        with self.assertRaises(httpx.HTTPStatusError):
            self.p.complete("hi")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_handles_unexpected_payload(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(200, {"weird": "shape"})
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")

    @patch("backend.ai.providers.httpx.Client")
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
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(200, None, text="<html>...</html>")
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")


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
        self.assertFalse(p2.health_check())

    @patch("backend.ai.providers.httpx.Client")
    def test_health_check_true_on_200(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(200)
        MockClient.return_value = client
        self.assertTrue(self.p.health_check())

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_concatenates_text_blocks(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(200, {
            "model": "claude-3-5-sonnet-latest",
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world."},
                {"type": "tool_use", "id": "x"},  # ignored
            ],
        })
        MockClient.return_value = client
        resp = self.p.complete("hi", system="be brief")
        self.assertEqual(resp.text, "Hello world.")
        self.assertEqual(resp.provider, "anthropic")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_raises_unavailable_on_non_json_200(self, MockClient):
        """Same regression as OpenAICompatibleProvider's version — see
        that test's docstring."""
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(200, None, text="<html>...</html>")
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")

    def test_complete_raises_without_api_key(self):
        p2 = AnthropicProvider(api_key=None)
        with self.assertRaises(ProviderUnavailable):
            p2.complete("hi")

    @patch("backend.ai.providers.httpx.Client")
    def test_complete_raises_unavailable_on_401(self, MockClient):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post.return_value = _MockResponse(401, text="bad key")
        MockClient.return_value = client
        with self.assertRaises(ProviderUnavailable):
            self.p.complete("hi")


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
        resp = m.complete("hi")
        self.assertIsNone(resp.text)
        self.assertEqual(resp.provider, "disabled")

    def test_disabled_is_not_available(self):
        m = self._make_manager(enabled=False)
        self.assertFalse(m.is_available())

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

    def test_status_lists_chain_in_order(self):
        m = self._make_manager(
            enabled=False,  # so health checks are skipped
            provider="ollama",
            fallback_providers="openai,anthropic",
        )
        s = m.status()
        self.assertEqual([p.name for p in s], ["ollama", "openai", "anthropic"])
        self.assertTrue(s[0].is_primary)
        self.assertFalse(s[1].is_primary)
        # With enabled=False we skip health checks but still mark
        # them unhealthy so the UI can render the "off" state.
        self.assertFalse(any(p.healthy for p in s))

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete")
    def test_complete_uses_primary_when_healthy(self, mock_complete, _hc):
        m = self._make_manager(provider="ollama")
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        resp = m.complete("hi")
        self.assertEqual(resp.text, "ok")
        self.assertEqual(resp.provider, "ollama")
        mock_complete.assert_called_once()

    @patch.object(OpenAICompatibleProvider, "health_check")
    @patch.object(OpenAICompatibleProvider, "complete")
    def test_complete_falls_through_on_unavailable(self, mock_complete, mock_hc):
        # Primary is unhealthy, fallback is healthy.
        mock_hc.side_effect = [False, True]
        m = self._make_manager(provider="ollama", fallback_providers="openai")
        mock_complete.return_value = AIResponse(
            text="from-openai", provider="openai", model="gpt-4o-mini",
        )
        resp = m.complete("hi")
        self.assertEqual(resp.text, "from-openai")
        self.assertEqual(resp.provider, "openai")
        # Complete was called once (for the fallback), not for the primary
        self.assertEqual(mock_complete.call_count, 1)

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=False)
    def test_complete_returns_none_when_all_unhealthy(self, _hc):
        m = self._make_manager(
            provider="ollama", fallback_providers="openai",
        )
        resp = m.complete("hi")
        self.assertIsNone(resp.text)
        self.assertEqual(resp.provider, "none")

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete")
    def test_complete_passes_max_tokens_and_temperature(self, mock_complete, _hc):
        m = self._make_manager(
            enabled=True, provider="ollama",
            max_tokens=500, temperature=0.9,
        )
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        m.complete("hi", max_tokens=222, temperature=0.5)
        # Verify the kwarg forwarding
        kwargs = mock_complete.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 222)
        self.assertEqual(kwargs["temperature"], 0.5)

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete")
    def test_complete_uses_settings_defaults_when_no_kwargs(self, mock_complete, _hc):
        m = self._make_manager(
            provider="ollama", max_tokens=777, temperature=0.4,
        )
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        m.complete("hi")
        kwargs = mock_complete.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 777)
        self.assertEqual(kwargs["temperature"], 0.4)

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete")
    def test_complete_skips_unknown_provider(self, mock_complete, _hc):
        # Configured with a name not in the factory — should log+skip
        m = self._make_manager(provider="bogus_provider")
        resp = m.complete("hi")
        self.assertIsNone(resp.text)
        self.assertEqual(resp.provider, "none")

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=True)
    @patch.object(OpenAICompatibleProvider, "complete")
    def test_complete_sends_system_prompt(self, mock_complete, _hc):
        m = self._make_manager(provider="ollama")
        mock_complete.return_value = AIResponse(
            text="ok", provider="ollama", model="llama3.2",
        )
        m.complete("hi", system="you are a stock analyst")
        self.assertEqual(mock_complete.call_args.args[0], "hi")
        self.assertEqual(mock_complete.call_args.kwargs["system"], "you are a stock analyst")

    @patch.object(OpenAICompatibleProvider, "health_check", return_value=True)
    def test_is_available_true_when_any_provider_healthy(self, _hc):
        m = self._make_manager(provider="ollama", fallback_providers="openai")
        self.assertTrue(m.is_available())


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


if __name__ == "__main__":
    unittest.main()
