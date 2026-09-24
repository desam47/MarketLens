# Version 5 Audit Tables

**Written:** 2026-09-23 · **Covers:** `development` after the Chat intent, benchmark-scope, and transcript-format hardening
**Companion to:** [phase_audit_v5.md](phase_audit_v5.md), which requires these tables in its 5.1, 5.2, 5.4, and 5.7 sections.

Every row was taken from the code and test files, not from earlier audit
prose. Tool facts come from the registry, each handler's source, and a scan
of `backend/tests`. A "—" means no such test exists. Findings the tables
exposed are listed at the end. Gaps closed while writing this audit are
marked **(added in this audit)**.

## 5.1 — Calculator operations

Golden values are computed by hand in the test, independently of
`backend/ai/calculator.py`. Unknown fields are rejected by
`CalculationRequest`. No operation evaluates an expression, imports a
module, or touches files or the network.

| Operation | Formula(s) | Golden / direct tests | Also exercised by |
| --- | --- | --- | --- |
| `percentage_change` | `(new - old) / abs(old) * 100` | `test_calculator` (3) | registry, chat fallback |
| `dollar_change` | `new - old` | `test_calculator` | registry |
| `return` | `(end - start) / abs(start) * 100` | golden + zero-start rejection **(added in this audit)** | — |
| `cagr` | `((end / start) ** (1 / years) - 1) * 100` | golden + negative-start rejection **(added in this audit)** | — |
| `weighted_average` | `sum(p·w) / sum(w)` | `test_calculator` | — |
| `position_size` | `abs(entry - stop)`; `account * risk% / 100`; `risk$ / per-share` | `test_calculator` | `build_trade_plan`, `assess_portfolio_risk`, `decision_checklist` |
| `position_risk` | `abs(entry - stop)`; `× shares`; `entry × shares`; optional `total / account * 100` and `abs(target - entry) / per-share` | `test_calculator` (3) | E2E `calculation_position_risk`, `followup_reuses_previous_stop` |
| `risk_reward` | `abs(entry - stop)`; `abs(target - entry)`; `reward / risk` | golden + equal entry/stop rejection **(added in this audit)** | `test_chat_calculation`, `build_trade_plan` |
| `allocation` | `position / portfolio * 100` | golden **(added in this audit)** | `test_chat_calculation` |
| `volatility` | `sample_stddev(p[i]/p[i-1] - 1) * 100` (not annualized) | golden **(added in this audit)** | `assess_portfolio_risk` |
| `drawdown` | `(peak - trough) / peak * 100` | golden **(added in this audit)** | — |
| `max_drawdown` | `max((running_peak - p) / running_peak * 100)` | `test_calculator` | `assess_portfolio_risk` |
| `correlation` | `cov(a, b) / (σa · σb)` | `test_calculator` | `assess_portfolio_risk` |
| `options_breakeven` | `strike ± premium` | golden call + put, missing type rejected **(added in this audit)** | `options_research` |
| `options_intrinsic_value` | `max(S - K, 0)` / `max(K - S, 0)` | golden call + put **(added in this audit)** | `options_research` |
| `options_extrinsic_value` | `max(premium - intrinsic, 0)` | golden **(added in this audit)** | `options_research` |
| `options_max_gain_loss` | long call: unlimited / premium; long put: `K - premium` / premium | `test_calculator` | `options_research` |
| `options_assignment_exposure` | `contracts × multiplier`; `strike × shares` | `test_calculator` | `options_research` |
| `options_vertical_spread` | net debit, width, four sign-convention branches | `test_calculator` (5, textbook examples) | `options_research` |
| `expected_move` | `price × IV × sqrt(days / 365)` | `test_calculator` | `options_research` |

Restricted custom formulas (`backend/ai/verified_formula.py`) accept
arithmetic over verified result fields only. Calls, attributes, indexing,
imports, and unknown names are rejected (`test_verified_formula`).

## 5.2 — Tools: source, cache, freshness, fallback, and tests

The registry has 45 tools. Every read-only and calculation tool runs under
the hard deadline (`AI_CHAT_TOOL_TIMEOUT_SECONDS`, default 20 s).
`save_to_journal` is the only mutating tool: it needs a server-authored
confirmation and never times out.

How freshness is decided (fixed during this audit, see Findings 1–3):
- A tool's own `source_timestamp` is used when it sets one.
- Otherwise the registry takes the **oldest** timestamp in its `sources`
  or `signals`.
- Otherwise freshness is unknown.
- "Call time" remains only for live application-database reads, which are
  current as of the call.
- Naive timestamps are read as New York time, per the project convention.

"Unit tests" lists test modules that reference the tool beyond the registry
and schema checks. "E2E eval" means an end-to-end Chat case routes to it.

| Tool | Source API / service | Cache and provider-call impact | Freshness (`source_timestamp`) | Fallback / delayed handling | Contract test | Unit tests | E2E eval |
| --- | --- | --- | --- | --- | :---: | --- | :---: |
| `anomaly_analysis` | Bars, quote, options, tape (+ benchmark bars) | Sum of sub-tools (≤5 reads) | Oldest nested source timestamp (derived by the registry) | Explicit unknowns per check | — | chat_intents, market_tools, visual_trace_payload | — |
| `assess_portfolio_risk` | Risk dashboard + scenario + bars for ≤10 positions | ≤10 bars reads, fetched in parallel | From nested sources when present, else none | Refuses sizes that breach limits; honest unavailable without positions | — | market_tools, phase_5_8_private_smoke | — |
| `assumption_tracking` | Chat session planner state | Session JSON only | Call time | Immutable originals; stale/broken transitions | — | chat_intents, market_tools | — |
| `build_trade_plan` | Calculator (`risk_reward`, `position_size`) | Pure arithmetic | From nested sources when present, else none | Raises on missing stop/target; no guessed size | — | market_tools | — |
| `calculate` | `backend/ai/calculator.py` | Pure arithmetic; no I/O | None (not market data) | n/a | — | calculator, chat_calculation, context_tape, context_track_record, digest, market_tools, nudges, phase16_analyze, response_blocks | ✅ |
| `compare_symbols` | `get_bars` per symbol (+ watchlist DB) | One bars read per symbol, fetched in parallel (≤4 at a time), ≤25 symbols | Oldest nested source timestamp (derived by the registry) | Per-symbol unknowns; never fails all for one symbol | — | answer_verifier, chat_intents, market_tools, semantic_router, visual_trace_payload | ✅ |
| `counterargument_review` | `signal_explanation` evidence | Same reads as `signal_explanation` | Oldest nested source timestamp (derived by the registry) | No manufactured counter-evidence | — | chat_intents, market_tools | — |
| `decision_checklist` | Trend, calendar, options, quote, calculator | ≤4 reads | From nested sources when present, else none | Each check completed / failed / unavailable / skipped | — | market_tools | — |
| `export_report` | Re-runs the selected plan/risk/options/journal tool | Same reads as the re-run tool | From nested sources when present, else none | Local-only provenance | — | market_tools, response_blocks | — |
| `get_alerts` | `AlertRepository` | SQLite read; no provider call | Call time (live read, current as of the call) | n/a | ✅ | market_tools | ✅ |
| `get_application_help` | Parses `appNavigation.ts` / `App.tsx` | Reads frontend source files; no provider | Call time | Labelled fallback snapshot when frontend source is unreadable | — | market_tools | — |
| `get_bars` | `MarketDataManager.get_historical_bars` | Redis bar cache, then `MarketDataManager`; stale cache tagged; ≤1 fetch per symbol | Newest bar timestamp | `fallback` vs primary; stale-cache tag passes through | — | answer_verifier, chat_actions, chat_intents, market_tools, phase_5_8_private_smoke, response_blocks, trade_plan_tracker, visual_trace_payload | ✅ |
| `get_calendar` | `calendar_service.events_for_symbol` (same as `/api/calendar/symbol`) | Service TTL cache; yfinance on a miss | None at top level | Events carry `source` (default `yfinance`) | ✅ | aux_market_tools, market_tools | — |
| `get_confluence` | `build_confluence_payload` (shared with the MTF endpoint) | Reads a warmed in-process engine; no provider call | Engine signal timestamp (null when cold) | Provider carried in the shared payload | ✅ | market_tools | — |
| `get_fundamentals` | `AuxDataManager.get_fundamentals` | `AuxDataManager` fallback chain with per-category rate throttle; no manager-level response cache (1 call per use) | Provider response timestamp | `fallback` vs fundamentals primary | — | aux_market_tools, context_news_fundamentals, market_tools | — |
| `get_indicator` | `get_bars` + indicator math | Redis bar cache, then `MarketDataManager`; stale cache tagged; ≤1 fetch per symbol (shared with `get_bars`) | Inherited from bars | Inherited from bars | — | market_tools, semantic_router, visual_trace_payload | — |
| `get_market_context` | Market-context engine (`/api/market-context/current` serializers) | Reads a warmed in-process engine; no provider call | Engine signal timestamp | Composite label `MarketLens engine`; honest `no_data` when cold | ✅ | chat_intents, market_tools, semantic_router | — |
| `get_market_regime` | Regime engine (`/api/regime` serializers) | Reads a warmed in-process engine; no provider call | Engine signal timestamp | Engine provider vs primary; honest `unknown` when cold | — | market_tools | — |
| `get_news` | `AuxDataManager.get_news` | `AuxDataManager` fallback chain with per-category rate throttle; no manager-level response cache (1 call per use) | Provider response timestamp | `fallback` vs news primary | — | aux_market_tools, chat_actions, context_news_fundamentals, market_tools, semantic_router, visual_trace_payload | ✅ |
| `get_options_snapshot` | `AuxDataManager.get_options` | `AuxDataManager` fallback chain with per-category rate throttle; no manager-level response cache (1 call per use) | Provider response timestamp | `fallback` vs options primary; delayed/approximate labels from provider | — | aux_market_tools, chat_intents, market_tools, response_blocks, semantic_router | ✅ |
| `get_quote` | `MarketDataManager.get_quote` | Redis quote cache, then `MarketDataManager` fallback chain; ≤1 provider call on a miss | Provider quote timestamp | `fallback` when provider ≠ configured primary; single-observation reconciliation record | — | answer_verifier, chat_actions, chat_calculation, market_tools, phase_5_8_chat_evaluation, phase_5_8_observability, response_blocks, workflows | — |
| `get_relative_strength` | RS engine via `_get_rs_engine` | Reads a warmed in-process engine; no provider call | Oldest benchmark signal timestamp (derived) | Composite label `MarketLens engine` | ✅ | market_tools, visual_trace_payload | — |
| `get_risk_dashboard` | Browser-local positions | Caller-supplied opt-in browser snapshot; no provider or DB call | None — snapshot age unknown | Honest unavailable without opt-in; risk answers label current-price vs entry-price basis | — | market_tools, phase_5_8_private_smoke, semantic_router, visual_trace_payload | ✅ |
| `get_saved_scans` | Browser-local Scanner presets | Caller-supplied opt-in browser snapshot; no provider or DB call | None | Honest unavailable without opt-in; explicit named preset reuse routes through the scanner filter contract | — | market_tools | ✅ |
| `get_sector_data` | `SectorEngine` via regime router's shared cache | Reads a warmed in-process engine; no provider call | Sector signal timestamp | Trend-engine provider vs primary | ✅ | market_tools | — |
| `get_session_stats` | `get_bars` (1m) scoped by each bar's own `session` | Redis bar cache, then `MarketDataManager`; stale cache tagged; ≤1 fetch per symbol (shared) | Newest bar timestamp | Inherited; honest `available: false` when the session has no bars | — | market_tools, visual_trace_payload | ✅ |
| `get_signal_history` | `SignalRepository.get_history` | SQLite read; no provider call | Newest recorded signal | Outcome fields marked unavailable until backfilled | — | market_tools | ✅ |
| `get_support_resistance` | `get_bars` + S/R engine math | Redis bar cache, then `MarketDataManager`; stale cache tagged; ≤1 fetch per symbol (shared) | Inherited from bars | Inherited from bars | — | market_tools | — |
| `get_tape_state` | `get_tape_engine(...).get_snapshot()` | In-process tape engine; `seed=True` can schedule a background Webull seed | Engine's last trade time; none before any trade | `TAPE_ENABLED=false` returned as a tool error | ✅ | market_tools | — |
| `get_trade_journal` | Browser-local Journal | Caller-supplied opt-in structured snapshot; no provider or DB call | None — snapshot age unknown | Honest unavailable without opt-in; persisted Chat data is aggregate/sanitized | — | market_tools | ✅ |
| `get_trend` | `_build_trend_payload` (same as `GET /api/trend/...`) | Reads a warmed in-process engine; no provider call | Engine signal timestamp (null when cold) | Provider left exactly as the endpoint reports; `fallback` computed locally | ✅ | answer_verifier, chat_intents, market_tools, semantic_router | ✅ |
| `get_watchlist` | `WatchlistRepository` | SQLite read; no provider call | Call time (live read, current as of the call) | n/a | ✅ | chat_actions, watchlist_intelligence_tool | — |
| `get_watchlist_intelligence` | Scanner cache + watchlist DB | Reads the scanner cache; warms missing symbols on demand (provider calls only for those) | Briefing `generated_at` | Coverage/warming warnings | — | chat_intents, semantic_router, visual_trace_payload, watchlist_intelligence_tool | — |
| `historical_similarity` | `get_bars` | One bars read | Newest bar timestamp | Look-ahead-safe marker; small-sample labels | — | chat_intents, market_tools, visual_trace_payload | — |
| `import_csv` | stdlib `csv` parser | Caller-supplied browser snapshot; no provider or DB call; nothing persisted | None | Formula cells stay inert text | — | market_tools | — |
| `market_event_timeline` | Bars, alerts, calendar, confluence, fundamentals, news, options | Sum of sub-tools (≤7 reads) | Oldest nested source timestamp (derived by the registry) | Per-source provider metadata | — | chat_intents, market_tools | ✅ |
| `options_research` | `get_options_snapshot` + `get_quote` + calculator | ≤2 provider reads | Options snapshot timestamp | `premium_source` label; skips math without a live quote | — | market_tools | — |
| `save_to_journal` | Validated entry returned for the browser Journal | No server write; **mutating** (server confirmation required, no timeout) | From nested sources when present, else none | n/a | — | chat_actions, market_tools, response_blocks | ✅ |
| `scenario_analysis` | Caller-supplied positions | Pure arithmetic | From nested sources when present, else none | Honest unavailable without positions | — | chat_intents, market_tools, visual_trace_payload | — |
| `sensitivity_analysis` | Calculator over explicit inputs | Pure arithmetic | From nested sources when present, else none | Conditional-analysis assumptions | — | chat_intents, market_tools | — |
| `signal_explanation` | Trend, confluence, tape, bars, optional similarity | Sum of sub-tools (≤5 reads) | Oldest nested source timestamp (derived by the registry) | Explicit unknowns for missing inputs | — | chat_intents, market_tools | — |
| `trade_journal_coach` | Caller-supplied Journal entries + calculator | Pure arithmetic | From nested sources when present, else none | `skipped_entries` with reasons; observations, not advice | — | market_tools, phase_5_8_private_smoke | — |
| `what_changed` | `get_bars` baseline comparison | One bars read | Oldest nested source timestamp (derived by the registry) | Explicit unknowns when no baseline bar | — | chat_intents, market_tools | ✅ |
| `why_did_it_move` | Composite of bars, regime, news, options, sector, tape | Sum of its sub-tools (≤6 reads) | Oldest nested source timestamp (derived by the registry) | Per-source provider/fallback in `sources`; non-causal correlations and explicit unknowns | — | chat_intents, market_tools, semantic_router | ✅ |

**Conflict precedence (5.2.6).** `reconcile_observations()`: a configured
primary wins, otherwise the newest observation; a material difference adds
an explicit conflict warning. It is unit-tested, but no code path produces
more than one observation for the same value (see phase_audit_v5.md, 5.2
post-completion review item 2).

## 5.4 — Analysis tools: tests

| Tool | Plan verification point | Tests (module: count) | Total |
| --- | --- | --- | ---: |
| `why_did_it_move` | facts vs correlations vs unknowns | `market_tools` (3: facts/correlations/unknowns, headlines never causal, missing news as unknown); routing in `chat_intents`, `semantic_router`, E2E | 6 + E2E |
| `what_changed` | baseline comparison | `market_tools` (3: previous close, premarket vs previous regular close, no earlier day → unknown); routing; E2E date scope | 5 + E2E |
| `compare_symbols` | aligned, reproducible rankings | `market_tools` (5: ranking, missing symbol, parallel fetch, misaligned times flagged, aligned times); `visual_trace_payload`, `answer_verifier`, routing | 10 + E2E |
| `scenario_analysis` | deterministic shocks, no forecast | `market_tools` (3: price/stop recalculation, honest unavailable, deterministic multi-position portfolio shock) | 5 |
| `historical_similarity` | look-ahead leakage, small samples | `market_tools` (2: sample outcomes end before the current window, too few bars for a non-overlapping sample) | 4 |
| `signal_explanation` | triggers, agreement, tape | `market_tools` (2), `scanner/test_explanation` (2) | 6 |
| `counterargument_review` | no manufactured counter-evidence | `market_tools` (2: available opposing evidence, none manufactured when absent) | 4 |
| `sensitivity_analysis` | one factor at a time, conditional | `market_tools::test_sensitivity_analysis_varies_one_factor_at_a_time` | 3 |
| `market_event_timeline` | NY ordering, before/after/between | `market_tools` (2: normalization/ordering, between-bounds with mixed UTC/NY offsets); E2E date bounds | 4 + E2E |
| `anomaly_analysis` | explicit baseline and magnitude | `market_tools` (1), `visual_trace_payload` (4) | 7 |
| `assumption_tracking` | immutable originals, stale/broken | `market_tools` (2), `chat_intents` (3) | 6 |

Counts are test functions that reference the tool; each count includes the
generic registry test. Nine behavioral tests were added after the first
draft of this table. `signal_explanation`, `sensitivity_analysis`, and
`anomaly_analysis` still rest on one or two behavioral cases each.

## 5.7 — Response blocks

All blocks are stored as versioned JSON in `chat_messages.response_blocks`
(migration `20260927_chat_response_blocks`) and returned by both the
blocking and streaming endpoints. History renders from the stored blocks
without re-running tools.

- **Older messages:** rows created before the migration (NULL) and corrupted
  JSON both degrade to an empty block list plus the original prose. Both are
  tested in `api/test_chat_router`.
- **Unknown block types:** a type this client doesn't know renders as "Some
  structured answer details are unavailable in this client version."
- **Quality source:**
  - "Own tool" means the block's quality comes from the one tool result that
    built it.
  - "Weakest input" means the turn's fallback-or-oldest successful source.
  - "—" means the block has no data-quality label.

| Block | Produced from | Quality source | Accessible name / role | Quality label shown | Backend tests | Frontend render tests |
| --- | --- | --- | --- | :---: | ---: | ---: |
| `prose` | model or server reply text | weakest input | message bubble text | — | 3 | many (message content) |
| `verification` | `answer_verifier` result | weakest input | "Answer verification", `role=status` | ✅ | 1 | 1 |
| `evidence` | every tool/context trace item | weakest input (per-item status inside) | "Evidence", `role=status` | ✅ | 5 | 3 |
| `calculation` | `calculate` trace | own tool | "Verified calculation" | ✅ | 9 | 1 |
| `chart` | `get_bars` | own tool | "Mini price chart"; SVG `role=img` | ✅ | 8 | 3 |
| `indicator_table` | `get_indicator`, `signal_explanation` | own tool | "Indicator table" | ✅ | 1 **(added in this audit)** | 2 |
| `options_chain` | `get_options_snapshot`, `options_research` | own tool | "Options chain card" | ✅ | 4 | 2 |
| `risk_card` | `get_risk_dashboard`, `assess_portfolio_risk` | own tool | "Risk snapshot" | ✅ | 1 **(added in this audit)** | 1 |
| `scenario` | `scenario_analysis` | own tool | "Scenario analysis" | ✅ | 1 **(added in this audit)** | 2 |
| `session_stats` | `get_session_stats` | own tool | "Session statistics" | ✅ | 1 **(added in this audit)** | 1 |
| `historical_outcomes` | `historical_similarity` | own tool | "Historical outcomes" | ✅ | 1 **(added in this audit)** | 1 |
| `comparison_table` | `compare_symbols` | own tool | "Comparison table" | ✅ | 2 | 2 |
| `ranked_results` | relative strength, anomalies, watchlist intelligence | own tool | "Ranked results" | ✅ | 6 | 2 |
| `report` | `export_report` | own tool | "Local report" | ✅ | 2 | 2 |
| `journal_save` | `save_to_journal` | own tool | "Journal save" | ✅ | 2 | 1 |
| `warning` | tool warnings/errors, partial/unavailable symbols | weakest input | "Answer warnings", `role=status` | ✅ | 1 | 1 |
| `action_confirmation` | alert/watchlist/journal/report action steps | weakest input | "Action status", `role=status` | — | 2 | 2 |
| `suggested_followups` | fixed list + preference mode | weakest input | "Suggested follow-ups" | — | 2 | 1 |

Quality labels are text ("verified", "partial", "stale", …), not color
alone. Tables sit in horizontally scrollable wrappers. Responsive and
screen-reader behavior has automated component coverage only. The device
pass in `phase_5_7_manual_qa.md` has not been run.

## End-to-end verification matrix (plan)

| # | Scenario | Status | Evidence |
| ---: | --- | --- | --- |
| 1 | Buy 200 AAPL at , stop  → ,600 risk with formula | ✅ Automated | E2E `calculation_position_risk` |
| 2 | Premarket change uses previous regular close, labels premarket time | ✅ Tool | `test_premarket_change_uses_previous_regular_close` (fixed: the baseline was the previous bar) |
| 3 | AAPL vs MSFT from aligned timeframes and timestamps | ✅ Tool | one timeframe per request; misaligned latest bars now flagged (`test_compare_symbols_flags_misaligned_latest_bars`) |
| 4 | Why move separates confirmed news from inferred effects | ✅ Tool + routing | `test_move_analysis_separates_facts_correlations_and_unknowns`; E2E `why_did_it_move_routes` |
| 5 | 5% portfolio shock gives deterministic impacts | ⚠️ Tool + privacy harness | `test_portfolio_shock_is_deterministic_across_positions`; the opt-in Chat path is covered by `opted_in_positions_reach_risk_tool_only` and privacy assertions, while live UI smoke and a full Chat shock calculation remain release follow-up |
| 6 | NL scanner filters identical to executed filters | ✅ Scanner page | `nl_search/test_scanner_builder`, `api/test_nl_search_router` (preview payload = executed payload) |
| 7 | "Use the same stop but 100 shares" reuses state | ✅ Automated | E2E `followup_reuses_previous_stop` |
| 8 | Breakeven and expected move show delayed/approximate provenance | ✅ Tool | options results now carry `data_status: DELAYED` → a visible "Provider data status: DELAYED." warning (`test_options_snapshot_is_labelled_delayed_in_the_tool_result`) |
| 9 | Fired alert opens a conversation with exact trigger evidence | ✅ API | `api/test_alerts_api` conversation-context tests |
| 10 | Saved plan and later review keep original assumptions/calculations | ✅ Tool + E2E | `test_saved_plan_keeps_original_assumptions_and_calculations_through_review`; E2E confirmation |
| 11 | Provider outage → partial/stale answer with explicit warning | ✅ Automated | E2E `model_outage_is_explicit` (+ stream), `tool_timeout_is_explicit`, verifier stale cases |
| 12 | Destructive actions confirmed; no order execution | ✅ Automated | E2E confirmation, decline, and self-confirmation cases; no order tool is registered |

## Findings from this audit

Fixed after the tables were first written:

1. **Composite analysis tools reported no freshness.**
   - Affected: `why_did_it_move`, `what_changed`, `compare_symbols`,
     `signal_explanation`, `counterargument_review`,
     `market_event_timeline`, `anomaly_analysis`, and
     `get_relative_strength`.
   - They set no top-level source time. The registry now uses their oldest
     nested source or signal timestamp, so an answer is only as fresh as
     its stalest input.
   - Example: a `compare_symbols` run over bars from 15:59 and 15:00 now
     reports 15:00.
   - `get_calendar` still reports none; its events are dates, not
     observation times.
2. **`get_tape_state` reported call time as data time.** The tape engine
   snapshot now includes `last_trade_at`, and the tool reports it. With no
   trades yet there is no data time.
3. **Browser-snapshot and computed tools reported call time.**
   - Affected: `get_risk_dashboard`, `get_trade_journal`,
     `assess_portfolio_risk`, `trade_journal_coach`,
     `scenario_analysis`, `sensitivity_analysis`, `build_trade_plan`,
     `decision_checklist`, `export_report`, and `save_to_journal`.
   - These no longer stamp the call time. They report nested source times
     when present; otherwise freshness is unknown.
   - Call time is kept only for live database reads (`get_alerts`,
     `get_watchlist`) and application metadata.
4. **Naive timestamps were read as UTC.** The registry treated a naive
   `source_timestamp` as UTC. Under the project convention (naive = New
   York), that aged such data by 4–5 hours. `get_bars` passes the bar's
   own datetime, so naive bar times were affected. Chat's symbol-context
   evidence (`_context_freshness_seconds`) had the same bug. Both now read
   naive times as New York time.

Found and fixed while adding coverage:

5. **`historical_similarity` sample outcomes overlapped the current setup.**
   A match could end so close to the current window that its outcome bars
   landed inside it; the code comment claimed otherwise. Matches now end at
   least `max(horizon) + 1` bars before the current window, and too-short
   ranges report unavailable.
6. **"Previous close" meant "previous bar".** `what_changed` compared
   against `bars[-2]` for `previous_close`/`yesterday`, so an intraday or
   premarket question compared against the prior minute. It now uses the
   last regular-session bar from an earlier New York day, and labels both
   sessions.
7. **Comparisons silently mixed observation times.** `compare_symbols` now
   reports `aligned` and flags differing latest-bar times
   (`misaligned_ranking`).
8. **Options snapshots carried no delayed label.** Options results now set
   `data_status: DELAYED`, which surfaces as a warning in the answer.

Still open:

9. **Scenario #5 from Chat.** Risk Dashboard positions, structured Journal
   fields, and saved Scanner presets now have an explicit per-category opt-in
   browser snapshot path. Relevant Chat turns receive only the needed
   structured data; raw browser rows, journal prose, and screenshots are not
   retained in persisted Chat messages. Without opt-in, the tools remain
   honestly unavailable. Live UI smoke and a full Chat portfolio-shock
   calculation remain follow-up coverage.
10. `signal_explanation`, `sensitivity_analysis`, and `anomaly_analysis`
   still have one or two behavioral cases each.

Closed while writing this audit:
- 11 golden formula cases and 4 invalid-input cases for `return`, `cagr`,
  `volatility`, `drawdown`, `risk_reward`, `allocation`, and the
  single-leg options operations. All matched the hand-computed values.
- 6 trace-to-block mapping tests for `chart`, `indicator_table`,
  `risk_card`, `scenario`, `session_stats`, and `historical_outcomes`.
