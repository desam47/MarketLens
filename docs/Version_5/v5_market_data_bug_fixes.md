# Version 5 Market Data Bug Fixes

**Created:** 2026-09-24
**Last updated:** 2026-09-24 (batch 3: MD-07, MD-08, MD-09)
**Status:** Review complete. All nine findings are fixed. MD-07: signal-recorder seeding now prioritizes the timeframes closest to real time instead of seeding them last. MD-08: the two missing `historical_signals` indexes were added by a new migration. MD-09: the stale 13:30-snapshot comments were corrected. MD-05 and MD-06 are fixed: console-log redirects are rotated and capped, dated Webull SDK logs are pruned, and the stream reconnect loop is paused outside the extended session with 429-aware backoff. MD-04 is fixed: a periodic sweep purges symbols in no active watchlist once they go stale, deactivating a watchlist now stops tracking immediately, and the six known orphans are purged. MD-03 is fixed: stored 1m bars are settled from Alpaca's consolidated (SIP) feed, so every timeframe carries full-market volume. MD-01 is fixed: 1h bars are placed on the clock hour, and the stored 1h/4h history and its signals were repaired on the live database. MD-02 is fixed: log records are redacted, confirmed on live Webull errors, the old log files holding credentials are deleted, and the Webull token was rotated. Nine findings: one Critical, two High, two Medium, four Low. The Critical finding affects every 1h and 4h chart, signal, and AI answer built on stored bars before 2026-09-23.
**Scorecard:** 9 ✅ COMPLETE, 0 ⚠️ PARTIAL, 0 ❌ NOT STARTED, 0 🟡 DEFERRED.
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
| MD-01 | Critical | Bars | Provider 1h bars are stored 30 minutes earlier than the data they hold | Verified | ✅ COMPLETE |
| MD-02 | High | Security | Webull credentials are written to the log files in plain text | Verified | ✅ COMPLETE |
| MD-03 | High | Bars | One series mixes providers whose volume differs by up to 2,800 times | Verified | ✅ COMPLETE |
| MD-04 | Medium | Storage | Data for symbols in no watchlist is never removed | Verified | ✅ COMPLETE |
| MD-05 | Medium | Operations | `logs/backend.log` grows without limit | Verified | ✅ COMPLETE |
| MD-06 | Low | Providers | Overnight Webull requests repeat about 80 times an hour and are rate-limited | Verified | ✅ COMPLETE |
| MD-07 | Low | Signals | After each restart, signal recording takes about 30 minutes to catch up | Verified | ✅ COMPLETE |
| MD-08 | Low | Storage | The live schema is missing two indexes the migrations create | Verified | ✅ COMPLETE |
| MD-09 | Low | Code | A recorder comment describes intraday snapshots in the daily series that no longer exist | Verified | ✅ COMPLETE |

**Order followed:**

1. **Batch 1 — credentials and bar correctness:** MD-02 first, because it is small and a credential leak; then MD-01 and MD-03.
2. **Batch 2 — storage and operations:** MD-04, MD-05, and MD-06.
3. **Batch 3 — clean-up:** MD-07, MD-08, and MD-09.

All nine complete as of batch 3.

---

## Critical

### MD-01 — Provider 1h bars are stored 30 minutes earlier than the data they hold

**Status:** ✅ COMPLETE (2026-09-24, batch 1b). New 1h bars are placed on the clock hour, and the stored 1h/4h bars and their signals were repaired on the live database.
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

**Resolution:** 1h bars now always cover a clock hour. They are built from our 1m bars where those exist, and otherwise come from Alpaca's consolidated (SIP) hourly bars, which start on the hour.

- **Probe (read-only, SPY, 2026-09-23):** Alpaca's free plan serves SIP bars older than 15 minutes, back at least a year. Its hourly bars match the 1m-built hours to the cent, with regular-session volume within 1%. Its IEX bars, the feed used until now, carried 1,199 shares for 08:00 and 109,823 for 09:00, against 3.67 million on SIP.
- **Skip, don't floor:** `_normalize_1h_bar` now drops any provider 1h bar that does not start on the hour, instead of flooring it. A kept bar gets its session from its clock hour (`hour_session`: the 09:00 hour is "mixed").
- **1m-built hours win:** `upsert_bars` never lets a provider 1h bar replace one built from 1m (`live_from_1m`). A 1m-built bar still replaces a provider bar. The 16:02 daily bar still replaces the live 1d bar.
- **Alpaca over SIP:** `get_historical_bars` and the new `get_bars_between` request `ALPACA_HISTORICAL_FEED` (default `sip`), with `end` capped at 15 minutes ago. If the account is refused SIP (401/403), the provider falls back to `ALPACA_DATA_TIER` for the rest of the process. This also gives Alpaca's 1m and 1d fallback bars full-market volume (MD-03).
- **1h chain:** `BACKFILL_1H_PRIMARY=alpaca` and an empty `BACKFILL_1H_FALLBACK`, in `.env`, `.env.example`, and the settings defaults. Webull and Yahoo cannot serve as fallbacks: every hourly bar they return starts on the half hour.
- **Closed hours only:** the 1h write and gap-fill loops and the tier-2 backfill keep only hours that had ended 15 minutes before, so a partial SIP hour is never stored as HISTORICAL. The tier-2 backfill is also cut to `BACKFILL_1H_DAYS`; it used to keep the whole `5y` response.
- **Shared builders:** `backend/market_data/hourly_bars.py` holds the 1m→1h and 1h→4h builders, used by the ingestion loops and the repair. It does not import the market-data manager, which would authenticate Webull.

**Repair:** `scripts/repair_hourly_bars.py --db <path> [--apply]` runs `backend/market_data/hourly_repair.py` on each symbol with 1h bars, one transaction per symbol:

- builds every whole hour our 1m bars cover;
- keeps existing `live_from_1m` bars;
- fills every other hour from Alpaca SIP, starting at the symbol's first stored 1h day;
- deletes every other provider 1h bar, whether or not it was replaced;
- rebuilds the symbol's 4h bars from the result;
- deletes its 1h and 4h historical signals. The recorder re-records them from the repaired bars when the server restarts.

A dry run does all of this and rolls it back. The data is fetched before anything is deleted, so a failed Alpaca request leaves the symbol unchanged.

**Trial run on a `.backup` copy of the live database (2026-09-24 17:49):**

- **Coverage:** 25 symbols, none failed. Every 1h bar is on the hour, and `misplaced_hours` is 0 for every symbol: each 1h bar with 1m data closes on its last 1m close.
- **1h rows, 38,367 → 69,553:** 4,378 built from 1m and 64,165 from Alpaca SIP. The count grows because SIP has every hour from 04:00 to 19:00 (16 a day), the same span the 1m-built bars have had since 2026-09-17. The old history had 7–9 hours a day.
- **Dropped:** 34 shifted hours had no SIP bar and were removed: 33 for CTNT and 1 for CYN, both thinly traded.
- **4h and signals:** the 25 symbols' 4h rows were rebuilt, 11,388 → 16,957. 46,576 1h and 4h signals were deleted. 1,578 of them carried a recorded market regime; the regime is recorded only when a bar is fresh, so re-recorded rows will show "not recorded".
- **Spot check, SPY 2026-01-08:** the old "09:00" Webull bar opened at 688.76, the 09:30 price. The new 09:00 bar opens at 689.07 and is tagged "mixed"; the 10:00 bar opens at 688.28.

**Tests:**

| File | What it covers |
|---|---|
| `backend/tests/market_data/test_hourly_bars.py` (new, 9 tests) | A 1h bar's close is the close of its hour's last 1m bar. Also sessions, the 4h buckets, and closed-hour filtering. |
| `backend/tests/market_data/test_hourly_repair.py` (new, 6 tests) | The rebuilt series, 4h, signal deletion, rollback, a failed fetch, a symbol with no 1h bars. |
| `test_bar_repository.py` | 3 precedence tests. |
| `test_alpaca_provider.py` | 4 tests: SIP feed, refused-SIP fallback, no retry on other errors, `end` cap. |
| `test_bar_normalization.py`, `test_ingestion_service.py` | Updated to skipping instead of flooring. A provider bar no longer replaces a 1m-built hour. |

**Live repair (2026-09-24 17:53–17:56):**

1. **Backup:** the live database went to `data/marketlens_pre_md01_20260924.db` (`.backup`, integrity check ok).
2. **Repair:** `scripts/repair_hourly_bars.py --apply` gave the same result as the trial:
   - 25 symbols, none failed;
   - 1h rows 38,367 → 69,553, none off the hour;
   - 34 hours dropped;
   - 46,576 1h/4h signals deleted.
3. **Misplaced hours:** 0 for 24 symbols. The one QQQ hour flagged was 17:00, still in progress: a 1m bar landed after the repair built it, and the live builder refreshes that hour every 2 minutes.
4. **Restart:** the stack restarted at 17:57. Within minutes the recorder had re-recorded 63,706 1h and 16,190 4h signals, with outcomes for 62,928 and 15,968.
   - Every closed 1h bar of the watched symbols has a signal.
   - NOK and XLK have none: they are in no watchlist, so the recorder skips them (MD-04).

---

## High

### MD-02 — Webull credentials are written to the log files in plain text

**Status:** ✅ COMPLETE (2026-09-24, batch 1a). Records are redacted, confirmed on live Webull errors, the log files written before the fix are deleted, and the Webull app key/secret rotated.
**Where:** the Webull SDK logger `webull.core.client`. The patches in `backend/market_data/providers/webull_provider.py:100`–`:152` only lower log levels and redirect files.

When a Webull request fails, the SDK logs an ERROR containing the full request, headers included. These include `x-app-key`, `x-access-token`, and `x-signature`. The existing patches lower the SDK's DEBUG noise but still pass ERROR records through unchanged.

**Verified (live logs; the values themselves were not printed):**

- A logged `ServerException` from 2026-09-21 contains a 35-character `x-app-key`, a 32-character `x-access-token`, and 44-character signatures.
- `logs/backend.log` contains `"x-access-token": "` 1,413 times.
- It also appears in `logs/marketlens.log` and its rotated copies `.1` to `.3`.
- The RQ worker logs held them too: `logs/rq_workers.log` had 22,893 credential values, the last on 2026-09-19, and `logs/rq_backfill_1.log` had 17.
- `logs/` is ignored by git, so none of this is committed.

**Impact:** anyone who can read the logs, or who receives a copy for debugging, gets working API credentials for your Webull account. The Version 5 security review describes observability as sanitized, which does not hold for these records.

**Resolution:** a new module, `backend/observability/redaction.py`, wraps the process-wide log record factory. A filter on our own handlers would miss the SDK's, because the SDK attaches handlers of its own. Every record from a `webull*` logger, or whose message names a credential key, is redacted as it is created, so every handler receives the cleaned text:

- values of `x-app-key`, `x-access-token`, `x-signature`, `access_token`, `app_secret`, `app_key`, and `Authorization` become `***`;
- the message and any exception text are both cleaned;
- other records cost one substring check.

`webull_provider.py` installs it before importing the SDK, and `configure_logging` installs it too. The streaming client imports the provider first, so it is covered as well. The provider's docstring no longer claims keys are never logged and tokens never reach disk: the SDK stores its token in `conf/token.txt`.

**Tests:** `backend/tests/observability/test_secret_redaction.py` has 8 tests, using fake values shaped like the real `ServerException`:

- a record through a handler attached directly to an SDK logger;
- the request passed as a format argument;
- a logged exception;
- one of our own loggers mentioning a token;
- plain text left unchanged.

With the factory not installed, 4 of them fail. The full backend suite passes: 3,425 tests.

**Old log files (done, 2026-09-24):** every file in `logs/` that named a credential key was cleared, 114 in all, and `logs/` went from 845 MB to 2.4 MB. The five the running processes hold open were emptied rather than deleted, so they keep receiving new lines: `backend.log`, `marketlens.log`, `rq_workers.log`, and the two current Webull SDK logs. All five are opened in append mode, so emptying them leaves no gap. The other 109 were deleted: the rotated `marketlens.log.1`–`.5`, `rq_backfill_1.log`, and 103 rotated Webull SDK logs. Afterwards no file in `logs/` names a credential key. `data/logs/marketlens.log`, a stale copy from 2026-09-13, names the keys on 7 lines but holds no values, so it was kept.

**Remaining:**

- ~~**Token rotation:**~~ done 2026-09-24 18:36. New `WEBULL_APP_KEY`/`WEBULL_APP_SECRET` from the Webull developer portal, `conf/token.txt` and `conf/token_stream/token.txt` deleted so the SDK could not reuse the old token, then a restart. The handshake cycled `PENDING` for about 20s (normal) and settled to `status: NORMAL`; a few `INVALID_TOKEN`/`TOO_MANY_REQUESTS` errors during that window were transient, none since. The old app key/secret held two access-token values that had been printed into this review's session output by a check that should have hidden them — the reason rotation was needed here rather than left for routine rotation.
- ~~**Worker restart:**~~ done 2026-09-24 17:35 with `scripts/restart_dev.sh`, after commit `63a11a8`. The RQ workers run `SimpleWorker`, which does not fork, so until then they ran the code loaded at 00:31, before the fix. After the restart, no errors and no credential values in `logs/`.
- ~~**Live confirmation:**~~ done 2026-09-24. The dev-server reloads during MD-01 drew Webull 429s on `/openapi/config`, and the SDK logged them in full: 230 `ServerException` records across `backend.log`, `marketlens.log`, and the SDK's own `webull_trade_sdk.log`. The logs hold 673 redacted credential values and none in the clear.

### MD-03 — One series mixes providers whose volume differs by up to 2,800 times

**Status:** ✅ COMPLETE (2026-09-24, batch 1c). Stored 1m bars are settled from Alpaca's consolidated (SIP) feed once they are 15 minutes old, and every timeframe built from them follows. The 1m history was settled on the live database.
**Where:** the fallback chains in `_fetch_bars_with_fallback` (`ingestion_service.py:1336`) and `backfill_service.py`, and the per-bar `provider` column in `bars`.

A fallback provider fills any gap the primary leaves, bar by bar. Providers report volume differently. Alpaca's free tier (IEX) counts only trades on one exchange, a few percent of the consolidated volume Webull and Yahoo report.

**Verified (read-only query of the live database):**

- **Providers per day:** SPY's 1h bars on 2026-09-16 come from three providers: `live_from_1m`, `alpaca`, and `yahoo_finance`.
- **One hour, 375× apart:** the 08:00 Alpaca bar has volume 200. The 1m bars for the same hour sum to 75,289.
- **Another hour, about 2,800× apart:** the 16:00 Alpaca bar has 2,313 against a 1m sum of 6,452,715.
- **1m series:** SPY's regular-session Alpaca bars average 3,087 shares a minute against Webull's 93,326.
- **Totals:** 4,051 Alpaca 1h bars and 1,376 Alpaca 1m bars are stored.

**Impact:** anything built on volume sees sudden drops or spikes that are only a change of provider. That includes relative volume, VWAP, volume filters in the Scanner, the Replay volume column, and any AI answer about volume.

**Further findings (2026-09-24, read-only):** every stored 1m bar since 2026-09-09 was compared with Alpaca's SIP bar for the same minute. The comparison covered 10 symbols; the ratio is stored volume ÷ SIP volume.

| Source | Session | Volume ratio (median) | 10th–90th percentile |
|---|---|---|---|
| Webull REST | regular | 0.999 | 0.96–1.04 |
| Webull REST | pre-market/after-hours | 0.53 | 0.11–0.98 |
| Webull stream (Nasdaq Basic) | extended | 0.42 | — |
| Alpaca IEX | regular | 0.054 | — |
| Yahoo | regular | 1.00 | up to 1.6 |

So even the primary source carried about half the market's extended-hours volume. The stream's rows were never replaced, because the 1m gap-fill only writes minutes that are missing. Since MD-01, the 1h history before the 1m window is SIP, so extended-hours 1h volume fell about 2× at the window's edge.

**Resolution (chosen 2026-09-24):** SIP settles the 1m series. Webull stays the live feed.

- **Settle loop:** `MarketDataIngestionService._settle_1m_loop` runs every 15 minutes, 04:15–20:30 ET on weekdays. It replaces each 1m bar older than 15 minutes with its Alpaca SIP bar. Each pass covers the last 90 minutes, so a late SIP correction is picked up. At startup it covers the last two days.
- **Rebuild:** after a pass writes, the 2m–30m bars (`_resample_and_upsert(since=…)`, floored to the hour), 1h (from 1m) and 4h are rebuilt over the settled span.
- **Settled means final:** `upsert_bars` never lets another provider replace a 1m bar from `alpaca`. Webull's recent-window writes, the stream and the gap-fills therefore can't undo a settled minute. `_SETTLED_BY` holds both precedence rules, this one and MD-01's.
- **IEX is labelled:** if Alpaca refuses SIP, its bars come back as `alpaca_iex`. Those neither settle a minute nor are protected. `ALPACA_SETTLE_1M` (default true) turns the loop off.
- **No SIP bar:** minutes SIP has no bar for, meaning no consolidated trades, keep what was stored. In the probe these carried under 0.3% of volume.
- **Signals:** not re-recorded. They keep the volume the engine saw live.

**One-off settle:** `scripts/settle_1m_from_sip.py --db <path> [--apply]` runs `backend/market_data/sip_settle.py`, one transaction per symbol. It settles every stored minute, starting at each symbol's first whole 1m hour, and rebuilds the 2m–30m, 1h and 4h bars. By default it covers only the enabled symbols of active watchlists: a dry run showed it would otherwise add thousands of minutes for NOK, XLK, SOXL and WMT, which nothing keeps current (MD-04).

**Live run (2026-09-24 18:07):**

- **Backup:** first, to `data/marketlens_pre_md03_20260924.db` (integrity check ok).
- **Settle:** across 23 symbols, 168,458 minutes were replaced and 3,126 missing minutes added. 1m volume went from 11.86 billion to 13.39 billion shares. By symbol, the change ran from 0.98× for NVDA through 1.05× for SPY to 1.44× for CTNT.
- **Probe afterwards:** every stored minute that has a SIP bar matches it exactly: 79,700 minutes at a median ratio of 1.000, with no close differences. Every 1h bar in the 1m window equals the sum of its 1m bars (3,964 of 3,964).
- **Restart:** the stack restarted at 18:08, and its startup pass settled the last two days again without errors.

**Tests:**

| File | What it covers |
|---|---|
| `backend/tests/market_data/test_sip_settle.py` (new, 8 tests) | The live pass: the 15-minute cutoff, 5m and 1h rebuilt, IEX ignored, Webull can't overwrite, no Alpaca. The one-off: whole hours, rebuilds, IEX, rollback. |
| `test_bar_repository.py` | A SIP minute isn't replaced by Webull; an `alpaca_iex` minute is. |
| `test_alpaca_provider.py` | Refused-SIP bars are labelled `alpaca_iex`. |

**Left as is:**

- **Older 1h bars:** 1,009 1h bars built from 1m before the 1m window (2026-08-25 to 2026-09-08) still carry Webull volume. 985 of them are regular-session, where Webull matches SIP.
- **Daily bars:** 1d bars come from Webull and were not compared.
- **Provider mix:** showing each series' provider mix is left to Enhancement 2.

---

## Medium

### MD-04 — Data for symbols in no watchlist is never removed

**Status:** ✅ COMPLETE (2026-09-24, batch 2a).
**Where:** `purge_symbol_from_database_safe`, called only from the two watchlist delete routes (`backend/api/watchlist/router.py:222` and `:356`). Also `MarketDataManager.get_historical_bars` (`backend/market_data/services/manager_class.py:252`) and the stream bar writer (`backend/market_data/streaming/live_bar_persistence.py:124`), which persist bars for any symbol they are asked about.

**Verified (read-only query of the live database):**

- **Orphaned symbols:** six symbols with stored bars are not in any watchlist.
  - NOK and XLK have 8,476 and 6,565 bars and 7,604 and 5,403 signals, with their last bar at 2026-09-11 15:21. They are not in `watchlist_symbols` at all, so they were removed without the purge running. Retained logs start on 2026-09-13, so the path that removed them could not be traced.
  - GOOGL and RIVN have 65 daily Webull bars each, written by on-demand fetches.
  - SOXL and WMT have one Webull stream bar each.
- **Stale incomplete bars:** four NOK and XLK 1h and 1d bars from 2026-09-11 still have `data_status = INCOMPLETE`.

**Impact:** orphaned rows fill the outcome queue and the stale-bar checks, and they survive until retention prunes them: 1,096 days for daily bars. They also produced the stuck rows in Historical Signals HS-15.

**A self-inflicted complication, found during this fix:** `scripts/repair_hourly_bars.py` (MD-01) defaulted to every symbol with stored 1h bars, not the watchlist-scoped list `scripts/settle_1m_from_sip.py` (MD-03) used. Run live during MD-01, it fetched about 8.5 months of fresh Alpaca 1h/4h bars for NOK and XLK — neither watched — and deleted their old 1h/4h signals (53 and 62 rows) with no path to rebuild them, since nothing re-records signals for an unwatched symbol. NOK went from 8,476 bars (stale, last bar 2026-09-11) to 10,061 (fresh through 2026-09-24); XLK similarly. This also masked both from the grace-period check below for another week, since their newest bar became "today". The six symbols were purged by name rather than waiting on the sweep, so this is folded into MD-04's fix rather than filed separately.

**Resolution:**

- **Periodic sweep:** `MarketDataIngestionService._orphan_sweep_loop`, every 6h (`backend/market_data/services/ingestion_service.py`). `purge_service.find_orphaned_symbols(db, grace_days)` finds every symbol with a bar or signal, not currently in an active watchlist, whose newest bar or signal is older than `MARKET_DATA_ORPHAN_GRACE_DAYS` (default 7); each is purged via the existing `purge_symbol_from_database_safe`. The grace period protects a symbol being viewed on demand (a chart lookup with no watchlist entry): its newest stored bar stays recent for as long as it keeps being viewed. `MARKET_DATA_ORPHAN_SWEEP_ENABLED` turns it off.
- **Deactivating a watchlist now refreshes ingestion:** `PUT /api/watchlists/{id}` calls `ingestion_service.refresh_symbols_from_watchlist()` when `is_active` changes, matching delete and remove-symbol. It does not purge immediately — deactivation is reversible — the sweep purges those symbols once they go stale.
- **Closed a related gap:** `WatchlistRepository.symbol_exists_in_any_watchlist` checked `WatchlistSymbol.is_enabled` but never joined `Watchlist.is_active`, so a symbol left only in a *deactivated* watchlist counted as "still watched" by the purge-on-remove check, even though ingestion had already stopped tracking it. Now joins and filters on `Watchlist.is_active`, matching its own docstring and what ingestion tracks. New `all_watchlisted_symbols()` is the shared definition both the sweep and this check use.
- **Run once, live:** the six named symbols were purged directly by name (2026-09-24 18:44), ahead of the sweep's grace period, since they were already confirmed orphaned: 34,119 rows across bars, signals, quotes, market_status, and backfill_jobs. Verified empty afterward.
- **Stale incomplete bars:** resolved as a side effect — NOK and XLK no longer have any rows.
- **Not changed:** `MarketDataManager.get_historical_bars` and the stream writer still persist bars for any symbol asked about, on demand — the sweep is the backstop for that, not a restriction on it; an on-demand lookup for a non-watchlisted symbol is intentional (Chat, Scanner, and Chart all support querying a symbol that isn't watchlisted).

**Tests:**

| File | What it covers |
|---|---|
| `test_watchlist_repository_active_filter.py` (new, 5 tests) | `symbol_exists_in_any_watchlist` / `all_watchlisted_symbols` against a real active/deactivated-watchlist join. |
| `test_purge_service.py` (6 new) | `find_orphaned_symbols`: stale-unwatched is orphaned, fresh-unwatched is protected by the grace period, watched-but-stale is never orphaned, a symbol only in a deactivated watchlist is orphaned once stale, a recent signal also counts as activity, and the sweep step purges end to end. |
| `test_watchlist_api.py` (2 new) | Deactivating a watchlist calls `refresh_symbols_from_watchlist`; renaming does not. |

Full run: `backend/tests/market_data`, `services`, `watchlist`, `repositories`, `config` — 808 passed.

**Live confirmation (2026-09-24 18:44–18:49):** restarted; the startup sweep pass found 0 orphans (the six were already purged by name) and logged nothing, as designed; live ingestion continued normally afterward (23 symbols, unaffected). `find_orphaned_symbols` called directly afterward also returns `[]`.

### MD-05 — `logs/backend.log` grows without limit

**Status:** ✅ COMPLETE (2026-09-24, batch 2b).
**Where:** `scripts/restart_dev.sh:84` appends the server's stdout and stderr to `logs/backend.log` with `>>`.

The app's own `RotatingFileHandler` keeps `logs/marketlens.log` to 5 × 50 MB. The console copy of the same records, plus the Webull SDK's output, goes to `backend.log`, which nothing rotates.

**Verified:**

- **backend.log:** it is 442 MB, covering 2026-09-13 to 2026-09-24, which is about 40 MB a day.
- **logs/ overall:** the directory holds 842 MB, including 146 dated Webull SDK log files (93 MB).
- **What fills it:** in the last 50 MB of `backend.log`, the busiest sources are the Webull provider (39,220 records), the trend registry (32,485), and `backend.api.main` (21,548).

**Impact:** disk use grows by gigabytes a quarter, and every leaked credential from MD-02 is kept indefinitely.

**Resolution:** `scripts/rotate_stdin.py` (new) reads stdin line by line and rotates the same way `logging.handlers.RotatingFileHandler` does — 50 MB × 5 files, matching `marketlens.log`'s own cap — without needing a second process to watch the file from outside. `restart_dev.sh` pipes every console-log redirect through it instead of a raw `>>`.

- **Scope widened beyond `backend.log`:** the same unrotated-`>>` defect was in `logs/frontend.log` and `logs/rq_workers.log` too (craco and both RQ workers). All three now go through the rotator.
- **The two RQ workers no longer share one log file:** two independent rotator processes appending *and* rotating the same path would race on the rename — each holds a stable fd across an external rename, so it would keep writing to what's now a stale backup instead of the fresh file. `marketlens-backfill` now writes to its own `logs/rq_backfill.log`.
- **Dated Webull SDK logs pruned:** the SDK's own `TimedRotatingFileHandler` (`backup_count=72`, hourly) only deletes past-count files when *it* rotates, and with `--reload` restarting the process (and the handler) more often than hourly during active dev work, that rollover rarely fires — 146 dated files piled up regardless of the count. `restart_dev.sh` now deletes `logs/webull_*.log.*` older than 7 days on every restart.
- **`start.sh`/`scripts/run.py` untouched:** they stream to the attached terminal, not a file — there was nothing to rotate there.

**Tests:** `backend/tests/scripts/test_rotate_stdin.py` (new, 5 tests) — writes pass through below the cap, rotation at the cap, `backup_count` respected past many rotations, a write failure is reported and never raises, and a real subprocess pipe end to end.

**Live confirmation (2026-09-24 19:02):** restarted; `backend.log`, `frontend.log`, `rq_workers.log`, and the new `rq_backfill.log` all received correctly formatted, unbroken content through their rotators; no rotator traceback in any of the four.

---

## Low

### MD-06 — Overnight Webull requests repeat about 80 times an hour and are rate-limited

**Status:** ✅ COMPLETE (2026-09-24, batch 2b).
**Where:** the Webull SDK's `/openapi/config` request, reached through the provider or stream reconnect path.

**Verified (live logs):** `/openapi/config` fails with `TOO_MANY_REQUESTS` (HTTP 429) about 80 times an hour between 01:00 and 04:00 ET. Examples: 89, 79, and 85 an hour on 2026-09-24 from 05:00 to 07:00 UTC, with the same pattern on 2026-09-23. In the last 50 MB of `backend.log`, 1,021 of 1,111 Webull `ServerException` records are 429s.

**Impact:** each failure writes a credential-bearing log record (MD-02) and keeps Webull's rate limit tripped at a time when no market data is needed.

**Root cause:** the stream reconnect supervisor (`backend/market_data/streaming/webull_stream.py:_supervise`) rebuilds the SDK client — a fresh token handshake, hitting `/openapi/config` — on every reconnect attempt, 24/7, with no gate for the market being closed. Its backoff (5s, doubling to a 300s ceiling) resets to 5s on any brief, even momentary, connect — so a connection that connects then drops right away churns at roughly the low end of that ramp indefinitely instead of climbing, which is consistent with the observed ~80/hour (about one every 45s).

**Resolution:**

- **Paused outside the extended session:** `_in_extended_session` gates the whole reconnect loop to 04:00-20:00 ET, Mon-Fri — the same window `ingestion_service._gapfill_1m_loop` already uses. No market data is needed outside it, and this alone removes every call in the reported 01:00-04:00 ET window. Polls every 5 minutes while paused, and logs one line entering and one line leaving the pause rather than nothing (silent) or one per poll.
- **A confirmed 429 skips the normal ramp:** `_is_rate_limited` (`webull_provider.py`, reused by the supervisor) checks the SDK's own `ServerException.get_error_code()`/`get_http_status()`, falling back to a string match. On a confirmed 429, the supervisor waits `_RATE_LIMITED_BACKOFF_S` (300s) outright instead of the normal doubling-from-5s ramp, and skips the up-to-25s connection wait it already knows will fail.
- **One summary line per burst:** `_RateLimitSummaryFilter` (`webull_provider.py`), attached to the SDK's `webull.core.client` logger, lets through only the first `TOO_MANY_REQUESTS` ERROR record in a rolling 60s window — collapsing the SDK's own per-request `ServerException` + `get_response exception` pair. The supervisor's own per-attempt `logger.warning` already accounts for every attempt; this only trims the SDK's duplicate low-level record. Scoped to the whole `webull.core.client` logger, so it also trims a 429 burst from the REST provider, not just the stream.

**Tests:**

| File | What it covers |
|---|---|
| `test_webull_stream.py` (5 new, plus 2 existing tests pinned to a fixed in-session clock) | Gate boundaries (session start/end, weekend); no reconnect attempted outside the session; reconnects resume once it starts; a confirmed 429 uses the long cooldown; a non-429 failure keeps the normal ramp. |
| `test_webull_provider.py` (11 new) | `_is_rate_limited` against the real SDK exception types and a string fallback; `_RateLimitSummaryFilter` collapses a burst to one line, a second burst after the window passes again, unrelated/non-ERROR records are never suppressed. |

Full run: `backend/tests/market_data`, `scripts`, `observability` — 567 passed.

**Note:** the two existing `TestSupervisor` tests started depending on wall-clock time the moment the session gate was added — they call the real `_supervise()` loop via `.start()`, and it now checks `now_ny()`. Both now patch `webull_stream.now_ny` to a fixed in-session moment so they stay deterministic regardless of when the suite runs.

### MD-07 — After each restart, signal recording takes about 30 minutes to catch up

**Status:** ✅ COMPLETE (2026-09-24, batch 3).
**Where:** `SignalRecorder.record_from_recent_bars` with `SEED_BUDGET_SECONDS = 3.0` (`backend/services/signal_recorder.py:40`). A restarted process must re-seed every symbol and timeframe before it records new bars for them.

**Verified (live database, during the Historical Signals HS-12 rollout):**

- **Before catch-up:** after the 16:46 ET restart, 89 closed 1m bars from 16:40–16:47 across 15 symbols still had no signal at 16:51.
- **After:** by 17:19 all had one, and no closed bar from that day was missing a signal.

With `--reload`, every saved backend file restarts the process.

**Impact:** anything reading recent signals lags by up to about 30 minutes after a restart: Chat's signal history, AI signal stats, and the Replay markers. No data is lost.

**Root cause:** `_SEED_ORDER` prioritized "cheapest first" — the coarse timeframes, because they have the fewest bars and were assumed most durable — which meant 1m, the timeframe closest to real time, was always seeded dead last, after every other pair of every symbol. `_replays` is in-memory and always starts empty on a restart, so every pair needs a full-history replay before it can be advanced live.

**Resolution:** `_SEED_ORDER` now ranks by how often a timeframe closes — 1m first, 1wk last — not by replay cost. This directly matches the resolution's "seed the pairs with the most recent unrecorded closed bars first": whichever timeframe closes most often always has the freshest unrecorded bar, and matters most for near-real-time features (Chat's signal history, AI signal stats, Replay markers). `SEED_BUDGET_SECONDS` was also doubled, 3.0 → 6.0, to shrink the wall-clock time for whichever tier is in progress.

This does **not** reduce the total CPU cost of a cold restart's catch-up — every pair still replays its full stored history, which the recorder needs to correctly rebuild the trend engine's state, not just to avoid rewriting rows that already exist. It only decides which timeframe's lag is felt. Coarse timeframes (1h now retains 366 days per MD-01, averaging ~2,770 bars/pair across the 23 watched symbols) now wait longer than before, in exchange for the timeframe the finding was actually about no longer waiting at all.

**Tests:**

| File | What it covers |
|---|---|
| `test_signal_replay.py` (1 test updated, 2 new) | The two-timeframe budget-ordering test now expects 1h before 1d (was 1d first); the full `_SEED_ORDER` ranking is asserted directly; a live symptom regression test (a 1m pair queued after a 1d pair is still seeded first). |

Full run: `backend/tests/services`, `test_signals_api.py`, `migrations` — 121 passed. Full backend suite — 3,494 passed.

**Live confirmation (2026-09-24 19:23 restart):** 1m signals started writing 24 seconds after restart and reached steady-state (advancing with each newly-closed bar, no backlog) within about 2 minutes; 2m, 3m, 5m, 15m, and 30m cascaded through in that same order right behind it, confirmed from each row's `created_at`. No errors. The coarser tiers (1h, 4h, 1d, 1wk) took considerably longer to be reached, as expected given the total-cost trade-off above; the API stayed responsive throughout (health check ~2ms) since this work runs off the main event loop thread.

### MD-08 — The live schema is missing two indexes the migrations create

**Status:** ✅ COMPLETE (2026-09-24, batch 3).

**Verified:** a fresh database built with `alembic upgrade head` was compared with the live database. The only difference is that the live `historical_signals` table lacks `ix_historical_signals_symbol` and `ix_historical_signals_timeframe`. All columns match. The retention prune's query plan uses `ix_historical_signals_timestamp`, and the new unique index covers symbol lookups, so no query was found to be slower.

**Investigated further:** the model (`index=True` on both columns) and the initial-schema migration both say these indexes should exist, and no migration in the history drops them — `20260919_index_tuning`'s own docstring even states "historical_signals keeps its symbol/timeframe indexes" while dropping other redundant ones elsewhere, implying they were believed present at the time. The live table was most likely created outside the migration chain at some point, before they existed, and nothing since has needed to touch this table's indexes in a way that would have caught the drift.

**Resolution:** `alembic/versions/20261004_historical_signals_missing_indexes.py` — `CREATE INDEX IF NOT EXISTS` for both, a no-op on a fresh build and additive on the live database. Chosen over dropping them from the model: a query pattern that would benefit from filtering by symbol or timeframe alone isn't ruled out just because none was found today.

**Process note:** this migration was applied to the live database automatically, by `--reload`, the moment the file was saved into `alembic/versions/` — before the intended scratch-tree validation step ran. The migration is a simple, idempotent, non-destructive DDL change (no data touched, no table rebuild), and it applied cleanly with no errors; `pragma integrity_check` passed and both indexes were created on the correct columns. Still a process miss: the established rule (validate in a scratch tree → `.backup` → install) exists precisely so a riskier migration doesn't get this same accidental live exposure.

**Tests:** `backend/tests/migrations/test_historical_signals_missing_indexes.py` (new, 4 tests) — a fresh build already has both indexes; a database with the exact drifted state found live (migrated to the prior revision, then the two indexes dropped by hand) gets them added; downgrade removes them and upgrade restores them; the index columns are correct.

### MD-09 — A recorder comment describes intraday snapshots in the daily series that no longer exist

**Status:** ✅ COMPLETE (2026-09-24, batch 3).
**Where:** `SignalRecorder._compute_outcome_for_signal` and `SignalRecorder._price_at` (`backend/services/signal_recorder.py`, the 1d anchor comments — the same false claim appeared in both).

The comment says the 1d table mixes midnight bars with 13:30 intraday snapshots from Alpaca. `_normalize_1d_bar` has normalized daily bars since 2026-09-09.

**Verified:** no stored 1d or 1wk bar has a non-midnight timestamp (checked again on 2026-09-24, after this session's other bar-repair work).

**Resolution:** corrected both comments to describe the current, actual reason for the midnight normalization — `_normalize_1d_bar` already guarantees every stored 1d bar is at midnight, so the anchor normalization is a no-op in practice, kept as a cheap guard against a caller passing a non-midnight timestamp. The midnight anchor itself is unchanged, since daily bars are stamped at midnight. The similarly-worded "13:30 ET noise" comments elsewhere (`ingestion_service.py`, `backfill_service.py`) were checked too and left alone: they already correctly describe themselves as a defensive backstop behind normalization, not a claim that the mixing currently happens.

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

### Batch 3 (2026-09-24, MD-07, MD-08, MD-09)

| Suite | Result |
|---|---|
| `backend/tests/services`, `test_signals_api.py`, `migrations` | 121 passed |
| Full backend suite | 3,494 passed |
| Live: restart, signal-recording priority order | 1m signals from 24s post-restart, steady-state by ~2 min; 2m/3m/5m/15m/30m cascaded in order behind it; no errors |
| Live: MD-08 migration | applied automatically by `--reload` before the intended scratch-tree validation ran (a process miss — see the MD-08 entry); integrity check passed, both indexes created correctly regardless |

### Batch 2b (2026-09-24, MD-05, MD-06)

| Suite | Result |
|---|---|
| `backend/tests/market_data`, `scripts`, `observability` | 567 passed |
| New: `test_rotate_stdin.py` (5), 5 new + 2 pinned in `test_webull_stream.py`, 11 new in `test_webull_provider.py` | 21 passed |
| Full backend suite | 3,488 passed |
| Live: restart with the new redirects | backend/frontend/rq_workers/rq_backfill logs all received correct content through their rotators; no rotator traceback |

### Batch 2a (2026-09-24, MD-04)

| Suite | Result |
|---|---|
| `backend/tests/market_data`, `services`, `watchlist`, `repositories`, `config` | 808 passed |
| New: `test_watchlist_repository_active_filter.py`, 6 new in `test_purge_service.py`, 2 new in `test_watchlist_api.py` | 13 passed |
| Live: six named orphans purged | 34,119 rows across bars, signals, quotes, market_status, backfill_jobs; verified 0 remaining |
| Live: restart + startup sweep pass | 0 orphans found (already purged by name); ingestion continued normally (23 symbols) |

### Batch 1c (2026-09-24, MD-03)

| Suite | Result |
|---|---|
| `backend/tests/market_data`, `backend/tests/repositories`, `backend/tests/config`, `test_chat_reply_paths.py` | 691 passed |
| New: `test_sip_settle.py` | 8 passed |
| SIP probe after the live settle | every minute with a SIP bar matches it; 1h = sum of 1m for 3,964 of 3,964 hours |

### Batch 1b (2026-09-24, MD-01)

| Suite | Result |
|---|---|
| `backend/tests/market_data` and `backend/tests/repositories` | 668 passed |
| New: `test_hourly_bars.py`, `test_hourly_repair.py` | 15 passed |
| `ruff check` on the changed files; `ruff format` on new files | clean |
| Repair trial on a `.backup` copy | 25 symbols, 0 failures, 0 misplaced hours, 0 1h rows off the hour |
| Live repair and restart | as the trial; 63,706 1h and 16,190 4h signals re-recorded |

### Batch 1a (2026-09-24)

| Suite | Result |
|---|---|
| `backend/tests/observability/test_secret_redaction.py` | 8 passed; 4 fail with the factory not installed |
| Full backend suite | 3,425 passed |
| `ruff` on the changed files | clean |

### Review probes (2026-09-24, read-only)

| Check | Result | Finding |
|---|---|---|
| Regular-session 1h closes since 2026-09-09 compared with 1m closes | Webull 312/312 and Yahoo 844/846 shifted 30 min; `live_from_1m` 370/370 aligned | MD-01 |
| 1h provider rows before the 1m window; 4h rows before it | 32,824; 10,192 | MD-01 |
| Credential headers in logs (values not printed) | `x-access-token` 1,413 times in `backend.log`; also in `marketlens.log`, its rotated copies, the RQ worker logs and the Webull SDK logs. After cleanup: 0 files in `logs/` | MD-02 |
| SPY 1h volume against its 1m sum, 2026-09-16 | Alpaca 200 vs 75,289 and 2,313 vs 6,452,715 | MD-03 |
| Symbols with bars but in no watchlist | NOK, XLK, GOOGL, RIVN, SOXL, WMT | MD-04 |
| `logs/` size | `backend.log` 442 MB in 11 days; directory 842 MB | MD-05 |
| Webull 429s by hour | about 80 an hour, 01:00–04:00 ET | MD-06 |
| Closed bars without signals after a restart | 89 at 16:51, none by 17:19 | MD-07 |
| Fresh migrated schema against the live schema | two missing `historical_signals` indexes; columns identical | MD-08 |
| 1d and 1wk bars not at midnight | 0 | MD-09 |

The fresh schema for MD-08 was built under `.pytest_tmp/` and deleted afterwards. `conf/token.txt` was unchanged by the Alembic run.

## Fix log

- **Review:** commit `e4c1d69`, `docs(v5): review market data ingestion and track findings`.
- **Batch 1a (MD-02):** `63a11a8`, tracker completed after token rotation in `6798e22`.
- **Batch 1b (MD-01):** `65ce005`.
- **Batch 1c (MD-03):** `11246cc`.
- **Batch 2a (MD-04):** `051b048`.
- **Batch 2b (MD-05, MD-06):** `68b5478`.
- **Batch 3 (MD-07, MD-08, MD-09):** in the working tree, not yet committed.

| Date | ID | Status | Commit | Files | Tests | Notes |
|---|---|---|---|---|---|---|
| 2026-09-24 | MD-01 to MD-09 | ❌ NOT STARTED | `e4c1d69` | `docs/Version_5/v5_market_data_bug_fixes.md` | 10 probes | Review logged nine findings. |
| 2026-09-24 | MD-03 | ✅ COMPLETE | `11246cc` | `sip_settle.py`, `scripts/settle_1m_from_sip.py`, `ingestion_service.py`, `bar_repository.py`, `alpaca_provider.py`, `settings.py`, `chat_replies.py` | 11 new | 1m settled from Alpaca SIP, live and one-off; IEX labelled. |
| 2026-09-24 | MD-01 | ✅ COMPLETE | `65ce005` | `hourly_bars.py`, `hourly_repair.py`, `scripts/repair_hourly_bars.py`, `ingestion_service.py`, `backfill_service.py`, `bar_repository.py`, `alpaca_provider.py`, `settings.py`, `.env.example` | 24 new, 1 rewritten | 1h on the clock hour; Alpaca SIP; live 1h/4h history repaired and signals re-recorded. |
| 2026-09-24 | MD-05 | ✅ COMPLETE | `68b5478` | `scripts/rotate_stdin.py`, `restart_dev.sh` | 5 new | 50MB x5 rotation on backend/frontend/rq_workers/rq_backfill logs; dated Webull SDK logs pruned past 7 days. |
| 2026-09-24 | MD-06 | ✅ COMPLETE | `68b5478` | `webull_stream.py`, `webull_provider.py` | 16 new, 2 updated | Reconnect paused outside 04:00-20:00 ET; 429 skips the normal backoff ramp; SDK log burst collapsed to one line. |
| 2026-09-24 | MD-04 | ✅ COMPLETE | `051b048` | `purge_service.py`, `watchlist_repository.py`, `ingestion_service.py`, `api/watchlist/router.py`, `settings.py`, plus 3 new test files | 13 new, 2 updated | Periodic sweep; deactivate refreshes ingestion; `symbol_exists_in_any_watchlist` now joins `Watchlist.is_active`; six known orphans purged live (34,119 rows). |
| 2026-09-24 | MD-07 | ✅ COMPLETE | batch 3 | `signal_recorder.py` | 1 updated, 2 new | `_SEED_ORDER` ranks by close frequency (1m first); `SEED_BUDGET_SECONDS` 3.0 to 6.0; live-confirmed 1m catches up in ~2 min. |
| 2026-09-24 | MD-08 | ✅ COMPLETE | batch 3 | `alembic/versions/20261004_historical_signals_missing_indexes.py` | 4 new | `CREATE INDEX IF NOT EXISTS` for both missing indexes; applied live. |
| 2026-09-24 | MD-09 | ✅ COMPLETE | batch 3 | `signal_recorder.py` | — | corrected two stale comments claiming the 1d table still mixes 13:30 snapshots. |
| 2026-09-24 | MD-02 | ✅ COMPLETE | `63a11a8` | `backend/observability/redaction.py`, `webull_provider.py`, `structured_logging.py`, `test_secret_redaction.py` | 8 tests | New records redacted; 114 old log files holding credentials deleted or emptied; workers restarted; redaction confirmed on 230 live Webull errors; Webull app key/secret rotated 18:36. |

---

## Reference

Ingestion writes 1m bars from the live provider (Webull), fills gaps from fallback providers, settles each minute from Alpaca SIP once it is 15 minutes old (since MD-03), and resamples 2m to 30m from 1m. After a restart, the signal recorder seeds the pairs closest to real time first (since MD-07). A periodic sweep purges symbols not in an active watchlist once they go stale (since MD-04). 1h is built from 1m where 1m exists and otherwise comes from Alpaca SIP (since MD-01); a provider 1h bar never replaces a 1m-built one; 4h is resampled from 1h; 1d comes from providers and 1wk from 1d. Every write goes through `upsert_bars`, keyed on `(symbol, timeframe, timestamp)`, so the last writer wins except over a settled bar (`_SETTLED_BY`), and each bar records the `provider` that wrote it. Retention prunes each timeframe on its own window. A periodic sweep (since MD-04) purges any symbol with stored bars or signals that is not in an active watchlist and has gone stale, protected by a grace period for on-demand lookups. Historical Signals, the trend engines, charts, Chat, and AI Analysis all read these stored bars.
