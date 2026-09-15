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
  passes a health check (retried once on failure — see
  ``_healthy()``). Useful for UI badges.
- ``status()``: per-provider health snapshot for dashboards.

API keys are read from settings and never appear in any ``status()``
or ``safe_config()`` payload. Frontend code must use ``safe_config()``
when surfacing the manager to a UI.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

from backend.ai.provider import AIProvider, AIResponse, ProviderUnavailable
from backend.ai.providers import build_provider
from backend.ai.sync_bridge import on_bridge
from backend.config.settings import AISettings

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Lightweight per-provider health record for UI consumption."""

    name: str
    healthy: bool
    is_primary: bool
    error: str | None = None
    # The model this chain entry actually resolved to (may differ from
    # a "type:model" entry's suffix if that suffix was empty and it
    # fell through to a default) — None if the provider couldn't be
    # built at all. Lets the UI distinguish two same-type chain
    # entries ("openai_compatible" primary + "openai_compatible"
    # fallback running a different model) that would otherwise show
    # identical names.
    model: str | None = None


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
        # Which provider/model actually answered the most recent successful
        # complete() call — e.g. settings.model may be a gateway-side alias
        # ("static-best-free") that resolves to a real model name
        # ("openai/gpt-oss-120b") only once a request is actually made; this
        # is that resolved value, for UI badges that want "what's actually
        # running" rather than "what's configured" (safe_config() alone).
        # None until the first successful call since process start.
        self._last_success: dict[str, str] | None = None
        # O3: TTL cache of provider health results. is_available()/status()
        # are polled by UI badges and each health check is a real
        # network round-trip — caching the *resolved* result (after the
        # retry in _healthy) avoids redundant checks within the window.
        # Keyed by chain-entry name (same key space as _providers).
        # Values: (healthy: bool, timestamp: float).
        self._health_cache: dict[str, tuple[bool, float]] = {}
        self._cache_lock = Lock()

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

        ``name`` is a chain entry as returned by
        ``settings.all_providers()``: either a bare provider type
        ("ollama") or a "type:model" composite
        ("openai_compatible:deepseek-v3"). The composite form lets two
        chain entries share one provider TYPE while running different
        models — providers are cached by this exact string, so two
        bare entries with the same type would just collide on the
        cache and silently reuse the first one's model.

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

        A non-primary entry gets ``None`` for base_url/api_key (falling
        through to ``build_provider``'s own per-type default, e.g.
        ollama's ``http://localhost:11434/v1`` + ``llama3.2``) unless
        an explicit ``:model`` suffix names a model, in which case that
        wins. There is no separate override field for "the first
        fallback" — every chain entry's model comes from its own
        string, uniformly.

        A chain entry whose TYPE matches the primary's (e.g. a second
        agentrouter model listed as a fallback) is a different case:
        it's genuinely the *same* provider/gateway, just a different
        model, so it reuses the primary's base_url/api_key rather than
        falling through to that type's generic defaults.
        """
        with self._lock:
            if name in self._providers:
                return self._providers[name]
            provider_type, _, explicit_model = name.partition(":")
            explicit_model = explicit_model or None
            is_primary = name == self.settings.provider
            is_same_type_as_primary = (
                not is_primary and provider_type == self.settings.provider
            )
            if is_primary:
                resolved_base = self.settings.base_url
                resolved_model = self.settings.model
                resolved_key = self.settings.api_key
            elif is_same_type_as_primary:
                resolved_base = self.settings.base_url
                resolved_model = explicit_model or self.settings.model
                resolved_key = self.settings.api_key
            else:
                resolved_base = resolved_key = None
                resolved_model = explicit_model
            # OpenAI-compatible providers (ollama, lm_studio) need /v1
            # appended to the base URL; the user-facing settings.base_url
            # omits it so it's discoverable without knowing the path.
            if provider_type in ("ollama", "lm_studio") and resolved_base:
                if not resolved_base.rstrip("/").endswith("/v1"):
                    resolved_base = resolved_base.rstrip("/") + "/v1"
            provider = build_provider(
                provider_type,
                base_url=resolved_base,
                model=resolved_model,
                api_key=resolved_key,
                timeout=self.settings.timeout,
                health_check_timeout=self.settings.health_check_timeout,
                structured_output=self.settings.structured_output,
            )
            self._providers[name] = provider
            return provider

    def _all_providers(self) -> list[str]:
        return self.settings.all_providers()

    def primary_supports_structured_output(self) -> bool:
        """True iff the primary provider was built with structured output.

        Used by ``analyze_symbol()`` to decide whether to pass
        ``response_format`` and skip regex extraction on the response.
        """
        if not self._providers:
            return False
        primary = self.settings.provider
        provider = self._providers.get(primary)
        return provider is not None and provider.supports_structured_output

    # --- Public API ----------------------------------------------------

    async def is_available(self) -> bool:
        """True iff AI is enabled and at least one provider is healthy.

        We don't cache the answer: a health check takes a couple of
        milliseconds against localhost and several hundred against
        a cloud provider, but the manager is only consulted on
        explicit user requests — there's no high-frequency call site.
        """
        if not self.enabled:
            return False
        for name in self._all_providers():
            if await self._healthy(name):
                return True
        return False

    async def status(self) -> list[ProviderStatus]:
        """Per-provider health snapshot for the dashboard.

        Always returns one entry per provider in the chain (even if
        ``enabled`` is False, the providers are still listed as
        ``healthy=False`` so the UI can show "configured but off").
        """
        out: list[ProviderStatus] = []
        for i, name in enumerate(self._all_providers()):
            healthy = False
            err: str | None = None
            model: str | None = None
            try:
                model = getattr(self._get_provider(name), "_model", None)
            except ValueError:
                pass
            if self.enabled:
                try:
                    healthy = await self._healthy(name)
                except Exception as e:  # noqa: BLE001
                    err = str(e)[:200]
            out.append(
                ProviderStatus(
                    name=name,
                    healthy=healthy,
                    is_primary=(i == 0),
                    error=err,
                    model=model,
                )
            )
        return out

    def safe_config(self) -> dict[str, Any]:
        """Return a frontend-safe view of the configuration.

        Strips ``api_key`` and any other secret-looking fields. The
        UI uses this to render provider badges without ever learning
        the credentials.

        ``last_provider``/``last_model`` are None until the first
        successful ``complete()`` since process start — before that,
        the UI has nothing better than the configured (possibly an
        unresolved alias) ``provider``/``model`` to show.
        """
        with self._lock:
            last = dict(self._last_success) if self._last_success else None
        return {
            "enabled": self.enabled,
            "provider": self.settings.provider,
            "fallback_providers": self.settings.fallback_chain(),
            "model": self.settings.model,
            "last_provider": last["provider"] if last else None,
            "last_model": last["model"] if last else None,
            "base_url": self.settings.base_url,
            "timeout": self.settings.timeout,
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature,
            "api_key_set": bool(self.settings.api_key),
        }

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> AIResponse:
        """Run a completion, walking the fallback chain.

        Returns ``AIResponse(text=None, provider=...)`` when:
        - ``enabled`` is False (the safe default)
        - every provider in the chain is unavailable

        The ``provider`` field on the returned response records the
        name of the first provider we tried (or "none" if AI is
        disabled) so callers can surface "we tried Ollama" in the UI.

        ``model`` (O12): when provided, try this specific chain entry
        first — e.g. ``model="openai:gpt-4o-mini"`` for a quick chat
        turn, ``model="openai:gpt-4o"`` for a formal analysis. The
        entry is tried before the default chain; if it fails, the
        manager falls through to the standard fallback chain as usual.
        This lets callers pick a cheaper/faster model for low-stakes
        requests without reconfiguring the whole provider chain.
        """
        if not self.enabled:
            return AIResponse(text=None, provider="disabled", model=self.settings.model)

        # O12: optional model-specific routing — try the named entry
        # first, then fall through to the standard chain.
        chain = [model] + self._all_providers() if model else self._all_providers()
        # Dedupe while preserving order (the named entry may already be
        # the primary — we don't want to try it twice).
        seen: set[str] = set()
        deduped_chain: list[str] = []
        for n in chain:
            if n not in seen:
                seen.add(n)
                deduped_chain.append(n)

        last_error: str | None = None
        for name in deduped_chain:
            try:
                provider = self._get_provider(name)
            except ValueError as e:
                # Unknown provider name in the chain. Skip.
                logger.warning("Skipping unknown AI provider %r: %s", name, e)
                last_error = str(e)
                continue

            # O4: no pre-flight health_check() here. The real
            # provider.complete() call raises ProviderUnavailable on
            # connection/auth/runtime errors, which is caught below
            # and triggers fallback. The pre-flight was redundant
            # (it made a lightweight /v1/models round-trip that the
            # actual /v1/chat/completions call would have surfaced
            # anyway) and doubled the latency per provider attempt
            # when the gateway was flapping.
            try:
                resp = await provider.complete(
                    prompt,
                    system=system,
                    max_tokens=max_tokens or self.settings.max_tokens,
                    temperature=temperature if temperature is not None else self.settings.temperature,
                    response_format=response_format,
                )
            except ProviderUnavailable as e:
                logger.info("AI provider %r unavailable: %s", name, e)
                last_error = str(e)
                continue
            with self._lock:
                self._last_success = {"provider": resp.provider, "model": resp.model}
            return resp

        logger.warning(
            "All AI providers unavailable (last error: %s). Returning empty response.",
            last_error,
        )
        return AIResponse(text=None, provider="none", model=self.settings.model)

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion, walking the fallback chain.

        ``model`` (O12): when provided, try this specific chain entry
        first — see ``complete()`` for the full rationale.

        Yields incremental text chunks. Yields nothing when AI is
        disabled or every provider is unavailable — the caller treats an
        empty stream the same way it treats ``complete()`` returning
        ``text=None``.

        Fallthrough to the next provider only happens *before the first
        chunk*: once a provider has emitted text, a mid-stream failure is
        logged and the stream ends (no silent restart with a different
        provider, which would duplicate or contradict what's already
        been shown).
        """
        if not self.enabled:
            return

        chain = [model] + self._all_providers() if model else self._all_providers()
        seen: set[str] = set()
        deduped_chain: list[str] = []
        for n in chain:
            if n not in seen:
                seen.add(n)
                deduped_chain.append(n)

        last_error: str | None = None
        for name in deduped_chain:
            try:
                provider = self._get_provider(name)
            except ValueError as e:
                logger.warning("Skipping unknown AI provider %r: %s", name, e)
                last_error = str(e)
                continue

            # O4: skip pre-flight health_check() — let provider.stream()
            # surface ProviderUnavailable directly, same rationale as
            # complete() above.
            started = False
            try:
                async for piece in provider.stream(
                    prompt,
                    system=system,
                    max_tokens=max_tokens or self.settings.max_tokens,
                    temperature=temperature if temperature is not None else self.settings.temperature,
                    response_format=response_format,
                ):
                    started = True
                    yield piece
                return
            except ProviderUnavailable as e:
                if started:
                    logger.warning("AI provider %r failed mid-stream: %s", name, e)
                    return
                logger.info("AI provider %r unavailable: %s", name, e)
                last_error = str(e)
                continue

        logger.warning("All AI providers unavailable for stream (last error: %s).", last_error)

    # --- Internals -----------------------------------------------------

    # A health check that fails is retried once, after a short pause,
    # before the provider is actually reported unhealthy. Scoped to
    # this status-facing check only (is_available()/status(), i.e. the
    # UI badge) — deliberately NOT applied to complete()'s own
    # per-attempt gate, which calls provider.health_check() directly:
    # that gate already has a real fallback chain to fall through to
    # on a miss, so retrying there would only add latency without
    # changing what the caller sees.
    #
    # Found live 2026-09-09: the primary gateway's health-check
    # endpoint (GET /v1/models) was flakier than its actual completion
    # endpoint (POST /v1/chat/completions) — /api/ai/status sometimes
    # reported the primary unhealthy even though a real analysis call
    # through it succeeded moments later. A single retry absorbs that
    # kind of transient blip; a genuinely-down provider still reports
    # unhealthy (just ~_HEALTH_RETRY_DELAY seconds slower).
    _HEALTH_RETRY_DELAY = 0.25

    # O3: how long a cached health result is considered fresh. UI
    # badges poll every few seconds — this window absorbs the gap
    # without surfacing a stale "healthy" for a provider that just
    # went down, while still cutting ~90% of the check load.
    _HEALTH_CACHE_TTL = 10.0

    def _cached_health(self, name: str) -> bool | None:
        """Return a cached health result if fresh, else None.

        The caller (only ``_healthy``) treats ``None`` as a cache
        miss and recomputes via health check + retry, then stores
        the final result. This keeps the retry path's call counts
        intact (tests assert on ``health_check`` invocation count)
        while still short-circuiting repeated checks within the TTL.
        """
        with self._cache_lock:
            if name in self._health_cache:
                result, ts = self._health_cache[name]
                if time.monotonic() - ts < self._HEALTH_CACHE_TTL:
                    return result
        return None

    async def _healthy(self, name: str) -> bool:
        cached = self._cached_health(name)
        if cached is not None:
            return cached
        try:
            provider = self._get_provider(name)
        except ValueError:
            return False
        if await provider.health_check():
            result = True
        else:
            import asyncio
            await asyncio.sleep(self._HEALTH_RETRY_DELAY)
            result = await provider.health_check()
        with self._cache_lock:
            self._health_cache[name] = (result, time.monotonic())
        return result

    async def shutdown(self) -> None:
        """Close all persistent provider clients and clear caches.

        Each provider's ``aclose()`` is dispatched onto the shared bridge
        loop (see :mod:`backend.ai.sync_bridge`), because the pooled
        ``httpx.AsyncClient`` connections were bound to that loop when
        first used.  Safe to call multiple times — already-closed
        providers are no-ops.

        Called from the FastAPI lifespan shutdown handler; sync callers
        outside an event loop should wrap with ``run_sync``.
        """
        with self._lock:
            providers = list(self._providers.values())
            self._providers.clear()
            self._health_cache.clear()
        for provider in providers:
            aclose = getattr(provider, "aclose", None)
            if aclose is None:
                continue
            try:
                await on_bridge(aclose())
            except Exception as e:  # noqa: BLE001
                logger.warning("Error closing provider %s: %s", provider.name, e)


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
