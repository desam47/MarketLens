"""
Rate of Change (ROC) indicator
"""

import logging
from typing import Any, cast

from .base_indicator import BaseIndicator

logger = logging.getLogger(__name__)


class ROCIndicator(BaseIndicator):
    """Rate of Change indicator"""

    def __init__(self, period: int = 10):
        super().__init__("ROC", {"period": period})
        self.period = period

    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate ROC for the given data"""
        if not data or len(data) < self.period + 1:
            return []

        # Extract close prices
        closes = [float(d["close"]) for d in data]

        # Calculate ROC: ((Current Close - Close n periods ago) / Close n periods ago) * 100
        roc_values: list[float | None] = [None] * self.period  # First 'period' values are undefined

        for i in range(self.period, len(closes)):
            if closes[i - self.period] != 0:  # Avoid division by zero
                roc = ((closes[i] - closes[i - self.period]) / closes[i - self.period]) * 100
                roc_values.append(roc)
            else:
                roc_values.append(0.0)  # Or handle as appropriate

        # Filter out None values for clean return
        self.values = cast(list[float], [v for v in roc_values if v is not None])
        return self.values.copy()

    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update ROC with new data point"""
        close_price = float(new_data["close"])

        # Initialize price history if needed
        if not hasattr(self, "_price_history"):
            self._price_history = []

        self._price_history.append(close_price)

        # Keep only what we need for calculation
        if len(self._price_history) < self.period + 1:
            return None

        # Keep recent history for efficiency
        if len(self._price_history) > self.period + 10:
            self._price_history = self._price_history[-(self.period + 10) :]

        # Calculate ROC with current history
        try:
            result = self.calculate([{"close": price} for price in self._price_history])
            if result:
                latest_value = result[-1]
                self.values.append(latest_value)
                return latest_value
        except Exception:
            logger.debug("ROC update failed; value skipped", exc_info=True)

        return None
