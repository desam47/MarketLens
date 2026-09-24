# Version 5 Market Data Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (review)
**Status:** Review complete; no fixes started. Nine findings: one Critical, two High, two Medium, four Low. The Critical finding affects every 1h and 4h chart, signal, and AI answer built on stored bars before 2026-09-23.
**Scorecard:** 0 ✅ COMPLETE, 0 ⚠️ PARTIAL, 9 ❌ NOT STARTED, 0 🟡 DEFERRED.
**Source:** 2026-09-24 review of market-data ingestion, bar storage, retention, and the logs they produce. It covered `backend/market_data/services/ingestion_service.py`, `backend/market_data/providers/*`, `backend/repositories/bar_repository.py`, `backend/services/purge_service.py`, `backend/api/watchlist/router.py`, and `scripts/restart_dev.sh`, at `b41530c`, and checked each finding against the live database and logs.
**Related:** [Historical Signals fixes](v5_historical_signal_bug_fixes.md), [AI Analysis fixes](v5_ai_analysis.md), [Chat bug fixes](v5_bug_fixes.md), [Phase audit](phase_audit_v5.md)

"Verified" means the behaviour was shown by a read-only query of the live database or by reading the live logs. "Code-read" means it follows from the code but was not reproduced.

Status legend (same as the phase audits):

- ✅ **COMPLETE** — fixed and covered by tests
- ⚠️ **PARTIAL** — the harmful behaviour is fixed, but specific gaps remain (listed in the entry)
- ❌ **NOT STARTED** — no change made yet
- 🟡 **DEFERRED** — intentionally postponed

Line numbers refer to the code at `b41530c`.

## Scorecard

| ID | Severity | Area | Title | Evidence | Status |
|---|---|---|---|---|---|
| MD-01 | Critical | Bars | Provider 1h bars are stored 30 minutes earlier than the data they hold | Verified | ❌ NOT STARTED |
| MD-02 | High | Security | Webull credentials are written to the log files in plain text | Verified | ❌ NOT STARTED |
| MD-03 | High | Bars | One series mixes providers whose volume differs by up to 2,800 times | Verified | ❌ NOT STARTED |
| MD-04 | Medium | Storage | Data for symbols in no watchlist is never removed | Verified | ❌ NOT STARTED |
| MD-05 | Medium | Operations | `logs/backend.log` grows without limit | Verified | ❌ NOT STARTED |
| MD-06 | Low | Providers | Overnight Webull requests repeat about 80 times an hour and are rate-limited | Verified | ❌ NOT STARTED |
| MD-07 | Low | Signals | After each restart, signal recording takes about 30 minutes to catch up | Verified | ❌ NOT STARTED |
| MD-08 | Low | Storage | The live schema is missing two indexes the migrations create | Verified | ❌ NOT STARTED |
| MD-09 | Low | Code | A recorder comment describes intraday snapshots in the daily series that no longer exist | Verified | ❌ NOT STARTED |

**Next suggested order:**

1. **Batch 1 — credentials and bar correctness:** MD-02 first, because it is small and a credential leak; then MD-01 and MD-03.
2. **Batch 2 — storage and operations:** MD-04, MD-05, and MD-06.
3. **Batch 3 — clean-up:** MD-07, MD-08, and MD-09.

---

## Critical

### MD-01 — Provider 1h bars are stored 30 minutes earlier than the data they hold

**Status:** ❌ NOT STARTED
**Where:** `_normalize_1h_bar` (`backend/market_data/services/ingestion_service.py:70`), used by `_write_1h_recent_window` (`:1465`, hourly) and `_gapfill_1h_once` (`:1601`, every 30 minutes).

Webull and Yahoo return regular-session hourly bars anchored on the half hour: the first covers 09:30–10:30. `_normalize_1h_bar` floors a `:30` timestamp to the preceding `:00`, so that bar is stored as "09:00". Its open, high, low, close, and volume all come from 09:30–10:30, but the bar is labelled 09:00–10:00.

The code already knows this. A 2026-09-09 note in `_resample_1h_from_1m_and_upsert` says Webull's `:30` bars were being mislabelled. The fix it describes rebuilds 1h bars from stored 1m data, but only for today's hours and for newly added symbols. The provider write and gap-fill loops still floor and store provider bars.

**Verified (read-only query of the live database):**

- **Test:** for every regular-session 1h bar since 2026-09-09 with 1m data on both sides, I compared its close with the 1m close at hh:59 (aligned) and at hh+1:29 (shifted 30 minutes).
- **Provider bars are shifted:** all 312 Webull bars and 844 of 846 Yahoo bars match the shifted close. All 370 bars rebuilt from 1m (`live_from_1m`) match the aligned close.
- **Worked example:** SPY on 2026-09-16. The Yahoo bar stored as "09:00" opens at 759.54, the 1m open at 09:30. It closes at 758.96, the 1m close at 10:29.
- **Scope:** every regular-session hour through 2026-09-22 is a provider bar (Yahoo from 2026-09-14). 2026-09-23 and 2026-09-24 are 1m-built and correct.
- **Older history:** 32,824 provider 1h rows predate the 1m retention window, so they cannot be rebuilt from 1m. The 10,192 4h rows before that date are built from these 1h bars.

**Impact:** every 1h and 4h chart, trend score, recorded signal, outcome, and AI or Chat answer that uses those bars puts price action 30 minutes earlier than it happened. Each labelled hour contains the first half of the next hour. The pre-market half hour before 09:30 is missing from the "09:00" bar, and the last bar of the day holds only 15:30–16:00. A 1h signal "at the 10:00 close" is scored on prices up to 11:30.

**Resolution:**

- **Stop the mislabelling:** stop flooring `:30`-anchored provider bars. Within the 1m retention window, build 1h only from 1m, which is already correct. Beyond it, use a provider whose hourly bars start on the hour, or skip them.
- **Repair stored bars:** rebuild every 1h bar in the 1m window from 1m, then rebuild the 4h bars from those.
- **Older history:** either re-fetch it from an aligned source, or mark it as legacy-shifted and keep it out of signals.
- **Signals:** re-record the affected 1h and 4h signals and their outcomes.
- **Test:** add one that fails if a stored 1h bar's close does not equal the close of its last 1m member.

---

## High

### MD-02 — Webull credentials are written to the log files in plain text

**Status:** ❌ NOT STARTED
**Where:** the Webull SDK logger `webull.core.client`. The patches in `backend/market_data/providers/webull_provider.py:100`–`:152` only lower log levels and redirect files.

When a Webull request fails, the SDK logs an ERROR containing the full request, headers included. These include `x-app-key`, `x-access-token`, and `x-signature`. The existing patches lower the SDK's DEBUG noise but still pass ERROR records through unchanged.

**Verified (live logs; the values themselves were not printed):**

- A logged `ServerException` from 2026-09-21 contains a 35-character `x-app-key`, a 32-character `x-access-token`, and 44-character signatures.
- `logs/backend.log` contains `"x-access-token": "` 1,413 times.
- It also appears in `logs/marketlens.log` and its rotated copies `.1` to `.3`.
- `logs/` is ignored by git, so none of this is committed.

**Impact:** anyone who can read the logs, or who receives a copy for debugging, gets working API credentials for your Webull account. The Version 5 security review describes observability as sanitized, which does not hold for these records.

**Resolution:** add a logging filter on the `webull` loggers and the root handlers that redacts these header values: `x-app-key`, `x-access-token`, `x-signature`, `access_token`, and `app_secret`. Test it with a synthetic `ServerException` record. Then decide what to do with the existing log files: redact them in place or delete them. Rotate the Webull token if the logs were ever shared.

### MD-03 — One series mixes providers whose volume differs by up to 2,800 times

**Status:** ❌ NOT STARTED
**Where:** the fallback chains in `_fetch_bars_with_fallback` (`ingestion_service.py:1336`) and `backfill_service.py`, and the per-bar `provider` column in `bars`.

A fallback provider fills any gap the primary leaves, bar by bar. Providers report volume differently. Alpaca's free tier (IEX) counts only trades on one exchange, a few percent of the consolidated volume Webull and Yahoo report.

**Verified (read-only query of the live database):**

- **Providers per day:** SPY's 1h bars on 2026-09-16 come from three providers: `live_from_1m`, `alpaca`, and `yahoo_finance`.
- **One hour, 375× apart:** the 08:00 Alpaca bar has volume 200. The 1m bars for the same hour sum to 75,289.
- **Another hour, about 2,800× apart:** the 16:00 Alpaca bar has 2,313 against a 1m sum of 6,452,715.
- **1m series:** SPY's regular-session Alpaca bars average 3,087 shares a minute against Webull's 93,326.
- **Totals:** 4,051 Alpaca 1h bars and 1,376 Alpaca 1m bars are stored.

**Impact:** anything built on volume sees sudden drops or spikes that are only a change of provider. That includes relative volume, VWAP, volume filters in the Scanner, the Replay volume column, and any AI answer about volume.

**Resolution:**

- **Scope Alpaca:** don't use IEX-only volume as a fill for consolidated series. Either drop Alpaca from volume-bearing fallbacks, or store its bars with a flag and exclude their volume.
- **Provider per hour:** for 1h, building from 1m (MD-01) removes most of the mixing.
- **Visibility:** show each series' provider mix wherever data quality is reported.

---

## Medium

### MD-04 — Data for symbols in no watchlist is never removed

**Status:** ❌ NOT STARTED
**Where:** `purge_symbol_from_database_safe`, called only from the two watchlist delete routes (`backend/api/watchlist/router.py:222` and `:356`). Also `MarketDataManager.get_historical_bars` (`backend/market_data/services/manager_class.py:252`) and the stream bar writer (`backend/market_data/streaming/live_bar_persistence.py:124`), which persist bars for any symbol they are asked about.

**Verified (read-only query of the live database):**

- **Orphaned symbols:** six symbols with stored bars are not in any watchlist.
  - NOK and XLK have 8,476 and 6,565 bars and 7,604 and 5,403 signals, with their last bar at 2026-09-11 15:21. They are not in `watchlist_symbols` at all, so they were removed without the purge running. Retained logs start on 2026-09-13, so the path that removed them could not be traced.
  - GOOGL and RIVN have 65 daily Webull bars each, written by on-demand fetches.
  - SOXL and WMT have one Webull stream bar each.
- **Stale incomplete bars:** four NOK and XLK 1h and 1d bars from 2026-09-11 still have `data_status = INCOMPLETE`.

**Impact:** orphaned rows fill the outcome queue and the stale-bar checks, and they survive until retention prunes them: 1,096 days for daily bars. They also produced the stuck rows in Historical Signals HS-15.

**Resolution:** add a periodic sweep that purges symbols in no watchlist, allowing a grace period for symbols viewed on demand. Run it once now for the six symbols above. Also make sure disabling a symbol or deactivating a watchlist refreshes the ingestion list, as removing one already does.

### MD-05 — `logs/backend.log` grows without limit

**Status:** ❌ NOT STARTED
**Where:** `scripts/restart_dev.sh:84` appends the server's stdout and stderr to `logs/backend.log` with `>>`.

The app's own `RotatingFileHandler` keeps `logs/marketlens.log` to 5 × 50 MB. The console copy of the same records, plus the Webull SDK's output, goes to `backend.log`, which nothing rotates.

**Verified:**

- **backend.log:** it is 442 MB, covering 2026-09-13 to 2026-09-24, which is about 40 MB a day.
- **logs/ overall:** the directory holds 842 MB, including 146 dated Webull SDK log files (93 MB).
- **What fills it:** in the last 50 MB of `backend.log`, the busiest sources are the Webull provider (39,220 records), the trend registry (32,485), and `backend.api.main` (21,548).

**Impact:** disk use grows by gigabytes a quarter, and every leaked credential from MD-02 is kept indefinitely.

**Resolution:** either stop writing the console copy to a file, or rotate `backend.log` with a size cap. Prune dated Webull SDK logs after a retention period.

---

## Low

### MD-06 — Overnight Webull requests repeat about 80 times an hour and are rate-limited

**Status:** ❌ NOT STARTED
**Where:** the Webull SDK's `/openapi/config` request, reached through the provider or stream reconnect path.

**Verified (live logs):** `/openapi/config` fails with `TOO_MANY_REQUESTS` (HTTP 429) about 80 times an hour between 01:00 and 04:00 ET. Examples: 89, 79, and 85 an hour on 2026-09-24 from 05:00 to 07:00 UTC, with the same pattern on 2026-09-23. In the last 50 MB of `backend.log`, 1,021 of 1,111 Webull `ServerException` records are 429s.

**Impact:** each failure writes a credential-bearing log record (MD-02) and keeps Webull's rate limit tripped at a time when no market data is needed.

**Resolution:** back off exponentially after a 429 and pause reconnect attempts outside the extended session. Log one summary line per back-off rather than one record per request.

### MD-07 — After each restart, signal recording takes about 30 minutes to catch up

**Status:** ❌ NOT STARTED
**Where:** `SignalRecorder.record_from_recent_bars` with `SEED_BUDGET_SECONDS = 3.0` (`backend/services/signal_recorder.py:40`). A restarted process must re-seed every symbol and timeframe before it records new bars for them.

**Verified (live database, during the Historical Signals HS-12 rollout):**

- **Before catch-up:** after the 16:46 ET restart, 89 closed 1m bars from 16:40–16:47 across 15 symbols still had no signal at 16:51.
- **After:** by 17:19 all had one, and no closed bar from that day was missing a signal.

With `--reload`, every saved backend file restarts the process.

**Impact:** anything reading recent signals lags by up to about 30 minutes after a restart: Chat's signal history, AI signal stats, and the Replay markers. No data is lost.

**Resolution:** seed the pairs with the most recent unrecorded closed bars first, or record new closed bars directly without waiting for a full seed.

### MD-08 — The live schema is missing two indexes the migrations create

**Status:** ❌ NOT STARTED

**Verified:** a fresh database built with `alembic upgrade head` was compared with the live database. The only difference is that the live `historical_signals` table lacks `ix_historical_signals_symbol` and `ix_historical_signals_timeframe`. All columns match. The retention prune's query plan uses `ix_historical_signals_timestamp`, and the new unique index covers symbol lookups, so no query was found to be slower.

**Resolution:** add a migration that creates them if missing, or drop them from the model and migrations if they are not needed. Either way, the live schema should match the migration history.

### MD-09 — A recorder comment describes intraday snapshots in the daily series that no longer exist

**Status:** ❌ NOT STARTED
**Where:** `SignalRecorder._compute_outcome_for_signal` (`backend/services/signal_recorder.py`, the 1d anchor comment).

The comment says the 1d table mixes midnight bars with 13:30 intraday snapshots from Alpaca. `_normalize_1d_bar` has normalized daily bars since 2026-09-09.

**Verified:** no stored 1d or 1wk bar has a non-midnight timestamp.

**Resolution:** correct the comment. The midnight anchor itself is still right, since daily bars are stamped at midnight.

---

## Gaps (not bugs)

- **Not reviewed:**
  - Redis bar-cache coherence with the database;
  - the backfill queue's job lifecycle;
  - holiday and half-day handling in the market calendar;
  - Webull and Yahoo daily closes compared with each other;
  - `tape_bars`.
- **Future days' 1h bars:** 2026-09-23 and 2026-09-24 are correct 1m-built bars. Whether the provider loops overwrite them once they are older than today was not observed. Check again after a few sessions.
- **Checked and fine:**
  - no bar has impossible OHLC values or a non-positive price;
  - every stored 1m bar has a session tag;
  - SPY's 1m regular session has all 390 minutes on each day checked.

## Enhancements

1. **Data-quality report:** one System Health panel listing, per series:
   - provider mix;
   - any 1h bar whose close does not match its last 1m member;
   - volume discontinuities between adjacent bars from different providers;
   - orphaned symbols.
2. **Provenance in answers:** when Chat or AI Analysis cites a bar, include its provider, so a mixed series is visible.
3. **One redaction point:** apply the MD-02 filter to every third-party logger, not just Webull's.

## Verification

### Review probes (2026-09-24, read-only)

| Check | Result | Finding |
|---|---|---|
| Regular-session 1h closes since 2026-09-09 compared with 1m closes | Webull 312/312 and Yahoo 844/846 shifted 30 min; `live_from_1m` 370/370 aligned | MD-01 |
| 1h provider rows before the 1m window; 4h rows before it | 32,824; 10,192 | MD-01 |
| Credential headers in logs (values not printed) | `x-access-token` 1,413 times in `backend.log`; also in `marketlens.log` and rotated copies | MD-02 |
| SPY 1h volume against its 1m sum, 2026-09-16 | Alpaca 200 vs 75,289 and 2,313 vs 6,452,715 | MD-03 |
| Symbols with bars but in no watchlist | NOK, XLK, GOOGL, RIVN, SOXL, WMT | MD-04 |
| `logs/` size | `backend.log` 442 MB in 11 days; directory 842 MB | MD-05 |
| Webull 429s by hour | about 80 an hour, 01:00–04:00 ET | MD-06 |
| Closed bars without signals after a restart | 89 at 16:51, none by 17:19 | MD-07 |
| Fresh migrated schema against the live schema | two missing `historical_signals` indexes; columns identical | MD-08 |
| 1d and 1wk bars not at midnight | 0 | MD-09 |

The fresh schema for MD-08 was built under `.pytest_tmp/` and deleted afterwards. `conf/token.txt` was unchanged by the Alembic run.

## Fix log

- **Review:** in the working tree, not yet committed.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | MD-01 to MD-09 | ❌ NOT STARTED | review | `docs/Version_5/v5_market_data_bug_fixes.md` | 10 probes | Review logged nine findings. |

---

## Reference

Ingestion writes 1m bars from the live provider, fills gaps from fallback providers, and resamples 2m to 30m from 1m. 1h is written from providers and rebuilt from 1m for the current day; 4h is resampled from 1h; 1d comes from providers and 1wk from 1d. Every write goes through `upsert_bars`, keyed on `(symbol, timeframe, timestamp)`, so the last writer wins, and each bar records the `provider` that wrote it. Retention prunes each timeframe on its own window. Historical Signals, the trend engines, charts, Chat, and AI Analysis all read these stored bars.
