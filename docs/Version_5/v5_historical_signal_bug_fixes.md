# Version 5 Historical Signals Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (batch 2a: HS-05 and HS-06)
**Status:** Batch 1 and the scope/coverage half of Batch 2 are complete. Six findings are fixed and covered by focused backend/frontend regressions; eight follow-up gaps remain.
**Scorecard:** 6 ✅ COMPLETE, 0 ⚠️ PARTIAL, 8 ❌ NOT STARTED, 0 🟡 DEFERRED.
**Source:** 2026-09-24 code review of the Historical Signals backend, API, storage, card, research dashboard, and replay panel at `d0aebd2`.
**Related:** [Phase audit](phase_audit_v5.md), [AI Analysis fixes](v5_ai_analysis.md), [Chat bug fixes](v5_bug_fixes.md)

"Verified" means the behaviour was reproduced with a throwaway probe test run under the project's offline pytest guards, or shown by a read-only query of the live database. "Code-read" means it follows from the code but was not reproduced. HS-01 through HS-06 are now verified; the remaining entries are Code-read.

Status legend (same as the phase audits):

- ✅ **COMPLETE** — fixed and covered by tests
- ⚠️ **PARTIAL** — the harmful behaviour is fixed, but specific gaps remain (listed in the entry)
- ❌ **NOT STARTED** — no change made yet
- 🟡 **DEFERRED** — intentionally postponed

Line numbers refer to the code at `d0aebd2`.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| HS-01 | Critical | Backend | Partial forward outcomes can remain incomplete forever | Verified | ✅ COMPLETE |
| HS-02 | Critical | API + UI | “Delete >180d” deletes rows older than 30 days | Verified | ✅ COMPLETE |
| HS-03 | Critical | Research + Replay | Raw underlying returns are presented as signal P&L | Verified | ✅ COMPLETE |
| HS-04 | Critical | Research | Cumulative return chart is not a valid equity curve | Verified | ✅ COMPLETE |
| HS-05 | High | Scope | Default scope silently uses only the first active watchlist | Verified | ✅ COMPLETE |
| HS-06 | High | Research | Date filters and exports are silently limited to the newest 1,000 rows | Verified | ✅ COMPLETE |
| HS-07 | High | Methodology | All timeframes combines incomparable horizons and hides recorded timeframes | Code-read | ❌ NOT STARTED |
| HS-08 | High | Provenance | Regime data is not consistently historical, despite UI claims | Code-read | ❌ NOT STARTED |
| HS-09 | High | Data quality | Several stored context fields are placeholders rather than evidence | Code-read | ❌ NOT STARTED |
| HS-10 | High | Replay | Neutral or unknown signals are simulated as long trades; session scope drifts | Code-read | ❌ NOT STARTED |
| HS-11 | Medium | API safety | Manual recording accepts ignored client scope and mutations lack server guardrails | Code-read | ❌ NOT STARTED |
| HS-12 | Medium | Storage | No database uniqueness constraint protects signal identity | Code-read | ❌ NOT STARTED |
| HS-13 | Medium | API + presentation | A valid zero regime average is returned as unavailable | Code-read | ❌ NOT STARTED |
| HS-14 | Medium | Tests | Regression coverage preserves faulty semantics and misses lifecycle transitions | Code-read | ❌ NOT STARTED |

**Next suggested order:**

1. ~~**Batch 1 — decision-data correctness:** HS-01, HS-02, HS-03, and HS-04.~~ Done.
2. **Batch 2 — truthful scope and methodology:** HS-05 and HS-06 are done; HS-07 through HS-10 remain.
3. **Batch 3 — operational integrity and regressions:** HS-11 through HS-14.

---

## Critical

### HS-01 — Partial forward outcomes can remain incomplete forever

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `SignalRecorder._compute_outcome_for_signal` (`backend/services/signal_recorder.py:617`) and `SignalRepository.get_signals_needing_outcomes` (`backend/repositories/signal_repository.py:146`).

The recorder intentionally calculates the outcome windows currently available. With five future bars it writes 5b, and with ten it writes 5b and 10b. Its comment says later backfills will fill remaining fields.

That cannot happen: the repository selects only rows where `return_5b IS NULL`. Once 5b exists, the row leaves the queue even if 10b and 20b are still null. The stored completion flag is also set false-to-complete on that partial write.

**Impact:** a row can look completed while its later returns permanently remain absent and MFE/MAE reflect a shorter, unstated observation window.

**Resolution:** the outcome queue now keeps any row with a missing return or excursion eligible. MFE/MAE are calculated only across the completed 20-bar window, and `_outcome_missing` remains true until all five values are present. The API's “completed only” filter and the frontend's completed counts use the same full-outcome contract.

**Tests:** `test_backfill_outcomes_partial_row_matures_on_later_pass` proves one row progresses from 5/10 bars to a complete 20-bar outcome on a later pass. The focused recorder, repository, and signals API suite passed: 61 tests.

### HS-02 — “Delete >180d” deletes rows older than 30 days

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `api.deleteOldSignals` (`frontend/src/services/api.ts:2156`), `HistoricalSignalCard.handleCleanup` (`frontend/src/components/HistoricalSignalCard.tsx:140`), and `delete_old_signals` (`backend/api/signals/router.py:301`).

The visible destructive action confirms deletion beyond 180 days and calls `deleteOldSignals(180)`. The client sends `days=180`; the server accepts only `older_than_days` and falls back to 30 days.

**Impact:** confirming a six-month retention action deletes everything older than one month, with no recovery path in the product.

**Resolution:** the client and API now use `older_than_days` consistently. The destructive route rejects a request unless `confirm=true` is explicit; the existing browser confirmation remains the user-facing confirmation step.

**Tests:** API coverage now proves a confirmed 180-day deletion works and an otherwise identical request without confirmation is rejected without deleting data.

### HS-03 — Raw underlying returns are presented as signal P&L

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `HistoricalSignalCard` (`frontend/src/components/HistoricalSignalCard.tsx:37`), `SignalResearchDashboard.metricRows` (`frontend/src/components/SignalResearchDashboard.tsx:31`), and replay statistics (`frontend/src/components/HistoricalReplayPanel.tsx:176`).

Stored returns correctly represent raw price movement. A bearish call followed by a decline therefore has a negative raw return. The screens colour it red, average it as a loss, and the replay counts a win only when raw return is positive. MFE and MAE use the same long-only meaning.

**Impact:** correct bearish calls look like losing signals, and research tables can contradict their own directional win-rate column.

**Resolution:** raw fields remain in storage and CSV export as explicitly named underlying movement. A shared frontend helper and the regime aggregate now derive direction-adjusted signal returns, favorable excursion, and adverse excursion. Neutral/unknown rows are excluded from directional metrics; the card and research labels now say “signal” rather than implying raw movement is P&L.

**Tests:** the research regression uses a +2% bullish call and a −1% bearish call and now requires a +1.50% average signal return with 100% directional wins. Repository/API regressions require a correctly bearish raw decline to aggregate as +1.0%.

### HS-04 — Cumulative return chart is not a valid equity curve

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `SignalResearchDashboard.equityPoints` (`frontend/src/components/SignalResearchDashboard.tsx:134`).

The chart sums every raw 5-bar return in timestamp order. It combines symbols, directions, 1-minute through daily horizons, and overlapping forward windows. Multiple signals can count the same price move many times. It has no entry timing, allocation, position limit, cost, slippage, holding, or overlap rule.

**Impact:** the chart visually reads as an equity curve but cannot measure return, drawdown, exposure, or risk.

**Resolution:** the chart has been removed. The dashboard states why overlapping signals and mixed horizons are not a strategy equity curve and directs the trader to a dedicated backtest for execution, allocation, and cost assumptions.

**Tests:** the research component regression requires the methodology notice and confirms that no cumulative-return chart remains. The production frontend build also completed successfully.

---

## High

### HS-05 — Default scope silently uses only the first active watchlist

**Status:** ✅ COMPLETE (2026-09-24, batch 2a)
**Where:** signal-list and research routes (`backend/api/signals/router.py:128`, `:166`, and `:193`).

Each route iterates active watchlists and stops at the first non-empty list. It does not accept a selected watchlist ID, union active watchlists, or return resolved scope metadata.

**Impact:** research silently excludes names whenever more than one watchlist is active; result membership can change with watchlist ordering.

**Resolution:** every signal route now accepts explicit `all_active`, selected `watchlist`, or offline `all_stored` scope. `all_active` unions enabled symbols from every active list rather than stopping at the first one. The historical card and research dashboard expose the selected scope; the research response reports the exact watchlists and enabled symbols used.

**Tests:** API coverage proves that two active lists are unioned and that selecting one list returns only its symbols. The research UI requires a coverage line naming the resolved watchlist and enabled-symbol count.

### HS-06 — Date filters and exports are silently limited to the newest 1,000 rows

**Status:** ✅ COMPLETE (2026-09-24, batch 2a)
**Where:** dashboard load (`frontend/src/components/SignalResearchDashboard.tsx:93`) and history query (`backend/repositories/signal_repository.py:85`).

The dashboard fetches the newest 1,000 records once, then filters date and timeframe in the browser. The repository supports start/end times but the API does not expose them. CSV export uses that same truncated browser array.

**Impact:** older date ranges can look empty or incomplete with no coverage warning, and exported research is not necessarily the selected population.

**Resolution:** a new server-side research endpoint applies scope, timeframe, start/end dates, complete-outcome policy, limit, and offset before returning records. It includes total count, page range, next-page availability, and resolved scope. The dashboard labels page-only metrics as page metrics and shows loaded-versus-total coverage. CSV export streams the full same scoped query on the server with no hidden row cap rather than exporting the current browser page.

**Tests:** API regressions prove date filtering, selected-watchlist isolation, pagination metadata, and full scoped CSV export. The focused signal API/repository suite passed 46 tests; affected frontend suites and the production build passed.

### HS-07 — All timeframes combines incomparable horizons and hides recorded timeframes

**Status:** ❌ NOT STARTED
**Where:** selectors in `SignalResearchDashboard.tsx:5`, `HistoricalSignalCard.tsx:8`, and `HistoricalReplayPanel.tsx:7`.

Five bars means five minutes for `1m` and five trading days for `1d`; pooling their returns or wins is not meaningful. The recorder supports `4h` and `1wk`, but the visible selectors omit those recorded buckets.

**Impact:** dense intraday rows dominate default research while valid higher-timeframe records are hidden.

**Resolution:** require one timeframe for all performance and backtest metrics. An all-timeframe view may show separated coverage/counts but must not aggregate returns. Derive every selector from one supported-timeframe source.

### HS-08 — Regime data is not consistently historical, despite UI claims

**Status:** ❌ NOT STARTED
**Where:** signal insertion (`backend/services/signal_recorder.py:542`) and regime-performance copy (`frontend/src/components/HistoricalSignalCard.tsx:281`).

Backfilled signals deliberately have no regime because the regime engine knows only the present. A row written within fifteen minutes of its close receives the current regime; older backfilled rows retain `NULL`. The UI says regimes come from the engine at signal time.

**Impact:** regime research is a partial recent sample with unknown coverage but is presented as complete historical segmentation.

**Resolution:** reconstruct and persist point-in-time regime from stored benchmark bars, or call this a live regime snapshot, omit it from historical performance, and show regime coverage.

### HS-09 — Several stored context fields are placeholders rather than evidence

**Status:** ❌ NOT STARTED
**Where:** insertion and volume classification (`backend/services/signal_recorder.py:542` and `:587`).

`relative_strength` and `sector_alignment` are stored as `None`; `_classify_volume` always returns `normal`, including zero volume; `strategy_version` is always `v1`; and `data_quality` is always `good`.

**Impact:** downstream research and AI context can mistake schema fields for evidence fields.

**Resolution:** calculate each field causally with versioned inputs, or leave it unavailable and expose a coverage/data-quality reason. Store configuration and provider/bar provenance.

### HS-10 — Replay simulates neutral or unknown signals as long trades and session scope drifts

**Status:** ❌ NOT STARTED
**Where:** replay filters and simulation (`frontend/src/components/HistoricalReplayPanel.tsx:158` and `:191`).

Only a state matching bearish/down/sell becomes short. Neutral, warm-up `NULL`, and unknown values become long simulated trades. The session filter applies to candles, but replay signal statistics use date filtering only.

**Impact:** simulated net can include trades the engine never called for, while chart and statistics cover different session populations.

**Resolution:** simulate only explicit bullish/bearish calls, show entry/exit and intrabar assumptions, and apply one resolved session scope to bars, signals, markers, statistics, and current-signal lookup.

---

## Medium

### HS-11 — Manual recording accepts ignored client scope and mutations lack server guardrails

**Status:** ❌ NOT STARTED
**Where:** client request (`frontend/src/services/api.ts:2146`) and record/backfill routes (`backend/api/signals/router.py:209` and `:223`).

The client accepts symbols/timeframes and sends JSON. The route declares query parameters, ignores that body, and records all active ingestion symbols/timeframes when no query parameters are present. Bulk record and backfill have no server-side confirmation, idempotency key, estimated work, audit record, or rate limit.

**Impact:** a caller can believe it requested a narrow operation while triggering a broad one; repeated clicks can compete with the ingestion loop.

**Resolution:** use typed single/bulk request models, return resolved scope and work estimate, and add server-owned confirmation plus idempotency for manual bulk operations.

### HS-12 — No database uniqueness constraint protects signal identity

**Status:** ❌ NOT STARTED
**Where:** `HistoricalSignal` (`backend/models/signal.py:26`) and initial schema (`alembic/versions/20260828_8175af1a213e_initial_schema.py:159`).

The recorder checks `(symbol, timeframe, timestamp)` before insert, but the table has no matching unique constraint. Concurrent ingestion, manual recording, retry, or two processes can pass that read-before-write check together.

**Impact:** duplicate rows bias counts, averages, win rates, exports, and replay results.

**Resolution:** add a unique index, deduplicate existing rows with an audited migration, and treat uniqueness conflicts as safe no-ops.

### HS-13 — A valid zero regime average is returned as unavailable

**Status:** ❌ NOT STARTED
**Where:** `SignalRepository.get_performance_by_regime` (`backend/repositories/signal_repository.py:225`).

The serializer checks truthiness rather than `is not None`. An exact 0.0% average becomes `null` and renders as an em dash instead of a valid flat result.

**Resolution:** preserve zeroes and add a zero-average API/UI regression that distinguishes no observations from a flat average.

### HS-14 — Regression coverage preserves faulty semantics and misses lifecycle transitions

**Status:** ❌ NOT STARTED
**Where:** partial-fill test (`backend/tests/services/test_signal_recorder.py:219`), research test (`frontend/src/components/SignalResearchDashboard.test.tsx:8`), and replay test (`frontend/src/components/HistoricalReplayPanel.test.tsx:8`).

The partial-fill test says a later backfill completes remaining outcomes but does not run that later pass. The research test expects a cumulative raw result from bullish and bearish calls, encoding the directionality problem. There is no card-specific test or coverage for deletion parameters, multi-watchlist scope, date coverage, neutral replay calls, session alignment, or duplicate inserts.

**Resolution:** add focused unit, API, and UI regressions for HS-01 through HS-13 before changing behaviour; retain them as the Historical Signals release gate.

---

## Reference

Historical Signals records a causal trend label for each closed stored bar. `BarReplay` feeds bars oldest-first through a private trend engine so future bars cannot change a prior label. Later backfill records raw 5/10/20-bar price movement plus forward high/low excursions. The card shows individual records and regime aggregates, the research dashboard groups outcomes, and replay steps through bars with a simplified stop/target simulation.

That causal-label foundation is valuable. The fixes above are required to make outcome maturity, scope, directionality, methodology, provenance, and destructive controls equally trustworthy for trader-facing research.
