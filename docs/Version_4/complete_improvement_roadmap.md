# MarketLens Version 4 Improvement Roadmap

This roadmap combines the real-time, microstructure, market intelligence, portfolio, replay, and usability improvements planned for MarketLens. Features are ordered by dependency and implementation priority.

Status legend: `[x]` complete, `[~]` partially complete, `[ ]` remaining.

## Phase 1 — Shared real-time data foundation

### 1. Shared live microstructure cache — [x] Done

- One Webull MQTT stream for all subscribed symbols
- Shared BBO and Time & Sales data across every page
- No per-component polling
- Cached latest snapshots with timestamps
- Duplicate-tick protection
- Local retention limits

### 2. True real-time quote WebSocket — [x] Done

- Stream live price, bid, ask, sizes, and volume to the Symbol Page, Dashboard, Watchlists, Scanner, and Alerts
- Show `LIVE`, `STALE`, or `REST FALLBACK`

### 3. Real-time chart updates — [~] Partial

The backend now aggregates Webull trades into a shared live 1-minute candle and pushes it to chart subscribers; the Symbol Page also keeps a client-side fallback. Persisting completed stream bars and eliminating all REST backfill dependence remain.

- Update the active candle from incoming ticks
- Build 1-minute bars locally
- Push the forming candle to every chart subscriber
- Reduce dependence on REST backfills
- Preserve REST history as a fallback

### 4. Reliability and rate-limit controls — [~] Partial

Shared subscriptions, reconnect backoff, deduplication, cache limits, stream-status alerts, and per-symbol stale-data status alerts are implemented. Complete fallback observability remains.

- Shared subscriptions
- Reconnect backoff
- Stream health monitoring
- Subscription deduplication
- Cached snapshots
- Local storage limits
- Graceful fallback when Webull disconnects

## Phase 2 — Microstructure features

### 5. Symbol-page BBO panel — [x] Done

- Bid
- Ask
- Midpoint
- Spread
- Bid/ask sizes
- Quote age
- Provider status
- Webull connection state

### 6. Better Time & Sales analytics — [x] Done

- Buy/sell volume
- Large prints
- Trade velocity
- Uptick/downtick ratio
- Tape-pressure trend
- Recent trade imbalance

### 7. Microstructure Scanner filters — [x] Done

- Tight spread
- Spread widening
- Positive/negative tape pressure
- Large-print activity
- Trade-rate spikes
- Bid/ask imbalance
- Live volume acceleration

### 8. Microstructure alerts — [x] Done

- Spread widening
- Bid/ask imbalance changes
- Large prints
- Tape-pressure reversals
- Trade-rate spikes
- Stream disconnects

### 9. Tick-level replay — [~] Partial

Local bounded retention and BBO/tape playback are implemented. Replaying derived signals and simulated trade outcomes directly from ticks remains.

- Store ticks locally
- Replay Time & Sales and BBO changes
- Replay without repeatedly calling Webull
- Compare tick behavior with technical signals

## Phase 3 — Intelligence and scanning

### 10. Better live market scanner — [~] Partial

Breakouts, volume, VWAP, EMA alignment/crossovers, relative strength, volatility, multi-timeframe, and microstructure filters are implemented. Event-driven scan refreshes remain.

- Breakouts
- Volume spikes
- VWAP/EMA alignment
- Relative strength
- Volatility contraction and expansion
- Multi-timeframe confirmation
- Microstructure confirmation

### 11. Signal Explanation Center — [x] Done

- Explain which indicators triggered
- Show signal age
- Show data freshness
- Show agreeing timeframes
- Compare with the previous signal
- Display historical performance of similar signals
- Show whether BBO and tape confirm or contradict the signal

### 12. Expanded alerts — [x] Done

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

### 13. Catalyst timeline — [x] Done

- News
- Earnings
- Insider activity
- Analyst recommendations
- Fundamentals
- Options activity
- Price and volume reaction
- Technical signals

### 14. Options Snapshot improvements — [x] Done

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

### 15. Provider and data-quality transparency — [~] Partial

Provider labels, freshness, live/stale state, fallback state, and auxiliary-data caveats are visible. Entitlement status and complete provider attribution for every timeframe still need work.

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

### 16. Risk Dashboard — [x] Done

- Position size
- Stop-loss risk
- Portfolio concentration
- Sector exposure
- Correlation
- Volatility
- Maximum drawdown

### 17. Trade journal — [x] Done

Manual journal entries, screenshots, P&L, review notes, local persistence, signal attachment, and automatic live market-context attachment are implemented.

- Thesis
- Entry and exit
- Stop and target
- Position size
- Screenshot
- Result
- Mistake/review notes
- Automatic signal and market-context attachment

### 18. Historical replay — [~] Partial

Candle replay, signal timing, performance summaries, tick replay, simulated entries/exits, stop/target tracking, and simulated outcomes are implemented. Exact tick-level signal reconstruction remains.

- Candle-by-candle replay
- Tick-level replay
- Show when signals appeared
- Simulated entries and exits
- Stop and target tracking
- Replay performance summary

## Phase 6 — Personalization and usability

### 19. Saved dashboard layouts — [x] Done

- Day trading
- Swing trading
- Earnings research
- Options research
- Long-term investing

### 20. Operational reliability UI — [~] Partial

Reconnect state, provider health, freshness badges, failover labels, and per-symbol stale-data alerts are implemented. A unified per-symbol last-successful-update view and complete per-provider failover history remain.

- Stream reconnect status
- Provider health
- Last successful update per symbol
- Per-provider failover visibility
- Better stale-data warnings
- Clear API and entitlement error messages

## Recommended implementation order

The shared Webull microstructure cache, quote WebSocket, and live 1-minute candle path are now in place. Next prioritize completed-bar persistence, event-driven scanner refreshes, unified per-symbol freshness visibility, and exact tick-level signal reconstruction. These close the remaining gaps in chart durability, scanner timeliness, operational transparency, and replay fidelity.
