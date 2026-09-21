# MarketLens Version 4 Improvement Roadmap

This roadmap combines the real-time, microstructure, market intelligence, portfolio, replay, and usability improvements planned for MarketLens. Features are ordered by dependency and implementation priority.

## Phase 1 — Shared real-time data foundation

### 1. Shared live microstructure cache

- One Webull MQTT stream for all subscribed symbols
- Shared BBO and Time & Sales data across every page
- No per-component polling
- Cached latest snapshots with timestamps
- Duplicate-tick protection
- Local retention limits

### 2. True real-time quote WebSocket

- Stream live price, bid, ask, sizes, and volume to the Symbol Page, Dashboard, Watchlists, Scanner, and Alerts
- Show `LIVE`, `STALE`, or `REST FALLBACK`

### 3. Real-time chart updates

- Update the active candle from incoming ticks
- Build 1-minute bars locally
- Reduce dependence on REST backfills
- Preserve REST history as a fallback

### 4. Reliability and rate-limit controls

- Shared subscriptions
- Reconnect backoff
- Stream health monitoring
- Subscription deduplication
- Cached snapshots
- Local storage limits
- Graceful fallback when Webull disconnects

## Phase 2 — Microstructure features

### 5. Symbol-page BBO panel

- Bid
- Ask
- Midpoint
- Spread
- Bid/ask sizes
- Quote age
- Provider status
- Webull connection state

### 6. Better Time & Sales analytics

- Buy/sell volume
- Large prints
- Trade velocity
- Uptick/downtick ratio
- Tape-pressure trend
- Recent trade imbalance

### 7. Microstructure Scanner filters

- Tight spread
- Spread widening
- Positive/negative tape pressure
- Large-print activity
- Trade-rate spikes
- Bid/ask imbalance
- Live volume acceleration

### 8. Microstructure alerts

- Spread widening
- Bid/ask imbalance changes
- Large prints
- Tape-pressure reversals
- Trade-rate spikes
- Stream disconnects

### 9. Tick-level replay

- Store ticks locally
- Replay Time & Sales and BBO changes
- Replay without repeatedly calling Webull
- Compare tick behavior with technical signals

## Phase 3 — Intelligence and scanning

### 10. Better live market scanner

- Breakouts
- Volume spikes
- VWAP/EMA alignment
- Relative strength
- Volatility contraction and expansion
- Multi-timeframe confirmation
- Microstructure confirmation

### 11. Signal Explanation Center

- Explain which indicators triggered
- Show signal age
- Show data freshness
- Show agreeing timeframes
- Compare with the previous signal
- Display historical performance of similar signals
- Show whether BBO and tape confirm or contradict the signal

### 12. Expanded alerts

- Live price and volume
- News arrival
- Earnings events
- Insider sentiment changes
- Options volume/open-interest changes
- Scanner results
- Regime changes
- Multi-timeframe alignment
- Microstructure events

## Phase 4 — Symbol research

### 13. Catalyst timeline

- News
- Earnings
- Insider activity
- Analyst recommendations
- Fundamentals
- Options activity
- Price and volume reaction
- Technical signals

### 14. Options Snapshot improvements

- Expiration selector
- Seven ITM and seven OTM strikes
- ITM/OTM visual distinction
- Implied volatility
- Expected move
- Volume
- Open interest
- Put/call ratio
- Basic unusual-activity flags
- Delayed/estimated data labeling

### 15. Provider and data-quality transparency

- Actual provider used
- Provider per timeframe
- Quote timestamp
- Data age
- Delay status
- Entitlement status
- Fallback state
- Webull connection status
- Clear Yahoo options/fundamentals limitations

## Phase 5 — Portfolio and learning tools

### 16. Risk Dashboard

- Position size
- Stop-loss risk
- Portfolio concentration
- Sector exposure
- Correlation
- Volatility
- Maximum drawdown

### 17. Trade journal

- Thesis
- Entry and exit
- Stop and target
- Position size
- Screenshot
- Result
- Mistake/review notes
- Automatic signal and market-context attachment

### 18. Historical replay

- Candle-by-candle replay
- Tick-level replay
- Show when signals appeared
- Simulated entries and exits
- Stop and target tracking
- Replay performance summary

## Phase 6 — Personalization and usability

### 19. Saved dashboard layouts

- Day trading
- Swing trading
- Earnings research
- Options research
- Long-term investing

### 20. Operational reliability UI

- Stream reconnect status
- Provider health
- Last successful update per symbol
- Per-provider failover visibility
- Better stale-data warnings
- Clear API and entitlement error messages

## Recommended implementation order

Build the shared Webull microstructure cache and quote WebSocket first. Nearly every later feature—live charts, scanner filters, alerts, signal explanations, BBO, replay, and provider transparency—can reuse that data foundation.
