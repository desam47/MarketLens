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


# ── Never write test logs into the live server's log file ────────────────────
#
# Importing ``backend.api.main`` calls ``configure_logging()``, which opens
# ``logs/marketlens.log`` -- the file the running dev server writes. Test runs therefore
# interleaved deliberate failures ("Failed to generate close digest", scanner "MagicMock"
# errors) with real events, and "errors since the last restart" counts were meaningless.
# Redirect to a throwaway directory; this must happen before the app is imported.
def _redirect_test_logs() -> None:
    if os.environ.get("MARKETLENS_LOG_DIR"):
        return
    import atexit
    import shutil
    import tempfile

    log_dir = tempfile.mkdtemp(prefix="marketlens-test-logs-")
    os.environ["MARKETLENS_LOG_DIR"] = log_dir
    atexit.register(shutil.rmtree, log_dir, ignore_errors=True)


_redirect_test_logs()


# ── Never touch the developer's live database or live Redis ─────────────────────
#
# The suite used to run against ``marketlens.db`` and Redis logical DB 0, the same ones the
# running dev server uses. Every "tests polluted live data" incident traced back to that: digest
# rows written on each reload, real backfill jobs, watchlist/experiment rows, cache keys flushed
# by a lifespan startup. Patching each symptom left the next careless test free to do it again.
#
# So the whole session gets its own database and Redis logical DB, before anything imports the app:
#   * MARKETLENS_DB_OVERRIDE -> a fresh SQLite file under <project>/data (backend/database/db.py
#     refuses paths outside the project root), built with the real migrations plus create_all
#     (a few tables have models but no migration);
#   * REDIS_URL -> logical DB 15, flushed at start;
#   * two guards that FAIL any test that still opens the live database file or connects to Redis
#     DB 0 (a test that reaches around the settings, e.g. a hardcoded redis://localhost:6379).
# Set MARKETLENS_TEST_USE_LIVE_DATA=1 to run against live data on purpose (the old behaviour).
_LIVE_TOUCHES: list[str] = []
_TEST_REDIS_DB = 15


def _isolate_databases() -> None:
    if os.environ.get("MARKETLENS_TEST_USE_LIVE_DATA") == "1":
        return
    import atexit
    import shutil
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    live_db = (root / "marketlens.db").resolve()
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="pytest-", dir=data_dir))
    atexit.register(shutil.rmtree, run_dir, ignore_errors=True)

    os.environ["MARKETLENS_DB_OVERRIDE"] = f"sqlite:///{run_dir / 'marketlens-test.db'}"
    os.environ["REDIS_URL"] = f"redis://localhost:6379/{_TEST_REDIS_DB}"

    # Schema: the real migrations (this is what the app runs at startup), then create_all for the
    # models that have no migration. Both are pointed at the test database by the env above.
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=root, env=os.environ, check=True, capture_output=True,
    )
    import backend.models  # noqa: F401  (registers every table on Base.metadata)
    from backend.database import Base, engine

    Base.metadata.create_all(bind=engine)

    _flush_test_redis_db()
    _install_live_resource_guards(live_db)


def _flush_test_redis_db() -> None:
    try:
        import redis

        client = redis.Redis.from_url(os.environ["REDIS_URL"])
        if client.connection_pool.connection_kwargs.get("db") == _TEST_REDIS_DB:
            client.flushdb()
        client.close()
    except Exception:  # noqa: BLE001  (no Redis: tests degrade the same way the app does)
        pass


def _install_live_resource_guards(live_db) -> None:
    from pathlib import Path

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _refuse_live_sqlite(dbapi_connection, _record):  # noqa: ANN001
        try:
            rows = dbapi_connection.execute("PRAGMA database_list").fetchall()
        except Exception:  # noqa: BLE001  (not SQLite)
            return
        for row in rows:
            if row[2] and Path(row[2]).resolve() == live_db:
                message = f"a test opened the LIVE database {live_db}"
                _LIVE_TOUCHES.append(message)
                raise RuntimeError(message)

    try:
        import redis.connection as redis_connection
        import redis.exceptions as redis_exceptions
    except ImportError:
        return
    real_connect = redis_connection.AbstractConnection.connect

    def _refuse_live_redis(self):  # noqa: ANN001
        if getattr(self, "db", None) == 0 and getattr(self, "host", None) in ("localhost", "127.0.0.1"):
            message = "a test connected to the LIVE Redis (logical DB 0)"
            _LIVE_TOUCHES.append(message)
            # redis-py's own error type, so components that tolerate a Redis outage degrade exactly
            # as they do in production (the autouse fixture still fails the test that did this).
            raise redis_exceptions.ConnectionError(message)
        return real_connect(self)

    redis_connection.AbstractConnection.connect = _refuse_live_redis


_isolate_databases()


# ── Never let a test reach the internet ─────────────────────────────────────────
#
# Webull is blocked above, but Alpaca / Finnhub / Yahoo / the AI providers were not: an audit
# of the suite found 14 outbound connections from 5 tests (build-context and ingestion-lifecycle
# tests, and an AI-manager health check), burning API quota and making the suite slow and flaky
# offline. Non-loopback connects are refused with the error a dead network gives, so components
# degrade exactly as they do when a provider is down. Attempts are listed at the end of the run.
# MARKETLENS_TEST_ALLOW_NETWORK=1 (the same switch as the Webull guard) allows live calls.
_NETWORK_ATTEMPTS: list[str] = []


def _block_external_network() -> None:
    if os.environ.get("MARKETLENS_TEST_ALLOW_NETWORK") == "1":
        return
    import socket

    real_connect = socket.socket.connect

    def _guarded_connect(self, address):  # noqa: ANN001
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(address, tuple) and str(host) not in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
            import traceback

            test = os.environ.get("PYTEST_CURRENT_TEST", "<collection>").split(" ")[0]
            # The innermost APPLICATION frame (not a test, not a library) names what asked for the
            # connection, which is what you need to find the provider that must be mocked.
            app_frames = [
                f for f in traceback.extract_stack()
                if "/backend/" in f.filename and "/backend/tests/" not in f.filename
            ]
            origin = f"{app_frames[-1].filename.split('/backend/', 1)[1]}:{app_frames[-1].lineno} {app_frames[-1].name}" if app_frames else "?"
            _NETWORK_ATTEMPTS.append(f"{test} -> {host}:{address[1] if len(address) > 1 else ''}  [{origin}]")
            raise ConnectionRefusedError(
                f"outbound network is disabled under pytest ({host}); "
                "set MARKETLENS_TEST_ALLOW_NETWORK=1 to allow live calls"
            )
        return real_connect(self, address)

    socket.socket.connect = _guarded_connect


_block_external_network()


def pytest_terminal_summary(terminalreporter):
    """List the tests that tried to reach the internet (refused), so new offenders stay visible."""
    if not _NETWORK_ATTEMPTS:
        return
    terminalreporter.section("blocked outbound network attempts")
    for line in sorted(set(_NETWORK_ATTEMPTS)):
        terminalreporter.write_line(line)


@pytest.fixture(autouse=True)
def _fail_if_live_resources_touched():
    """Fail the test that reached for the live database or live Redis, even if the code under
    test swallowed the resulting error (many components degrade silently when a store is down)."""
    _LIVE_TOUCHES.clear()
    yield
    if _LIVE_TOUCHES:
        touched = sorted(set(_LIVE_TOUCHES))
        _LIVE_TOUCHES.clear()
        pytest.fail("this test touched live resources: " + "; ".join(touched), pytrace=False)


@pytest.fixture(autouse=True)
def _no_real_backfill_jobs(request):
    """Tests must never enqueue a REAL backfill.

    ``enqueue_backfill`` talks to the live Redis, whose ``marketlens-backfill`` RQ worker runs the
    job for real: it calls Webull/Alpaca/Yahoo and writes ~14,000 bars into the live database. The
    watchlist add/import tests did exactly that on every run (AAPL, NVDA, TSLA), which contributed to
    the backfill-job flood. With no queue, ``enqueue_backfill`` returns None, the same way it does
    when Redis is down. ``test_backfill_queue`` is exempt: it tests the real functions and supplies
    its own queue and Redis fakes.
    """
    if request.module.__name__.endswith("test_backfill_queue"):
        yield
        return
    from unittest.mock import patch

    with patch("backend.market_data.services.backfill_queue.get_backfill_queue", return_value=None):
        yield


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
