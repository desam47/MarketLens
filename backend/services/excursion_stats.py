"""Excursion distribution statistics for historical signal outcomes.

Turns a set of outcome-complete ``HistoricalSignal`` rows into the
distribution a trade plan needs: how far a comparable setup normally moved
against the call before it worked (adverse excursion, the empirical stop) and
how far it ran in favour (favorable excursion, the empirical target).

Percentiles, not means. Stored excursions carry real outliers -- splits and
penny-stock artifacts push ``mfe`` past 20,000% -- which a mean cannot
survive but a quantile shrugs off.

Pure: no DB session, no SQLAlchemy query. Callers fetch rows (see
``SignalRepository.fetch_excursion_rows``) and pass them in, so this is
unit-testable against hand-built tuples.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from backend.repositories.signal_repository import directional_outcome

PERCENTILES = (25, 50, 75, 90)

RETURN_FIELDS = ("return_5b", "return_10b", "return_20b")

# Below this many rows the distribution is not worth acting on, so percentiles
# are withheld entirely rather than published as a weak number.
DEFAULT_MIN_SAMPLE = 100

# A slice this large makes the quantiles stable enough to size a stop from.
HIGH_CONFIDENCE_SAMPLE = 500

# An adverse/favorable excursion past this (percent) is almost certainly a
# split or penny-stock artifact rather than a tradeable move.
OUTLIER_PCT = 50.0


def _percentiles(values: Sequence[float]) -> dict[str, float] | None:
    """Linear-interpolated quantiles keyed ``p25``/``p50``/``p75``/``p90``."""
    if not values:
        return None
    computed = np.percentile(np.asarray(values, dtype=float), PERCENTILES)
    return {f"p{p}": round(float(v), 4) for p, v in zip(PERCENTILES, computed, strict=True)}


def _mean(values: Sequence[float]) -> float | None:
    return round(float(np.mean(values)), 4) if values else None


def _median(values: Sequence[float]) -> float | None:
    return round(float(np.median(values)), 4) if values else None


def excursion_distribution(
    rows: Sequence[Any], *, min_sample: int = DEFAULT_MIN_SAMPLE
) -> dict[str, Any]:
    """Summarise excursions and forward returns for one conditioned slice.

    ``rows`` need ``trend_state``, ``mae``, ``mfe`` and the ``return_*``
    fields -- ORM objects, SQLAlchemy ``Row``s and plain namespaces all work,
    since every read goes through :func:`directional_outcome`.

    Excursions are reported from the signal's own point of view:
    ``adverse_excursion_pct`` as a positive magnitude, so its ``p75`` reads
    "75% of comparable signals drew down less than this" and can be used
    directly as a stop distance. ``favorable_excursion_pct`` stays signed, so
    its ``p50`` is the median run in favour -- the empirical target.

    When fewer than ``min_sample`` rows carry a directional outcome, every
    percentile and aggregate is ``None``: a half-trusted stop is worse than an
    admitted gap, and the caller is expected to relax its filters and retry.
    """
    adverse: list[float] = []
    favorable: list[float] = []
    returns: dict[str, list[float]] = {field: [] for field in RETURN_FIELDS}

    for row in rows:
        mae = directional_outcome(row, "mae")
        mfe = directional_outcome(row, "mfe")
        # Neutral/unknown rows made no call, so they have no directional
        # outcome and must not dilute the distribution.
        if mae is None or mfe is None:
            continue
        adverse.append(abs(mae))
        favorable.append(mfe)
        for field in RETURN_FIELDS:
            value = directional_outcome(row, field)
            if value is not None:
                returns[field].append(value)

    sample_size = len(adverse)
    sufficient = sample_size >= min_sample

    if sample_size >= HIGH_CONFIDENCE_SAMPLE:
        confidence = "high"
    elif sufficient:
        confidence = "moderate"
    else:
        confidence = "insufficient"

    result: dict[str, Any] = {
        "sample_size": sample_size,
        "min_sample": min_sample,
        "sufficient": sufficient,
        "confidence": confidence,
        "units": "percent",
        "win_rate": None,
        "adverse_excursion_pct": None,
        "favorable_excursion_pct": None,
        "notes": [],
    }
    for field in RETURN_FIELDS:
        result[f"avg_{field}"] = None
        result[f"median_{field}"] = None

    if not sufficient:
        result["notes"].append(
            f"Only {sample_size} comparable signals (need {min_sample}); "
            "statistics withheld."
        )
        return result

    result["adverse_excursion_pct"] = _percentiles(adverse)
    result["favorable_excursion_pct"] = _percentiles(favorable)

    wins = returns["return_10b"]
    if wins:
        result["win_rate"] = round(sum(1 for v in wins if v > 0) / len(wins), 4)
    for field in RETURN_FIELDS:
        result[f"avg_{field}"] = _mean(returns[field])
        result[f"median_{field}"] = _median(returns[field])

    adverse_p90 = result["adverse_excursion_pct"]["p90"]
    favorable_p90 = result["favorable_excursion_pct"]["p90"]
    if max(adverse_p90, favorable_p90) > OUTLIER_PCT:
        result["notes"].append(
            f"Tail exceeds {OUTLIER_PCT:.0f}% (adverse p90 {adverse_p90:.1f}%, "
            f"favorable p90 {favorable_p90:.1f}%); suspect split or penny-stock "
            "artifacts. Prefer p50/p75."
        )

    return result
