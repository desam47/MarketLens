# Version 5 Historical Signals Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (review of batches 1 and 2a: HS-15 to HS-18 added; HS-13 and HS-14 status corrected)
**Status:** Batch 1 and batch 2a are complete. A review of them found four new problems, two of them Critical. HS-15 keeps HS-01's fix from taking effect on the live database, and HS-16 is a path HS-03 missed. Eleven findings remain open.
**Scorecard:** 7 ✅ COMPLETE, 1 ⚠️ PARTIAL, 10 ❌ NOT STARTED, 0 🟡 DEFERRED.
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
| HS-15 | Critical | Backend | The outcome queue stalls behind rows that cannot finish | Verified | ❌ NOT STARTED |
| HS-16 | Critical | AI | AI Analysis reads raw price movement as the signals' track record | Verified | ❌ NOT STARTED |
| HS-05 | High | Scope | Default scope silently uses only the first active watchlist | Verified | ✅ COMPLETE |
| HS-06 | High | Research | Date filters and exports are silently limited to the newest 1,000 rows | Verified | ✅ COMPLETE |
| HS-07 | High | Methodology | All timeframes combines incomparable horizons and hides recorded timeframes | Verified | ❌ NOT STARTED |
| HS-08 | High | Provenance | Regime data is not historical and covers under a fifth of rows | Verified | ❌ NOT STARTED |
| HS-09 | High | Data quality | Several stored context fields are placeholders rather than evidence | Verified | ❌ NOT STARTED |
| HS-10 | High | Replay | Neutral or unknown signals are simulated as long trades; session scope drifts | Code-read | ❌ NOT STARTED |
| HS-11 | Medium | API safety | Manual recording accepts ignored client scope and mutations lack server guardrails | Code-read | ❌ NOT STARTED |
| HS-12 | Medium | Storage | No database uniqueness constraint protects signal identity | Verified | ❌ NOT STARTED |
| HS-13 | Medium | API + presentation | A valid zero regime average is returned as unavailable | Code-read | ✅ COMPLETE |
| HS-14 | Medium | Tests | Regression coverage preserves faulty semantics and misses lifecycle transitions | Code-read | ⚠️ PARTIAL |
| HS-17 | Medium | Research | Research metrics describe one 250-row page, not the selected population | Code-read | ❌ NOT STARTED |
| HS-18 | Medium | API + UI | “Delete >180d” is undone at restart and ignores the selected scope | Code-read | ❌ NOT STARTED |

**Next suggested order:**

1. ~~**Batch 1 — decision-data correctness:** HS-01, HS-02, HS-03, HS-04, and HS-13.~~ Done.
2. ~~**Batch 2a — truthful scope:** HS-05 and HS-06.~~ Done.
3. **Batch 2b — outcomes and AI correctness:** HS-15 and HS-16. Do these first: HS-15 keeps outcomes from maturing on the live database, and HS-16 feeds the model inverted track-record numbers.
4. **Batch 2c — methodology:** HS-07, HS-08, HS-09, HS-10, and HS-17.
5. **Batch 3 — operational integrity and regressions:** HS-11, HS-12, HS-18, then the rest of HS-14 as the release gate.

---

## Critical

### HS-01 — Partial forward outcomes can remain incomplete forever

**Status:** ✅ COMPLETE (2026-09-24, batch 1). See HS-15: the fix is correct row by row, but on the live database the queue stalls before most rows are reached.
**Where:** `SignalRecorder._compute_outcome_for_signal` (`backend/services/signal_recorder.py:617`) and `SignalRepository.get_signals_needing_outcomes` (`backend/repositories/signal_repository.py:146`).

The recorder intentionally calculates the outcome windows currently available. With five future bars it writes 5b, and with ten it writes 5b and 10b. Its comment says later backfills will fill remaining fields.

That cannot happen: the repository selects only rows where `return_5b IS NULL`. Once 5b exists, the row leaves the queue even if 10b and 20b are still null. The stored completion flag is also set false-to-complete on that partial write.

**Impact:** a row can look completed while its later returns permanently remain absent and MFE/MAE reflect a shorter, unstated observation window.

**Resolution:** the outcome queue now keeps any row with a missing return or excursion eligible. MFE/MAE are calculated only across the completed 20-bar window, and `_outcome_missing` remains true until all five values are present. The API's “completed only” filter and the frontend's completed counts use the same full-outcome contract. The AI stats and the chat tool still use the old 5-bar contract (HS-16).

**Tests:** `test_backfill_outcomes_partial_row_matures_on_later_pass` proves one row progresses from 5/10 bars to a complete 20-bar outcome on a later pass. The focused recorder, repository, and signals API suite passed: 61 tests.

### HS-02 — “Delete >180d” deletes rows older than 30 days

**Status:** ✅ COMPLETE (2026-09-24, batch 1). See HS-18 for what the corrected delete still does wrong.
**Where:** `api.deleteOldSignals` (`frontend/src/services/api.ts:2156`), `HistoricalSignalCard.handleCleanup` (`frontend/src/components/HistoricalSignalCard.tsx:140`), and `delete_old_signals` (`backend/api/signals/router.py:301`).

The visible destructive action confirms deletion beyond 180 days and calls `deleteOldSignals(180)`. The client sends `days=180`; the server accepts only `older_than_days` and falls back to 30 days.

**Impact:** confirming a six-month retention action deletes everything older than one month, with no recovery path in the product.

**Resolution:** the client and API now use `older_than_days` consistently. The destructive route rejects a request unless `confirm=true` is explicit; the existing browser confirmation remains the user-facing confirmation step. The client always sends `confirm=true`, so the server check protects only against other callers.

**Tests:** API coverage now proves a confirmed 180-day deletion works and an otherwise identical request without confirmation is rejected without deleting data.

### HS-03 — Raw underlying returns are presented as signal P&L

**Status:** ✅ COMPLETE (2026-09-24, batch 1). The screens and regime aggregate are fixed; the AI path is not (HS-16).
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

**Status:** ❌ NOT STARTED
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

**Resolution:** pick candidates that can make progress. For example, record when each row was last checked and order by that, or skip rows whose symbol and timeframe have no bar newer than the last check. Mark rows that can no longer mature, such as a symbol that stopped updating, as abandoned with a reason instead of retrying them forever. Add a regression test with more than one batch of unfinishable older rows and one finishable newer row.

### HS-16 — AI Analysis reads raw price movement as the signals' track record

**Status:** ❌ NOT STARTED
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

**Resolution:** return direction-adjusted averages that use the same rule as the regime aggregate. Exclude neutral and unknown rows, count only complete outcomes, and include the sample size. In the chat tool, label raw fields as underlying movement and apply the full-outcome rule. Share one backend helper with the export's `_directional_value` so the rule is defined once.

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

**Status:** ❌ NOT STARTED
**Where:** selectors in `SignalResearchDashboard.tsx:5`, `HistoricalSignalCard.tsx:8`, and `HistoricalReplayPanel.tsx:7`.

Five bars means five minutes for `1m` and five trading days for `1d`; pooling their returns or wins is not meaningful. The live database holds signals for ten timeframes. The card offers only 1m, 1h, and 1d; Research and Replay offer 1m, 5m, 15m, 1h, and 1d. 2m, 3m, 30m, 4h, and 1wk are hidden everywhere. Together they are about 239,000 of 617,000 rows (39%).

**Impact:** dense intraday rows dominate default research while valid higher-timeframe records are hidden.

**Resolution:** require one timeframe for all performance and backtest metrics. An all-timeframe view may show separated coverage/counts but must not aggregate returns. Derive every selector from one supported-timeframe source.

### HS-08 — Regime data is not historical and covers under a fifth of rows

**Status:** ❌ NOT STARTED
**Where:** signal insertion (`backend/services/signal_recorder.py:542`) and regime-performance tables (`frontend/src/components/HistoricalSignalCard.tsx:281`).

Backfilled signals deliberately have no regime because the regime engine knows only the present. A row written within fifteen minutes of its close receives the current regime; older backfilled rows retain `NULL`. Batch 1 removed the card's claim that regimes come from the engine at signal time. The card still shows no regime coverage, so the regime tables read as a complete breakdown.

**Verified (read-only query of the live database):** about 116,000 of 617,000 rows (19%) carry a regime. Only 66 of 16,673 daily rows have one, and none of the 3,457 weekly rows do.

**Impact:** regime research is a partial recent sample with unknown coverage but is presented as complete historical segmentation.

**Resolution:** reconstruct and persist point-in-time regime from stored benchmark bars, or call this a live regime snapshot, omit it from historical performance, and show regime coverage.

### HS-09 — Several stored context fields are placeholders rather than evidence

**Status:** ❌ NOT STARTED
**Where:** insertion and volume classification (`backend/services/signal_recorder.py:542` and `:587`).

`relative_strength` and `sector_alignment` are stored as `None`; `_classify_volume` always returns `normal`, including zero volume; `strategy_version` is always `v1`; and `data_quality` is always `good`.

**Verified (read-only query of the live database):** all 617,118 rows have `volume_state=normal`, `data_quality=good`, and `strategy_version=v1`. Chat's `get_signal_history_tool` passes `data_quality` to the model with each row.

**Impact:** downstream research and AI context can mistake schema fields for evidence fields.

**Resolution:** calculate each field causally with versioned inputs, or leave it unavailable and expose a coverage/data-quality reason. Store configuration and provider/bar provenance.

### HS-10 — Replay simulates neutral or unknown signals as long trades and session scope drifts

**Status:** ❌ NOT STARTED. Batch 1 limited the replay statistics to complete bullish and bearish outcomes (`HistoricalReplayPanel.tsx:173`). The simulated trades are unchanged.
**Where:** replay filters and simulation (`frontend/src/components/HistoricalReplayPanel.tsx:158` and `:191`).

Only a state matching bearish/down/sell becomes short. Neutral, warm-up `NULL`, and unknown values become long simulated trades. The session filter applies to candles, but replay signal statistics use date filtering only. On the live database, 46% of rows are neutral and 24,999 have no trend state.

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

**Verified (read-only query of the live database):** 1,879 `(symbol, timeframe, timestamp)` groups have two rows each, which is 1,879 extra rows. In every group the two rows agree on trend state, so deduplication can keep either row. The duplicates also inflate the signal count that startup gap-fill (`_fill_signal_gaps`) compares with the bar count, which can hide real gaps.

**Impact:** duplicate rows bias counts, averages, win rates, exports, and replay results.

**Resolution:** add a unique index, deduplicate existing rows with an audited migration, and treat uniqueness conflicts as safe no-ops.

### HS-13 — A valid zero regime average is returned as unavailable

**Status:** ✅ COMPLETE (2026-09-24, batch 1)
**Where:** `SignalRepository.get_performance_by_regime` (`backend/repositories/signal_repository.py:225`).

The serializer checks truthiness rather than `is not None`. An exact 0.0% average becomes `null` and renders as an em dash instead of a valid flat result.

**Resolution:** every average in the serializer now uses `is not None`. The card's `fmtPct` already treats only `null` as unavailable, so 0.00% renders as a flat result.

**Tests:** `test_get_performance_by_regime_preserves_a_zero_average` offsets a +1% bullish call against the same raw move on a bearish call and requires `0.0`, not `None`. There is no API- or UI-level zero test; the repository test covers the serializer, where the bug was.

### HS-14 — Regression coverage preserves faulty semantics and misses lifecycle transitions

**Status:** ⚠️ PARTIAL (2026-09-24, batches 1 and 2a)
**Where:** partial-fill test (`backend/tests/services/test_signal_recorder.py:219`), research test (`frontend/src/components/SignalResearchDashboard.test.tsx:8`), and replay test (`frontend/src/components/HistoricalReplayPanel.test.tsx:8`).

The partial-fill test said a later backfill completes remaining outcomes but did not run that later pass. The research test expected a cumulative raw result from bullish and bearish calls, encoding the directionality problem. There was no card-specific test or coverage for deletion parameters, multi-watchlist scope, date coverage, neutral replay calls, session alignment, or duplicate inserts.

**Done:**

- the partial-fill test now runs the later pass (HS-01);
- the research test requires direction-adjusted results and no equity curve (HS-03, HS-04);
- deletion requires confirmation (HS-02);
- multi-watchlist union, selected-watchlist isolation, date filtering, pagination, and full export (HS-05, HS-06);
- a zero regime average (HS-13).

**Remaining:**

- a `HistoricalSignalCard` test (none exists);
- neutral replay calls and session alignment (HS-10);
- duplicate inserts (HS-12);
- queue progress past unfinishable rows (HS-15);
- direction-adjusted AI stats (HS-16).

**Resolution:** add focused unit, API, and UI regressions for each open finding before changing behaviour; retain them as the Historical Signals release gate.

### HS-17 — Research metrics describe one 250-row page, not the selected population

**Status:** ❌ NOT STARTED
**Where:** the research query (`frontend/src/components/SignalResearchDashboard.tsx:79`, `limit: 250`) and the metrics computed from the loaded page (`metricRows` and the summary figures).

HS-06 moved filtering to the server and labels the metrics as page metrics. But the win rate, the averages, and every group table are still computed in the browser from the current page: the newest 250 complete records. Before batch 2a, they were computed from 1,000. With the default “All timeframes”, the newest records are mostly 1m, 2m, and 3m rows. Moving to the next page changes every metric.

**Impact:** the numbers no longer mislead about their coverage, but they don't answer the question the filters ask. A trader filtering a year of daily signals sees results for the newest 250.

**Resolution:** add a server-side aggregate endpoint over the full scoped population: counts, directional win rate, and averages by regime, trend, and timeframe, with sample sizes. Page only the record list.

### HS-18 — “Delete >180d” is undone at restart and ignores the selected scope

**Status:** ❌ NOT STARTED
**Where:** `HistoricalSignalCard.handleCleanup` (`frontend/src/components/HistoricalSignalCard.tsx:154`), `DELETE /api/signals/old` (`backend/api/signals/router.py:496`), `prune_signals_by_retention` (`backend/repositories/signal_repository.py:415`), and `_fill_signal_gaps` (`backend/api/main_helpers.py:69`).

Automatic retention already prunes signals per timeframe:

- 1m to 30m after 16 days;
- 1h and 4h after 366 days;
- 1d and 1wk after 1,096 days.

A 180-day manual delete therefore reaches only 1h, 4h, 1d, and 1wk signals. The bars for those timeframes are kept longer. On every startup, `_fill_signal_gaps` rebuilds signals for any watched symbol and timeframe whose bars outnumber its signals. The deleted rows come back without the regime they had, because backfilled rows get none, and their outcomes are recomputed. The delete also covers every symbol, although the card now shows a scope selector next to the button.

**Impact:** the destructive action only lasts until the next restart. Its lasting effects are stripped regime labels and a large re-backfill. A trader may also believe it applies only to the selected scope.

**Resolution:** remove the manual button, since automatic retention already bounds storage. If a manual delete is kept, scope it and exclude symbols and timeframes whose bars are still retained. Add a test that a deleted range stays deleted after gap-fill runs.

---

## Gaps (not bugs)

- **Outcome throughput:** the outcome loop handles at most 1,000 rows every 300 s, about 12,000 an hour. The pending backlog is 156,940 rows. Even after HS-15, clearing it takes about half a day of ingestion.
- **Scope definitions differ:** startup gap-fill's `_watched_symbols` takes every enabled symbol, whether or not its watchlist is active. The research `all_active` scope requires an active watchlist. Signals are therefore recorded for symbols that the default research scope excludes.
- **Export memory:** the server streams the CSV, but the client reads it whole with `fetchRaw` before downloading. An `all_stored` export of about 617,000 rows is held in browser memory.
- **Test coverage:** the gaps still open are listed in HS-14. None of the batch 1 or 2a regressions exercises the ingestion loop's batch size, which is why HS-15 wasn't caught.

## Enhancements

1. **Explicit outcome state:** store `pending`, `complete`, or `abandoned` with a reason, rather than inferring state from null columns in several places (repository, API, frontend helper, AI stats, chat tool).
2. **Horizon labels:** show what five bars means for the selected timeframe, for example “5 bars = 25 minutes” on 5m.
3. **Sample-size cues:** show counts and a confidence interval next to each win rate and average, and flag groups too small to trust.
4. **Regime coverage badge:** show what share of the selected rows carry a regime before showing regime tables (pairs with HS-08).
5. **Replay costs:** add optional commission and slippage to the simulated trades once HS-10 is fixed.

## Verification

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

---

## Reference

Historical Signals records a causal trend label for each closed stored bar. `BarReplay` feeds bars oldest-first through a private trend engine so future bars cannot change a prior label. Later backfill records raw 5/10/20-bar price movement plus forward high/low excursions. The card shows individual records and regime aggregates, the research dashboard groups outcomes, and replay steps through bars with a simplified stop/target simulation. AI Analysis reads a per-symbol summary of these rows as the engine's track record, and Chat can read the rows directly.

That causal-label foundation is valuable. The fixes above are required to make outcome maturity, scope, directionality, methodology, provenance, and destructive controls equally trustworthy for trader-facing research.
