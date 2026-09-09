"""
Phase 15 — ``AIManager``: registers providers, runs health checks,
and resolves completions against the configured fallback chain.

The manager is a module-level singleton so the rest of the app
calls ``ai_manager.complete(prompt)`` without juggling instances.
It exposes three primitives:

- ``complete(prompt, system, **kwargs)``: run a completion, walking
  the fallback chain on ``ProviderUnavailable``. Returns
  ``AIResponse(text=None)`` when AI is disabled or every provider
  is unreachable — never raises for these "expected" unavailability
  modes so callers can treat "no AI answer" as a normal outcome.
- ``is_available()``: True if at least one provider in the chain
  passes a health check. Useful for UI badges.
- ``status()``: per-provider health snapshot for dashboards.

API keys are read from settings and never appear in any ``status()``
or ``safe_config()`` payload. Frontend code must use ``safe_config()``
when surfacing the manager to a UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

from backend.ai.provider import AIProvider, AIResponse, ProviderUnavailable
from backend.ai.providers import build_provider
from backend.config.settings import AISettings

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Lightweight per-provider health record for UI consumption."""

    name: str
    healthy: bool
    is_primary: bool
    error: str | None = None


class AIManager:
    """Coordinates the provider chain and exposes the safe public API."""

    def __init__(self, settings: AISettings) -> None:
        self.settings = settings
        # Lazily-constructed providers, keyed by name.
        self._providers: dict[str, AIProvider] = {}
        self._lock = Lock()
        # Runtime override — None means "use settings.enabled"; set True/False
        # to shadow it without needing to rebuild the manager.
        self._enabled_override: bool | None = None

    @property
    def enabled(self) -> bool:
        """Runtime toggle; falls back to settings if not overridden."""
        if self._enabled_override is not None:
            return self._enabled_override
        return self.settings.enabled

    def set_enabled(self, enabled: bool) -> None:
        """Override the enabled flag without rebuilding the manager."""
        self._enabled_override = enabled

    # --- Provider lookup -----------------------------------------------

    def _get_provider(self, name: str) -> AIProvider:
        """Build (or return cached) provider for ``name``.

        ``settings.base_url`` / ``model`` / ``api_key`` configure the
        PRIMARY provider (``settings.provider``) only. A fallback-chain
        entry is, by definition, a different service — applying the
        primary's base_url/model/key to it doesn't make sense and
        silently defeats the whole point of a fallback chain.

        Found live 2026-09-09: with primary=a paid OpenAI-compatible
        gateway and fallback_providers="ollama", the "ollama" fallback
        was built pointed at the *primary's* base_url asking for the
        *primary's* model name — so it inherited the exact same
        unreachable/wrong-model failure as the primary instead of
        actually falling back to the working local Ollama instance.
        Non-primary providers now get ``None`` for these three fields,
        which makes ``build_provider`` fall through to its own
        ``_DEFAULT_BASE_URLS`` / ``_DEFAULT_MODELS`` (e.g. ollama's
        ``http://localhost:11434/v1`` + ``llama3.2``) instead — unless
        this provider happens to also be primary, or is unconfigurable
        without a base_url (``openai_compatible`` requires one; not a
        concern for named providers with sensible defaults).
        """
        with self._lock:
            if name in self._providers:
                return self._providers[name]
            is_primary = name == self.settings.provider
            resolved_base = self.settings.base_url if is_primary else None
            resolved_model = self.settings.model if is_primary else None
            resolved_key = self.settings.api_key if is_primary else None
            # OpenAI-compatible providers (ollama, lm_studio) need /v1
            # appended to the base URL; the user-facing settings.base_url
            # omits it so it's discoverable without knowing the path.
            if name in ("ollama", "lm_studio") and resolved_base:
                if not resolved_base.rstrip("/").endswith("/v1"):
                    resolved_base = resolved_base.rstrip("/") + "/v1"
            provider = build_provider(
                name,
                base_url=resolved_base,
                model=resolved_model,
                api_key=resolved_key,
                timeout=self.settings.timeout,
                health_check_timeout=self.settings.health_check_timeout,
            )
            self._providers[name] = provider
            return provider

    def _all_providers(self) -> list[str]:
        return self.settings.all_providers()

    # --- Public API ----------------------------------------------------

    def is_available(self) -> bool:
        """True iff AI is enabled and at least one provider is healthy.

        We don't cache the answer: a health check takes a couple of
        milliseconds against localhost and several hundred against
        a cloud provider, but the manager is only consulted on
        explicit user requests — there's no high-frequency call site.
        """
        if not self.enabled:
            return False
        return any(self._healthy(name) for name in self._all_providers())

    def status(self) -> list[ProviderStatus]:
        """Per-provider health snapshot for the dashboard.

        Always returns one entry per provider in the chain (even if
        ``enabled`` is False, the providers are still listed as
        ``healthy=False`` so the UI can show "configured but off").
        """
        out: list[ProviderStatus] = []
        for i, name in enumerate(self._all_providers()):
            healthy = False
            err: str | None = None
            if self.enabled:
                try:
                    healthy = self._healthy(name)
                except Exception as e:  # noqa: BLE001
                    err = str(e)[:200]
            out.append(
                ProviderStatus(
                    name=name,
                    healthy=healthy,
                    is_primary=(i == 0),
                    error=err,
                )
            )
        return out

    def safe_config(self) -> dict[str, Any]:
        """Return a frontend-safe view of the configuration.

        Strips ``api_key`` and any other secret-looking fields. The
        UI uses this to render provider badges without ever learning
        the credentials.
        """
        return {
            "enabled": self.enabled,
            "provider": self.settings.provider,
            "fallback_providers": self.settings.fallback_chain(),
            "model": self.settings.model,
            "base_url": self.settings.base_url,
            "timeout": self.settings.timeout,
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature,
            "api_key_set": bool(self.settings.api_key),
        }

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Run a completion, walking the fallback chain.

        Returns ``AIResponse(text=None, provider=...)`` when:
        - ``enabled`` is False (the safe default)
        - every provider in the chain is unavailable

        The ``provider`` field on the returned response records the
        name of the first provider we tried (or "none" if AI is
        disabled) so callers can surface "we tried Ollama" in the UI.
        """
        if not self.enabled:
            return AIResponse(text=None, provider="disabled", model=self.settings.model)

        last_error: str | None = None
        for name in self._all_providers():
            try:
                provider = self._get_provider(name)
            except ValueError as e:
                # Unknown provider name in the chain. Skip.
                logger.warning("Skipping unknown AI provider %r: %s", name, e)
                last_error = str(e)
                continue

            if not provider.health_check():
                logger.info("AI provider %r unhealthy, falling through", name)
                last_error = f"{name} health check failed"
                continue

            try:
                return provider.complete(
                    prompt,
                    system=system,
                    max_tokens=max_tokens or self.settings.max_tokens,
                    temperature=temperature if temperature is not None else self.settings.temperature,
                )
            except ProviderUnavailable as e:
                logger.info("AI provider %r unavailable: %s", name, e)
                last_error = str(e)
                continue

        logger.warning(
            "All AI providers unavailable (last error: %s). Returning empty response.",
            last_error,
        )
        return AIResponse(text=None, provider="none", model=self.settings.model)

    # --- Internals -----------------------------------------------------

    def _healthy(self, name: str) -> bool:
        try:
            provider = self._get_provider(name)
        except ValueError:
            return False
        return provider.health_check()


# --- Singleton ------------------------------------------------------

# The settings are read at import time. Tests that want to override
# the manager should call ``ai_manager.reload(settings)`` rather than
# re-importing.
def _build_default_manager() -> AIManager:
    from backend.config.settings import settings

    return AIManager(settings.ai)


ai_manager: AIManager = _build_default_manager()


def reload_ai_manager(settings: AISettings) -> AIManager:
    """Replace the singleton — used by tests and runtime config changes."""
    global ai_manager
    ai_manager = AIManager(settings)
    return ai_manager


__all__ = [
    "AIManager",
    "ProviderStatus",
    "ai_manager",
    "reload_ai_manager",
]
