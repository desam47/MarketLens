"""
The suite must never touch the developer's live database or live Redis.

``backend/tests/conftest.py`` gives the session its own SQLite file and Redis logical DB before
the app is imported, and installs guards that fail any test that opens the live database file or
connects to Redis DB 0. These tests pin that: the isolation is in effect, the schema is complete,
and the guards actually fire (an isolation that silently stops working would look like a green suite).
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LIVE_DB = (_ROOT / "marketlens.db").resolve()

pytestmark = pytest.mark.skipif(
    os.environ.get("MARKETLENS_TEST_USE_LIVE_DATA") == "1",
    reason="isolation deliberately disabled for this run",
)


def _conftest_attr(name: str):
    """A conftest module global (found via sys.modules: importing it again would re-run its setup)."""
    for module in list(sys.modules.values()):
        if getattr(module, "__file__", "") and str(module.__file__).endswith(
            str(Path("tests") / "conftest.py")
        ):
            if hasattr(module, name):
                return getattr(module, name)
    raise AssertionError(f"conftest.{name} not found")


def _conftest_touches() -> list:
    return _conftest_attr("_LIVE_TOUCHES")


class TestSessionIsolation(unittest.TestCase):
    def test_the_database_is_a_throwaway_file_inside_the_project(self):
        from backend.config.settings import settings
        from backend.database import engine

        for url in (settings.database.url, str(engine.url)):
            path = Path(url.split("sqlite:///", 1)[1]).resolve()
            self.assertNotEqual(path, _LIVE_DB)
            self.assertTrue(path.is_relative_to(_ROOT / "data"), path)
            self.assertIn("pytest-", str(path))

    def test_redis_is_a_separate_logical_database(self):
        from backend.config.settings import settings

        parsed = urlparse(settings.redis.url)
        self.assertEqual(parsed.path, "/15")

    def test_the_schema_is_complete(self):
        import backend.models  # noqa: F401
        from backend.database import Base, engine

        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.exec_driver_sql("select name from sqlite_master where type='table'")
            }
            head = conn.exec_driver_sql("select version_num from alembic_version").scalar()
        self.assertTrue(set(Base.metadata.tables) <= tables, set(Base.metadata.tables) - tables)
        self.assertIsNotNone(head, "migrations were applied")
        # tables that have models but no migration are created by create_all
        for name in ("ai_analysis_jobs", "ai_templates", "custom_indicators", "drawing_tools"):
            self.assertIn(name, tables)

    def test_the_test_database_starts_empty_of_market_data(self):
        """No developer bars/watchlists leak in (a test that needs data must seed it)."""
        from backend.database import engine

        with engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("select count(*) from bars").scalar(), 0)


class TestGuardsFire(unittest.TestCase):
    def tearDown(self):
        _conftest_touches().clear()  # these tests trigger the guards ON PURPOSE

    def test_opening_the_live_sqlite_file_is_refused(self):
        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{_LIVE_DB}")
        try:
            with self.assertRaises(RuntimeError) as cm:
                engine.connect()
        finally:
            engine.dispose()
        self.assertIn("LIVE database", str(cm.exception))
        self.assertTrue(any("LIVE database" in m for m in _conftest_touches()))

    def test_a_scratch_sqlite_file_is_not_refused(self):
        import tempfile

        from sqlalchemy import create_engine

        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{tmp}/scratch.db")
            try:
                with engine.connect() as conn:
                    self.assertEqual(conn.exec_driver_sql("select 1").scalar(), 1)
            finally:
                engine.dispose()
        self.assertEqual(_conftest_touches(), [])

    def test_connecting_to_redis_db_0_is_refused(self):
        import redis

        client = redis.Redis(host="localhost", port=6379, db=0)
        with self.assertRaises(redis.exceptions.ConnectionError):
            client.ping()
        self.assertTrue(any("LIVE Redis" in m for m in _conftest_touches()))

    def test_the_test_redis_database_is_reachable_and_not_refused(self):
        import redis

        client = redis.Redis(host="localhost", port=6379, db=15)
        try:
            client.ping()
        except redis.exceptions.ConnectionError as exc:
            if "LIVE Redis" in str(exc):
                self.fail("the guard refused the TEST Redis database")
            self.skipTest("no Redis server available")
        self.assertEqual(_conftest_touches(), [])


@pytest.mark.skipif(
    os.environ.get("MARKETLENS_TEST_ALLOW_NETWORK") == "1", reason="network allowed for this run"
)
class TestNetworkGuard(unittest.TestCase):
    """No test may reach the internet (Alpaca / Finnhub / Yahoo / AI providers burned quota and made
    the suite slow and flaky offline); loopback must keep working (Redis, in-process servers)."""

    def tearDown(self):
        _conftest_attr("_NETWORK_ATTEMPTS").clear()  # these tests trigger the guard ON PURPOSE

    def test_an_outbound_connection_is_refused_before_anything_is_sent(self):
        import socket

        with self.assertRaises(ConnectionRefusedError) as cm:
            socket.create_connection(("93.184.216.34", 80), timeout=2)
        self.assertIn("outbound network is disabled", str(cm.exception))
        self.assertTrue(
            any("93.184.216.34:80" in line for line in _conftest_attr("_NETWORK_ATTEMPTS"))
        )

    def test_the_refusal_is_an_oserror_so_provider_error_handling_still_applies(self):
        import socket

        with self.assertRaises(OSError):
            socket.create_connection(("198.51.100.7", 443), timeout=2)

    def test_curl_cffi_requests_are_refused_too(self):
        """BF-18: the Yahoo provider uses curl_cffi, whose libcurl sockets bypass the
        socket.connect guard; a probe under pytest got a real Yahoo response."""
        from curl_cffi import requests as curl_requests

        with self.assertRaises(curl_requests.exceptions.ConnectionError) as cm:
            curl_requests.get("https://query1.finance.yahoo.com/v7/finance/quote", timeout=2)
        self.assertIn("outbound network is disabled", str(cm.exception))
        self.assertIsInstance(cm.exception, OSError)
        self.assertTrue(
            any("query1.finance.yahoo.com" in line for line in _conftest_attr("_NETWORK_ATTEMPTS"))
        )

    def test_curl_cffi_async_requests_are_refused_too(self):
        import asyncio

        from curl_cffi import requests as curl_requests

        async def fetch():
            async with curl_requests.AsyncSession() as session:
                return await session.get("https://query1.finance.yahoo.com/", timeout=2)

        with self.assertRaises(curl_requests.exceptions.ConnectionError):
            asyncio.run(fetch())

    def test_loopback_connections_still_work(self):
        import socket

        server = socket.socket()
        try:
            server.bind(("127.0.0.1", 0))
        except PermissionError as exc:
            server.close()
            self.skipTest(f"sandbox forbids loopback sockets: {exc}")
        server.listen(1)
        try:
            client = socket.create_connection(server.getsockname(), timeout=2)
            client.close()
        finally:
            server.close()
        self.assertEqual(_conftest_attr("_NETWORK_ATTEMPTS"), [])


class TestThePlainSqliteModuleStillWorks(unittest.TestCase):
    def test_the_guard_only_watches_sqlalchemy_engines(self):
        """Documented limit: a raw ``sqlite3.connect`` bypasses the engine guard. The isolation of the
        DEFAULT engine (test above) is what protects code that uses the app's own session factory."""
        self.assertTrue(hasattr(sqlite3, "connect"))


if __name__ == "__main__":
    unittest.main()
