"""
Tests for DatabaseSettings — permanent DB path fix.

The DB path is hard-coded to <project_root>/marketlens.db and cannot be
overridden by DATABASE_URL env var or constructor argument. The only way to
use a different DB is MARKETLENS_DB_OVERRIDE.
"""

import os
import unittest
from unittest.mock import patch

from backend.config.settings import _DB_URL, DatabaseSettings


class TestDatabaseUrlNormalisation(unittest.TestCase):
    """DatabaseSettings always returns the hard-coded project-root DB path."""

    def setUp(self):
        # The suite runs with MARKETLENS_DB_OVERRIDE pointing at a throwaway database (see
        # backend/tests/conftest.py). These tests are about the DEFAULT path, so run each without
        # it and restore the environment afterwards. They used to ``del`` the variable in a
        # ``finally``, which would have stripped the override for every later test.
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MARKETLENS_DB_OVERRIDE", None)

    def _project_root_path(self) -> str:
        """Return the expected hard-coded DB path."""
        from pathlib import Path

        root = Path(__file__).parent.parent.parent.parent / "marketlens.db"
        return f"sqlite:///{root.resolve()}"

    def test_url_is_hard_coded(self):
        """URL argument is ignored; the hard-coded project-root path is returned."""
        s = DatabaseSettings(url="sqlite:///./completely/wrong/path.db")
        self.assertEqual(s.url, self._project_root_path())

    def test_url_ignored_for_postgres(self):
        """Even a Postgres URL is overridden by the hard-coded path."""
        s = DatabaseSettings(url="postgresql://user:pass@localhost/db")
        self.assertEqual(s.url, self._project_root_path())

    def test_url_ignored_for_absolute_sqlite(self):
        """Absolute SQLite paths are still overridden."""
        s = DatabaseSettings(url="sqlite:////var/data/marketlens.db")
        self.assertEqual(s.url, self._project_root_path())

    def test_constructor_url_ignored(self):
        """Passing url= to the constructor has no effect."""
        s = DatabaseSettings(url="sqlite:///./test.db")
        self.assertNotIn("test.db", s.url)

    def test_override_env_var_takes_priority(self):
        """MARKETLENS_DB_OVERRIDE takes priority over the hard-coded path."""
        os.environ["MARKETLENS_DB_OVERRIDE"] = "sqlite:////tmp/override.db"
        try:
            s = DatabaseSettings()
            self.assertEqual(s.url, "sqlite:////tmp/override.db")
        finally:
            del os.environ["MARKETLENS_DB_OVERRIDE"]

    def test_override_env_var_overrides_explicit_url(self):
        """MARKETLENS_DB_OVERRIDE wins even when url= is passed."""
        os.environ["MARKETLENS_DB_OVERRIDE"] = "sqlite:////tmp/override.db"
        try:
            s = DatabaseSettings(url="sqlite:///./different.db")
            self.assertEqual(s.url, "sqlite:////tmp/override.db")
        finally:
            del os.environ["MARKETLENS_DB_OVERRIDE"]

    def test_url_matches_module_constant(self):
        """The URL matches the module-level _DB_URL constant."""
        s = DatabaseSettings()
        self.assertEqual(s.url, _DB_URL)


if __name__ == "__main__":
    unittest.main()
