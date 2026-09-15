"""
Phase 15 — AIProvider abstract base.

Defines the contract every provider adapter must implement:

- ``name``: short identifier registered with ``AIManager`` (e.g. ``ollama``)
- ``health_check()``: cheap call used by the manager to decide whether
  to try this provider, or fall through to the next in the chain
- ``complete(prompt, system, **kwargs)``: blocking text completion

The base class intentionally has no SDK dependencies — all providers
use ``httpx`` so the test suite doesn't have to install ``openai`` or
``anthropic`` SDKs. Real-world deployments are free to add SDK-backed
adapters in addition to (or in place of) the HTTP ones.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass
class AIResponse:
    """Normalised provider response.

    ``text`` is the provider's reply (or ``None`` on hard failure).
    ``provider`` records which adapter produced the answer (useful
    for debugging fallback chains). ``model`` is the actual model
    used, which may differ from the configured default (e.g. when
    the provider routes through several models).
    """

    text: str | None
    provider: str
    model: str
    raw: dict[str, Any] | None = None
    # True when the provider was asked to return structured JSON
    # (via response_format / tool-use). Lets callers skip regex
    # extraction and parse the raw text directly.
    structured: bool = False


class AIProvider(ABC):
    """Abstract base for all AI providers.

    Implementations must be cheap to construct (the manager may
    instantiate several in a row to run health checks) and must
    surface a clean ``ProviderUnavailable`` exception for the two
    cases the manager handles: connection refused / timeout (the
    provider is down) vs. 4xx/5xx (the request is malformed).
    """

    name: str = "abstract"
    # O11: whether this provider supports a structured JSON output
    # guarantee (OpenAI response_format, Anthropic tool-use). When
    # True, the manager passes a JSON schema and the response text is
    # guaranteed to be valid JSON — callers can skip regex extraction.
    supports_structured_output: bool = False

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider can answer right now.

        Implementations should use ``self.settings.health_check_timeout``
        and MUST NOT raise — catch transport errors and return False.
        """

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AIResponse:
        """Return a completion for ``prompt`` (with optional system prompt).

        Raises ``ProviderUnavailable`` when the provider is unreachable
        or the request is structurally invalid (bad API key, missing
        model, etc.) so the manager can try the next fallback.
        """

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield the completion for ``prompt`` in incremental text chunks.

        Same ``ProviderUnavailable`` contract as :meth:`complete` — a
        provider that can't start the stream raises so the manager can
        fall through. The default implementation is non-streaming: it
        runs :meth:`complete` and yields the whole reply once, so a
        provider that hasn't implemented real streaming still works.
        """
        resp = await self.complete(
            prompt, system=system, max_tokens=max_tokens,
            temperature=temperature, response_format=response_format,
        )
        if resp.text:
            yield resp.text

    async def aclose(self) -> None:
        """Release held resources (persistent HTTP clients, pools).

        Default no-op so providers without pooled state can ignore it.
        ``AIManager.shutdown()`` calls this on every built provider at
        app shutdown; implementations holding an ``httpx.AsyncClient``
        MUST close it here, and MUST assume it is awaited on the event
        loop the client was used on (the sync-bridge loop), not an
        arbitrary caller's loop.
        """


class ProviderUnavailable(RuntimeError):
    """Raised when a provider can't answer the request.

    The manager treats this as "try the next provider". Other
    exceptions are treated as fatal and re-raised.
    """
