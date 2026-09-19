"""
Root conftest — reset shared in-process rate limiters before every test.

The rate limiters in `backend.api.main` (`_write_limiter`) and
`backend.api.rate_limit` (`_ai_limiter`, `_alerts_limiter`,
`_backtest_limiter`) are module-level singletons that accumulate hits
across the full test suite run. Without a reset hook, tests that run late
in the suite (e.g. watchlist import tests, or any test hitting
/api/ai/analyze) can hit 429s even though they only made one request in
their own test.

A session-scoped fixture would also work, but function-scoped ensures
complete isolation when tests run in random order.
"""
import os

import pytest


# ── Never call the real Webull API from the test suite ──────────────────────
#
# Importing ``backend.api.main`` (which most API tests do, at collection time)
# builds the market-data manager, which constructs ``WebullProvider``. Its
# ``__init__`` makes a REAL signed request to api.webull.com. A batch of test
# runs therefore burned Webull's rate limit (429 TOO_MANY_REQUESTS): the
# provider then failed to register in the *live dev server* too after its next
# reload (logs held hundreds of such failures across earlier sessions).
#
# ``ApiClient.get_response`` is the SDK's single HTTP funnel, so replacing it
# with an offline error makes provider construction fail fast and locally —
# the same "provider absent" state the suite already passes under whenever
# Webull is unreachable. Tests that patch higher up (``WebullProvider.__init__``,
# ``_TradeClient`` ...) are unaffected. This must run at conftest import time:
# the app is imported during collection, before any fixture would execute.
# Set MARKETLENS_TEST_ALLOW_NETWORK=1 to allow live calls on purpose.
def _block_webull_network() -> None:
    if os.environ.get("MARKETLENS_TEST_ALLOW_NETWORK") == "1":
        return
    try:
        import webull.core.client as _wb_client
    except ImportError:  # SDK not installed -> nothing to guard
        return

    def _offline_get_response(self, api_request):  # noqa: ANN001
        raise ConnectionError(
            "Webull network access is disabled under pytest "
            "(set MARKETLENS_TEST_ALLOW_NETWORK=1 to allow live calls)"
        )

    _wb_client.ApiClient.get_response = _offline_get_response


_block_webull_network()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset every known rate limiter (in-memory fallback + Redis) before each test."""
    redis_client = None
    try:
        from backend.api.main import _write_limiter
        _write_limiter.reset()
        redis_client = _write_limiter._redis_client
    except ImportError:
        pass  # App hasn't been imported yet; skip.

    # Per-endpoint limiters (AI, alerts, backtest) are separate singleton
    # instances — namespaced by `name` so they don't share a Redis key with
    # `_write_limiter`, but that also means resetting `_write_limiter` alone
    # never touches their in-memory fallback counters either.
    try:
        from backend.api.rate_limit import _ai_limiter, _alerts_limiter, _backtest_limiter
        for limiter in (_ai_limiter, _alerts_limiter, _backtest_limiter):
            limiter.reset()
            if redis_client is None:
                redis_client = limiter._redis_client
    except ImportError:
        pass

    # Flush Redis rate-limit keys for the test client so Redis-backed
    # limiters don't carry state from previous tests. Key format is
    # `rate_limit:{name}:{client_ip}:{window}` (see RedisRateLimiter.is_allowed)
    # — `name` sits between the prefix and the client IP, so the pattern
    # must wildcard that segment too. A prior version of this pattern
    # (`rate_limit:testclient:*`) never matched any real key and was a
    # silent no-op for every Redis-backed limiter this whole time.
    if redis_client is not None:
        try:
            pattern = "rate_limit:*:testclient:*"
            keys = redis_client.keys(pattern)
            if keys:
                redis_client.delete(*keys)
        except Exception:
            pass  # Redis not available or keys not found — non-fatal.
    yield


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
