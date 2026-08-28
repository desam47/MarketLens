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


class AIProvider(ABC):
    """Abstract base for all AI providers.

    Implementations must be cheap to construct (the manager may
    instantiate several in a row to run health checks) and must
    surface a clean ``ProviderUnavailable`` exception for the two
    cases the manager handles: connection refused / timeout (the
    provider is down) vs. 4xx/5xx (the request is malformed).
    """

    name: str = "abstract"

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the provider can answer right now.

        Implementations should use ``self.settings.health_check_timeout``
        and MUST NOT raise — catch transport errors and return False.
        """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Return a completion for ``prompt`` (with optional system prompt).

        Raises ``ProviderUnavailable`` when the provider is unreachable
        or the request is structurally invalid (bad API key, missing
        model, etc.) so the manager can try the next fallback.
        """


class ProviderUnavailable(RuntimeError):
    """Raised when a provider can't answer the request.

    The manager treats this as "try the next provider". Other
    exceptions are treated as fatal and re-raised.
    """
