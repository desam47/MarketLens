"""
Market Context Engine — aggregates SPY/QQQ/IWM/VIX into a single regime.

Phase 8 spec §1: analyze SPY, QQQ, IWM, VIX. Emit one of
RISK_ON / RISK_OFF / NEUTRAL / TRANSITION. Also report market trend,
volatility state, momentum, and trend strength.

Hard rule: the per-symbol MarketRegimeEngine is used as the building
block for each sub-index, but the per-symbol API now returns the
new 4-name enum. The aggregation rule (consensus threshold) is
configurable via MarketContextSettings.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config.settings import settings
from .market_regime_engine import MarketRegime, MarketRegimeEngine

logger = logging.getLogger(__name__)


@dataclass
class MarketContextSignal:
    """Phase 8: market-wide regime + supporting metrics."""

    regime: str  # MarketRegime.value
    confidence: float  # 0.0..1.0
    trend_strength: float  # 0.0..1.0
    momentum: float  # -1.0..+1.0  (negative = bearish, positive = bullish)
    volatility_state: str  # "low" | "normal" | "high" | "unknown"
    sub_regimes: dict[str, str] = field(default_factory=dict)
    # SPY/QQQ/IWM/VIX sub-regime values
    contributing_factors: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None
    data_age_seconds: float | None = None
    freshness: str = "unknown"
    sub_data_age_seconds: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "confidence": round(self.confidence, 3),
            "trend_strength": round(self.trend_strength, 3),
            "momentum": round(self.momentum, 3),
            "volatility_state": self.volatility_state,
            "sub_regimes": self.sub_regimes,
            "contributing_factors": self.contributing_factors,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "data_age_seconds": self.data_age_seconds,
            "freshness": self.freshness,
            "sub_data_age_seconds": self.sub_data_age_seconds,
        }


class MarketContextEngine:
    """
    Phase 8 spec: SPY/QQQ/IWM/VIX → market-wide regime.

    Aggregation rule:
      * 3+ sub-regimes RISK_ON   → RISK_ON
      * 3+ sub-regimes RISK_OFF  → RISK_OFF
      * 3+ sub-regimes NEUTRAL   → NEUTRAL
      * Mixed / conflicting / high VIX → TRANSITION
    """

    def __init__(self):
        self._cfg = settings.market_context
        self.sub_engines: dict[str, MarketRegimeEngine] = {
            sym: MarketRegimeEngine(sym) for sym in self._cfg.indices
        }
        self._price_history: dict[str, list[tuple[datetime, float]]] = {
            sym: [] for sym in self._cfg.indices
        }
        self._signals: deque[MarketContextSignal] = deque(maxlen=1000)
        self._last_vix_factors: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        price: float,
        volume: float,
        timestamp: datetime,
        symbol: str,
        high: float | None = None,
        low: float | None = None,
        open_price: float | None = None,
        provider: str = "internal",
    ) -> None:
        """
        Feed a new tick for one of the configured index symbols.

        Routes the tick to the right sub-engine and tracks price history
        for momentum / volatility calculations.
        """
        symbol = symbol.upper()
        if symbol not in self.sub_engines:
            logger.warning(f"MarketContextEngine: unknown index {symbol}, ignoring")
            return

        self.sub_engines[symbol].update(
            price=price,
            volume=volume,
            timestamp=timestamp,
            high=high,
            low=low,
            open_price=open_price,
            provider=provider,
        )
        self._price_history[symbol].append((timestamp, price))
        self._prune_history(symbol)

    def get_current_context(self) -> MarketContextSignal | None:
        """Compute and return the current aggregated market context signal."""
        sub_regimes = self._collect_sub_regimes()
        if not sub_regimes:
            return None

        regime, confidence, factors = self._aggregate(sub_regimes)
        factors.update(self._last_vix_factors)
        trend_strength, momentum, volatility_state = self._aggregate_metrics()
        sub_ages = {
            sym: self._age_seconds(signal.timestamp)
            for sym, engine in self.sub_engines.items()
            if (signal := engine.get_current_regime()) is not None
        }
        ts = max(
            (signal.timestamp for engine in self.sub_engines.values() if (signal := engine.get_current_regime())),
            default=datetime.now(UTC),
        )
        age = self._age_seconds(ts)

        signal = MarketContextSignal(
            regime=regime,
            confidence=confidence,
            trend_strength=trend_strength,
            momentum=momentum,
            volatility_state=volatility_state,
            sub_regimes={k: v.value for k, v in sub_regimes.items()},
            contributing_factors=factors,
            timestamp=ts,
            data_age_seconds=age,
            freshness=self._freshness(age),
            sub_data_age_seconds=sub_ages,
        )
        if self._signals and self._same_signal_state(self._signals[-1], signal):
            self._signals[-1] = signal
        else:
            self._signals.append(signal)
        return signal

    def get_history(self, limit: int | None = None) -> list[MarketContextSignal]:
        if limit is None:
            return list(self._signals)
        if limit <= 0:
            return []
        return list(self._signals)[-limit:]

    @staticmethod
    def _age_seconds(timestamp: datetime | None) -> float | None:
        if timestamp is None:
            return None
        now = datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return max(0.0, (now - timestamp.astimezone(UTC)).total_seconds())

    @staticmethod
    def _freshness(age: float | None) -> str:
        if age is None:
            return "unknown"
        if age < 60:
            return "fresh"
        if age < 300:
            return "recent"
        if age < 3600:
            return "stale"
        return "stuck"

    @staticmethod
    def _same_signal_state(previous: MarketContextSignal, current: MarketContextSignal) -> bool:
        return (
            previous.regime == current.regime
            and previous.confidence == current.confidence
            and previous.trend_strength == current.trend_strength
            and previous.momentum == current.momentum
            and previous.volatility_state == current.volatility_state
            and previous.sub_regimes == current.sub_regimes
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_sub_regimes(self) -> dict[str, MarketRegime]:
        """Read each sub-engine's most recent regime. Skip if cold-start."""
        out: dict[str, MarketRegime] = {}
        self._last_vix_factors = {}
        for sym, engine in self.sub_engines.items():
            sig = engine.get_current_regime()
            if sig is not None:
                if self._is_vix_symbol(sym):
                    out[sym] = self._classify_vix(sym, sig.regime)
                else:
                    out[sym] = sig.regime
        return out

    def _is_vix_symbol(self, symbol: str) -> bool:
        return symbol.upper() in {"^VIX", "VIX", "VIXY"}

    def _classify_vix(self, symbol: str, fallback: MarketRegime) -> MarketRegime:
        history = self._price_history.get(symbol, [])
        if not history:
            return fallback
        price = history[-1][1]
        previous = history[-2][1] if len(history) > 1 else None
        change = ((price - previous) / previous) if previous else 0.0
        cfg = self._cfg
        if change >= cfg.vix_spike_threshold:
            regime = MarketRegime.TRANSITION
            reason = "vix_spike"
        elif price >= cfg.vix_risk_off_min:
            regime = MarketRegime.RISK_OFF
            reason = "vix_high"
        elif price <= cfg.vix_risk_on_max:
            regime = MarketRegime.RISK_ON
            reason = "vix_low"
        else:
            # Between configured levels, invert the underlying VIX product's
            # directional regime as a conservative fallback.
            regime = (
                MarketRegime.RISK_OFF
                if fallback == MarketRegime.RISK_ON
                else MarketRegime.RISK_ON
                if fallback == MarketRegime.RISK_OFF
                else fallback
            )
            reason = "vix_mid_range"
        self._last_vix_factors.update({f"vix_{symbol}_level": price, "vix_change": change, "vix_reason": reason})
        return regime

    def _aggregate(
        self,
        sub_regimes: dict[str, MarketRegime],
    ) -> tuple[str, float, dict[str, Any]]:
        """
        Aggregate sub-regimes into a single market-wide regime.

        Per spec:
          3+ of 4 RISK_ON   → RISK_ON
          3+ of 4 RISK_OFF  → RISK_OFF
          3+ of 4 NEUTRAL   → NEUTRAL
          mixed              → TRANSITION
        """
        factors: dict[str, Any] = {}

        if not sub_regimes:
            factors["reason"] = "no_sub_regimes"
            return MarketRegime.UNKNOWN.value, 0.0, factors

        # Count sub-regimes (skip UNKNOWN)
        counts = {
            MarketRegime.RISK_ON: 0,
            MarketRegime.RISK_OFF: 0,
            MarketRegime.NEUTRAL: 0,
            MarketRegime.TRANSITION: 0,
        }
        for sym, reg in sub_regimes.items():
            if reg in counts:
                counts[reg] += 1
            factors[f"sub_{sym}"] = reg.value

        threshold = self._cfg.consensus_threshold

        # Apply consensus
        if counts[MarketRegime.RISK_ON] >= threshold:
            factors["primary_reason"] = "consensus_risk_on"
            return (
                MarketRegime.RISK_ON.value,
                min(0.9, 0.5 + counts[MarketRegime.RISK_ON] * 0.1),
                factors,
            )
        if counts[MarketRegime.RISK_OFF] >= threshold:
            factors["primary_reason"] = "consensus_risk_off"
            return (
                MarketRegime.RISK_OFF.value,
                min(0.9, 0.5 + counts[MarketRegime.RISK_OFF] * 0.1),
                factors,
            )
        if counts[MarketRegime.NEUTRAL] >= threshold:
            factors["primary_reason"] = "consensus_neutral"
            return (
                MarketRegime.NEUTRAL.value,
                min(0.9, 0.5 + counts[MarketRegime.NEUTRAL] * 0.1),
                factors,
            )

        # Mixed → TRANSITION
        factors["primary_reason"] = "mixed_sub_regimes"
        return MarketRegime.TRANSITION.value, 0.6, factors

    def _aggregate_metrics(self) -> tuple[float, float, str]:
        """Average trend strength/momentum/volatility across sub-engines.

        Reuses each sub-engine's own RegimeSignal.strength (0..1, already
        its considered conviction in the current call) and
        supporting_factors (trend_direction, volatility_pct) instead of
        recomputing anything from raw price history.
        """
        strengths: list[float] = []
        momenta: list[float] = []
        vol_pcts: list[float] = []
        for sym, engine in self.sub_engines.items():
            sig = engine.get_current_regime()
            if sig is None:
                continue
            strengths.append(sig.strength)
            history = self._price_history.get(sym, [])
            if len(history) >= 2 and history[0][1] > 0:
                returns = (history[-1][1] - history[0][1]) / history[0][1]
                momenta.append(max(-1.0, min(1.0, returns * 10.0)))
            else:
                momenta.append(0.0)
            vol_pct = sig.supporting_factors.get("volatility_pct")
            if vol_pct is not None:
                vol_pcts.append(vol_pct)

        if not strengths:
            return 0.0, 0.0, "unknown"

        trend_strength = sum(strengths) / len(strengths)
        momentum = sum(momenta) / len(momenta)

        if vol_pcts:
            avg_vol = sum(vol_pcts) / len(vol_pcts)
            # Same thresholds MarketRegimeEngine itself classifies with —
            # every sub-engine carries the same constants, so any one works.
            sample_engine = next(iter(self.sub_engines.values()))
            if avg_vol > sample_engine.volatility_threshold_high:
                volatility_state = "high"
            elif avg_vol < sample_engine.volatility_threshold_low:
                volatility_state = "low"
            else:
                volatility_state = "normal"
        else:
            volatility_state = "unknown"

        # A high/spiking VIX should dominate the aggregate volatility label.
        if any(reason in {"vix_high", "vix_spike"} for reason in [self._last_vix_factors.get("vix_reason")]):
            volatility_state = "high"
        return trend_strength, momentum, volatility_state

    def _prune_history(self, symbol: str, max_len: int = 100) -> None:
        hist = self._price_history[symbol]
        if len(hist) > max_len:
            self._price_history[symbol] = hist[-max_len:]
