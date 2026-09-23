# Version 5 — Intelligent AI Hub Chat

**Date:** 2026-09-22
**Last updated:** 2026-09-22
**Status:** Active. Phase 5.1 and Phase 5.2 complete; Phase 5.3 bounded orchestration is in progress.
**Scope:** Turn AI Hub Chat into a grounded, calculation-capable MarketLens copilot that can answer broad market, symbol, watchlist, portfolio, risk, options, journal, and application-workflow questions through bounded backend tools and typed responses.
**Repository workflow:** Build and commit Version 5 changes on `development`; merge reviewed work into protected `main` for stable releases.

---

## Goals

1. Make calculations deterministic and backend-verified instead of trusting model arithmetic.
2. Let Chat retrieve the exact MarketLens data required for each question through explicit read-only tools.
3. Support bounded multi-step questions, comparisons, scenarios, and follow-ups without creating an unrestricted autonomous agent.
4. Show provider, timestamp, timeframe, market session, freshness, assumptions, and formulas with every data-backed answer.
5. Turn Chat into a practical workspace for screening, planning, risk review, alerts, watchlists, options research, and journal review.
6. Preserve MarketLens safety rules: no invented data, no order execution, explicit confirmation for destructive actions, and graceful uncertainty when evidence is missing.

**Non-goals for this version:**
- Broker order execution, account linking, or automatic trade placement.
- Unbounded autonomous tool loops or background actions initiated without user intent.
- Treating model-generated arithmetic, ticker identity, prices, statistics, or action IDs as authoritative.
- Replacing existing Scanner, Symbol, Alerts, Risk Dashboard, Journal, Replay, or Options pages; Chat orchestrates and explains those capabilities.
- Real-time data beyond the user's provider entitlements.

---

## Background — current Chat and why this phase exists

AI Hub Chat already has a useful grounded base. Each turn resolves tickers,
builds symbol and market context, includes a clipped transcript, streams a
structured reply, and can execute a closed set of actions: fresh analysis,
alert CRUD, watchlist CRUD, entity-type correction, a fixed backtest, and
natural-language screening. Destructive actions have a backend confirmation
gate, and several watchlist questions are answered deterministically from the
database.

The limiting architecture is that most questions still become one large prompt
followed by one model-generated JSON object. Context inclusion is selected by
regular-expression intent gates, calculation is not exposed as a first-class
verified tool, and the frontend primarily renders prose. This works well for
known symbol questions but does not scale cleanly to arbitrary comparisons,
portfolio scenarios, exact calculations, historical lookups, or questions that
need several dependent data reads.

Version 5 keeps the current safety model and evolves it into a bounded tool
orchestrator. The backend owns data access, arithmetic, validation, permissions,
and side effects. The language model selects tools and explains verified
results; it never becomes the source of market facts or numerical truth.

---

## Phase 5.1 — Tool foundation and safe calculator

**Why first:** Every later capability depends on a stable tool contract and reliable arithmetic.

### Items

#### 5.1.1 Typed Chat tool protocol
- Define a common request/result envelope with `tool_name`, validated arguments, result data, errors, duration, provider, timestamps, freshness, and warnings.
- Separate read-only, calculation, and mutating tools.
- Give every tool a stable schema that can be tested independently of the model.
- Keep the existing flat `ChatReplyResponse` action path operational during migration.

#### 5.1.2 Safe calculation engine
- Add backend-owned calculations for percentage and dollar change, simple and annualized return, CAGR, weighted average, position value, position size, stop risk, reward/risk, allocation, correlation, volatility, and maximum drawdown.
- Return per-share risk, total dollar risk, portfolio-risk percentage, and reward/risk as distinct outputs whenever the required entry, stop, target, quantity, and portfolio value are available.
- Add options calculations for breakeven, intrinsic/extrinsic value, expected move, maximum gain/loss where defined, and assignment/expiration exposure.
- Accept only named operations or a restricted expression grammar; never use `eval` and never execute model-generated code.
- Allow restricted custom formulas to reference verified values from prior tool results by stable result/field identifiers; never substitute model-invented numbers into a formula.
- Return inputs, units, formula, raw value, formatted value, assumptions, and validation errors.

#### 5.1.3 Time, session, and unit normalization
- Resolve relative dates such as “today”, “yesterday”, and “last Friday” in America/New_York.
- Represent premarket, regular, after-hours, and all-session scope explicitly.
- Normalize percentages, currency, shares, timestamps, timeframes, and annualization conventions before calculation.

#### 5.1.4 Tool registry and permissions
- Register tools centrally with input schemas, output schemas, read/write classification, timeout, and rate-limit policy.
- Require backend confirmation for destructive tools regardless of model output.
- Keep broker/order tools absent from the registry.

#### 5.1.5 Canonical metric and terminology catalog
- Define the authoritative meaning, formula, units, valid range, required inputs, timeframe/session behavior, and owning service for every Chat-visible MarketLens metric.
- Include price/change percentage, relative volume, tape pressure, imbalance, confidence, trend strength, confluence, regime, expected move, risk, and performance statistics.
- Make tools and explanations reference catalog identifiers so the same metric cannot be described or calculated differently across Chat and application pages.

### Verification
- Unit tests cover formulas, zero/negative inputs, missing data, timezone boundaries, session selection, malformed expressions, and rounding.
- Tool schemas reject unknown fields and invalid symbols/timeframes.
- No calculator path can import modules, access files, make network calls, or execute arbitrary code.

---

## Phase 5.2 — Grounded market-data tools and provenance

**Why now:** Chat must fetch the exact evidence needed for a question instead of relying on a large preassembled prompt.

### Items

#### 5.2.1 Quote and bar tools
- `get_quote(symbol, session)` with price, change, change percentage, bid/ask, quote age, provider, and fallback state.
- `get_bars(symbol, timeframe, range, session)` with bounded row limits and data-availability metadata.
- Use existing shared caches and subscriptions; Chat must not create per-turn provider polling.

#### 5.2.2 Technical and market-structure tools
- Expose explicit tools including `get_indicator(symbol, indicator, timeframe)`, `get_support_resistance(symbol)`, `get_market_regime()`, `get_market_context()`, and `get_sector_data(symbol)`.
- Retrieve trend, multi-timeframe confluence, relative strength, BBO, tape pressure, large prints, and session statistics through the same typed tool contract.
- Preserve each engine's real timeframe and last successful update.

#### 5.2.3 Research tools
- Expose explicit tools including `get_news(symbol, range)`, `get_fundamentals(symbol)`, and `get_options_snapshot(symbol, expiration)`.
- Retrieve catalysts, earnings, insider activity, analyst recommendations, and sector context through the same typed tool contract.
- Label delayed, approximate, estimated, unavailable, and fallback data clearly.

#### 5.2.4 User-data tools
- Expose explicit tools including `get_watchlist(name)`, `get_alerts()`, `get_trade_journal()`, and `get_risk_dashboard()`.
- `get_watchlist` reads the application database without mutating it. Risk Dashboard and Trade Journal are currently browser-local, so their tools accept explicit bounded snapshots and report unavailable when the server cannot access localStorage.
- Read manually tracked positions, saved scans, and recent signal history through the same typed tool contract when a server-backed or explicitly supplied snapshot exists.
- Apply bounded result limits and return summaries plus IDs for follow-up retrieval.

#### 5.2.5 Evidence bundle
- Every tool result includes actual provider, source timestamp, retrieval timestamp, data age, timeframe, session, entitlement/fallback status, and warnings.
- The final response exposes the evidence used rather than merely claiming it is grounded.

#### 5.2.6 Data-conflict detection and reconciliation
- Detect disagreements between providers, live and cached observations, sessions, timeframes, and page/tool results.
- Never silently average incompatible values; show the conflict, select a source through deterministic precedence rules, and explain that selection.

#### 5.2.7 Application-help and navigation metadata
- Add a verified application-help tool backed by current routes, feature metadata, configuration descriptions, and page capabilities—not free-form model memory. The initial tool now returns current page titles, feature topics, and hash routes from the navigation catalog.
- Return deep-link targets and required navigation state for the relevant MarketLens page or setting.

#### 5.2.8 Safe user-provided data tools
- Import local CSV trade history, positions, and watchlists through explicit schemas and bounded file/row limits.
- Validate types and columns, keep imported data local, and treat spreadsheet formulas/macros as inert text rather than executable content.

### Verification
- Contract tests compare tool output with the existing page/API output for the same symbol and scope.
- Cache tests confirm one Chat question does not multiply provider calls.
- Stale, missing, delayed, and fallback cases are visible and never silently presented as live.

---

## Phase 5.3 — Bounded orchestration, intent, and memory

The first slice is in progress: Chat supports bounded compound-action chaining
with configurable planning budgets (default three steps), duplicate-action
suppression, destructive-action confirmation, per-turn planner state, and safe
partial-failure handling. The remaining items below
extend this foundation into a general planner and structured conversational
state. Session-backed memory now preserves bounded symbols, watchlist,
timeframe, session, and last-question fields, and ambiguous multi-symbol
follow-ups ask for clarification before tool execution. Compound replies label
completed subtasks and explicitly report a safely stopped continuation.
Current assistant responses expose a transient ordered tool trace with provider,
freshness, fallback, and failure metadata; historical prose remains unchanged.

**Why now:** Broad questions often need several dependent operations, but the loop must stay predictable and safe.

### Items

#### 5.3.1 Planner/executor loop
- Add a bounded cycle: understand → select tool → validate → execute → observe → continue or answer.
- Default maximum: 5 tool calls and 2 model planning calls per turn, configurable through environment settings.
- Enforce a configurable wall-clock budget for continuation planning (default 30 seconds); never start another continuation after it expires.
- Stop on repeated calls, repeated errors, exhausted budget, timeout, or sufficient evidence.

#### 5.3.2 Clarification and ambiguity handling
- Ask for missing ticker, watchlist, timeframe, session, date range, portfolio scope, stop, target, or risk budget when those materially change the answer.
- Never silently choose among multiple watchlists, alerts, positions, or expirations.

#### 5.3.3 Structured conversation state
- Store active symbols, previous ticker, current watchlist, timeframe, session, date range, last calculation inputs, last tool result IDs, pending confirmation, and user preferences separately from prose history.
- Resolve “it”, “that stock”, “the previous ticker”, “same timeframe”, and “use the previous stop” from structured state.
- Keep memory local to the user/session and provide a clear reset path.
- Current state now persists `previous_ticker`, `last_calculation_inputs`, and a bounded `last_tool_result`; exact calculations and missing calculation inputs are handled deterministically before an AI call.

#### 5.3.4 Intent coverage
- Support symbol, market, comparison, scanner, watchlist, portfolio, risk, options, historical, journal, calculation, and app-action intents.
- Replace fragile regex-only routing gradually; deterministic routes remain for exact DB questions and confirmation handling.

#### 5.3.5 Cost, latency, and rate-limit budgets
- Prefer local database/cache tools before provider calls.
- Run independent reads concurrently, deduplicate identical calls, and reuse results within a turn.
- Expose a concise failure when a budget is exhausted rather than producing an ungrounded fallback answer.

#### 5.3.6 Visible question decomposition
- Split complex requests into ordered, user-visible subtasks with dependencies and completion state.
- Continue with independent subtasks after a partial failure and state exactly which result could not be produced.

#### 5.3.7 Reusable workflows
- Save editable workflows such as Morning Review, Evaluate a Breakout, Options Setup Review, and End-of-Day Journal Review.
- Store tool sequence, user-visible parameters, confirmation requirements, and output layout—not hidden model prose.

#### 5.3.8 Model routing and deterministic fallback
- Use configurable model routes for tool selection, complex synthesis, and repair while preserving one evidence contract.
- Fall back to deterministic calculator/database answers when AI is unavailable and show which model, if any, generated the explanation.

### Verification
- Multi-step tests cover comparisons, follow-up pronouns, ambiguous requests, repeated-tool prevention, and partial failures.
- The orchestrator cannot exceed configured call, time, or token budgets.
- Destructive actions still require backend-enforced confirmation.

---

## Phase 5.4 — Analysis, comparisons, scenarios, and explanations

**Why now:** Once data and arithmetic are reliable, Chat can answer higher-value analytical questions.

### Items

#### 5.4.1 “Why did it move?”
- Combine price/session movement, relative volume, news, earnings, options activity, sector movement, market regime, and tape evidence.
- Separate confirmed catalysts, correlations, and unknown causes.

#### 5.4.2 “What changed?”
- Compare now with previous close, previous signal, yesterday, last visit, or a user-selected timestamp.
- Highlight signal transitions, regime changes, support/resistance breaks, freshness changes, catalysts, and alert events.

#### 5.4.3 Comparisons and rankings
- Compare symbols or watchlists on returns, trend, confluence, relative strength, volatility, volume, valuation, catalysts, options, and data quality.
- Backend performs sorting, normalization, and ranking; the model explains the result.

#### 5.4.4 Scenario analysis
- Answer position and portfolio “what if” questions: price shocks, stop changes, target changes, allocation changes, volatility changes, and broad-market selloffs.
- Display assumptions and avoid presenting scenario output as a forecast.

#### 5.4.5 Historical similarity
- Find comparable stored setups and report 1-, 5-, and 20-session outcomes, sample size, distribution, and confidence limits.
- Prevent look-ahead leakage and label small samples.

#### 5.4.6 Signal explanation
- Explain triggered indicators, agreeing timeframes, conflicting evidence, BBO/tape confirmation, signal age, previous state, and historical performance.

#### 5.4.7 Counterargument and invalidation review
- For bullish conclusions, surface material bearish evidence; for bearish conclusions, surface material bullish evidence.
- State the observations or thresholds that would invalidate the conclusion and avoid manufacturing a balanced argument when no credible counter-evidence exists.

#### 5.4.8 Sensitivity analysis
- Vary entry, stop, target, position size, allocation, volatility, and expected move across explicit user-selected or bounded scenarios.
- Identify the assumptions with the greatest effect and label sensitivity output as conditional analysis rather than a forecast.

#### 5.4.9 Unified market-event timeline
- Order prices, session transitions, signals, alerts, news, earnings, analyst changes, insider activity, and options events on one normalized New York timeline.
- Support “before”, “after”, and “between” questions without losing source timestamps or event provenance.

#### 5.4.10 Proactive anomaly explanations
- Detect unusual price, volume, spread, tape, options, correlation, and portfolio-risk changes relative to an explicit historical baseline.
- Explain the baseline, magnitude, sample window, and available corroborating evidence.

#### 5.4.11 Research assumption tracking
- Save user-approved assumptions such as expected growth, stop, catalyst date, volatility, and invalidation conditions with source and creation time.
- Mark assumptions stale or broken when verified evidence changes; never silently rewrite the original assumption.

### Verification
- Golden tests use fixed fixtures and independently calculated expected values.
- Answers distinguish causation, correlation, inference, and unavailable evidence.
- Rankings are stable and reproducible from returned tool data.

---

## Phase 5.5 — Scanner, watchlist, alerts, and briefings

**Why now:** These are high-frequency workflows where Chat can save navigation and repetitive setup.

### Items

#### 5.5.1 Natural-language scanner builder
- Convert requests into visible, editable Scanner filters.
- Preview parsed filters before execution when the request is ambiguous.
- Support technical, session, volume, relative-strength, catalyst, and microstructure criteria already available in MarketLens.

#### 5.5.2 Watchlist intelligence
- Daily briefing, best/worst movers, new breakouts, deteriorating setups, volume spikes, relative-strength changes, multi-timeframe alignment, earnings, catalysts, and sector rotation.
- Rank from backend data and preserve the selected session scope.

#### 5.5.3 Alert-to-conversation workflow
- Open Chat with the fired alert, triggering observation, current quote, chart state, signal explanation, catalyst context, and recent history already attached.
- Allow safe follow-up creation or modification of related alerts.

#### 5.5.4 Scheduled summaries
- Optional premarket plan, midday update, post-market recap, and weekly review.
- The weekly review includes performance, recurring mistakes, plan-versus-execution differences, and the user's strongest/weakest setups when sufficient Journal data exists.
- Deliver only through existing local MarketLens surfaces unless a future version explicitly adds external messaging.

#### 5.5.5 What-changed inbox
- Summarize changes since the user's last visit across watchlists, alerts, signals, catalysts, and provider health.
- Deduplicate repeated events and link each summary item to its source page.

### Verification
- Natural-language filters match Scanner's displayed and executed filters exactly.
- Briefings state their cutoff time and do not mix sessions silently.
- Alert conversations reproduce the actual triggering values and timestamps.

---

## Phase 5.6 — Trade planning, risk, options, and journal coaching

**Why now:** These workflows require the verified calculations and data tools delivered by earlier phases.

### Items

#### 5.6.1 Trade-plan builder
- Produce entry zone, stop, targets, position size, reward/risk, invalidation, catalysts, risks, timeframe, session, and assumptions.
- User reviews the plan before saving it or creating alerts.

#### 5.6.2 Portfolio and Risk Dashboard assistant
- Explain concentration, sector exposure, correlation, volatility, stop risk, drawdown, and scenario results.
- Recommend no trade size when required inputs or risk limits are missing.

#### 5.6.3 Options research assistant
- Explain and compare calls, puts, and defined-risk spreads across expirations and strikes; explain IV, IV percentile, expected move, volume, open interest, put/call ratio, unusual activity, breakeven, maximum gain/loss, assignment, and expiration risk.
- Use existing delayed/approximate labels and never imply executable prices.

#### 5.6.4 Trade Journal coach
- Attach signals, market conditions, calculations, and plans to journal entries.
- Compare planned versus actual execution and identify recurring mistakes, setup performance, personal win rate, and expectancy.
- Keep coaching evidence-based and distinguish observations from advice.

#### 5.6.5 Save/export workflows
- Save approved plans and reviews to the Journal.
- Export a response as a local report and provide deep links to Symbol, Scanner, Risk, Replay, Alerts, and Journal pages.

#### 5.6.6 Configurable decision checklist
- Support pre-plan checks such as trend alignment, catalyst review, defined stop, verified position size, earnings risk, options liquidity, and data freshness.
- Let the user configure required checks; clearly distinguish completed, failed, unavailable, and intentionally skipped items.

### Verification
- All monetary and percentage values trace to calculator results.
- Missing stop, target, capital, contract multiplier, or expiration produces clarification rather than invented defaults.
- Journal analytics can be reproduced from stored entries.

---

## Phase 5.7 — Structured Chat UI and personalization

**Why now:** Typed results should be interactive and readable, not flattened into model-generated prose.

### Items

#### 5.7.1 Typed response blocks
- Support prose, calculation cards, evidence lists, comparison tables, ranked results, warnings, suggested follow-ups, and action confirmations.
- Require every data-backed block to carry a confidence/data-quality state derived from evidence completeness and freshness, not model self-confidence.
- Persist typed blocks with the message so history renders consistently.

#### 5.7.2 Interactive visual components
- Mini price charts, indicator tables, options-chain cards, risk cards, scenario controls, session statistics, and historical-outcome distributions.
- Buttons for Add Alert, Add to Watchlist, Open Symbol, Open Scanner, Save to Journal, and Run Again.

#### 5.7.3 Personal preferences
- Remember the user's explicit operating mode—day trading, swing trading, options, or long-term investing—plus preferred timeframes, default session, risk-per-trade limit, primary watchlist, answer detail level, and preferred units.
- Tailor terminology, default comparisons, risk framing, and suggested follow-ups to the selected mode without changing underlying calculations or evidence.
- Make every preference visible, editable, and resettable; do not infer high-impact risk settings silently.

#### 5.7.4 Answer contract
- Visually separate facts, calculations, assumptions, model interpretation, and uncertainty.
- Show provider, source time, freshness, session, timeframe, fallback, entitlement, and confidence/data-quality status without cluttering the main answer.

#### 5.7.5 Accessibility and responsive behavior
- Keyboard navigation, readable tables, accessible status labels, mobile-safe cards, and non-color-only data-quality indicators.

#### 5.7.6 Chart-state awareness
- Pass the currently visible symbol, timeframe, session, zoom range, selected candle, active indicators, and drawings into Chat as typed state.
- Support “explain what I’m looking at” without making Chat infer chart state from prose or an unverified screenshot.

#### 5.7.7 App navigation actions
- Let Chat open Symbol, Scanner, Replay, Risk, Journal, Alerts, Options, or System Health with symbol, timeframe, session, filters, and selected records preserved.
- Navigation changes UI state only; it never implies a market or account action.

#### 5.7.8 Feedback and correction loop
- Add Correct, Incorrect, and Not Useful feedback with optional categories: wrong data, wrong calculation, misunderstood intent, stale data, poor explanation, or unsafe action.
- Turn approved failures into regression fixtures; do not perform uncontrolled online model retraining from user feedback.

#### 5.7.9 Response regeneration controls
- Regenerate as More Detail, Simpler, Bull Case, Bear Case, Calculations Only, Sources Only, or with a different timeframe/session.
- Reuse existing tool results when still valid and fetch new data only when the requested scope or freshness requires it.

#### 5.7.10 Saved research notebooks
- Group conversations, calculations, charts, journal entries, assumptions, and local reports by symbol or research idea.
- Preserve original evidence timestamps and show when notebook conclusions depend on stale inputs.

#### 5.7.11 Freshness-aware answer refresh
- Track which evidence records support each response and mark the response stale when those observations expire or materially change.
- Offer an explicit Refresh with Current Data action while preserving the original answer for comparison.

### Verification
- Component tests cover every block type, loading/error states, long values, missing data, and mobile widths.
- Historical messages render without rerunning tools.
- Screen-reader text conveys freshness and warning states.

---

## Phase 5.8 — Reliability, evaluation, and release hardening

**Why now:** “Answers almost everything” is only useful when correctness and failure behavior are measurable.

### Items

#### 5.8.1 Grounding and hallucination controls
- Reject unsupported claims, unknown tickers, unavailable data, and claims of live status without freshness evidence.
- Require references to tool-result IDs for every numerical market claim.

#### 5.8.2 Answer verification pass
- Before persistence or display, verify every ticker, number, unit, timeframe, session, formula result, and factual market claim against cited tool results.
- Detect contradictions between prose and evidence, independently recompute critical calculations, and replace an unverifiable answer with explicit uncertainty.

#### 5.8.3 Evaluation suite
- Build a versioned set of questions covering calculations, market data, sessions, comparisons, follow-ups, scanner filters, portfolio risk, options, actions, uncertainty, and adversarial prompts.
- Score factual correctness, calculation correctness, tool choice, provenance, clarification quality, latency, and action safety.

#### 5.8.4 Observability and audit trail
- Record tool name, sanitized arguments, duration, cache/provider usage, result status, and final evidence references.
- Never log secrets, full private journal text, or sensitive credentials.

#### 5.8.5 Failure and fallback behavior
- Tool timeout, provider outage, stale data, partial symbol failure, parse failure, and model outage all return useful partial answers or explicit uncertainty.
- Retrying must be bounded and must not multiply provider requests.

#### 5.8.6 Performance targets
- Define latency budgets for calculation-only, cached-data, database, and provider-backed turns.
- Track cache hit rate, tool-call count, model calls, prompt size, and provider request count per turn.

#### 5.8.7 Release gate
- No order execution path.
- All destructive actions confirmation-tested.
- Calculator golden tests pass.
- Full backend/frontend suites pass.
- Manual smoke test covers the major question categories and failure modes.

---

## Before starting — re-verify against current code

- `backend/ai/chat.py` remains the shared blocking/streaming Chat orchestration path.
- `backend/ai/prompt.py::ChatReplyResponse` remains the current flat action contract.
- `backend/ai/context.py`, `backend/ai/market_baseline.py`, and `backend/ai/chat_symbols.py` remain the current grounding inputs.
- Existing Chat actions still cover reanalysis, alerts, watchlists, entity type, fixed backtesting, and natural-language screening.
- Existing shared market-data caches and Webull subscriptions are reused; no Chat-specific provider polling is introduced.
- Risk Dashboard, Trade Journal, Scanner, Replay, Options Snapshot, Alerts, and System Health APIs are treated as sources of truth rather than reimplemented inside Chat.
- Configuration belongs in `.env` and `.env.example`; secrets remain only in `.env`.

---

## End-to-end verification matrix

1. **Calculation:** “Buy 200 AAPL at $220, stop $212” returns $1,600 risk with inputs and formula.
2. **Session:** A premarket-change question uses previous regular close and labels the premarket timestamp.
3. **Comparison:** AAPL versus MSFT is calculated from aligned timeframes and timestamps.
4. **Why move:** Answer distinguishes confirmed news from inferred sector/market effects.
5. **Scenario:** A 5% portfolio shock returns deterministic position and portfolio impacts.
6. **Scanner:** Natural language produces visible filters identical to executed filters.
7. **Follow-up:** “Use the same stop but 100 shares” reuses structured state correctly.
8. **Options:** Breakeven and expected move show delayed/approximate provenance.
9. **Alert:** A fired alert opens a conversation with exact trigger evidence.
10. **Journal:** A saved plan and later review preserve original assumptions and calculations.
11. **Failure:** Provider outage produces a partial/stale answer with an explicit warning, never fabricated live data.
12. **Safety:** Destructive actions require confirmation; order execution is impossible.

---

## Open questions

1. Which local/default model will be the minimum supported model for reliable tool selection and structured output?
2. Should proactive briefings run only on demand initially, or also through the existing local scheduler?
3. What default per-turn limits should ship for tool calls, model calls, time, and context tokens?
4. Should structured Chat messages be stored in the existing message table as versioned JSON or in a separate response-block table?
5. Which personal preferences should sync through the database versus remain browser-local?
