"""
Phase 19 — Cross-split overfitting detector.

A single ``BacktestRun`` already gets a per-run overfit warning from
``_compute_metrics`` (win rate > 70 % or Sharpe > 2.0). This module
adds a second, **cross-split** check that runs after an experiment's
in-sample / validation / out-of-sample slices have all completed.

The output is an ``OverfitReport`` with:

  * ``score``           — accumulated 0.0–1.0+ red-flag score
  * ``warnings``        — human-readable bullet list
  * ``is_stable``       — True when ``score < OVERFIT_SCORE_WARNING``
  * ``oos_sharpe_ratio``— IS Sharpe / OOS Sharpe (None when undefined)
  * ``oos_win_rate_gap``— IS win rate − OOS win rate (None when undefined)

These are heuristics, not statistical proofs. A high score means
"verify on out-of-sample data", not "this is overfit". The warning
banner always reads that way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Per-run thresholds (unchanged from Phase 14) ---
OVERFIT_WINRATE_THRESHOLD = 0.70
OVERFIT_SHARPE_THRESHOLD = 2.0

# --- Cross-split stability thresholds ---
# Sharpe deterioration: IS_sharpe / OOS_sharpe > this ratio → overfit.
# e.g. 2.0 means OOS Sharpe must be at least 50 % of IS Sharpe.
OVERFIT_SHARPE_RATIO = 2.0
# Win rate gap: IS_win_rate - OOS_win_rate > this → overfit.
OVERFIT_WINRATE_GAP = 0.15
# Return sign flip: IS_return > 0 but OOS_return < 0 → overfit.
OVERFIT_RETURN_FLIP = True
# Min signals per slice for the report to consider a slice meaningful.
MIN_SIGNALS_PER_SLICE = 10

# --- Overall score thresholds ---
OVERFIT_SCORE_WARNING = 0.5  # Score >= this → show warning banner
OVERFIT_SCORE_DANGER = 0.8  # Score >= this → strong caution banner


@dataclass
class OverfitReport:
    """Output of ``compute_overfit_report()``."""

    score: float
    warnings: list[str] = field(default_factory=list)
    is_stable: bool = True
    oos_sharpe_ratio: float | None = None
    oos_win_rate_gap: float | None = None


def _safe(m: dict | None, key: str) -> float | None:
    return m.get(key) if m else None


def _check_single(
    m: dict | None,
    label: str,
    warnings: list[str],
    score: list[float],
) -> None:
    """Apply per-slice red flags (high winrate, high Sharpe, low signal count)."""
    wr = _safe(m, "win_rate_1d")
    sh = _safe(m, "sharpe_ratio")
    n = _safe(m, "total_signals") or 0
    if wr is not None and wr > OVERFIT_WINRATE_THRESHOLD:
        warnings.append(f"{label}: win rate {wr:.0%} exceeds {OVERFIT_WINRATE_THRESHOLD:.0%}")
        score.append(0.3)
    if sh is not None and sh > OVERFIT_SHARPE_THRESHOLD:
        warnings.append(f"{label}: Sharpe {sh:.1f} exceeds {OVERFIT_SHARPE_THRESHOLD:.1f}")
        score.append(0.3)
    if n < MIN_SIGNALS_PER_SLICE and n > 0:
        warnings.append(f"{label}: only {n} signals — results may not be statistically meaningful")
        score.append(0.1)


def compute_overfit_report(
    is_metrics: dict | None,
    val_metrics: dict | None,
    oos_metrics: dict | None,
) -> OverfitReport:
    """Compute overfitting score from three-slice metrics dicts.

    Each ``metrics`` dict is expected to have keys: ``win_rate_1d``,
    ``sharpe_ratio``, ``avg_return_1d``, ``total_signals``. Any of
    these may be ``None`` (or the whole dict may be ``None``).
    """
    warnings: list[str] = []
    score_parts: list[float] = []

    _check_single(is_metrics, "in-sample", warnings, score_parts)
    _check_single(val_metrics, "validation", warnings, score_parts)
    _check_single(oos_metrics, "out-of-sample", warnings, score_parts)

    oos_sharpe_ratio: float | None = None
    oos_win_rate_gap: float | None = None

    if is_metrics and oos_metrics:
        is_sharpe = _safe(is_metrics, "sharpe_ratio")
        oos_sharpe = _safe(oos_metrics, "sharpe_ratio")
        if is_sharpe and oos_sharpe and oos_sharpe != 0:
            ratio = is_sharpe / oos_sharpe
            oos_sharpe_ratio = ratio
            if ratio > OVERFIT_SHARPE_RATIO:
                warnings.append(
                    f"Sharpe deteriorated {ratio:.1f}x from IS to OOS "
                    f"(IS={is_sharpe:.2f}, OOS={oos_sharpe:.2f})"
                )
                score_parts.append(0.4)

        is_wr = _safe(is_metrics, "win_rate_1d")
        oos_wr = _safe(oos_metrics, "win_rate_1d")
        if is_wr is not None and oos_wr is not None:
            gap = is_wr - oos_wr
            oos_win_rate_gap = gap
            if gap > OVERFIT_WINRATE_GAP:
                warnings.append(
                    f"Win rate dropped {gap:.0%} from IS to OOS (IS={is_wr:.0%}, OOS={oos_wr:.0%})"
                )
                score_parts.append(0.3)

        is_ret = _safe(is_metrics, "avg_return_1d")
        oos_ret = _safe(oos_metrics, "avg_return_1d")
        if OVERFIT_RETURN_FLIP and is_ret is not None and oos_ret is not None:
            if is_ret > 0 and oos_ret < 0:
                warnings.append(
                    f"Average return flipped sign: IS=+{is_ret:.2f}%, OOS={oos_ret:.2f}%"
                )
                score_parts.append(0.5)
            elif is_ret > 0 and oos_ret > 0 and oos_ret < is_ret * 0.5:
                warnings.append(
                    f"OOS return only {oos_ret / is_ret:.0%} of IS return — "
                    "performance did not carry forward"
                )
                score_parts.append(0.2)

    score = round(sum(score_parts), 2)
    return OverfitReport(
        score=score,
        warnings=warnings,
        is_stable=score < OVERFIT_SCORE_WARNING,
        oos_sharpe_ratio=oos_sharpe_ratio,
        oos_win_rate_gap=oos_win_rate_gap,
    )
