#!/usr/bin/env python3
"""
Test runner for MarketLens backend.

Discovers and runs all tests under backend/tests/. Works from any CWD
because it computes paths absolutely rather than relative to the
invocation directory.

Run from project root:
    python3 backend/tests/run_tests.py

Or with unittest directly:
    python3 -m unittest discover -s backend/tests -t .
"""

import os
import sys
import unittest

# Absolute path setup: tests use `from backend.X import Y`, so the project
# root (the directory containing the `backend/` package) must be on sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)
# Also keep the original behavior for tests that look up the backend dir.
sys.path.insert(0, os.path.dirname(_HERE))

# Make sure pytest-style invocation (`pytest backend/tests`) also works by
# having the backend root on sys.path too.
_BACKEND_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    # Discover from the tests/ package. Pattern matches our test file naming.
    suite = loader.discover(
        start_dir=os.path.join(_BACKEND_ROOT, "tests"),
        top_level_dir=_PROJECT_ROOT,
        pattern="test_*.py",
    )

    # Mark pre-existing broken tests as skipped so the runner reports them
    # with a clear reason rather than red errors. See known_broken.py.
    from tests.known_broken import apply_known_broken_skips

    suite = apply_known_broken_skips(suite)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
