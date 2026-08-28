"""Phase 17 — Natural-language market search layer.

Public surface:

- :class:`NLFilters` — controlled Pydantic schema the AI is told to emit
  (see :mod:`backend.nl_search.schema`).
- :class:`NLSearchResponse` — response shape returned by the
  ``/api/nl-search`` endpoint.
- :func:`parse_query` — top-level entry: AI-first, rule-based fallback.
  Returns a tuple of (NLFilters, used_ai_translation).
- :func:`execute_query` — runs a validated :class:`NLFilters` against the
  scanner cache and returns a ranked slice.
"""
from backend.nl_search.executor import execute_query
from backend.nl_search.parser import parse_query
from backend.nl_search.schema import NLFilters, NLSearchResponse, ScannedResultItem

__all__ = [
    "NLFilters",
    "NLSearchResponse",
    "ScannedResultItem",
    "parse_query",
    "execute_query",
]
