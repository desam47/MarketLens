# PHASE 5 — TECHNICAL INDICATOR ENGINE

Implement a modular indicator framework.

Indicators:

TREND:

- EMA

- SMA

- SuperTrend

MOMENTUM:

- RSI

- MACD

- ROC

TREND STRENGTH:

- ADX

- ATR

VOLUME:

- Volume SMA

- Relative Volume

- OBV

VOLATILITY:

- Bollinger Bands

- Bollinger Band Width

- ATR

PRICE STRUCTURE:

- swing highs

- swing lows

Every indicator must be:

- independently testable

- configurable

- timeframe-aware

- deterministic

Create configuration such as:

indicators:

  ema:

    periods: [9,20,50,200]

  rsi:

    period: 14

  macd:

    fast: 12

    slow: 26

    signal: 9

  adx:

    period: 14

  atr:

    period: 14

  supertrend:

    atr_period: 10

    multiplier: 3

Do not put indicator logic into TrendEngine.

Create an IndicatorEngine that consumes normalized OHLCV data.

Optimize calculations so indicators are incrementally updateable later.

Write extensive unit tests using known historical examples.

Do not implement AI.

Do not implement scanner.

Then STOP.

