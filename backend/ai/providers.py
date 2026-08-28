"""
Concrete Phase 15 provider implementations.

All providers speak HTTP via ``httpx`` so we don't need to install
``openai`` or ``anthropic`` SDKs. Each one exposes the same
``AIProvider`` interface and raises ``ProviderUnavailable`` on the
two recoverable failure modes (connection / 4xx). Authentication
errors (missing/bad API key) are treated as ``ProviderUnavailable``
so the manager can fall through to the next chain entry.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.ai.provider import AIProvider, AIResponse, ProviderUnavailable

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(AIProvider):
    """Talks the OpenAI ``/v1/chat/completions`` dialect.

    Used for:
    - ``ollama`` (OpenAI-compat mode on http://localhost:11434/v1)
    - ``lm_studio`` (http://localhost:1234/v1)
    - ``openai`` (https://api.openai.com/v1)
    - ``openrouter`` (https://openrouter.ai/api/v1)
    - generic ``openai_compatible`` (whatever ``base_url`` points at)

    The bearer token is sent only when ``api_key`` is set; Ollama
    and LM Studio ignore it but accept an empty string.
    """

    name: str = "openai_compatible"

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout: float = 30.0,
        health_check_timeout: float = 2.0,
    ) -> None:
        self.name = provider_name
        # Strip a trailing slash so urljoin doesn't double up.
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._health_check_timeout = health_check_timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def health_check(self) -> bool:
        """Hit ``/v1/models`` (Ollama / LM Studio / OpenAI / OpenRouter).

        Returns False on any error so the manager can fall through.
        """
        url = f"{self._base_url}/models"
        try:
            with httpx.Client(timeout=self._health_check_timeout) as client:
                r = client.get(url, headers=self._headers())
            return r.status_code == 200
        except (httpx.HTTPError, httpx.StreamError) as e:
            logger.debug("AI health check failed for %s: %s", self.name, e)
            return False
        except Exception as e:  # noqa: BLE001 — defensive, never raise
            logger.warning("AI health check raised for %s: %s", self.name, e)
            return False

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        url = f"{self._base_url}/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature

        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(url, json=body, headers=self._headers())
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise ProviderUnavailable(f"{self.name} unreachable: {e}") from e

        if r.status_code in (401, 403, 404):
            # Auth / not-found — treat as recoverable so the manager
            # can try the next provider (e.g. Ollama is up but the
            # model name is wrong).
            raise ProviderUnavailable(
                f"{self.name} auth/routing error {r.status_code}: {r.text[:200]}"
            )
        if r.status_code >= 500:
            raise ProviderUnavailable(f"{self.name} server error {r.status_code}")
        if r.status_code >= 400:
            # Other client errors (malformed request, rate limit). The
            # request is broken — we shouldn't keep retrying. Raise
            # the original exception so the caller sees it.
            r.raise_for_status()

        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderUnavailable(
                f"{self.name} returned unexpected payload: {e}"
            ) from e

        return AIResponse(
            text=text,
            provider=self.name,
            model=data.get("model", self._model),
            raw=data,
        )


class AnthropicProvider(AIProvider):
    """Anthropic Messages API (https://api.anthropic.com/v1/messages).

    Note: Anthropic's API differs from OpenAI's (system prompt is a
    top-level field, not a message, and the response shape is
    ``content[0].text``). Kept as a separate class to make the
    special-casing obvious.
    """

    name: str = "anthropic"

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        model: str = "claude-3-5-sonnet-latest",
        api_key: str | None = None,
        timeout: float = 30.0,
        health_check_timeout: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._health_check_timeout = health_check_timeout

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    def health_check(self) -> bool:
        # Anthropic has no list-models endpoint. Probe a tiny
        # completion request with max_tokens=1 — if the API key is
        # bad, this returns 401; if the network is up, this returns
        # 200 in <1s.
        if not self._api_key:
            return False
        try:
            with httpx.Client(timeout=self._health_check_timeout) as client:
                r = client.post(
                    f"{self._base_url}/v1/messages",
                    headers=self._headers(),
                    json={
                        "model": self._model,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            return r.status_code == 200
        except (httpx.HTTPError, httpx.StreamError):
            return False
        except Exception:  # noqa: BLE001
            return False

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        if not self._api_key:
            raise ProviderUnavailable("anthropic: api_key not set")
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or 1000,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature

        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._base_url}/v1/messages",
                    headers=self._headers(),
                    json=body,
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise ProviderUnavailable(f"anthropic unreachable: {e}") from e

        if r.status_code in (401, 403, 404):
            raise ProviderUnavailable(
                f"anthropic auth/routing error {r.status_code}: {r.text[:200]}"
            )
        if r.status_code >= 500:
            raise ProviderUnavailable(f"anthropic server error {r.status_code}")
        if r.status_code >= 400:
            r.raise_for_status()

        data = r.json()
        try:
            text = "".join(
                block.get("text", "")
                for block in data["content"]
                if block.get("type") == "text"
            )
        except (KeyError, TypeError) as e:
            raise ProviderUnavailable(
                f"anthropic returned unexpected payload: {e}"
            ) from e

        return AIResponse(
            text=text,
            provider=self.name,
            model=data.get("model", self._model),
            raw=data,
        )


# --- Factory --------------------------------------------------------

_PROVIDER_CLASSES: dict[str, type[AIProvider]] = {
    "ollama": OpenAICompatibleProvider,
    "lm_studio": OpenAICompatibleProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "openai": OpenAICompatibleProvider,
    "openrouter": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}

# Sensible default base URLs for the providers that need one.
_DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://localhost:11434/v1",
    "lm_studio": "http://localhost:1234/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# Sensible default model hints.
_DEFAULT_MODELS: dict[str, str] = {
    "ollama": "llama3.2",
    "lm_studio": "local-model",
    "openai": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
}


def build_provider(
    name: str,
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    timeout: float,
    health_check_timeout: float,
) -> AIProvider:
    """Factory: instantiate the right provider class for ``name``.

    Raises ``ValueError`` for unknown names — the manager logs and
    skips to the next fallback.
    """
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"Unknown AI provider: {name!r}")

    if name == "anthropic":
        return AnthropicProvider(
            base_url=base_url or "https://api.anthropic.com",
            model=model or _DEFAULT_MODELS["anthropic"],
            api_key=api_key,
            timeout=timeout,
            health_check_timeout=health_check_timeout,
        )
    # OpenAI-compatible family.
    if name == "openai_compatible":
        if not base_url:
            raise ValueError("openai_compatible requires base_url")
        provider_name = "openai_compatible"
    else:
        provider_name = name
    resolved_base = base_url or _DEFAULT_BASE_URLS.get(name, "http://localhost:11434/v1")
    resolved_model = model or _DEFAULT_MODELS.get(name, "gpt-3.5-turbo")
    return OpenAICompatibleProvider(
        provider_name=provider_name,
        base_url=resolved_base,
        model=resolved_model,
        api_key=api_key,
        timeout=timeout,
        health_check_timeout=health_check_timeout,
    )


__all__ = [
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "build_provider",
    "ProviderUnavailable",
    "AIResponse",
    "AIProvider",
]
