"""
Tests for GET /api/system/backup-status (Phase 3.3.3).

Validates the response shape, the WAL mode reporting, the Litestream
reachability field, and graceful degradation when Litestream is not
running.
"""
import unittest


class TestBackupStatusEndpoint(unittest.TestCase):
    """The endpoint must return the documented response shape."""

    def setUp(self):
        # Importing app lazily so conftest.py's autouse fixtures have
        # a chance to run first.
        from fastapi.testclient import TestClient
        from backend.api.main import app
        self.client = TestClient(app)

    def test_endpoint_returns_200(self):
        r = self.client.get("/api/system/backup-status")
        self.assertEqual(r.status_code, 200)

    def test_response_shape(self):
        r = self.client.get("/api/system/backup-status")
        data = r.json()
        expected_keys = {
            "timestamp",
            "journal_mode",
            "wal_checkpoint_busy",
            "wal_checkpoint_frames",
            "wal_checkpoint_end",
            "wal_size_bytes",
            "shm_size_bytes",
            "litestream_reachable",
            "litestream_generation",
            "litestream_dbs",
        }
        self.assertEqual(expected_keys.issubset(data.keys()), True,
                         f"missing keys: {expected_keys - set(data.keys())}")

    def test_journal_mode_is_wal(self):
        """The live engine should be in WAL mode after Phase 3.3.1."""
        r = self.client.get("/api/system/backup-status")
        data = r.json()
        self.assertEqual(data["journal_mode"].lower(), "wal")

    def test_wal_checkpoint_fields_are_int_or_bool(self):
        r = self.client.get("/api/system/backup-status")
        data = r.json()
        self.assertIsInstance(data["wal_checkpoint_busy"], bool)
        self.assertIsInstance(data["wal_checkpoint_frames"], int)
        self.assertIsInstance(data["wal_checkpoint_end"], int)
        self.assertIsInstance(data["wal_size_bytes"], int)
        self.assertIsInstance(data["shm_size_bytes"], int)

    def test_litestream_reachable_is_bool(self):
        r = self.client.get("/api/system/backup-status")
        data = r.json()
        self.assertIsInstance(data["litestream_reachable"], bool)
        # In test env Litestream is almost never running.
        self.assertFalse(data["litestream_reachable"])
        self.assertIsNone(data["litestream_generation"])
        self.assertIsNone(data["litestream_dbs"])


class TestSafeBackupStatusGracefulDegradation(unittest.TestCase):
    """The helper must surface 'unknown' shapes rather than raise on errors."""

    def test_returns_dict_with_journal_mode_on_real_engine(self):
        """On the real SQLite engine the helper returns a populated dict."""
        from backend.api.system.router import _safe_backup_status
        result = _safe_backup_status()
        # The live DB must return a real dict (not None).
        self.assertIsNotNone(result)
        self.assertIn("journal_mode", result)
        self.assertEqual(result["journal_mode"].lower(), "wal")


if __name__ == "__main__":
    unittest.main()
