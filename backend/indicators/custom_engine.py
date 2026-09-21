"""
CustomIndicatorEngine — compute custom indicators against live or historical bars.

Thin wrapper around the existing IndicatorEngine that:
- Maps our formula_type names to engine kind names
- Fetches bars from the database
- Returns a list of {timestamp, value} points
"""

import logging

from backend.database import SessionLocal
from backend.models.market_data_sql import BarModel

logger = logging.getLogger(__name__)


# Map our formula_type string to the engine's kind name. We accept a few
# aliases so the UI can be more flexible.
_FORMULA_ALIASES = {
    "sma": "sma",
    "ema": "ema",
    "rsi": "rsi",
    "macd": "macd",
    "bollinger": "bollinger_bands",
    "bollinger_bands": "bollinger_bands",
    "atr": "atr",
    "adx": "adx",
    "obv": "obv",
    "roc": "roc",
    "supertrend": "supertrend",
    "volume_sma": "volume_sma",
    "relative_volume": "relative_volume",
}


class CustomIndicatorEngine:
    """Compute custom indicator values for a symbol+timeframe."""

    def compute(
        self,
        formula_type: str,
        parameters: dict,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> list[dict]:
        """Return a list of {timestamp, value} points for the given indicator.

        Newest point is last. Returns an empty list if the symbol has no bars
        or the formula_type is not recognised.
        """
        bars = self._fetch_bars(symbol, timeframe, limit)
        if not bars:
            return []

        data = self._bars_to_data(bars)
        values = self._calculate(formula_type, parameters, data)
        if not values:
            return []
        timestamps = [b.timestamp for b in bars[-len(values) :]]
        return [
            {"timestamp": ts.isoformat() if hasattr(ts, "isoformat") else ts, "value": v}
            for ts, v in zip(timestamps, values, strict=False)
            if v is not None
        ]

    # ── internals ────────────────────────────────────────────────────────

    def _fetch_bars(self, symbol: str, timeframe: str, limit: int) -> list[BarModel]:
        db = SessionLocal()
        try:
            return (
                db.query(BarModel)
                .filter(
                    BarModel.symbol == symbol.upper(),
                    BarModel.timeframe == timeframe,
                )
                .order_by(BarModel.timestamp.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()

    @staticmethod
    def _bars_to_data(bars: list[BarModel]) -> list[dict]:
        # Feed oldest → newest to the indicator.
        reversed_bars = list(reversed(bars))
        return [
            {
                "timestamp": b.timestamp,
                "open": float(b.open or 0),
                "high": float(b.high or 0),
                "low": float(b.low or 0),
                "close": float(b.close or 0),
                "volume": int(b.volume or 0),
            }
            for b in reversed_bars
        ]

    def _map_to_timestamps(self, bars: list[BarModel], values: list) -> list[dict]:
        # Use attribute access on BarModel rows.
        if not values:
            return []
        timestamps = [b.timestamp for b in bars[-len(values) :]]
        return [
            {"timestamp": ts.isoformat() if hasattr(ts, "isoformat") else ts, "value": v}
            for ts, v in zip(timestamps, values, strict=False)
            if v is not None
        ]

    @staticmethod
    def _calculate(
        formula_type: str,
        parameters: dict,
        data: list[dict],
    ) -> list:
        """Dispatch to the right indicator class and return raw values."""
        # Lazy import the engine to avoid circular imports.
        from backend.indicators.base_indicator import IndicatorEngine

        kind = _FORMULA_ALIASES.get(formula_type.lower())
        if kind is None:
            logger.warning(f"Unknown formula_type: {formula_type}")
            return []

        try:
            indicator = IndicatorEngine.create_indicator(kind, parameters)
            return indicator.calculate(data)
        except Exception as e:
            logger.warning(f"Indicator calculation failed for {formula_type}: {e}")
            return []
