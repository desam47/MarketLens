"""
Average Directional Index (ADX) indicator
"""
from typing import Any

from .base_indicator import BaseIndicator


class ADXIndicator(BaseIndicator):
    """Average Directional Index indicator"""
    
    def __init__(self, period: int = 14):
        super().__init__("ADX", {"period": period})
        self.period = period
        self.dm_plus: list[float] = []
        self.dm_minus: list[float] = []
        self.tr: list[float] = []  # True Range
    
    def _calculate_dm_and_tr(self, data: list[dict[str, Any]]) -> tuple:
        """Calculate Directional Movement and True Range"""
        if len(data) < 2:
            return [], [], []
        
        dm_plus = []
        dm_minus = []
        tr = []
        
        for i in range(1, len(data)):
            high_curr = float(data[i]['high'])
            low_curr = float(data[i]['low'])
            high_prev = float(data[i-1]['high'])
            low_prev = float(data[i-1]['low'])
            close_prev = float(data[i-1]['close'])
            
            # Calculate Directional Movement
            up_move = high_curr - high_prev
            down_move = low_prev - low_curr
            
            if up_move > down_move and up_move > 0:
                dm_plus_val = up_move
            else:
                dm_plus_val = 0
                
            if down_move > up_move and down_move > 0:
                dm_minus_val = down_move
            else:
                dm_minus_val = 0
            
            dm_plus.append(dm_plus_val)
            dm_minus.append(dm_minus_val)
            
            # Calculate True Range
            tr1 = high_curr - low_curr
            tr2 = abs(high_curr - close_prev)
            tr3 = abs(low_curr - close_prev)
            tr_val = max(tr1, tr2, tr3)
            tr.append(tr_val)
        
        return dm_plus, dm_minus, tr
    
    def _wilder_smoothing(self, values: list[float], period: int) -> list[float]:
        """Apply Wilder's smoothing (similar to EMA but with 1/period)"""
        if len(values) < period:
            return [None] * len(values)
        
        smoothed = [None] * (period - 1)  # First 'period-1' values are undefined
        # First smoothed value is simple average
        first_avg = sum(values[:period]) / period
        smoothed.append(first_avg)
        
        # Subsequent values using Wilder's smoothing
        for i in range(period, len(values)):
            smoothed_val = (smoothed[i-1] * (period - 1) + values[i]) / period
            smoothed.append(smoothed_val)
        
        return smoothed
    
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate ADX for the given data"""
        if len(data) < self.period + 1:
            return []
        
        # Calculate DM and TR
        dm_plus, dm_minus, tr = self._calculate_dm_and_tr(data)
        
        if len(tr) < self.period:
            return []
        
        # Smooth DM and TR using Wilder's smoothing
        smoothed_dm_plus = self._wilder_smoothing(dm_plus, self.period)
        smoothed_dm_minus = self._wilder_smoothing(dm_minus, self.period)
        smoothed_tr = self._wilder_smoothing(tr, self.period)
        
        # Calculate DI+ and DI-
        di_plus = []
        di_minus = []
        dx = []
        
        for i in range(len(smoothed_tr)):
            # The first (period - 1) entries of Wilder-smoothed arrays
            # are None by construction; skip them to avoid division by None.
            if (
                smoothed_tr[i] is None
                or smoothed_dm_plus[i] is None
                or smoothed_dm_minus[i] is None
            ):
                di_plus_val = None
                di_minus_val = None
            elif smoothed_tr[i] == 0:
                di_plus_val = 0
                di_minus_val = 0
            else:
                di_plus_val = (smoothed_dm_plus[i] / smoothed_tr[i]) * 100
                di_minus_val = (smoothed_dm_minus[i] / smoothed_tr[i]) * 100
            
            di_plus.append(di_plus_val)
            di_minus.append(di_minus_val)

            # Calculate DX
            if di_plus_val is None or di_minus_val is None:
                dx_val = None
            elif di_plus_val + di_minus_val == 0:
                dx_val = 0
            else:
                dx_val = (
                    abs(di_plus_val - di_minus_val)
                    / (di_plus_val + di_minus_val)
                    * 100
                )
            dx.append(dx_val)

        # The first (period - 1) entries of dx are None by construction
        # (the underlying DI series hasn't been computed yet). Filter
        # them out before smoothing so _wilder_smoothing can operate on
        # numeric values only.
        dx_numeric = [v for v in dx if v is not None]

        # Smooth DX to get ADX
        adx = self._wilder_smoothing(dx_numeric, self.period)
        
        # Filter out None values and store
        self.values = [v for v in adx if v is not None]
        return self.values.copy()
    
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update ADX with new data point"""
        # For simplicity in streaming update, we'll recalculate with recent data
        # In a production system, you'd want to optimize this with proper state maintenance
        if not hasattr(self, '_price_history'):
            self._price_history = []

        # Add new data to history
        self._price_history.append({
            'high': float(new_data['high']),
            'low': float(new_data['low']),
            'close': float(new_data['close'])
        })

        # Keep only recent data needed for calculation
        # We need at least period * 2 + 1 data points for ADX calculation
        min_required = self.period * 2 + 1
        if len(self._price_history) < min_required:
            return None

        # Keep only the last 3 * period points for efficiency
        if len(self._price_history) > 3 * self.period:
            self._price_history = self._price_history[-3 * self.period:]

        # Recalculate ADX with recent data
        try:
            result = self.calculate(self._price_history)
            if result:
                latest_value = result[-1]
                self.values.append(latest_value)
                return latest_value
        except Exception:
            pass

        return None
