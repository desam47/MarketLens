# Version 5 Historical Signals Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (batch 3: HS-11, HS-12, HS-14, HS-18)
**Status:** Complete. All 18 findings are fixed and covered by regression tests, including two data migrations applied to the live database (HS-09, HS-12).
**Scorecard:** 18 ✅ COMPLETE, 0 ⚠️ PARTIAL, 0 ❌ NOT STARTED, 0 🟡 DEFERRED.
**Source:** 2026-09-24 code review of the Historical Signals backend, API, storage, card, research dashboard, and replay panel at `d0aebd2` (HS-01 to HS-14). A follow-up review of batches 1 and 2a at `8f6a803` added HS-15 to HS-18.
**Related:** [Phase audit](phase_audit_v5.md), [AI Analysis fixes](v5_ai_analysis.md), [Chat bug fixes](v5_bug_fixes.md)

"Verified" means the behaviour was reproduced with a throwaway probe test run under the project's offline pytest guards, or shown by a read-only query of the live database. "Code-read" means it follows from the code but was not reproduced.

Status legend (same as the phase audits):

- ✅ **COMPLETE** — fixed and covered by tests
- ⚠️ **PARTIAL** — the harmful behaviour is fixed, but specific gaps remain (listed in the entry)
- ❌ **NOT STARTED** — no change made yet
- 🟡 **DEFERRED** — intentionally postponed

Line numbers for HS-01 to HS-14 refer to the code at `d0aebd2`; line numbers for HS-15 to HS-18 refer to `8f6a803`.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| HS-01 | Critical | Backend | Partial forward outcomes can remain incomplete forever | Verified | ✅ COMPLETE |
| HS-02 | Critical | API + UI | “Delete >180d” deletes rows older than 30 days | Verified | ✅ COMPLETE |
| HS-03 | Critical | Research + Replay | Raw underlying returns are presented as signal P&L | Verified | ✅ COMPLETE |
| HS-04 | Critical | Research | Cumulative return chart is not a valid equity curve | Verified | ✅ COMPLETE |
| HS-15 | Critical | Backend | The outcome queue stalls behind rows that cannot finish | Verified | ✅ COMPLETE |
| HS-16 | Critical | AI | AI Analysis reads raw price movement as the signals' track record | Verified | ✅ COMPLETE |
| HS-05 | High | Scope | Default scope silently uses only the first active watchlist | Verified | ✅ COMPLETE |
| HS-06 | High | Research | Date filters and exports are silently limited to the newest 1,000 rows | Verified | ✅ COMPLETE |
| HS-07 | High | Methodology | All timeframes combines incomparable horizons and hides recorded timeframes | Verified | ✅ COMPLETE |
| HS-08 | High | Provenance | Regime data is not historical and covers under a fifth of rows | Verified | ✅ COMPLETE |
| HS-09 | High | Data quality | Several stored context fields are placeholders rather than evidence | Verified | ✅ COMPLETE |
| HS-10 | High | Replay | Neutral or unknown signals are simulated as long trades; session scope drifts | Code-read | ✅ COMPLETE |
| HS-11 | Medium | API safety | Manual recording accepts ignored client scope and mutations lack server guardrails | Code-read | ✅ COMPLETE |
| HS-12 | Medium | Storage | No database uniqueness constraint protects signal identity | Verified | ✅ COMPLETE |
| HS-13 | Medium | API + presentation | A valid zero regime average is returned as unavailable | Code-read | ✅ COMPLETE |
| HS-14 | Medium | Tests | Regression coverage preserves faulty semantics and misses lifecycle transitions | Code-read | ✅ COMPLETE |
| HS-17 | Medium | Research | Research metrics describe one 250-row page, not the selected population | Code-read | ✅ COMPLETE |
| HS-18 | Medium | API + UI | “Delete >180d” is undone at restart and ignores the selected scope | Code-read | ✅ COMPLETE |

**Next suggested order:**

1. ~~**Batch 1 — decision-data correctness:** HS-01, HS-02, HS-03, HS-04, and HS-13.~~ Done.
2. ~~**Batch 2a — truthful scope:** HS-05 and HS-06.~~ Done.
3. ~~**Batch 2b — outcomes and AI correctness:** HS-15 and HS-16.~~ Done.
4. ~~**Batch 2c — methodology:** HS-07, HS-08, HS-09, HS-10, and HS-17.~~ Done.
5. ~~**Batch 3 — operational integrity and regressions:** HS-11, HS-12, HS-18, then the rest of HS-14 as the release gate.~~ Done.

---

## Critical

### HS-01 — Partial forward outcomes can remain incomplete forever

**Status:** ✅ COMPLETE (2026-09-24, batch 1). On the live database the queue then stalled before most rows were reached; batch 2b fixed that (HS-15).
**Where:** `SignalRecorder._compute_outcome_for_signal` (`backend/services/signal_recorder.py:617`) and `SignalRepository.get_signals_needing_outcomes` (`backend/repositories/signal_repository.py:146`).

The recorder intentionally calculates the outcome windows currently available. With five future bars it writes 5b, and with ten it writes 5b and 10b. Its comment says later backfills will fill remaining fields.

That cannot happen: the repository selects only rows where `return_5b IS NULL`. Once 5b exists, the row leaves the queue even if 10b and 20b are still null. The stored completion flag is also set false-to-complete on that partial write.

**Impact:** a row can look completed while its later returns permanently remain absent and MFE/MAE reflect a shorter, unstated observation window.

**Resolution:** the outcome queue now keeps any row with a missing return or excursion eligible. MFE/MAE are calculated only across the completed 20-bar window, and `_outcome_missing` remains true until all five values are present. The API's “completed only” filter and the frontend's completed counts use the same full-outcome contract. Batch 2b moved the AI stats and the chat tool to the same contract (HS-16).

**Tests:** `test_backfill_outcomes_partial_row_matures_on_later_pass` proves one row progresses from 5/10 bars to a complete 20-bar outcome on a later pass. The focused recorder, repository, and signals API suite passed: 61 tests.

### HS-02 — “Delete >180d” deletes rows older than 30 days

**Status:** ✅ COMPLETE (2026-09-24, batch 1). See HS-18 for what the corrected delete still does wrong.
**Where:** `api.deleteOldSignals` (`frontend/src/services/api.ts:2156`), `HistoricalSignalCard.handleCleanup` (`frontend/src/components/HistoricalSignalCard.tsx:140`), and `delete_old_signals` (`backend/api/signals/router.py:301`).

The visible destructive action confirms deletion beyond 180 days and calls `deleteOldSignals(180)`. The client sends `days=180`; the server accepts only `older_than_days` and falls back to 30 days.

**Impact:** confirming a six-month retention action deletes everything older than one month, with no recovery path in the product.

**Resolution:** the client and API now use `older_than_days` consistently. The destructive route rejects a request unless `confirm=true` is explicit; the existing browser confirmation remains the user-facing confirmation step. The client always sends `confirm=true`, so the server check protects only against other callers.

**Tests:** API coverage now proves a confirmed 180-day deletion works and an otherwise identical request without confirmation is rejected without deleting data.

### HS-03 — Raw underlying returns are presented as signal P&L

**Status:** ✅ COMPLETE (2026-09-24, batch 1). Batch 2b fixed the AI path and the Scanner's Signal Explanation panel, which this fix missed (HS-16).
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

### HS-15 — The outcome queue stalls behind rows that cannot finish

**Status:** ✅ COMPLETE (2026-09-24, batch 2b)
**Where:** `SignalRepository.get_signals_needing_outcomes` (`backend/repositories/signal_repository.py:215`), called with `limit=1000` every 300 s by `_signal_outcome_backfill_loop` (`backend/market_data/services/ingestion_service.py:2393`).

The queue returns the 1,000 oldest rows that are missing any outcome. Since HS-01, a row stays queued until its full 20-bar outcome exists. Some rows can't get there for months, or ever:

- a weekly signal needs 20 weeks of later bars, and a daily signal needs 20 trading days;
- a signal for a symbol that has left every watchlist gets no more bars.

Each pass reprocesses those same rows and never reaches the newer rows behind them.

**Verified (read-only query of the live database):**

- 156,940 rows are pending, 146,669 of them partial: they have a 5-bar return but no 20-bar outcome.
- None of the 1,000 oldest pending rows has 20 later bars. They are 420 weekly rows (May–September), 291 daily rows, and intraday rows for NOK and XLK, whose last bar arrived on 2026-09-11 at 15:21.
- A TSLA 5m signal from 2026-09-14 18:20 has 787 later bars and is still missing its 20-bar return and excursions.

**Impact:** most rows never mature, so every screen that counts complete outcomes shows only rows that matured before the fix. This doesn't clear itself. New weekly and daily rows keep the front of the queue full. Signals for removed symbols stay until retention prunes them, which takes 1,096 days for daily and weekly rows.

**Resolution:** the queue now returns a pending row only when recomputing it would write something new. For each symbol and timeframe with pending rows, it reads the 20 newest bar timestamps. A row with N later bars is exactly one whose anchor is older than the Nth-newest bar. A row is selected when its pair holds enough later bars for its next missing window: 5 bars for the 5-bar return, 10 for the 10-bar return, and 20 for the 20-bar return and excursions. Candidates from every pair are then merged oldest first.

Rows that cannot advance are skipped rather than marked abandoned, so no schema change was needed. A symbol that starts receiving bars again becomes eligible on its own. The queue and the recorder now share `outcome_anchor` and `OUTCOME_WINDOWS`, so selection and calculation use the same daily midnight anchor and windows.

**Tests:**

- `test_get_signals_needing_outcomes_skips_rows_that_cannot_advance`: three stuck rows fill a batch of two, and the newer finishable row is still returned.
- `test_get_signals_needing_outcomes_waits_for_the_next_missing_window` and `..._needs_twenty_bars_for_the_final_window`: a partial row returns only once its 10th, then 20th, later bar exists.
- `test_backfill_outcomes_is_not_blocked_by_rows_that_cannot_finish`: through the recorder, a batch of two completes the newer row and leaves the stuck ones untouched.
- `test_backfill_outcomes_bulk_prefetch_two_signals_same_symbol` now expects one update, not two. The row with a single later bar is no longer selected, whereas before it counted as "updated" although nothing was written.

**Checked on a copy of the live tables:** the new selection returned 1,000 candidates in 1.7 s. It found 155,314 of 156,940 pending rows able to advance. The dev server runs with `--reload`, so the fix went live when the file was saved. Minutes later, the TSLA 5m signal from 2026-09-14 18:20 had its 20-bar return and excursions.

### HS-16 — AI Analysis reads raw price movement as the signals' track record

**Status:** ✅ COMPLETE (2026-09-24, batch 2b)
**Where:** `SignalRepository.get_stats` (`backend/repositories/signal_repository.py:367`) → `signal_recorder.get_stats` → `_signal_stats_context` (`backend/ai/context.py:568`) → `historical_signal_stats` in the analysis prompt. Also Chat's `get_signal_history_tool` (`backend/ai/market_tools.py:2584`; `outcome_available` at `:2619`).

`get_stats` averages raw `return_5b` and `return_10b` across every row that has a 5-bar return, including neutral and warm-up rows. The win rate beside those averages is directional, but the averages are not. A row counts as having an outcome once its 5-bar return exists, which is the rule HS-01 replaced. `get_signal_history_tool` returns raw returns without saying they are raw, and it sets `outcome_available` from the 5-bar return alone.

**Verified (read-only query of the live database, daily signals):**

| Symbol | Average the model sees | Directional average | Directional win rate |
|---|---|---|---|
| SMCI | +1.36% | −4.89% | 37.8% |
| NVDA | +1.31% | −0.60% | 47.9% |
| TSLA | +0.60% | −0.82% | 40.5% |
| PLTR | +2.11% | +1.34% | 58.6% |

**Impact:** for SMCI, NVDA, and TSLA, the model is told the engine's signals averaged gains when its calls lost money. The numbers also contradict the win rate next to them. This is the defect HS-03 fixed on screen.

`get_stats` also feeds the Scanner's Signal Explanation panel (`GET /api/scanner/{symbol}?include_history=true`), which showed the same raw averages as "avg 5-bar" next to a directional win rate.

**Resolution:**

- **Shared rule:** `signal_repository.py` now defines the rule once. `directional_outcome` works on a row and `directional_outcome_expr` in SQL; both negate a bearish call's return and swap its excursions. The full-outcome rule lives there too, as `is_outcome_complete` and `outcome_complete_filter`. The regime aggregate, the CSV export (replacing the router's `_directional_value`), `get_stats`, and the chat tool all use these helpers.
- **`get_stats`:** averages and win rate now cover complete bullish and bearish outcomes only, and the averages are direction-adjusted. It adds `directional_outcomes` (the sample size), and `with_outcomes` now counts complete outcomes.
- **AI context:** the model receives `avg_signal_return_5b`, `avg_signal_return_10b`, and `complete_directional_signals`, plus a `basis` note saying the figures are direction-adjusted.
- **Chat tool:** `get_signal_history` now labels raw fields `underlying_return_*` and adds `signal_return_*`. `outcome_available` is replaced by `outcome_complete`, which follows the full-outcome rule.
- **Signal Explanation panel:** it now reads "N completed bullish/bearish calls" and "avg signal 5-bar / 10-bar".

**Tests:**

- `test_averages_are_direction_adjusted`: a bearish call that worked counts as a gain, and a neutral row doesn't move the average.
- `test_partial_outcomes_are_left_out`: a row with only its 5-bar return is excluded.
- `test_signal_stats_context_is_populated` now expects the labelled keys and a +0.5% average (+2% bullish, −1% bearish loss).
- `test_signal_history_tool_reads_recorded_signals_and_transitions`: a bearish call followed by a 1.2% rise reports underlying +1.2% and signal −1.2%.
- The Signal Explanation panel test requires the new count and label.

**Checked on a copy of the live tables:** daily `get_stats` now reports SMCI −4.98%, NVDA −0.57%, TSLA −0.82%, and PLTR +1.45%, in line with the directional column above.

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

**Status:** ✅ COMPLETE (2026-09-24, batch 2a). The limit is now disclosed, but the metrics still cover only one page (HS-17).
**Where:** dashboard load (`frontend/src/components/SignalResearchDashboard.tsx:93`) and history query (`backend/repositories/signal_repository.py:85`).

The dashboard fetches the newest 1,000 records once, then filters date and timeframe in the browser. The repository supports start/end times but the API does not expose them. CSV export uses that same truncated browser array.

**Impact:** older date ranges can look empty or incomplete with no coverage warning, and exported research is not necessarily the selected population.

**Resolution:** a new server-side research endpoint applies scope, timeframe, start/end dates, complete-outcome policy, limit, and offset before returning records. It includes total count, page range, next-page availability, and resolved scope. The dashboard labels page-only metrics as page metrics and shows loaded-versus-total coverage. CSV export streams the full same scoped query on the server with no hidden row cap rather than exporting the current browser page.

**Tests:** API regressions prove date filtering, selected-watchlist isolation, pagination metadata, and full scoped CSV export. The focused signal API/repository suite passed 46 tests; affected frontend suites and the production build passed.

### HS-07 — All timeframes combines incomparable horizons and hides recorded timeframes

**Status:** ✅ COMPLETE (2026-09-24, batch 2c)
**Where:** selectors in `SignalResearchDashboard.tsx:5`, `HistoricalSignalCard.tsx:8`, and `HistoricalReplayPanel.tsx:7`.

Five bars means five minutes for `1m` and five trading days for `1d`; pooling their returns or wins is not meaningful. The live database holds signals for ten timeframes. The card offers only 1m, 1h, and 1d; Research and Replay offer 1m, 5m, 15m, 1h, and 1d. 2m, 3m, 30m, 4h, and 1wk are hidden everywhere. Together they are about 239,000 of 617,000 rows (39%).

**Impact:** dense intraday rows dominate default research while valid higher-timeframe records are hidden.

**Resolution:**

- **Selectors:** the card, Research, and Replay all build their timeframe lists from `TIMEFRAMES` in `timeframeUtils.ts`, so all ten recorded timeframes are offered everywhere.
- **Research:** the dashboard now opens on Daily. Its "All timeframes (coverage only)" option shows recorded and complete counts per timeframe and a note, with no returns or win rates. The server enforces this: `GET /api/signals/research/summary` returns `performance: null` and a `performance_note` when no timeframe is given.
- **Card:** the regime-performance and regime-count endpoints take a `timeframe`, and the card passes its selected one. Its regime table used to pool every timeframe.

**Tests:**

- `test_research_summary_does_not_pool_returns_across_timeframes`: no timeframe gives coverage only.
- `test_regime_performance_can_be_limited_to_one_timeframe`.
- Dashboard test: it offers all ten timeframes plus the coverage-only view, and hides performance in that view.
- New `HistoricalSignalCard.test.tsx`: it offers all ten timeframes and re-queries regime data for the selected one.

### HS-08 — Regime data is not historical and covers under a fifth of rows

**Status:** ✅ COMPLETE (2026-09-24, batch 2c). This takes the "live snapshot plus coverage" option; point-in-time reconstruction is listed under Enhancements.
**Where:** signal insertion (`backend/services/signal_recorder.py:542`) and regime-performance tables (`frontend/src/components/HistoricalSignalCard.tsx:281`).

Backfilled signals deliberately have no regime because the regime engine knows only the present. A row written within fifteen minutes of its close receives the current regime; older backfilled rows retain `NULL`. Batch 1 removed the card's claim that regimes come from the engine at signal time. The card still shows no regime coverage, so the regime tables read as a complete breakdown.

**Verified (read-only query of the live database):** about 116,000 of 617,000 rows (19%) carry a regime. Only 66 of 16,673 daily rows have one, and none of the 3,457 weekly rows do.

**Impact:** regime research is a partial recent sample with unknown coverage but is presented as complete historical segmentation.

**Resolution:** regime is now presented as a live snapshot, with its coverage shown.

- **Summary endpoint:** returns `regime_coverage`, the number of complete outcomes that carry a regime.
- **Research dashboard:** its table is now "By Regime at Recording". Rows without a regime are grouped as "not recorded" instead of being dropped. A note says why they are missing and gives the share with one.
- **Card:** its table is now "Directional Performance by Regime at Recording (timeframe)". It shows the same note and the "N of M complete outcomes carry a regime" count.

On a copy of the live data, 0 of 16,198 complete daily outcomes carry a regime. The daily regime table therefore now shows a single "not recorded" row, where before it silently used a handful of recent rows.

**Tests:** the summary API test groups 300 regime-less rows as "not recorded" and checks `regime_coverage`. The dashboard and card tests require the coverage note.

### HS-09 — Several stored context fields are placeholders rather than evidence

**Status:** ✅ COMPLETE (2026-09-24, batch 2c)
**Where:** insertion and volume classification (`backend/services/signal_recorder.py:542` and `:587`).

`relative_strength` and `sector_alignment` are stored as `None`; `_classify_volume` always returns `normal`, including zero volume; `strategy_version` is always `v1`; and `data_quality` is always `good`.

**Verified (read-only query of the live database):** all 617,118 rows have `volume_state=normal`, `data_quality=good`, and `strategy_version=v1`. Chat's `get_signal_history_tool` passes `data_quality` to the model with each row.

**Impact:** downstream research and AI context can mistake schema fields for evidence fields.

**Resolution:** the recorder no longer writes values it didn't measure.

- **Empty fields:** `volume_state` and `data_quality` are left empty, and `_classify_volume`, which always returned `normal`, is removed. `relative_strength` and `sector_alignment` were already empty.
- **Version:** `strategy_version` now comes from `settings.trend.strategy_version` (`v1.0`) rather than a hard-coded `v1`. `record_signal` no longer defaults to `v1`/`good`.
- **Provenance:** `confidence_inputs` now records each bar's `provider` and `data_status`. The recording queries load those two columns.
- **Chat:** `get_signal_history` no longer passes `data_quality` to the model.

**Stored rows:** migration `20261002_signal_placeholder_fields` sets the stored `normal`/`good` constants to NULL. Its downgrade restores the constants on every row, which is the state the old recorder produced.

It was first validated on a copy of the signals table, run from a scratch Alembic tree so that `--reload` could not apply it. Upgrade, downgrade, and upgrade again all gave the expected counts, the run took under 2 s, and `conf/token.txt` was untouched.

With approval, a `.backup` of the live database was taken and the migration was installed. The dev server's reload applied it within seconds. The live database is now at `20261002_signal_placeholder_fields`, and all 618,476 signals have empty `volume_state` and `data_quality`. `/api/health` returned 200 afterwards.

**Tests:** `test_backfill_signals_for_symbol_bulk_inserts_all` now also requires empty `volume_state` and `data_quality`, the settings version, and the bar's provider and data status in `confidence_inputs`.

### HS-10 — Replay simulates neutral or unknown signals as long trades and session scope drifts

**Status:** ✅ COMPLETE (2026-09-24, batch 2c). Batch 1 had already limited the replay statistics to complete bullish and bearish outcomes.
**Where:** replay filters and simulation (`frontend/src/components/HistoricalReplayPanel.tsx:158` and `:191`).

Only a state matching bearish/down/sell becomes short. Neutral, warm-up `NULL`, and unknown values become long simulated trades. The session filter applies to candles, but replay signal statistics use date filtering only. On the live database, 46% of rows are neutral and 24,999 have no trend state.

**Impact:** simulated net can include trades the engine never called for, while chart and statistics cover different session populations.

**Resolution:**

- **Trades:** only explicit `bullish` and `bearish` calls are simulated. A bearish call goes short, matched on the exact state rather than the old `bear|down|sell` pattern. Neutral and warm-up rows are not traded, and the "Simulated trades" count is out of directional calls only.
- **One scope:** a signal is recorded for the bar it closed on. The panel therefore keeps only signals whose bar is in the replay, so date and session filters apply to bars, signals, markers, statistics, and the "Signal at this point" lookup alike.
- **Markers:** these now match the exact bar rather than the first signal of the same day. They are coloured by the direction-adjusted 5-bar outcome, not the raw move, which fixes a leftover from HS-03.
- **Assumptions:** a note under the simulated trades states them. Entry is at the signal bar's close; exit is when a later bar reaches the stop or target; the stop is assumed first when one bar reaches both; there is no commission or slippage.

**Tests:** a new replay test has a neutral call and a premarket signal. With all sessions it shows three signals and "Simulated trades 1 / 2". With the regular session, the premarket bar and its signal drop out together, giving two signals and "0 / 1".

---

## Medium

### HS-11 — Manual recording accepts ignored client scope and mutations lack server guardrails

**Status:** ✅ COMPLETE (2026-09-24, batch 3)
**Where:** client request (`frontend/src/services/api.ts:2146`) and record/backfill routes (`backend/api/signals/router.py:209` and `:223`).

The client accepts symbols/timeframes and sends JSON. The route declares query parameters, ignores that body, and records all active ingestion symbols/timeframes when no query parameters are present. Bulk record and backfill have no server-side confirmation, idempotency key, estimated work, audit record, or rate limit.

**Impact:** a caller can believe it requested a narrow operation while triggering a broad one; repeated clicks can compete with the ingestion loop.

**Resolution:**

- **Request body:** bulk `POST /api/signals/record` now reads a typed body, `RecordSignalsRequest` (`symbols`, `confirm`). Unknown fields such as `timeframes` are rejected with 422 instead of ignored. Requested symbols must be ingested, otherwise the response is a 422 naming the ones that aren't.
- **Confirmation:** without `confirm: true` the route writes nothing. It returns a preview of the resolved symbols and the number of (symbol, timeframe) pairs with bars. The card shows that in a confirmation dialog, then repeats the call with `confirm: true`.
- **Guardrails:** manual record and `POST /api/signals/backfill` share a lock, so a second click while one is running gets 409 instead of stacking work beside the ingestion loop. Each run writes an audit line to the log with its scope and result. Idempotency comes from the data itself: recording skips bars already stored (and HS-12 makes that a database guarantee), and backfill only advances outcomes.
- **Card:** the card now sets its status line after reloading. Before, the reload cleared the "Recorded N" and "Backfilled N" messages as soon as they appeared.

The single-record mode (query parameters) is unchanged.

**Tests:**

- `test_record_now_previews_then_records_ingested_symbols`: the preview writes nothing, then confirmation records.
- `test_record_now_honours_the_requested_symbols`: the body's symbols are used; an unknown symbol and an unknown field are both rejected.
- `test_manual_bulk_runs_do_not_overlap`: 409 for both routes while the lock is held.
- Card tests: confirming records; declining writes nothing.

### HS-12 — No database uniqueness constraint protects signal identity

**Status:** ✅ COMPLETE (2026-09-24, batch 3)
**Where:** `HistoricalSignal` (`backend/models/signal.py:26`) and initial schema (`alembic/versions/20260828_8175af1a213e_initial_schema.py:159`).

The recorder checks `(symbol, timeframe, timestamp)` before insert, but the table has no matching unique constraint. Concurrent ingestion, manual recording, retry, or two processes can pass that read-before-write check together.

**Verified (read-only query of the live database):** 1,879 `(symbol, timeframe, timestamp)` groups have two rows each, which is 1,879 extra rows. In every group the two rows agree on trend state, so deduplication can keep either row. The duplicates also inflate the signal count that startup gap-fill (`_fill_signal_gaps`) compares with the bar count, which can hide real gaps.

**Impact:** duplicate rows bias counts, averages, win rates, exports, and replay results.

**Resolution:**

- **Migration:** `20261003_signal_identity_unique` removes the extra rows and adds the unique index `uq_historical_signals_symbol_timeframe_timestamp`. From each group it keeps the most complete row: most outcomes, then a regime, then the lowest id. It logs how many rows it removed per timeframe. It also drops `ix_historical_signals_symbol_timeframe_timestamp`, a non-unique index on the same columns that existed on the live database outside the migration history. The model declares the same unique index.
- **Conflict handling:** the recorder's bulk insert (`_insert_rows`) and `bulk_create` go through `SignalRepository.insert_ignoring_duplicates` (`INSERT … ON CONFLICT DO NOTHING`), so a bar another writer stored first is skipped. `record_signal` treats the `IntegrityError` from a lost race as a duplicate.

**Applied to the live database:**

- It was validated first from a scratch Alembic tree: its migration test passed there, and on a copy of the live signals it removed 1,879 rows in 1.9 s (5m 1,246, 1m 615, 30m 8, 2m 7, 3m 2, 15m 1).
- After a `.backup`, it was installed and the reload applied it. The live table went from 618,820 to 616,941 rows with no duplicate groups left.

**Deployment incident:** the recorder's `ON CONFLICT` code reached the dev server through `--reload` before the migration did. SQLite rejects `ON CONFLICT` without a matching unique index, so signal inserts failed for about two minutes (16:44:58 to 16:45:00 ET, 52 errors) until the migration was applied at 16:46. Bars from that window are written as the restarted recorder re-seeds each pair, which records any closed bar without a row. Seeding runs about 3 s per 90 s cycle, cheapest timeframes first, so it catches up over roughly half an hour. At 16:51, AAPL 1m was already complete, and 89 one-minute bars across 15 other symbols were still waiting. With `--reload`, install a migration before the code that depends on it.

**Tests:**

- `backend/tests/migrations/test_signal_identity_migration.py`: seeds duplicate groups and the old index, then checks the rows kept (2, 4, 5, 8), the audit log line, rejection of a new duplicate, and downgrade.
- `test_bulk_create_skips_rows_that_already_exist` and `test_database_rejects_a_duplicate_signal`.
- `test_insert_rows_race_with_another_writer_is_a_no_op`.

### HS-13 — A valid zero regime average is returned as unavailable

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `SignalRepository.get_performance_by_regime` (`backend/repositories/signal_repository.py:225`).

The serializer checks truthiness rather than `is not None`. An exact 0.0% average becomes `null` and renders as an em dash instead of a valid flat result.

**Resolution:** every average in the serializer now uses `is not None`. The card's `fmtPct` already treats only `null` as unavailable, so 0.00% renders as a flat result.

**Tests:** `test_get_performance_by_regime_preserves_a_zero_average` offsets a +1% bullish call against the same raw move on a bearish call and requires `0.0`, not `None`. There is no API- or UI-level zero test; the repository test covers the serializer, where the bug was.

### HS-14 — Regression coverage preserves faulty semantics and misses lifecycle transitions

**Status:** ✅ COMPLETE (2026-09-24, batches 1 to 3)
**Where:** partial-fill test (`backend/tests/services/test_signal_recorder.py:219`), research test (`frontend/src/components/SignalResearchDashboard.test.tsx:8`), and replay test (`frontend/src/components/HistoricalReplayPanel.test.tsx:8`).

The partial-fill test said a later backfill completes remaining outcomes but did not run that later pass. The research test expected a cumulative raw result from bullish and bearish calls, encoding the directionality problem. There was no card-specific test or coverage for deletion parameters, multi-watchlist scope, date coverage, neutral replay calls, session alignment, or duplicate inserts.

**Done:**

- the partial-fill test now runs the later pass (HS-01);
- the research test requires direction-adjusted results and no equity curve (HS-03, HS-04);
- deletion requires confirmation (HS-02);
- multi-watchlist union, selected-watchlist isolation, date filtering, pagination, and full export (HS-05, HS-06);
- a zero regime average (HS-13).

**Also covered since the review:**

- ~~a `HistoricalSignalCard` test~~: added in batch 2c;
- ~~neutral replay calls and session alignment (HS-10)~~: covered in batch 2c;
- ~~duplicate inserts (HS-12)~~: covered in batch 3;
- ~~queue progress past unfinishable rows (HS-15)~~: covered in batch 2b;
- ~~direction-adjusted AI stats (HS-16)~~: covered in batch 2b.

**Resolution:** add focused unit, API, and UI regressions for each open finding before changing behaviour; retain them as the Historical Signals release gate.

### HS-17 — Research metrics describe one 250-row page, not the selected population

**Status:** ✅ COMPLETE (2026-09-24, batch 2c)
**Where:** the research query (`frontend/src/components/SignalResearchDashboard.tsx:79`, `limit: 250`) and the metrics computed from the loaded page (`metricRows` and the summary figures).

HS-06 moved filtering to the server and labels the metrics as page metrics. But the win rate, the averages, and every group table are still computed in the browser from the current page: the newest 250 complete records. Before batch 2a, they were computed from 1,000. With the default “All timeframes”, the newest records are mostly 1m, 2m, and 3m rows. Moving to the next page changes every metric.

**Impact:** the numbers no longer mislead about their coverage, but they don't answer the question the filters ask. A trader filtering a year of daily signals sees results for the newest 250.

**Resolution:** a new endpoint, `GET /api/signals/research/summary`, computes everything over the whole filtered population in SQL (`SignalRepository.research_summary`). It uses the same scope, timeframe, and date filters as the export. It returns:

- recorded and complete counts;
- coverage per timeframe;
- regime coverage;
- for one timeframe, directional counts, win rate, and direction-adjusted 5- and 10-bar averages, overall and by regime and trend state.

The dashboard now renders only this summary, so paging is gone, and its coverage line says the metrics cover every matching record. The `/research/signals` page endpoint remains for API callers.

**Tests:** `test_research_summary_covers_every_record_not_one_page` seeds 304 rows, more than one old page. It checks the totals, the direction-adjusted average across all of them, the regime and trend groups, and that a partial row is excluded. The dashboard test requires the full-population coverage line and no paging controls.

**Checked on a copy of the live data:** the summary took 0.68 s for all 618,314 rows with no timeframe, 0.85 s for 1m (248,430 rows), and 0.04 s for 1d.

### HS-18 — “Delete >180d” is undone at restart and ignores the selected scope

**Status:** ✅ COMPLETE (2026-09-24, batch 3)
**Where:** `HistoricalSignalCard.handleCleanup` (`frontend/src/components/HistoricalSignalCard.tsx:154`), `DELETE /api/signals/old` (`backend/api/signals/router.py:496`), `prune_signals_by_retention` (`backend/repositories/signal_repository.py:415`), and `_fill_signal_gaps` (`backend/api/main_helpers.py:69`).

Automatic retention already prunes signals per timeframe:

- 1m to 30m after 16 days;
- 1h and 4h after 366 days;
- 1d and 1wk after 1,096 days.

A 180-day manual delete therefore reaches only 1h, 4h, 1d, and 1wk signals. The bars for those timeframes are kept longer. On every startup, `_fill_signal_gaps` rebuilds signals for any watched symbol and timeframe whose bars outnumber its signals. The deleted rows come back without the regime they had, because backfilled rows get none, and their outcomes are recomputed. The delete also covers every symbol, although the card now shows a scope selector next to the button.

**Impact:** the destructive action only lasts until the next restart. Its lasting effects are stripped regime labels and a large re-backfill. A trader may also believe it applies only to the selected scope.

**Resolution:** the manual delete is removed, since automatic retention already bounds storage. That means the card's button, `api.deleteOldSignals`, `DELETE /api/signals/old`, and `SignalRepository.delete_older_than`. The card now says signals are pruned automatically with their bars, on each timeframe's retention window. The test for a deleted range staying deleted after gap-fill no longer applies, because there is no manual delete to undo.

**Tests:** `test_manual_delete_route_is_gone` checks that the route no longer deletes anything. The card test checks there is no Delete control.

---

## Gaps (not bugs)

- **Outcome throughput:** the outcome loop handles at most 1,000 rows every 300 s, about 12,000 an hour. After HS-15, about 155,000 pending rows can advance, so clearing the backlog takes roughly 13 hours of ingestion. `POST /api/signals/backfill` can drain it faster by hand.
- **Scope definitions differ:** startup gap-fill's `_watched_symbols` takes every enabled symbol, whether or not its watchlist is active. The research `all_active` scope requires an active watchlist. Signals are therefore recorded for symbols that the default research scope excludes.
- **Export memory:** the server streams the CSV, but the client reads it whole with `fetchRaw` before downloading. An `all_stored` export of about 617,000 rows is held in browser memory.
- **Test coverage:** the gaps still open are listed in HS-14. HS-15 wasn't caught because no batch 1 or 2a regression filled a batch with rows that could not finish; batch 2b added one.

## Enhancements

1. **Explicit outcome state:** store `pending`, `complete`, or `abandoned` with a reason, rather than inferring state from null columns in several places (repository, API, frontend helper, AI stats, chat tool).
2. **Horizon labels:** show what five bars means for the selected timeframe, for example “5 bars = 25 minutes” on 5m.
3. **Sample-size cues:** show counts and a confidence interval next to each win rate and average, and flag groups too small to trust.
4. **Point-in-time regime:** rebuild each signal's regime from stored benchmark bars, so backfilled rows get one and regime research covers all of history. Batch 2c shows coverage instead (HS-08).
5. **Volume baseline:** if volume context is wanted, compute a causal relative-volume state (bar volume against a trailing, time-of-day-aware baseline) with a version tag. Batch 2c leaves `volume_state` empty (HS-09).
6. **Replay costs:** add optional commission and slippage to the simulated trades; the replay currently assumes none.

## Verification

### Batch 3 (2026-09-24)

| Suite | Result |
|---|---|
| Signals API, all repository tests, `backend/tests/services`, migrations, AI market tools, analysis, chat, scanner API, multi-timeframe | 617 passed, 17 subtests passed |
| Every backend test file that touches historical signals (15 files) | 370 passed, 5 subtests passed |
| `HistoricalSignalCard`, `SignalResearchDashboard`, `HistoricalReplayPanel` suites | 7 passed |
| TypeScript (`tsc --noEmit`) and `ruff` on the changed files | clean |
| HS-12 migration test, run from a scratch Alembic tree before installing | passed |
| HS-12 migration on a copy of the live signals | 1,879 duplicates removed; 1.9 s |
| HS-12 migration on the live database (after a `.backup`) | applied; 616,941 rows, no duplicate groups; recording resumed with no errors |

The full backend and frontend suites and the production build were not run.

### Batch 2c (2026-09-24)

| Suite | Result |
|---|---|
| Signals API, all repository tests, `backend/tests/services`, AI market tools, analysis, chat, scanner API, multi-timeframe | 609 passed, 17 subtests passed |
| `SignalResearchDashboard`, `HistoricalReplayPanel`, `HistoricalSignalCard` (new), `SignalExplanationPanel` suites | 6 passed |
| TypeScript (`tsc --noEmit`) and `ruff` on the changed files | clean |
| HS-09 migration on a copy of the signals table (scratch Alembic tree) | upgrade, downgrade, and upgrade again correct; under 2 s |
| HS-09 migration on the live database (after a `.backup`) | applied by the reload; 618,476 rows cleared; `/api/health` 200 |

**Summary endpoint on a copy of the live signals:** 0.68 s for 618,314 rows with no timeframe, 0.85 s for 1m, and 0.04 s for 1d.

The full backend and frontend suites and the production build were not run.

### Batch 2b (2026-09-24)

| Suite | Result |
|---|---|
| Signals API, all repository tests, signal recorder and replay, AI market tools, analysis, chat, scanner API | 558 passed, 17 subtests passed |
| `SignalExplanationPanel`, `SignalResearchDashboard`, `HistoricalReplayPanel` suites | 3 passed |
| TypeScript (`tsc --noEmit`) and `ruff` on the changed files | clean |

**Mutation check:**

- Restoring the old oldest-first queue fails five tests: the three new repository queue tests and both recorder tests above.
- Restoring raw averages in `get_stats` fails three stats tests.

**Probe on a copy of the live tables:** `signals` and `bars` were copied into a scratch database. The offline test guard refuses the live file, even read-only. On that copy, the queue selected 1,000 candidates in 1.7 s, and the direction-adjusted stats matched the HS-16 table.

The full backend and frontend suites and the production build were not run.

### Review of batches 1 and 2a (2026-09-24, at `8f6a803`)

| Suite | Result |
|---|---|
| `backend/tests/api/test_signals_api.py`, `backend/tests/repositories/test_signal_repository.py`, `backend/tests/services/test_signal_recorder.py` | 64 passed |
| `SignalResearchDashboard.test.tsx`, `HistoricalReplayPanel.test.tsx` | 2 suites, 2 tests passed |

The full backend and frontend suites and the production build were not run for this review.

**Live database checks (read-only, `mode=ro`):**

| Check | Result | Finding |
|---|---|---|
| Rows missing any outcome | 156,940 of 617,118; 146,669 partial | HS-15 |
| Oldest 1,000 pending rows with 20 later bars | 0 | HS-15 |
| TSLA 5m signal at 2026-09-14 18:20 | 787 later bars, still pending | HS-15 |
| Raw vs directional 5-bar averages, daily | Sign flips for SMCI, NVDA, TSLA | HS-16 |
| Timeframes recorded | 10; 2m, 3m, 30m, 4h, 1wk hidden in every selector | HS-07 |
| Rows with a regime | about 19%; daily 66 of 16,673; weekly 0 | HS-08 |
| `volume_state` / `data_quality` / `strategy_version` | `normal` / `good` / `v1` on every row | HS-09 |
| Trend state | 46% neutral; 24,999 null | HS-10 |
| Duplicate `(symbol, timeframe, timestamp)` groups | 1,879, none with conflicting trend states | HS-12 |

### Batch 2a (2026-09-24)

| Suite | Result |
|---|---|
| Focused signal API and repository tests | 46 passed |
| Affected frontend suites and production build | passed |

### Batch 1 (2026-09-24)

| Suite | Result |
|---|---|
| Focused recorder, repository, and signals API tests | 61 passed |
| Production frontend build | passed |

## Fix log

- **Review and batch 1:** commit `8eb7ac9`, `fix(signals): correct historical outcome research`.
- **Batch 2a:** commit `f762f8a`, `fix(signals): make research scope and coverage explicit`.
- **Review of batches 1 and 2a:** commit `59e269a`, `docs(v5): review historical signals batches 1 and 2a`.
- **Batch 2b:** commit `b430e06`, `fix(signals): unblock outcome queue and direction-adjust track record`.
- **Batch 3:** in the working tree, not yet committed. The HS-12 migration is installed and applied to the live database.
- **Batch 2c:** commit `2e46ecc`, `fix(signals): per-timeframe research, regime coverage, honest fields`. The HS-09 migration is applied to the live database.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | HS-01 to HS-14 | ❌ NOT STARTED | `8eb7ac9` | `docs/Version_5/v5_historical_signal_bug_fixes.md` | — | Review logged 14 findings. |
| 2026-09-24 | HS-01 | ✅ COMPLETE | `8eb7ac9` | `signal_repository.py`, `signal_recorder.py`, `test_signal_recorder.py`, `signalOutcomes.ts`, card, dashboard, replay | later-pass test | Partial rows stay queued; excursions use the full 20-bar window. |
| 2026-09-24 | HS-02 | ✅ COMPLETE | `8eb7ac9` | `router.py`, `api.ts`, `test_signals_api.py` | 1 new, 1 updated | `older_than_days` used end to end; `confirm=true` required. |
| 2026-09-24 | HS-03 | ✅ COMPLETE | `8eb7ac9` | `signalOutcomes.ts`, `signal_repository.py`, card, dashboard, replay, tests | API, repository, and UI | Direction-adjusted outcomes; raw fields named as underlying movement. |
| 2026-09-24 | HS-04 | ✅ COMPLETE | `8eb7ac9` | `SignalResearchDashboard.tsx`, test | UI | Equity curve removed; methodology notice added. |
| 2026-09-24 | HS-13 | ✅ COMPLETE | `8eb7ac9` | `signal_repository.py`, `test_signal_repository.py` | 1 test | Zero averages preserved. Recorded as complete in this review. |
| 2026-09-24 | HS-05 | ✅ COMPLETE | `f762f8a` | `router.py`, `api.ts`, card, dashboard, tests | API and UI | Explicit scope; active watchlists unioned. |
| 2026-09-24 | HS-06 | ✅ COMPLETE | `f762f8a` | `router.py`, `signal_repository.py`, `api.ts`, dashboard, tests | API and UI | Server-side research page and full scoped export. |
| 2026-09-24 | HS-14 | ⚠️ PARTIAL | `8eb7ac9`, `f762f8a` | tests | see entry | Several regressions added; card, replay, duplicate, queue, and AI coverage remain. |
| 2026-09-24 | HS-15 to HS-18 | ❌ NOT STARTED | `59e269a` | `docs/Version_5/v5_historical_signal_bug_fixes.md` | 9 live-database checks | Review of batches 1 and 2a logged four findings and refreshed HS-07 to HS-10 and HS-12 evidence. |
| 2026-09-24 | HS-15 | ✅ COMPLETE | `b430e06` | `signal_repository.py`, `signal_recorder.py`, `test_signal_repository.py`, `test_signal_recorder.py` | 4 new, 3 updated | Queue selects only rows that stored bars can advance. |
| 2026-09-24 | HS-16 | ✅ COMPLETE | `b430e06` | `signal_repository.py`, `router.py`, `context.py`, `market_tools.py`, `SignalExplanationPanel.tsx`, `api.ts`, tests | 2 new, 4 updated | One directional rule; AI, chat, and Scanner stats direction-adjusted and complete-only. |
| 2026-09-24 | HS-07 | ✅ COMPLETE | `2e46ecc` | `SignalResearchDashboard.tsx`, `HistoricalSignalCard.tsx`, `HistoricalReplayPanel.tsx`, `router.py`, `signal_repository.py`, `api.ts`, tests | 2 API, 3 UI | Every selector uses `TIMEFRAMES`; no returns pooled across timeframes. |
| 2026-09-24 | HS-08 | ✅ COMPLETE | `2e46ecc` | `signal_repository.py`, `router.py`, dashboard, card, tests | API + UI | Regime shown as a live snapshot with coverage and a "not recorded" group. |
| 2026-09-24 | HS-09 | ✅ COMPLETE | `2e46ecc` | `signal_recorder.py`, `market_tools.py`, `alembic/versions/20261002_signal_placeholder_fields.py`, `test_signal_recorder.py` | 1 updated + migration run | New rows carry no placeholders; stored placeholders cleared on the live database. |
| 2026-09-24 | HS-10 | ✅ COMPLETE | `2e46ecc` | `HistoricalReplayPanel.tsx`, test | 1 UI | Directional trades only; one session scope; assumptions shown. |
| 2026-09-24 | HS-17 | ✅ COMPLETE | `2e46ecc` | `signal_repository.py`, `router.py`, `api.ts`, `SignalResearchDashboard.tsx`, tests | 1 API, 2 UI | Server-side summary over the full filtered population. |
| 2026-09-24 | HS-11 | ✅ COMPLETE | batch 3 | `router.py`, `api.ts`, `HistoricalSignalCard.tsx`, tests | 3 API, 2 UI | Typed body, preview then confirm, one manual run at a time. |
| 2026-09-24 | HS-12 | ✅ COMPLETE | batch 3 | `alembic/versions/20261003_signal_identity_unique.py`, `signal.py`, `signal_repository.py`, `signal_recorder.py`, tests | migration test + 3 | Duplicates removed on the live database; unique index; conflicts are no-ops. |
| 2026-09-24 | HS-18 | ✅ COMPLETE | batch 3 | `router.py`, `signal_repository.py`, `api.ts`, `HistoricalSignalCard.tsx`, tests | 1 API, 1 UI | Manual delete removed; retention owns pruning. |
| 2026-09-24 | HS-14 | ✅ COMPLETE | batches 1–3 | tests | see entry | Every finding now has a regression. |

---

## Reference

Historical Signals records a causal trend label for each closed stored bar. `BarReplay` feeds bars oldest-first through a private trend engine so future bars cannot change a prior label. Later backfill records raw 5/10/20-bar price movement plus forward high/low excursions. The card shows individual records and regime aggregates, the research dashboard groups outcomes, and replay steps through bars with a simplified stop/target simulation. AI Analysis reads a per-symbol summary of these rows as the engine's track record, and Chat can read the rows directly.

That causal-label foundation is valuable. The fixes above are required to make outcome maturity, scope, directionality, methodology, provenance, and destructive controls equally trustworthy for trader-facing research.
