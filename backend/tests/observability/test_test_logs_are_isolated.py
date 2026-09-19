"""
The suite must not write into the live server's ``logs/marketlens.log``: importing the app
configures file logging, and that used to append test noise (deliberate failures, MagicMock
errors) to the file a running dev server is also writing.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import structured_logging

_PROJECT_LOGS = Path(structured_logging.__file__).resolve().parent.parent.parent / "logs"


class TestLogDirectory(unittest.TestCase):
    def test_the_suite_logs_outside_the_project_logs_directory(self):
        self.assertTrue(os.environ.get("MARKETLENS_LOG_DIR"), "conftest must set MARKETLENS_LOG_DIR")
        self.assertNotEqual(structured_logging._get_log_dir().resolve(), _PROJECT_LOGS.resolve())

    def test_the_root_file_handler_is_not_the_live_log(self):
        import logging
        from logging.handlers import RotatingFileHandler

        live = str((_PROJECT_LOGS / "marketlens.log").resolve())
        paths = [str(Path(h.baseFilename).resolve()) for h in logging.getLogger().handlers
                 if isinstance(h, RotatingFileHandler)]
        self.assertNotIn(live, paths, "a test-run handler is writing to the live server's log")

    def test_the_override_is_honoured_and_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "logs"
            with patch.dict(os.environ, {"MARKETLENS_LOG_DIR": str(target)}):
                self.assertEqual(structured_logging._get_log_dir(), target)
            self.assertTrue(target.is_dir())

    def test_without_the_override_the_default_is_the_project_logs_directory(self):
        env = {k: v for k, v in os.environ.items() if k != "MARKETLENS_LOG_DIR"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(structured_logging._get_log_dir().resolve(), _PROJECT_LOGS.resolve())


if __name__ == "__main__":
    unittest.main()
