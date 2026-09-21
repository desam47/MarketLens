"""
Phase 15/16 — Optional AI provider system and analysis layer.

Public surface:

- :class:`AIProvider` — abstract base, see :mod:`backend.ai.provider`
- :class:`AIManager` — the singleton that resolves completions across
  the configured provider chain, see :mod:`backend.ai.manager`
- :class:`AIResponse` — normalised response from any provider
- :func:`analyze_symbol` — Phase 16 entry point, see
  :mod:`backend.ai.analyze`

The module is designed to be importable and instantiable even when
``AI_ENABLED=false``. The default ``AIManager.complete()`` call
returns ``AIResponse(text=None, provider="disabled")`` so callers
can treat "AI off" as a normal outcome rather than handling a
special exception. ``analyze_symbol()`` returns an
``UncertaintyResponse`` for the same reason.
"""

from backend.ai.analyze import analyze_symbol
from backend.ai.context import AnalysisContext, InsufficientDataError, build_context
from backend.ai.manager import (
    AIManager,
    ProviderStatus,
    ai_manager,
    reload_ai_manager,
)
from backend.ai.prompt import (
    AnalysisResponse,
    UncertaintyResponse,
    build_user_prompt,
    extract_json_object,
    parse_ai_reply,
)
from backend.ai.provider import AIProvider, AIResponse, ProviderUnavailable
from backend.ai.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    build_provider,
)

__all__ = [
    "AIProvider",
    "AIResponse",
    "ProviderUnavailable",
    "AIManager",
    "ProviderStatus",
    "ai_manager",
    "reload_ai_manager",
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "build_provider",
    # Phase 16
    "analyze_symbol",
    "AnalysisContext",
    "InsufficientDataError",
    "build_context",
    "AnalysisResponse",
    "UncertaintyResponse",
    "parse_ai_reply",
    "extract_json_object",
    "build_user_prompt",
]
