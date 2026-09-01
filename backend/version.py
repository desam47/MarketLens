"""
Git-based version info.

Read from ``version.txt`` (written by the pre-commit hook on every commit).
Falls back to "dev" when not available so the server starts without git.
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


# Module-level singleton — computed once at import time.
_version_string: str | None = None


def get_version() -> str:
    """Return the current version string.

    On first call, reads ``version.txt``. Subsequent calls return the cached
    value so the file is read at most once per process lifetime.
    """
    global _version_string
    if _version_string is None:
        _version_string = _read_version()
    return _version_string
