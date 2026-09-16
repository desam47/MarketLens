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

import json
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

import httpx

from backend.ai.provider import AIProvider, AIResponse, ProviderUnavailable

logger = logging.getLogger(__name__)


def _raise_if_unavailable(status_code: int, name: str, body_preview: str = "") -> None:
    """Shared response-status classification for ``complete`` / ``stream``.

    401/402/403/404/429 and 5xx are recoverable — raise ``ProviderUnavailable``
    so the manager tries the next provider. Other 4xx mean our own request
    is malformed and should be loud; the caller handles those.
    """
    if status_code in (401, 402, 403, 404, 429):
        raise ProviderUnavailable(
            f"{name} auth/routing/rate-limit error {status_code}: {body_preview[:200]}"
        )
    if status_code >= 500:
        raise ProviderUnavailable(f"{name} server error {status_code}")


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
        structured_output: bool = False,
    ) -> None:
        self.name = provider_name
        # Strip a trailing slash so urljoin doesn't double up.
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._health_check_timeout = health_check_timeout
        # O11: when True, the provider sends response_format={"type":"json_object"}
        # and the manager trusts the response is valid JSON (skips regex).
        self.supports_structured_output = structured_output
        # Persistent clients with connection pooling — avoids TCP/TLS
        # setup on every call. Lazy-initialized on first use (under
        # _client_lock) because pooled connections bind to the event
        # loop that opens them — see backend/ai/sync_bridge.py.
        self._client: httpx.AsyncClient | None = None
        self._hc_client: httpx.AsyncClient | None = None
        self._client_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _get_client(self) -> httpx.AsyncClient:
        """Return the persistent completion client, creating it lazily.

        Double-checked under ``_client_lock``: first-use can be hit by
        several threads at once (to_thread workers, a FastAPI request
        beside a background job), and an unlocked check-then-create
        lets each loser build a client whose pool never gets closed.
        """
        client = self._client
        if client is not None and not client.is_closed:
            return client
        with self._client_lock:
            client = self._client
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=self._timeout,
                    limits=httpx.Limits(
                        max_connections=10, max_keepalive_connections=5,
                    ),
                )
                self._client = client
            return client

    def _get_hc_client(self) -> httpx.AsyncClient:
        """Return the persistent health-check client (shorter timeout).

        Same double-checked locking as :meth:`_get_client`.
        """
        client = self._hc_client
        if client is not None and not client.is_closed:
            return client
        with self._client_lock:
            client = self._hc_client
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=self._health_check_timeout,
                    limits=httpx.Limits(
                        max_connections=2, max_keepalive_connections=1,
                    ),
                )
                self._hc_client = client
            return client

    async def health_check(self) -> bool:
        """Hit ``/v1/models`` (Ollama / LM Studio / OpenAI / OpenRouter).

        Returns False on any error so the manager can fall through.
        """
        url = f"{self._base_url}/models"
        try:
            r = await self._get_hc_client().get(url, headers=self._headers())
            return r.status_code == 200
        except (httpx.HTTPError, httpx.StreamError) as e:
            logger.debug("AI health check failed for %s: %s", self.name, e)
            return False
        except Exception as e:  # noqa: BLE001 — defensive, never raise
            logger.warning("AI health check raised for %s: %s", self.name, e)
            return False

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
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
        # O11: structured output — only request JSON mode when this provider
        # actually supports it. Previously any non-None response_format was
        # forwarded even to providers that don't honor it, which is fine for
        # the uniform-settings case but produces a wrong `structured=True`
        # contract if a non-supporting provider ever answers. Gating the
        # request on self.supports_structured_output keeps the request shape
        # and the ai_resp.structured flag in lockstep.
        if response_format is not None and self.supports_structured_output:
            body["response_format"] = response_format

        try:
            r = await self._get_client().post(url, json=body, headers=self._headers())
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise ProviderUnavailable(f"{self.name} unreachable: {e}") from e

        if r.status_code in (401, 402, 403, 404, 429):
            # Auth / payment-required / not-found / rate-limited — treat as
            # recoverable so the manager can try the next provider (e.g.
            # Ollama is up but the model name is wrong; or, found live
            # 2026-09-09, openrouter's free-tier model returned a bare 429;
            # or, found live 2026-09-11, a gateway with exhausted credits
            # returns 402 instead of 401). None of these mean our request
            # is malformed — they belong with the other "provider
            # unavailable right now" cases below, not with genuine 4xx
            # request errors (see the raise_for_status() branch below,
            # which is deliberately NOT caught: those mean our own request
            # is broken and should be loud, not silently degraded). Before
            # this fix, these codes fell into that uncaught branch and
            # crashed the whole HTTP request with an unhandled 500 —
            # violating /api/ai/analyze's own contract of never 500ing for
            # a provider-side failure.
            raise ProviderUnavailable(
                f"{self.name} auth/routing/rate-limit error {r.status_code}: {r.text[:200]}"
            )
        if r.status_code >= 500:
            raise ProviderUnavailable(f"{self.name} server error {r.status_code}")
        if r.status_code >= 400:
            # Other client errors (malformed request). The request is
            # broken — we shouldn't keep retrying. Raise the original
            # exception so the caller sees it.
            r.raise_for_status()

        try:
            data = r.json()
        except ValueError as e:
            # A 200 with a non-JSON body — e.g. an HTML page (a
            # misconfigured base_url missing /v1 can land on a gateway's
            # own web UI instead of its API; a maintenance page; a proxy
            # error page) — is exactly as "this provider didn't give us
            # a usable answer" as a 4xx/5xx, but was raising a raw
            # json.JSONDecodeError that nothing upstream caught, crashing
            # the whole request with an unhandled 500 instead of falling
            # through to the next provider or an UncertaintyResponse.
            # Found live 2026-09-09 with exactly that base_url mistake.
            raise ProviderUnavailable(
                f"{self.name} returned a non-JSON body (HTTP {r.status_code}): {e}"
            ) from e
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
            structured=bool(response_format is not None and self.supports_structured_output),
        )

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        url = f"{self._base_url}/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None and self.supports_structured_output:
            body["response_format"] = response_format

        try:
            async with self._get_client().stream(
                "POST", url, json=body, headers=self._headers()
            ) as r:
                if r.status_code >= 400:
                    await r.aread()
                    _raise_if_unavailable(r.status_code, self.name, r.text)
                    # Anything reaching here is a 4xx NOT in
                    # _raise_if_unavailable's recoverable set — genuinely
                    # our own malformed request, the same class complete()
                    # lets raise uncaught so it surfaces loudly (see that
                    # method's comment). Streaming still degrades this to
                    # ProviderUnavailable below rather than raising
                    # uncaught (an uncaught exception mid-SSE-stream
                    # previously broke the connection ungracefully), but
                    # logging it at ERROR here — before it's downgraded to
                    # the same INFO-level "provider unavailable" fallback
                    # message as a routine outage — keeps a genuine bug in
                    # our own request from being silently indistinguishable
                    # from the provider just being down.
                    logger.error(
                        "%s returned request-error status %d during streaming "
                        "(not recoverable/transient — likely a malformed "
                        "request on our side): %s",
                        self.name, r.status_code, r.text[:500],
                    )
                    r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        piece = chunk["choices"][0].get("delta", {}).get("content")
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    if piece:
                        yield piece
        except httpx.HTTPError as e:
            # Covers ConnectError/TimeoutException/NetworkError/StreamError
            # (transport failures) and HTTPStatusError from raise_for_status()
            # above for non-recoverable/unlisted 4xx: any of these means the
            # provider can't serve this stream, so convert to ProviderUnavailable
            # and let AIManager.stream() try the next provider (pre-first-chunk)
            # or end the stream gracefully (mid-stream). Previously the narrow
            # (ConnectError, TimeoutException, NetworkError) tuple let a bare
            # HTTPStatusError escape, bypassing fallback and surfacing as an
            # unhandled error to the SSE layer.
            raise ProviderUnavailable(f"{self.name} unreachable: {e}") from e

    async def aclose(self) -> None:
        """Close both persistent clients (idempotent).

        MUST be awaited on the bridge loop — the loop the pooled
        connections were opened on. ``AIManager.shutdown()`` arranges
        that via ``sync_bridge.on_bridge``; nothing should call this
        from an arbitrary loop.
        """
        for attr in ("_client", "_hc_client"):
            client = getattr(self, attr)
            setattr(self, attr, None)
            if client is not None and not client.is_closed:
                await client.aclose()


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
        structured_output: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._health_check_timeout = health_check_timeout
        self.supports_structured_output = structured_output
        # Persistent clients — same pattern as OpenAICompatibleProvider
        # (lazy, lock-guarded, loop-bound; closed by manager.shutdown()).
        self._client: httpx.AsyncClient | None = None
        self._hc_client: httpx.AsyncClient | None = None
        self._client_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    def _get_client(self) -> httpx.AsyncClient:
        """Persistent completion client (double-checked under lock)."""
        client = self._client
        if client is not None and not client.is_closed:
            return client
        with self._client_lock:
            client = self._client
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=self._timeout,
                    limits=httpx.Limits(
                        max_connections=10, max_keepalive_connections=5,
                    ),
                )
                self._client = client
            return client

    def _get_hc_client(self) -> httpx.AsyncClient:
        """Persistent health-check client (double-checked under lock)."""
        client = self._hc_client
        if client is not None and not client.is_closed:
            return client
        with self._client_lock:
            client = self._hc_client
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=self._health_check_timeout,
                    limits=httpx.Limits(
                        max_connections=2, max_keepalive_connections=1,
                    ),
                )
                self._hc_client = client
            return client

    async def health_check(self) -> bool:
        # Anthropic has no list-models endpoint. Probe a tiny
        # completion request with max_tokens=1 — if the API key is
        # bad, this returns 401; if the network is up, this returns
        # 200 in <1s.
        if not self._api_key:
            return False
        try:
            r = await self._get_hc_client().post(
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

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
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
        # O11: Anthropic enforces structured output via tool-use. We
        # declare a single tool with the JSON schema from
        # response_format and force it: the model must call the tool,
        # guaranteeing the response is valid JSON matching the schema.
        if response_format is not None:
            schema = response_format.get("json_schema", response_format)
            tool_name = schema.get("name", "json_output") if isinstance(schema, dict) else "json_output"
            tool_schema = schema if isinstance(schema.get("parameters"), dict) else schema
            body["tools"] = [
                {
                    "name": tool_name,
                    "description": tool_schema.get("description", "Return the JSON result."),
                    "input_schema": tool_schema.get("parameters") or tool_schema,
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": tool_name}

        try:
            r = await self._get_client().post(
                f"{self._base_url}/v1/messages",
                headers=self._headers(),
                json=body,
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise ProviderUnavailable(f"anthropic unreachable: {e}") from e

        if r.status_code in (401, 402, 403, 404, 429):
            # See OpenAICompatibleProvider.complete's identical guard —
            # 402/429 belong with the recoverable "provider unavailable
            # right now" cases, not the uncaught malformed-request
            # branch below.
            raise ProviderUnavailable(
                f"anthropic auth/routing/rate-limit error {r.status_code}: {r.text[:200]}"
            )
        if r.status_code >= 500:
            raise ProviderUnavailable(f"anthropic server error {r.status_code}")
        if r.status_code >= 400:
            r.raise_for_status()

        try:
            data = r.json()
        except ValueError as e:
            # See OpenAICompatibleProvider.complete's identical guard —
            # a 200 with a non-JSON body must fall through like any
            # other bad-response case, not crash with a raw 500.
            raise ProviderUnavailable(
                f"anthropic returned a non-JSON body (HTTP {r.status_code}): {e}"
            ) from e
        try:
            if response_format is not None and data.get("content"):
                # Tool-use path: the model was forced to call our
                # json_output tool, so the first tool_use block holds
                # the guaranteed-valid JSON.
                text = ""
                for block in data["content"]:
                    if block.get("type") == "tool_use":
                        text = json.dumps(block.get("input", {}))
                        break
                if not text:
                    text = "".join(
                        block.get("text", "")
                        for block in data["content"]
                        if block.get("type") == "text"
                    )
            else:
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
            structured=bool(response_format is not None and self.supports_structured_output),
        )

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        if not self._api_key:
            raise ProviderUnavailable("anthropic: api_key not set")
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or 1000,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None:
            schema = response_format.get("json_schema", response_format)
            tool_name = schema.get("name", "json_output") if isinstance(schema, dict) else "json_output"
            tool_schema = schema if isinstance(schema.get("parameters"), dict) else schema
            body["tools"] = [
                {
                    "name": tool_name,
                    "description": tool_schema.get("description", "Return the JSON result."),
                    "input_schema": tool_schema.get("parameters") or tool_schema,
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": tool_name}

        try:
            async with self._get_client().stream(
                "POST", f"{self._base_url}/v1/messages",
                json=body, headers=self._headers(),
            ) as r:
                    if r.status_code >= 400:
                        await r.aread()
                        _raise_if_unavailable(r.status_code, "anthropic", r.text)
                        # See OpenAICompatibleProvider.stream()'s identical
                        # comment — a genuinely malformed request still
                        # degrades to ProviderUnavailable (not raised
                        # uncaught, to avoid breaking the SSE connection
                        # mid-stream), but is logged at ERROR first so it's
                        # not silently indistinguishable from a routine
                        # provider outage.
                        logger.error(
                            "anthropic returned request-error status %d during "
                            "streaming (not recoverable/transient — likely a "
                            "malformed request on our side): %s",
                            r.status_code, r.text[:500],
                        )
                        r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            evt = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        etype = evt.get("type")
                        if etype == "content_block_delta":
                            piece = (evt.get("delta") or {}).get("text")
                            if piece:
                                yield piece
                        elif etype in ("message_stop", "error"):
                            break
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"anthropic unreachable: {e}") from e

    async def aclose(self) -> None:
        """Close both persistent clients (idempotent).

        Same bridge-loop requirement as
        :meth:`OpenAICompatibleProvider.aclose`.
        """
        for attr in ("_client", "_hc_client"):
            client = getattr(self, attr)
            setattr(self, attr, None)
            if client is not None and not client.is_closed:
                await client.aclose()


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
    "openai_compatible": "gpt-3.5-turbo",
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
    structured_output: bool = False,
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
            structured_output=structured_output,
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
        structured_output=structured_output,
    )


__all__ = [
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "build_provider",
    "ProviderUnavailable",
    "AIResponse",
    "AIProvider",
]
