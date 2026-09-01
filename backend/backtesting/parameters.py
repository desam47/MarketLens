"""
Phase 19 — ExperimentParameters schema.

The Strategy Lab lets the front-end submit a partial Pydantic model of
indicator periods, signal thresholds, and scoring weights; the server
fills in defaults before the engine sees them. This is the same
defaults pattern the live scanner uses for ``IndicatorDefaults`` and
``TrendSignalWeights`` — keeping the defaults in a single Pydantic
class means the front-end never has to re-state the same numbers
across requests.

All bounds here are advisory; the engine itself doesn't enforce them
(e.g. ``rsi_period * 2`` warmup is computed from whatever the caller
chose). The bounds exist to catch obvious nonsense in the form.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExperimentParameters(BaseModel):
    """Every tunable parameter for a Strategy Lab experiment.

    All fields default to the server-side ``IndicatorDefaults`` and
    ``TrendSignalWeights`` values so the front-end can omit unchanged
    fields and the engine always has a full set to work with.
    """

    # --- Indicator periods ---
    rsi_period: int = Field(default=14, ge=2, le=100)
    macd_fast: int = Field(default=12, ge=2, le=200)
    macd_slow: int = Field(default=26, ge=2, le=200)
    macd_signal: int = Field(default=9, ge=2, le=100)
    adx_period: int = Field(default=14, ge=2, le=100)
    # ATR is used only for regime tagging (not signal generation in replay)
    atr_period: int = Field(default=14, ge=2, le=100)
    supertrend_atr_period: int = Field(default=10, ge=2, le=50)
    supertrend_multiplier: float = Field(default=3.0, ge=0.5, le=10.0)
    bollinger_period: int = Field(default=20, ge=2, le=200)
    bollinger_std_dev: float = Field(default=2.0, ge=0.5, le=5.0)
    ema_fast_period: int = Field(default=20, ge=2, le=200)
    ema_slow_period: int = Field(default=50, ge=2, le=500)

    # --- Signal thresholds ---
    # RSI is "oversold" below rsi_oversold, "overbought" above rsi_overbought.
    # Replay uses these to fire RSI_OVERSOLD / RSI_OVERBOUGHT; live scanner
    # still uses the hardcoded 30/70 thresholds (no live behaviour change).
    rsi_oversold: float = Field(default=30.0, ge=1, le=50)
    rsi_overbought: float = Field(default=70.0, ge=50, le=99)
    # MACD zero-line crossover is always used in v1.
    # SuperTrend direction flip is always used.

    # --- Regime thresholds (used by classify_regime) ---
    adx_trending_threshold: float = Field(
        default=25.0, ge=10, le=50,
        description="ADX above this value → trending regime",
    )
    rsi_bullish_ceiling: float = Field(
        default=55.0, ge=45, le=70,
        description="Within trending: RSI above this → bullish regime",
    )
    rsi_bearish_floor: float = Field(
        default=45.0, ge=30, le=55,
        description="Within trending: RSI below this → bearish regime",
    )

    # --- Signal weights (for Scanner; used in replay only for completeness).
    # Defaults must mirror ``settings.trend.signal_weights`` — the live
    # engine rebalances these in Phase 6.1 (see TrendSignalWeights), and
    # tests/backtesting/test_parameters.py asserts equality for each.
    weight_ema: float = Field(default=0.25, ge=0.0, le=2.0)
    weight_rsi: float = Field(default=0.10, ge=0.0, le=2.0)
    weight_macd: float = Field(default=0.20, ge=0.0, le=2.0)
    weight_adx: float = Field(default=0.15, ge=0.0, le=2.0)
    weight_volume: float = Field(default=0.05, ge=0.0, le=2.0)
    weight_momentum: float = Field(default=0.10, ge=0.0, le=2.0)
    weight_supertrend: float = Field(default=0.20, ge=0.0, le=2.0)
    weight_bollinger: float = Field(default=0.10, ge=0.0, le=2.0)

    def indicator_defaults(self) -> dict[str, int | float]:
        """Return a dict compatible with ``IndicatorDefaults`` field names.

        Currently used only for documentation; the engine reads
        individual fields directly off the ``ExperimentParameters``
        instance passed in ``BacktestConfig``.
        """
        return {
            "rsi_period": self.rsi_period,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "adx_period": self.adx_period,
            "supertrend_atr_period": self.supertrend_atr_period,
            "supertrend_multiplier": self.supertrend_multiplier,
            "bollinger_period": self.bollinger_period,
            "bollinger_std_dev": self.bollinger_std_dev,
        }

    def to_json_dict(self) -> dict:
        """Return a JSON-serializable dict of all parameter values.

        The ``Experiment.parameters_json`` column stores the result of
        ``json.dumps(self.to_json_dict())`` so the front-end can echo
        the configuration back without a separate round-trip.
        """
        return self.model_dump()
