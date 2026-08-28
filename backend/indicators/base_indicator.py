"""
Base technical indicator class
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

# Indicator concrete classes are imported lazily inside create_indicator()
# to keep this module a pure abstraction layer (Phase 0 Principle 17:
# "build interfaces before implementations"). Callers should depend on
# IndicatorEngine.create_indicator() rather than importing indicator
# classes directly.


class BaseIndicator(ABC):
    """Base class for all technical indicators"""

    def __init__(self, name: str, parameters: dict[str, Any]):
        self.name = name
        self.parameters = parameters
        self.values: list[float] = []
        self.timestamps: list[datetime] = []

    @abstractmethod
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate indicator values for the given data"""

    @abstractmethod
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update indicator with new data point and return latest value"""

    def get_latest(self) -> float | None:
        """Get the latest calculated value"""
        return self.values[-1] if self.values else None

    def get_values(self) -> list[float]:
        """Get all calculated values"""
        return self.values.copy()

    def reset(self):
        """Reset indicator to initial state"""
        self.values = []
        self.timestamps = []


class IndicatorEngine:
    """Engine for managing and calculating technical indicators.

    Acts as the single facade for indicator creation so callers don't have
    to import concrete indicator classes (Phase 0 Principle 17). Use
    ``create_indicator(kind, params)`` to build a specific indicator and
    ``build_timeframe_stack(timeframe, defaults)`` to build the full
    per-timeframe stack the trend engine needs.
    """

    # Maps a logical kind name to the concrete indicator class. Imports
    # are local to ``create_indicator`` so this module stays a pure ABC
    # layer that other engines can import without circular dependencies.
    _KIND_MAP: dict[str, str] = {
        "ema": "EMAIndicator",
        "sma": "SMAIndicator",
        "rsi": "RSIIndicator",
        "macd": "MACDIndicator",
        "adx": "ADXIndicator",
        "atr": "ATRIndicator",
        "supertrend": "SuperTrendIndicator",
        "bollinger_bands": "BollingerBandsIndicator",
        "volume_sma": "VolumeSMAIndicator",
        "relative_volume": "RelativeVolumeIndicator",
        "obv": "OBVIndicator",
        "roc": "ROCIndicator",
        "swing_high": "SwingHighIndicator",
        "swing_low": "SwingLowIndicator",
    }

    def __init__(self):
        self.indicators: dict[str, BaseIndicator] = {}
        self.data_history: list[dict[str, Any]] = []

    @classmethod
    def create_indicator(cls, kind: str, params: dict[str, Any] | None = None) -> BaseIndicator:
        """Factory: build a BaseIndicator subclass by logical kind name.

        ``params`` is forwarded to the concrete class. Unknown kinds raise
        ``ValueError`` so typos surface immediately rather than at update time.
        """
        if kind not in cls._KIND_MAP:
            raise ValueError(
                f"Unknown indicator kind '{kind}'. "
                f"Valid kinds: {sorted(cls._KIND_MAP)}"
            )
        # Lazy import — concrete classes are loaded only when needed.
        from . import adx as _adx
        from . import atr as _atr
        from . import bollinger_bands as _bb
        from . import ema as _ema
        from . import macd as _macd
        from . import obv as _obv
        from . import relative_volume as _relvol
        from . import roc as _roc
        from . import rsi as _rsi
        from . import sma as _sma
        from . import supertrend as _st
        from . import swing_high as _sw_high
        from . import swing_low as _sw_low
        from . import volume_sma as _vsma

        _module_map = {
            "ema": _ema,
            "sma": _sma,
            "rsi": _rsi,
            "macd": _macd,
            "adx": _adx,
            "atr": _atr,
            "supertrend": _st,
            "bollinger_bands": _bb,
            "volume_sma": _vsma,
            "relative_volume": _relvol,
            "obv": _obv,
            "roc": _roc,
            "swing_high": _sw_high,
            "swing_low": _sw_low,
        }
        module = _module_map[kind]
        cls_obj = getattr(module, cls._KIND_MAP[kind])
        return cls_obj(**(params or {}))

    @classmethod
    def build_timeframe_stack(
        cls,
        ema_fast: int,
        ema_slow: int,
        defaults: Any,
    ) -> dict[str, BaseIndicator]:
        """Build the standard indicator stack used by TrendEngine per timeframe.

        The stack is intentionally minimal at the short end (no supertrend/ADX
        on 1m) and gets richer as the timeframe extends, mirroring the
        original hard-coded config but driven by ``IndicatorDefaults`` rather
        than literals.
        """
        return {
            "ema_fast": cls.create_indicator("ema", {"period": ema_fast}),
            "ema_slow": cls.create_indicator("ema", {"period": ema_slow}),
            "rsi": cls.create_indicator("rsi", {"period": defaults.rsi_period}),
            "macd": cls.create_indicator(
                "macd",
                {
                    "fast": defaults.macd_fast,
                    "slow": defaults.macd_slow,
                    "signal": defaults.macd_signal,
                },
            ),
            "adx": cls.create_indicator("adx", {"period": defaults.adx_period}),
            "supertrend": cls.create_indicator(
                "supertrend",
                {
                    "atr_period": defaults.supertrend_atr_period,
                    "multiplier": defaults.supertrend_multiplier,
                },
            ),
            "bollinger_bands": cls.create_indicator(
                "bollinger_bands",
                {
                    "period": defaults.bollinger_period,
                    "std_dev": defaults.bollinger_std_dev,
                },
            ),
        }

    def add_indicator(self, indicator: BaseIndicator):
        """Add an indicator to the engine"""
        self.indicators[indicator.name] = indicator

    def remove_indicator(self, name: str):
        """Remove an indicator from the engine"""
        if name in self.indicators:
            del self.indicators[name]

    def update_data(self, new_data: dict[str, Any]):
        """Update engine with new market data"""
        self.data_history.append(new_data)

        # Update all indicators with the new data
        for indicator in self.indicators.values():
            indicator.update(new_data)

    def calculate_all(self, data: list[dict[str, Any]]) -> dict[str, list[float]]:
        """Calculate all indicators for the given data"""
        results = {}
        for name, indicator in self.indicators.items():
            results[name] = indicator.calculate(data)
        return results

    def get_latest_values(self) -> dict[str, float | None]:
        """Get latest values for all indicators"""
        return {name: indicator.get_latest() for name, indicator in self.indicators.items()}

    def get_indicator(self, name: str) -> BaseIndicator | None:
        """Get a specific indicator by name"""
        return self.indicators.get(name)

    def list_indicators(self) -> list[str]:
        """List all indicator names"""
        return list(self.indicators.keys())
