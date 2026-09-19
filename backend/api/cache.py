"""
Cache middleware for adding HTTP caching headers and ETag support to GET endpoints.

Provides HTTP caching with ETag-based conditional requests (304 Not Modified)
on cacheable GET responses. Non-GET methods and exempt paths (health, docs)
are passed through unchanged.

ETag handling: we hash the response body and compare against the
``If-None-Match`` request header. On a match, we return 304 with no body
and the original ``ETag`` so clients can serve their cached copy.

Per-path cache durations let us give market data a 5-minute TTL while
system status refreshes every 30 seconds.
"""
import hashlib
import logging
from typing import Set

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.security_headers import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)

# Path prefixes that are exempt from caching
_EXEMPT_PATH_PREFIXES = (
    "/api/health",
    "/api/system/status",
    "/api/market-data/ingestion",
    "/docs",
    "/openapi.json",
    "/redoc",
)

# Default cache time in seconds for GET endpoints
_DEFAULT_CACHE_SECONDS = 30

# Different cache durations for different endpoint types.
# Keys are checked as case-insensitive substrings of the URL path, so
# "market_data" matches both "/api/market_data/..." and "/api/market-data/...".
_CACHE_DURATIONS = {
    # Live quotes: 0 (must revalidate every request). Has to come before the
    # broader "market_data" key since the first substring match wins. With
    # max-age=300 the browser served the Dashboard's 5s price poll from its
    # own cache, freezing the price for up to 5 minutes. max-age=0 still
    # lets the ETag answer unchanged quotes with a cheap 304.
    "market_data/quote": 0,
    # Market data endpoints: 5 minutes (data changes quickly but frequent polling)
    "market_data": 300,
    # System status: 30 seconds (changes very quickly)
    "system_status": 30,
    # API docs: 1 hour (static)
    "docs": 3600,
    # Finnhub API: 1 hour (fundamentals don't change by the minute)
    "finnhub": 3600,
    # Default: 30 seconds (special key — never matched explicitly)
    "default": 30,
}


class CacheMiddleware(BaseHTTPMiddleware):
    """Add caching headers and ETag support to GET endpoints."""

    def __init__(
        self,
        app,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
        exempt_paths: Set[str] | None = None,
        cache_durations: dict[str, int] | None = None,
    ):
        super().__init__(app)
        self.cache_seconds = cache_seconds
        self.exempt_paths = exempt_paths or set(_EXEMPT_PATH_PREFIXES)
        # Per-path cache duration overrides for smarter caching
        self.cache_durations = cache_durations or _CACHE_DURATIONS

    def _get_cache_seconds(self, path: str) -> int:
        """Return cache duration based on path prefix.

        The "default" key is the fallback and never matches explicitly —
        it would always match every path because "default" is a substring
        of nothing meaningful. We skip it before the search.

        Matching is case-insensitive. Underscores and hyphens are treated
        as equivalent so "market_data" matches "/api/market-data/...".
        """
        # Normalise path: lower-case, replace hyphens with underscores.
        # This lets the config key "market_data" match "/api/market-data/..."
        # and "/api/market_data/..." alike.
        path_normalised = path.lower().replace("-", "_")
        for prefix, seconds in self.cache_durations.items():
            if prefix == "default":
                continue
            if prefix in path_normalised:
                return seconds
        return self.cache_seconds

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.exempt_paths)

    async def _read_body(self, response) -> bytes:
        """Extract body bytes from a response, handling streaming and static types.

        ``BaseHTTPMiddleware.call_next`` returns a ``_StreamingResponse`` whose
        ``body_iterator`` is an *async* generator. Joining it with ``b"".join()``
        raises ``TypeError`` (you can't join an async iterator) and the empty
        body propagates to clients as a truncated response. We must consume
        async iterators with ``async for`` — sync iterables with ``b"".join``.
        """
        # Async body iterator (e.g. _StreamingResponse from BaseHTTPMiddleware).
        # Has ``__aiter__`` but not ``__iter__`` consumed by b"".join.
        if (
            hasattr(response, "body_iterator")
            and response.body_iterator is not None
            and hasattr(response.body_iterator, "__aiter__")
        ):
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunks.append(chunk.encode("utf-8"))
                else:
                    chunks.append(chunk)
            return b"".join(chunks)
        # Sync body iterator (plain iterable, e.g. test mocks).
        if hasattr(response, "body_iterator") and response.body_iterator is not None:
            try:
                body = b"".join(response.body_iterator)
                return body if isinstance(body, bytes) else body.encode()
            except Exception:
                return b""
        # Plain Response / JSONResponse have .body as bytes.
        if hasattr(response, "body"):
            body = response.body
            return body if isinstance(body, bytes) else (
                body.encode() if isinstance(body, str) else b""
            )
        return b""

    async def dispatch(self, request: Request, call_next):
        """Compute ETag for GET requests and short-circuit on match.

        Non-GETs and exempt paths pass through unchanged. For cacheable
        responses we hash the body and emit ``ETag`` + ``Cache-Control``
        headers. When the request includes a matching ``If-None-Match``
        we return 304 with no body.
        """
        if request.method != "GET" or self._is_exempt(request.url.path):
            return await call_next(request)

        response = await call_next(request)

        # Only cache successful responses
        if response.status_code != 200:
            return response

        body = await self._read_body(response)

        etag = hashlib.md5(body).hexdigest()
        etag_header = f'"{etag}"'

        # Check if client has a matching cached version
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag_header:
            cache_seconds = self._get_cache_seconds(request.url.path)
            # Inline security headers: FastAPI's ExceptionMiddleware
            # intercepts short-circuit responses before outer
            # BaseHTTPMiddleware instances (SecurityHeadersMiddleware)
            # can attach them.
            headers = {
                "etag": etag_header,
                "cache-control": f"public, max-age={cache_seconds}",
            }
            headers.update(SecurityHeadersMiddleware._static_headers())
            return Response(
                status_code=304,
                headers=headers,
            )

        # Set caching headers on the response and re-emit the body
        cache_seconds = self._get_cache_seconds(request.url.path)
        new_response = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type"),
        )
        new_response.headers["cache-control"] = f"public, max-age={cache_seconds}"
        new_response.headers["etag"] = etag_header
        return new_response
