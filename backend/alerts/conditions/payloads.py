"""
Payload builders for alert condition evaluators.

These functions are called from the alert engine to construct the ``value``
dict that an evaluator receives. Each builder pulls recent bar data and
projects it into the shape its corresponding ``_eval_*`` function expects.
"""

from .helpers import (
    _compute_trend_score_from_bars,
    _get_recent_bars,
)


def build_trend_payload(symbol: str, timeframe: str = "1d", lookback: int = 14) -> dict:
    """Build the value dict for trend conditions from recent bars."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 2)
    if len(bars) < lookback + 1:
        return {
            "current": 0.0,
            "previous": 0.0,
            "current_direction": "neutral",
            "previous_direction": "neutral",
        }
    current, previous, curr_dir, prev_dir = _compute_trend_score_from_bars(bars, lookback)
    return {
        "current": current,
        "previous": previous,
        "current_direction": curr_dir,
        "previous_direction": prev_dir,
    }


def build_alignment_payload(symbol: str) -> dict:
    """Build the value dict for timeframe alignment/conflict conditions.

    Checks 1m, 5m, 15m, 30m, 1h, 1d, 1wk for trend agreement.
    """
    timeframes = ["1m", "5m", "15m", "30m", "1h", "1d", "1wk"]
    directions = []
    for tf in timeframes:
        bars = _get_recent_bars(symbol, tf, 15)
        if len(bars) < 5:
            directions.append("unknown")
            continue
        _, _, curr_dir, _ = _compute_trend_score_from_bars(bars)
        directions.append(curr_dir)
    return {"directions": directions, "symbol": symbol}


def build_volume_payload(symbol: str, timeframe: str = "1d", lookback: int = 20) -> dict:
    """Build the value dict for volume_expansion."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if not bars:
        return {"current_volume": 0, "avg_volume": 0.0, "symbol": symbol}
    current_volume = float(bars[-1].volume or 0)
    past_bars = bars[:-1]
    if not past_bars:
        return {"current_volume": current_volume, "avg_volume": 0.0, "symbol": symbol}
    avg_volume = sum(float(b.volume or 0) for b in past_bars) / len(past_bars)
    return {
        "current_volume": current_volume,
        "avg_volume": avg_volume,
        "symbol": symbol,
    }


def build_breakout_payload(symbol: str, timeframe: str = "1d", lookback: int = 20) -> dict:
    """Build the value dict for breakout."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if not bars:
        return {"current_price": 0.0, "highest_high": 0.0, "symbol": symbol}
    current_price = float(bars[-1].close)
    past_bars = bars[:-1]
    if not past_bars:
        return {"current_price": current_price, "highest_high": current_price, "symbol": symbol}
    highest = max(float(b.high) for b in past_bars)
    return {"current_price": current_price, "highest_high": highest, "symbol": symbol}


def build_breakdown_payload(symbol: str, timeframe: str = "1d", lookback: int = 20) -> dict:
    """Build the value dict for breakdown."""
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if not bars:
        return {"current_price": 0.0, "lowest_low": 0.0, "symbol": symbol}
    current_price = float(bars[-1].close)
    past_bars = bars[:-1]
    if not past_bars:
        return {"current_price": current_price, "lowest_low": current_price, "symbol": symbol}
    lowest = min(float(b.low) for b in past_bars)
    return {"current_price": current_price, "lowest_low": lowest, "symbol": symbol}


def build_divergence_payload(symbol: str, timeframe: str = "1d", lookback: int = 14) -> dict:
    """Build the value dict for divergence.

    Computes a simple RSI-like momentum from bars and compares with price change.
    """
    bars = _get_recent_bars(symbol, timeframe, lookback + 1)
    if len(bars) < lookback + 1:
        return {"price_change_pct": 0.0, "rsi_like": 50.0}
    closes = [float(b.close) for b in bars]
    # Price change: compare first to last bar in the window
    price_change_pct = (closes[-1] - closes[0]) / (closes[0] + 1e-10) * 100.0
    # RSI-like: compute gain/loss average ratio
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    avg_gain = sum(gains) / lookback if gains else 0
    avg_loss = sum(losses) / lookback if losses else 1e-10
    rs = avg_gain / avg_loss
    rsi_like = 100.0 - (100.0 / (1.0 + rs))
    return {"price_change_pct": price_change_pct, "rsi_like": rsi_like}


def build_regime_change_payload(symbol: str = "^MKT") -> dict:
    """Build the value dict for market_regime_change from MarketContextEngine history.

    Reuses the process-wide MarketContextEngine singleton which already maintains
    a history of MarketContextSignal objects. Returns current and previous regime
    values; if the engine hasn't computed two signals yet, returns unknown for
    both so the evaluator rejects it.
    """
    try:
        from backend.regime.market_context_engine import market_context_engine
    except Exception:
        return {"current_regime": "unknown", "previous_regime": "unknown", "symbol": symbol}
    history = market_context_engine.get_history(limit=2)
    if len(history) < 2:
        return {"current_regime": "unknown", "previous_regime": "unknown", "symbol": symbol}
    return {
        "current_regime": history[-1].regime,
        "previous_regime": history[-2].regime,
        "symbol": symbol,
    }
