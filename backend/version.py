"""
Git-based version info.

Read from ``version.txt`` (written by the pre-commit hook on every commit).
Falls back to "dev" when not available so the server starts without git.

Important: the file is re-read on every call so the version reflects the
latest commit even when the backend is long-running (the file is rewritten
in place by the hook — same path, new contents). The cost is one ~200-byte
file read per call, which is negligible.
"""

from __future__ import annotations

from pathlib import Path

# Project root is two levels up from this file.
_PROJECT_ROOT = Path(__file__).parent.parent
_VERSION_FILE = _PROJECT_ROOT / "version.txt"


def _read_version() -> str:
    """Return the version string from ``version.txt``, or "dev" if missing."""
    try:
        if _VERSION_FILE.is_file():
            content = _VERSION_FILE.read_text().strip()
            if content:
                return content
    except Exception:
        pass
    return "dev"


def get_version() -> str:
    """Return the current version string.

    Re-reads ``version.txt`` on every call so the value tracks the file as
    it is rewritten by the pre-commit hook. The earlier module-level cache
    was wrong: it froze the version to whatever was in the file at server
    startup, so the System Health page showed a stale value until the
    backend was restarted.
    """
    return _read_version()
