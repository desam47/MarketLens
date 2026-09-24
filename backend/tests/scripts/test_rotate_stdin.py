"""
MD-05: scripts/rotate_stdin.py caps a piped process's console output the way
logs/marketlens.log's own RotatingFileHandler already caps the app's structured log —
found live 2026-09-24: the raw `>>` copy in logs/backend.log grew to 442 MB in 11 days
because nothing rotated it.

Imported directly from its file path (it has no package __init__.py, matching how
scripts/repair_hourly_bars.py and scripts/settle_1m_from_sip.py are treated as
standalone CLIs, not an importable package) for the unit-level tests; a subprocess
end-to-end test covers the real CLI/pipe usage restart_dev.sh relies on.
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "rotate_stdin.py"

spec = importlib.util.spec_from_file_location("rotate_stdin", SCRIPT)
rotate_stdin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotate_stdin)


class TestRotatorUnit(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "test.log")
        self.addCleanup(self.tmp.cleanup)

    def test_writes_pass_through_below_the_cap(self):
        r = rotate_stdin._Rotator(self.path, max_bytes=1_000_000, backup_count=3)
        r.write_line("hello\n")
        r.write_line("world\n")
        r._fh.close()
        self.assertEqual(Path(self.path).read_text(), "hello\nworld\n")
        self.assertFalse(Path(f"{self.path}.1").exists())

    def test_rotates_when_the_cap_is_exceeded(self):
        r = rotate_stdin._Rotator(self.path, max_bytes=100, backup_count=3)
        line = "x" * 50 + "\n"  # 51 bytes; 3 lines exceeds the 100-byte cap
        for _ in range(3):
            r.write_line(line)
        r._fh.close()
        self.assertTrue(Path(f"{self.path}.1").exists())
        # the newest line always lands in the current (post-rotation) file
        self.assertIn(line, Path(self.path).read_text())

    def test_backup_count_is_respected_past_many_rotations(self):
        r = rotate_stdin._Rotator(self.path, max_bytes=60, backup_count=2)
        line = "x" * 50 + "\n"
        for _ in range(20):
            r.write_line(line)
        r._fh.close()
        self.assertTrue(Path(f"{self.path}.1").exists())
        self.assertTrue(Path(f"{self.path}.2").exists())
        self.assertFalse(Path(f"{self.path}.3").exists())

    def test_a_write_failure_is_reported_and_does_not_raise(self):
        r = rotate_stdin._Rotator(self.path, max_bytes=1_000_000, backup_count=3)
        r._fh.close()  # writing to a closed file object raises inside write_line
        r.write_line("this must not raise\n")  # should print to _real_stderr instead


class TestRotateStdinCLI(unittest.TestCase):
    """Black-box: the actual invocation shape restart_dev.sh uses (a real pipe)."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "piped.log")
        self.addCleanup(self.tmp.cleanup)

    def test_streams_stdin_into_a_capped_rotating_file(self):
        line = "y" * 40 + "\n"
        producer = subprocess.Popen(
            ["python3", "-c", f"import sys; [sys.stdout.write({line!r}) for _ in range(50)]"],
            stdout=subprocess.PIPE,
        )
        rotator = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                self.path,
                "--max-bytes",
                "200",
                "--backup-count",
                "2",
            ],
            stdin=producer.stdout,
            capture_output=True,
            text=True,
            timeout=30,
        )
        producer.wait(timeout=5)
        producer.stdout.close()
        self.assertEqual(rotator.returncode, 0)
        self.assertEqual(rotator.stderr, "")
        self.assertTrue(Path(f"{self.path}.1").exists())
        self.assertTrue(Path(f"{self.path}.2").exists())
        self.assertFalse(Path(f"{self.path}.3").exists())
        for f in [self.path, f"{self.path}.1", f"{self.path}.2"]:
            self.assertLessEqual(Path(f).stat().st_size, 200)


if __name__ == "__main__":
    unittest.main()
