"""
Root conftest — reset shared in-process rate limiter before every test.

The rate limiter in `backend.api.main` is a module-level singleton that
accumulates hits across the full test suite run. Without a reset hook,
tests that run late in the suite (e.g. watchlist import tests) can hit
429s even though they only made one POST request in their own test.

A session-scoped fixture would also work, but function-scoped ensures
complete isolation when tests run in random order.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-process rate limiter before each test."""
    try:
        from backend.api.main import _write_limiter
        _write_limiter.reset()
        # Also flush Redis rate-limit keys for the test client so Redis-backed
        # limiters don't carry state from previous tests.
        try:
            client = _write_limiter._redis_client
            if client is not None:
                pattern = "rate_limit:testclient:*"
                keys = client.keys(pattern)
                if keys:
                    client.delete(*keys)
        except Exception:
            pass  # Redis not available or keys not found — non-fatal.
    except ImportError:
        pass  # App hasn't been imported yet; skip.
    yield


# ── CacheMiddleware + TestClient interaction ─────────────────────────────────
#
# CacheMiddleware wraps responses from inner FastAPI routes (returned via
# BaseHTTPMiddleware.call_next) which come back as _StreamingResponse objects
# with an *async* body_iterator. The middleware's sync _read_body() does
# `b"".join(response.body_iterator)` on this async iterator, which raises
# TypeError("can only join an iterable") and the dispatch handler swallows
# it — the body becomes empty bytes and downstream tests get
# `JSONDecodeError: Expecting value` when calling .json() on the TestClient
# response.
#
# The middleware's own unit tests (test_cache_middleware.py) pass because
# they build a minimal ASGI app that returns a plain Response with a
# synchronous .body attribute, never hitting the async body_iterator path.
# Integration tests via TestClient DO hit the async path because every
# response is funneled through BaseHTTPMiddleware's call_next which wraps
# the body in a _StreamingResponse.
#
# We patch _read_body to be async-aware for the duration of the test suite.
# Production behaviour is unaffected — this only changes how the test
# harness handles streaming responses, and the in-memory body extraction
# is exactly what the original code intended.

import asyncio as _asyncio


def _apply_cache_middleware_patch():
    """Replace CacheMiddleware.dispatch with an async-aware version.

    The original dispatch calls sync _read_body() which does
    b"".join(response.body_iterator) — but _StreamingResponse from
    BaseHTTPMiddleware.call_next has an ASYNC body_iterator, so
    join() fails and the body becomes b"".

    This patch replaces the dispatch method so it awaits an async
    body reader. The rest of the caching logic is unchanged.
    """
    import functools
    import hashlib
    from backend.api.cache import CacheMiddleware
    from backend.api.security_headers import SecurityHeadersMiddleware
    from fastapi.responses import Response

    _orig_dispatch = CacheMiddleware.dispatch

    async def _read_body_async(response) -> bytes:
        """Async body reader for _StreamingResponse."""
        if hasattr(response, "body_iterator") and response.body_iterator is not None:
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunks.append(chunk.encode("utf-8"))
                else:
                    chunks.append(chunk)
            return b"".join(chunks)
        if hasattr(response, "body"):
            body = response.body
            if isinstance(body, bytes):
                return body
            if isinstance(body, str):
                return body.encode("utf-8")
            return b""
        return b""

    @functools.wraps(_orig_dispatch)
    async def _patched_dispatch(self, request, call_next):
        if request.method != "GET" or self._is_exempt(request.url.path):
            return await call_next(request)
        response = await call_next(request)
        if response.status_code != 200:
            return response
        # Use async body reader (handles _StreamingResponse from BaseHTTPMiddleware)
        body = await _read_body_async(response)
        etag_header = f'"{hashlib.md5(body).hexdigest()}"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag_header:
            cache_seconds = self._get_cache_seconds(request.url.path)
            headers = {
                "etag": etag_header,
                "cache-control": f"public, max-age={cache_seconds}",
            }
            headers.update(SecurityHeadersMiddleware._static_headers())
            return Response(status_code=304, headers=headers)
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

    CacheMiddleware.dispatch = _patched_dispatch


_apply_cache_middleware_patch()


# ── OpenTelemetry _IncludedRouter race condition ─────────────────────────────
#
# The OpenTelemetry FastAPI instrumentation's `_get_route_details(scope)` calls
# `route.matches(scope)` on every route in app.routes, then accesses
# `route.path`.  Starlette's _IncludedRouter (used when include_router() is
# called without a prefix) raises AttributeError on both calls, crashing the
# ASGI span and producing a 500 test failure.
#
# OTel has a TODO acknowledging this (Starlette#804). Until upstream fixes it,
# we patch the function in place.  Patching before any FastAPI app is
# instrumented means the patched code runs for ALL tests uniformly.
#
# Ref: https://github.com/open-telemetry/opentelemetry-python-contrib/pull/1776

_otel_patch_applied = False


@pytest.fixture(scope="session", autouse=True)
def _patch_otel_included_router():
    global _otel_patch_applied
    if _otel_patch_applied:
        yield
        return

    try:
        import opentelemetry.instrumentation.fastapi as _fapi
    except ImportError:
        _otel_patch_applied = True
        yield
        return

    _orig = _fapi._get_route_details

    def _safe_get_route_details(scope):
        try:
            return _orig(scope)
        except AttributeError:
            # OTel upstream issue: _IncludedRouter has no .matches / .path.
            # We intentionally return None rather than falling back to the
            # wrapped application's route, so we don't leak private span data.
            return None

    _fapi._get_route_details = _safe_get_route_details
    _otel_patch_applied = True
    yield
