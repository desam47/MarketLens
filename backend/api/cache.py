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

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class CacheMiddleware:
    """Add caching headers and ETag support to GET endpoints.

    A plain ASGI middleware (not ``BaseHTTPMiddleware``, which cost ~190 us per
    request for a task group + streams + a response wrapper on EVERY request — the
    largest fixed cost left in the stack once the others were converted). Only a
    cacheable response is buffered: non-GETs, exempt paths and non-200 responses
    stream straight through untouched, chunk boundaries and all.
    """

    def __init__(
        self,
        app: ASGIApp,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
        exempt_paths: set[str] | None = None,
        cache_durations: dict[str, int] | None = None,
    ):
        self.app = app
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

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Compute an ETag for cacheable GET responses and short-circuit on a match.

        Non-GETs and exempt paths pass through unchanged. A 200 response is buffered,
        hashed, and re-emitted with ``ETag`` + ``Cache-Control``; when the request's
        ``If-None-Match`` matches, a bodiless 304 is sent instead.
        """
        if scope["type"] != "http" or scope["method"] != "GET" or self._is_exempt(scope["path"]):
            await self.app(scope, receive, send)
            return

        held_start: Message | None = None
        chunks: list[bytes] = []
        buffering = False
        passthrough = False

        async def buffer_or_pass(message: Message) -> None:
            nonlocal held_start, buffering, passthrough
            if passthrough:
                await send(message)
                return
            kind = message["type"]
            if kind == "http.response.start":
                if message["status"] != 200:
                    # Only successful responses are cached: stream everything else as-is.
                    passthrough = True
                    await send(message)
                    return
                held_start, buffering = message, True
                return
            if buffering and kind == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                buffering, passthrough = False, True
                await self._respond(scope, receive, send, held_start, b"".join(chunks))
                return
            # Anything else mid-response (e.g. an extension message): stop buffering,
            # flush what was held, in order, and pass the rest through untouched.
            if buffering:
                buffering, passthrough = False, True
                await send(held_start)
                for chunk in chunks:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send(message)

        await self.app(scope, receive, buffer_or_pass)

    async def _respond(
        self, scope: Scope, receive: Receive, send: Send, start: Message, body: bytes
    ) -> None:
        etag_header = f'"{hashlib.md5(body).hexdigest()}"'
        cache_seconds = self._get_cache_seconds(scope["path"])

        # Check if client has a matching cached version
        if_none_match = Headers(scope=scope).get("if-none-match")
        if if_none_match and if_none_match == etag_header:
            # Inline security headers: this short-circuit response is built here, so
            # the inner SecurityHeadersMiddleware never sees it.
            headers = {
                "etag": etag_header,
                "cache-control": f"public, max-age={cache_seconds}",
            }
            headers.update(SecurityHeadersMiddleware._static_headers())
            await Response(status_code=304, headers=headers)(scope, receive, send)
            return

        # Set caching headers on the response and re-emit the body in one piece.
        start.setdefault("headers", [])
        headers = MutableHeaders(scope=start)
        headers["cache-control"] = f"public, max-age={cache_seconds}"
        headers["etag"] = etag_header
        headers["content-length"] = str(len(body))
        await send(start)
        await send({"type": "http.response.body", "body": body})
