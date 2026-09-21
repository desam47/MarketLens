"""Shared, engine-agnostic series/bar utilities.

Extracted from ``backend.api.analysis.router`` so non-router code
(``backend.ai.context.build_context()``, in particular) can reuse the
exact same bar-loading and indicator-series logic the ``/divergences``
endpoint already uses, instead of reaching into a router module's
private functions.
"""

from backend.analysis.series import (
    bar_dicts_to_arrays,
    load_bars,
    macd_histogram_series,
    rsi_series,
)

__all__ = [
    "load_bars",
    "bar_dicts_to_arrays",
    "rsi_series",
    "macd_histogram_series",
]
