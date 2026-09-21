"""
Shared module for test-patchable infrastructure used by cache.py, providers.py,
and manager_class.py.

Tests patch ``backend.market_data.services.manager._settings`` and
``backend.market_data.services.manager.redis``. We need those patches to be
visible from the submodules as well.

This module provides:
  - module-level ``_settings`` and ``redis`` that the submodules can import
    once at module load time (used for type hints and module-level constants)
  - ``get_settings()`` and ``get_redis()`` helper functions that look up the
    current value on the manager shim at call time, so test patches are honoured.

The shim (``manager.py``) re-exports these names so test patches against
``manager._settings`` and ``manager.redis`` are visible everywhere.
"""

import redis as _redis_lib

from backend.config.settings import settings as _real_settings

# Module-level defaults — used during initial import before the shim is
# fully set up, and as type-hint anchors in the submodules.
_settings = _real_settings
redis = _redis_lib


def get_settings():
    """Return the current ``_settings`` attribute on the manager shim.

    Tests patch ``backend.market_data.services.manager._settings``; by
    resolving through the shim module dynamically on every call we pick up
    the patched value rather than the one captured at import time.

    During the brief import window when the shim hasn't been loaded yet,
    we fall back to the local module-level ``_settings``.
    """
    import sys

    try:
        return sys.modules[__package__ + ".manager"]._settings
    except (KeyError, AttributeError):
        return _settings


def get_redis():
    """Return the current ``redis`` attribute on the manager shim.

    Same rationale as ``get_settings`` — tests patch
    ``backend.market_data.services.manager.redis`` and we want the patched
    module to be visible here.
    """
    import sys

    try:
        return sys.modules[__package__ + ".manager"].redis
    except (KeyError, AttributeError):
        return redis


def get_redis_cache():
    """Return the current ``_redis_cache`` attribute on the manager shim.

    ``manager_class.py`` binds ``_redis_cache`` from ``cache`` at import time.
    Tests patch ``manager._redis_cache`` — this helper resolves through the shim
    so the patched value is visible to all code that calls the getter.

    Falls back to the real ``cache._redis_cache`` singleton when the shim is
    not yet loaded.
    """
    import sys

    try:
        return sys.modules[__package__ + ".manager"]._redis_cache
    except (KeyError, AttributeError):
        # Defer the import to break the circular dependency
        # (_providers → cache → _providers would be a cycle).
        from backend.market_data.services.cache import _redis_cache as _fallback

        return _fallback
