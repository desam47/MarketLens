"""
Root conftest — reset shared in-process rate limiter before every test.

The rate limiter in `backend.api.main` is a module-level singleton that
accumulates hits across the full test suite run. Without a reset hook,
tests that run late in the suite (e.g. watchlist import tests) can hit
429s even though they only made one POST request in their own test.

A session-scoped fixture would also work, but function-scoped ensures
complete isolation when tests run in random order.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-process rate limiter before each test."""
    try:
        from backend.api.main import _write_limiter
        _write_limiter.reset()
    except ImportError:
        pass  # App hasn't been imported yet; skip.
    yield
