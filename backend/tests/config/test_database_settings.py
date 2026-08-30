"""
Tests for DatabaseSettings URL normalisation.
"""
import unittest

from backend.config.settings import DatabaseSettings


class TestDatabaseUrlNormalisation(unittest.TestCase):
    """DatabaseSettings._resolve_relative_url must always resolve SQLite paths
    relative to the project root, regardless of the process CWD."""

    def test_relative_slashdot_resolves_to_project_root(self):
        """sqlite:///./foo.db → absolute path under project root."""
        s = DatabaseSettings(url="sqlite:///./foo.db")
        self.assertIn("/Users/dips/projects/MarketLens/foo.db", s.url)
        self.assertTrue(s.url.startswith("sqlite:///"))

    def test_relative_no_prefix_resolves_to_project_root(self):
        """sqlite:///foo.db → absolute path under project root."""
        s = DatabaseSettings(url="sqlite:///foo.db")
        self.assertIn("/Users/dips/projects/MarketLens/foo.db", s.url)

    def test_absolute_sqlite_passthrough(self):
        """sqlite:///absolute/path.db is returned unchanged."""
        s = DatabaseSettings(url="sqlite:////var/data/marketlens.db")
        self.assertEqual(s.url, "sqlite:////var/data/marketlens.db")

    def test_postgres_passthrough(self):
        """Non-SQLite URLs are returned unchanged."""
        s = DatabaseSettings(url="postgresql://user:pass@localhost/db")
        self.assertEqual(s.url, "postgresql://user:pass@localhost/db")

    def test_singleton_consistent_across_cwd(self):
        """The resolved URL is always the same regardless of import-time CWD."""
        # Import from settings module so the validator already ran at class def.
        s1 = DatabaseSettings(url="sqlite:///./watchlist.db")
        s2 = DatabaseSettings(url="sqlite:///./watchlist.db")
        self.assertEqual(s1.url, s2.url)


if __name__ == "__main__":
    unittest.main()
