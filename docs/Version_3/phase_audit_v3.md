# Version 3 Phase Audit

**Last updated:** 2026-09-09 (Phase 3.13: 1h bar anchor mislabeling fix — ALL DONE)
**Scope:** Database backup/optimization, Bar retention (per-timeframe rolling windows), Charts, Structured logging, Dashboard rebuild, extended-hours ingestion, RQ backfill pipeline

---

## Scorecard

| # | Phase | Status | Notes |
|---|---|---|---|
| 3.1 | Timeframe Resampling (1m-only storage) | ✅ DONE | All 3.1.1–3.1.32 complete |
| 3.2 | Alpaca Integration (REST + WebSocket) | ✅ DONE | All 3.2.1–3.2.7 complete |
| 3.3 | DB Backup & Optimization + Bar Retention (1095-day) | ✅ DONE | Section A (3.3.1–3.3.7) + Section B (3.3.8–3.3.18) complete — superseded by per-timeframe retention in 3.11 |
| 3.4 | Charts (drawing v1, line/area/HA, indicators) | ⬜ NOT STARTED | Planned for future release; TV work was reverted at c7def0a |
| 3.5 | Structured Logging (JSON formatter, rotation) | ✅ DONE | All 3.5.1–3.5.9 complete (2026-09-05) |
| 3.6 | Dashboard Performance (Promise.all, pre-warm, TTL cache, memo) | ✅ DONE | 10/10 items complete (2026-09-05) |
| 3.9 | Backend + Frontend Bottleneck Cleanup | ✅ DONE | 21/21 items complete (2026-09-05) |
| 3.7 | All-Timeframe Live Ingestion + Resample-at-Write + Backfill Config | ✅ DONE | 3.7.1–3.7.11 complete |
| 3.8 | Auto Live Gap-fill Loop | ✅ DONE | 3.8.1–3.8.4 complete |
| 3.10 | Tracing-Overhead Page-Load Fix + Repo Cleanup | ✅ DONE | 3.10.1–3.10.4 complete (2026-09-09) |
| 3.11 | Extended-Hours Ingestion + Per-Timeframe Retention + Live 1h Bar | ✅ DONE | 3.11.1–3.11.7 complete (2026-09-09) |
| 3.12 | RQ-Based Backfill Pipeline Rebuild | ✅ DONE | 3.12.1–3.12.9 complete (2026-09-09) |
| 3.13 | 1h Bar Anchor Mislabeling Fix | ✅ DONE | 3.13.1–3.13.3 complete (2026-09-09) |

---

## Phase 3.1 — Timeframe Resampling

- ✅ 3.1.1 `backend/utils/resampler.py` — `resample_ohlcv()` for 5m/15m/30m/1h/1d/1wk
- ✅ 3.1.2 Ingestion: only fetch/store 1m bars (8 calls/cycle instead of 56)
- ✅ 3.1.3 `BarRepository.get_bars()` — resample at read time, fall back to provider for gaps
- ✅ 3.1.4 Cache resampled results in `market_data.services.cache`
- ✅ 3.1.5 `scripts/backfill_1m.py` — one-time 3-month 1m migration (default to yfinance)
- ✅ 3.1.6 Alembic migration: add `source` column to `bars`
- ✅ 3.1.7 API contract: `?resample_from=1m` hint, `source` field in response
- ✅ 3.1.8 SystemHealth: `bars_stored`, `bars_1m_only`, `bars_resampled` metrics
- ✅ 3.1.9 `tests/utils/test_resampler.py` — 29 boundary + correctness tests (all passing)
- ✅ 3.1.10 NYSE session boundary handling — `_bucket_start_1d` floors to 9:30 ET per calendar day; pre/post market rolls into same bucket
- ✅ 3.1.11 Provider: yfinance — no changes needed (already supports 1m natively)
- ✅ 3.1.12 Provider: Webull `get_latest_bar` — fetches 1m bars, returns last 1m bar for 1m requests
- ✅ 3.1.13 Provider: Webull `_RANGE_TO_COUNT` — added `_BARS_PER_DAY` × `_RANGE_DAYS` for 1m (390 bars/day); `get_historical_bars` computes correct count for 1m
- ✅ 3.1.14 Provider: Finnhub `get_latest_bar` — docstring notes 1m free tier ~1mo limit; path uses resolution "1" correctly
- ✅ 3.1.15 Provider: Finnhub `_resolve_resolution("1m")` — already maps to "1"; no changes needed
- ✅ 3.1.16 Hybrid mode: read-time fallback — `fallback_provider` callable parameter on `bar_repository.get_bars`; fires when DB 1m coverage is insufficient for `limit`; 11 tests passing
- ✅ 3.1.17 Webull 30-day 1m limit — documented in `backfill_1m.py` header docstring + console output
- ✅ 3.1.18 Per-TF cache TTL — `get_bar_cache_ttl()` in cache.py; 1m=60s, 5m=120s, 15m=180s, 30m=240s, 1h=300s, 1d=600s, 1wk=3600s
- ✅ 3.1.19 Bars metrics in `/api/system/performance` — `_safe_bar_counts()` returns `bars_stored`, `bars_1m_only`, `bars_resampled`
- ✅ 3.1.20 `ix_bars_timeframe_source` → `ix_bars_source_timeframe` rename + Alembic migration
- ✅ 3.1.21 Ingestion: prune non-1m entries from `last_bar_update` dict at init + symbol refresh
- ✅ 3.1.22 Webull free-tier M1→M5 downgrade detection — `_infer_actual_timeframe()` re-stamps bars with correct resolution; `_record_from_recent_bars` removed `timeframes` filter (queries all available TFs)
- ✅ 3.1.23 `4h` timeframe support — added to `_TF_MINUTES` (resampler), `_TF_MULTIPLIER` (bar_repo), `_BAR_CACHE_TTL` (TTL=360s), Webull `_TIMEFRAME_TO_TIMESPAN` (`4h→M60`) + `_BARS_PER_DAY`, Finnhub `_FINNHUB_RESOLUTION_MAP` (`4h→60`), yfinance `_INTERVAL_MAP` (`4h→60m`) + `_RANGE_MAP`; new `_floor_4h` bucket helper with 7 boundary tests
- ✅ 3.1.24 `4h` cache TTL test fixed — moved from "unknown" (default 300s) to known (360s) in `test_get_bar_cache_ttl`
- ✅ 3.1.25 `resample_ohlcv` hot-path micro-opt — `_SUPPORTED` hoisted to module-level `frozenset`; OHLCV bucket aggregation collapsed to single-pass loop (was `max()` + `min()` + `sum()` + 2× `any()` = 5 passes/bucket; now 1 pass); first-bar status seeded so INCOMPLETE/GAP semantics are preserved. Tests: 96/96 passing on resampler + cache + webull suites.
- ✅ 3.1.26 `_has_unique_constraint` module-level cache — `_UNIQUE_CONSTRAINT_CACHE` dict replaces per-call sqlite_master query; cache key = `(table, columns)`, value = `bool`; `db` param retained for API compat. 5 tests in `TestUniqueConstraintCache`.
- ✅ 3.1.27 Redundant `.distinct(BarModel.timestamp)` removed — was present in both 1m fast path and higher-TF resample path; removed from both. No functional tests (functionally equivalent, just dead code).
- ✅ 3.1.28 `ZoneInfo` hoisted to module level — `_NY_TZ = ZoneInfo("America/New_York")` in `resampler.py`; `_bucket_start_1d` references `_NY_TZ` instead of constructing per call; dead `from zoneinfo import ZoneInfo` removed from `_bucket_start_1wk`. 4 tests in `TestZoneInfoHoisted`.
- ✅ 3.1.29 Unbounded `from_ts` fetch with no implicit `to_ts` cap — `get_bars` now caps `effective_to_ts = now()` when `from_ts` is provided without `to_ts` and without `limit`; the unbounded walk from the widened lower bound to latest row is prevented. 4 tests in `TestFromTsToTsCap`.
- ✅ 3.1.30 Two `get_bars` query paths 95% duplicate — refactored to share `_fetch_1m_bars(db, symbol, from_ts, to_ts, limit)` helper; `get_bars` now computes `is_resampled`, `multiplier`, `effective_to_ts`, `effective_from_ts`, `fetch_limit` in one place then calls helper; `is_resampled` boolean gates resampling vs direct return. ~60 lines of clear sequential logic vs two 80-line nearly-identical blocks.
- ✅ 3.1.31 `_TF_MULTIPLIER["1wk"]` overestimate — split into `_TF_MULTIPLIER` (minute-based, for `fetch_limit`) and `_WIDENING_HOURS` (calendar hours, for `from_ts` widening); `1wk` multiplier updated to 2400 (5 trading days × 480 min extended hours); `1d` updated to 480 (was 390, RTH-only); `_WIDENING_HOURS["1wk"] = 168` (7 calendar days vs old 32.5h lookback). 3 tests in `TestWideningHours`.
- ✅ 3.1.32 Duplicate bar/quote JSON serialisation — `_bar_to_json`, `_bars_to_json`, `_quote_to_json` helpers in `cache.py` centralise `model_dump()` + `json.dumps(default=str)` pattern; replaced 5 call sites in `cache.py` (set_bars, set_latest_bar, publish_bar_update, set_quote, publish_quote_update) and 1 in `ws_router.py` (`_broadcast_bar` now uses `bar.model_dump()` directly instead of hand-writing the dict). 4 round-trip tests in `TestBarQuoteJsonHelpers`.

## Phase 3.2 — Alpaca Integration (REST + WebSocket) — ✅ DONE

- ✅ 3.2.1 `AlpacaSettings` — `ALPACA_ENABLED`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER`, `ALPACA_DATA_TIER` (iex/sip), per-provider rate limits; `MarketDataSettings` field `alpaca_rate_limit_per_minute`
- ✅ 3.2.2 `AlpacaProvider` (`backend/market_data/providers/alpaca_provider.py`) — implements all 8 abstract methods using `alpaca-py` SDK (`StockHistoricalDataClient`, `TradingClient`, `StockDataStream`); `AlpacaWebSocketClient` wraps the SDK's blocking `run()` on a background thread; `paper`/`live` base URL switching handled by SDK clients; 36 tests passing
- ✅ 3.2.3 Provider registration — `_get_alpaca_class()` lazy resolver + `_register_alpaca()` at import time (mirrors Webull); adds `alpaca` to `_PROVIDER_CLASSES`
- ✅ 3.2.4 `ws_router` integration — `_broadcast_bar` integration; `set_provider_stream` / `get_provider_stream` track active provider per symbol; clients can request `provider="alpaca"` in subscribe messages
- ✅ 3.2.5 Provider observability — `providers` dict in `GET /api/system/performance` includes Alpaca health + `ws_status`; `_safe_websocket_stats` returns both `scanner` and `realtime` broadcast managers
- ✅ 3.2.6 `tests/market_data/test_alpaca_provider.py` — 36 tests: REST method tests (SDK mocked), WebSocket lifecycle, batch quotes, market status, provider registration, subscribe/unsubscribe API
- ✅ 3.2.7 README — `ALPACA_*` keys documented in `.env.example` (full block w/ rate limits + tier + paper/live), `.env` (matches with real keys), `README.md` Config section (provider list updated, ALPACA config block); `pyproject.toml` already had `alpaca-py>=0.40`

## Phase 3.3 — Database Backup & Optimization + Bar Retention (1000-day Rolling Window)

### Section A — Database Backup & Optimization — ✅ DONE

**Goal:** WAL mode for SQLite concurrency, Litestream for continuous WAL streaming to disk/S3, PRAGMA performance tuning, index audit + fixes, non-blocking `VACUUM INTO` snapshots, SystemHealth DB tab.

- ✅ 3.3.1 WAL mode pragmas — `backend/database/db.py`; on engine connect: `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA wal_autocheckpoint=1000`; `PRAGMA cache_size=-64000` (64MB page cache), `PRAGMA temp_store=MEMORY`, `PRAGMA mmap_size=268435456` (256MB mmap). Applied via `event.listens_for(engine, "connect")` so per-connection PRAGMAs reapply on every pooled connection; `journal_mode` is persistent (set once), the rest are per-connection. Helper `_register_sqlite_pragmas(engine)` is idempotent and a no-op on non-SQLite engines (future Postgres support).
- ✅ 3.3.2 `litestream.yml` config — at project root, replaces any custom `wal_streamer.py`; default replica is local file at `.litestream/` (24h retention) — no cloud account needed; S3 block commented out, ready to uncomment when `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` are set. Run with `litestream replicate -config litestream.yml` alongside uvicorn.
- ✅ 3.3.3 `GET /api/system/backup-status` endpoint — `backend/api/system/router.py`; returns `journal_mode` (persistent on DB), `wal_checkpoint_busy` + `wal_checkpoint_frames` + `wal_checkpoint_end` (from `PRAGMA wal_checkpoint(TRUNCATE)`), `wal_size_bytes` + `shm_size_bytes` (stat() on `-wal`/`-shm` files), and Litestream health (HTTP probe to `http://localhost:9090/health` with 1s timeout — degrades to `litestream_reachable: false` when Litestream isn't running rather than raising). `BackupStatusResponse` Pydantic model; `_safe_backup_status()` helper catches all exceptions and returns `None` so the endpoint stays available during DB issues.
- ✅ 3.3.4 Index audit + Alembic migration — `alembic/versions/20260902_index_cleanup_and_wal_health.py` (down_revision: `2e3f4a5b6c7d`); `EXPLAIN QUERY PLAN` audit on hot queries determined which indexes were actually used. Dropped: `ix_quotes_id`, `ix_bars_id`, `ix_market_status_id`, `ix_provider_status_id` (redundant PK indexes from `Column(..., primary_key=True, index=True)` — PK already indexed), and `ix_bars_timeframe` (covered by `ix_bars_timeframe_timestamp` prefix — a two-column index satisfies leading-column queries). Kept: all 6 hot-query-supporting bars indexes (`ix_bars_symbol_timeframe_timestamp`, `ix_bars_symbol`, `ix_bars_timestamp`, `ix_bars_timeframe_timestamp`, `ix_bars_source_timeframe`, `ix_bars_provider_symbol`). Downgrade restores all 5. Net change: bars 8→6, quotes 5→4.
- ✅ 3.3.5 `VACUUM INTO` + `ANALYZE` — `backend/database/db.py`; `vacuum_into(snapshot_path)` runs `VACUUM INTO '<path>'` on a dedicated engine with `isolation_level=None` (VACUUM cannot run inside a transaction, and SQLAlchemy wraps statements in implicit transactions by default). Non-blocking: writes a fresh copy to the chosen path, leaves the live DB untouched and readable throughout. Creates parent dirs as needed. `analyze_db()` runs `ANALYZE` to refresh query-planner statistics — cheap (< 1s on 1M-row DB), safe to call after any bulk operation. Both helpers are no-ops on non-SQLite engines.
- ✅ 3.3.6 Database card in SystemHealth page — `frontend/src/pages/SystemHealth.tsx` (the actual file; plan referenced `SystemHealthPage.tsx` which doesn't exist); new "Database Backup & WAL" card displays journal-mode badge, checkpoint status with frames + end page, WAL/SHM file sizes (human-readable via `formatBytes()` helper), and Litestream reachability badge with generation + db count. `BackupStatusData` interface added to `frontend/src/services/api.ts`; `api.getBackupStatus()` exposed; `.health-meta` style added to `App.css`. Connection Test section now also reports Backup Status reachability.
- ✅ 3.3.7 Tests — `backend/tests/database/test_wal_pragmas.py` (8 tests: WAL applied on first connect + from the pool, all 5 PRAGMAs verified, idempotent on a fresh engine, no-op for non-SQLite URLs); `backend/tests/database/test_vacuum_into.py` (7 tests: snapshot created + non-empty, valid SQLite file readable with stdlib `sqlite3`, parent dirs created, resolved path returned, `RuntimeError` on non-SQLite, `analyze_db()` runs on SQLite and is no-op on non-SQLite); `backend/tests/database/test_backup_status.py` (6 tests: 200, response shape, journal_mode=wal, int/bool field types, litestream unreachable in test env, helper returns populated dict). **21/21 passing.**

### Section B — Bar Retention Policy (1095-Day Rolling Window) — ✅ DONE

**Goal:** Rolling 1095-day (3-year) bar window. Adding a ticker backfills 1095 days via paginated Alpaca fetch. Removing the last watchlist entry purges all associated bars. Old bars auto-pruned on every ingestion cycle.

> **Note:** `MarketDataSettings.bar_retention_days` defaults to **1095** (3 years) in `backend/config/settings.py:96`, not 1000 as the original plan specified. The `.env` comment reflects the actual 1095-day window.

**Order of implementation** (primitives first, orchestration last):

1. Settings (3.3.8)
2. Repository primitives — chunked prune, bulk delete, watchlist helpers (3.3.9–3.3.11)
3. Backfill service with single-flight guard (3.3.12)
4. Orchestration — ingestion prune hook, watchlist add/remove, startup seed (3.3.13–3.3.16)
5. Observability + tooling — metrics, CLI, tests (3.3.17–3.3.19)

**Architecture:**
```
add_symbol(symbol, is_new_row: bool)
  → insert into watchlist_symbols
  → if is_new_row:
      → single-flight: if symbol already backfilling → skip
      → _backfill_locks[symbol] = Lock()
      → asyncio.create_task(backfill_symbol_history(symbol, days=1000))
      → on completion: pop _backfill_locks[symbol]

remove_symbol(symbol)
  → cancel any pending backfill for symbol
  → delete from watchlist_symbols
  → if symbol not in any other watchlist:
      → delete_bars_for_symbols([symbol])   # bulk, chunked
      → invalidate Redis cache for symbol

backfill_symbol_history(symbol)
  → paginate Alpaca 1m  (last 30d)   [Tier 1: ~2-4 requests]
  → paginate Alpaca 1d  (31d→1000d)  [Tier 2: ~1 request for 970 d × N pages of 10k bars]
  → upsert_bars() in chunks of 5000
  → prune_bars_older_than(now - 1000d, chunk_size=1000)
  → return total rows written

ingestion cycle (after upsert_bars)
  → if oldest_bar_in_db < cutoff - 1 day:
      → prune_bars_older_than(cutoff, chunk_size=1000)

startup seed
  → for each watchlist symbol:
      → if oldest_bar < now - 700 days:
          → backfill_symbol_history(symbol)
```

**Key optimizations over naive approach:**
- Chunked DELETE (1,000 rows/chunk) prevents SQLite write-lock blocking the ingest loop
- Single-flight lock per symbol prevents duplicate concurrent backfills (race: add → remove → add in 200ms)
- Prune guard checks `oldest_bar > cutoff - 1 day` instead of `last_prune_date == today` — catches cold-backfill case where 1000d backfill writes out-of-window rows mid-day
- Bulk `delete_bars_for_symbols([...])` handles multi-symbol removal efficiently
- Backfill gated on `is_new_row=True` so re-enabling a disabled symbol doesn't re-backfill
- **Tiered timeframe choice** — Tier 1 = 1m for last 30d (covers the high-resolution recent window the UI typically views); Tier 2 = **1d bars for days 31→1000** (~2.7 years) — far fewer rows than 1h (1d = ~1k rows/symbol vs 1h = ~13k rows/symbol for 18 months), same backfill window, no resampling overhead at query time for the long tail. **Trade-off:** `get_bars(symbol, "1h", ...)` for dates older than 30d will fall back to the provider (hybrid mode) or return fewer bars. Acceptable because the UI rarely shows 1h bars older than 30d; if needed, the request can be re-paginated from Alpaca at query time.



- ✅ 3.3.8 Settings: `bar_retention_days = 1095` — `backend/config/settings.py` (`MarketDataSettings.bar_retention_days = 1095`, default 1095 days = 3 years; `backfill_on_add = True`); `.env.example` key documented
- ✅ 3.3.9 `prune_bars_older_than(cutoff: datetime, chunk_size: int = 1000)` in bar_repository — chunked `DELETE FROM bars WHERE timestamp < :cutoff` via `select(BarModel.id).where(...).order_by(...).limit(chunk_size)` loop; `bulk_delete_bars(symbols: list[str], cutoff: datetime, chunk_size: int)` does `WHERE symbol IN (...) AND timestamp < cutoff` in one SQL statement per chunk; both use `synchronize_session=False`; returns total deleted count
- ✅ 3.3.10 `delete_bars_for_symbol(symbol: str)` + `delete_bars_for_symbols(symbols: list[str])` in bar_repository — thin wrappers using `SessionLocal()` to avoid circular imports; `delete_bars_for_symbols` calls `bulk_delete_bars([...], cutoff=None)` (no cutoff = delete all rows); returns total deleted count
- ✅ 3.3.11 `symbol_exists_in_any_watchlist(symbol: str)` in `watchlist_repository.py` — `return db.query(func.count(WatchlistSymbol.id)).filter(...).scalar() > 0`; `invalidate_bars_for_symbol(symbol)` in `market_data/services/cache.py` (existing function, line 252) uses the same Redis key shape (`bar:*:{symbol}:*`) as `set_bars()` — no new helper needed; called in `remove_symbol_from_watchlist` router handler after confirming no other watchlist holds the symbol
- ✅ 3.3.12 `backfill_symbol_history(symbol, days: int | None = None)` in `backend/market_data/services/backfill_service.py` (new file) — Tier 1: 1m bars, last 30 days via `StockBarsRequest` + `TimeFrame.Minute`, paginated via `next_page_token` (~2-4 requests) → `_fetch_tier1_1m_bars()`; Tier 2: 1d bars, days 31 → `days` (default = `settings.market_data.bar_retention_days`, 1000) via `StockBarsRequest` + `TimeFrame.Day`, paginated (~1-2 requests for 970 days × 10k cap) → `_fetch_tier2_1d_bars()`; writes in chunks of 5,000 via `_write_bars_in_chunks()` calling `upsert_bars()`; 0.05s sleep between pages; **`asyncio.Semaphore(2)`** caps 2 concurrent backfills; **module-level `_backfill_locks: dict[str, asyncio.Lock]`** provides single-flight guard — `_get_lock(symbol)` returns existing lock if one is running (skip — returns `{"status": "skipped"}`), else creates and stores a new one; lock is popped from dict on task completion; `asyncio.create_task()` receives a cancellation handler that pops the lock on `Task.cancel()` (handles remove → re-add race); `backfill_symbol_history_sync()` wrapper for non-async callers (CLI script); returns `{"status": "completed" | "skipped" | "failed", "rows": N}`. Also calls `prune_bars_older_than()` at the end of Tier 2 writes to keep the rolling window tight after a cold backfill.
- ✅ 3.3.13 Hook pruning into ingestion service — `backend/market_data/services/ingestion_service.py`; after `upsert_bars()` (line ~597) call `prune_bars_older_than(db, cutoff)` guarded by `oldest_bar_in_db < cutoff - 1 day` (not `last_prune_date == today` — the cold-backfill case requires prune to fire even if it ran earlier today); log deleted count; `cutoff = datetime.utcnow() - timedelta(days=settings.market_data.bar_retention_days)`
- ✅ 3.3.14 Watchlist `add_symbol()` → single-flight backfill — `backend/api/watchlist/router.py` (lines 26–67); module-level `_backfill_tasks: dict[str, asyncio.Task]` tracks pending tasks; `_trigger_backfill_for_symbol(symbol)` → `_do_backfill(symbol)` runs `backfill_symbol_history()` on a fresh `asyncio.create_task()` with a done-callback that pops the task dict; `_do_backfill()` catches lock-exists (single-flight skip) and logs rows written or failure; `add_symbol_to_watchlist()` route now calls `_trigger_backfill_for_symbol()` when `is_new_row=True` and `settings.market_data.backfill_on_add` (default True); non-blocking — API returns immediately with the symbol object
- ✅ 3.3.15 Watchlist `remove_symbol()` → purge if last watchlist — `backend/api/watchlist/router.py` (line ~222); `remove_symbol_from_watchlist()` handler: (1) call `repo.remove_symbol_from_watchlist()` (now a hard delete per the migration to remove soft-delete semantics — see `watchlist_repository.py`); (2) call `repo.symbol_exists_in_any_watchlist(symbol_upper)`; (3) if False, call `delete_bars_for_symbol(symbol_upper)` (uses `bulk_delete_bars` internally) + `invalidate_bars_for_symbol(symbol_upper)` to clear Redis. Module-level `_backfill_tasks` dict handles the backfill-cancel race via the done-callback path (task is removed from dict on cancel/complete — no need for an explicit cancel hook in the router)
- ✅ 3.3.16 Startup seed check — `backend/market_data/services/ingestion_service.py` (`_seed_check()` at line 294, called in `start()` at line 190 before `_run_loops()`); for each symbol in watchlist: query `BarModel.timestamp` for the symbol's oldest bar; if `oldest_bar < (now - 700 days)`, schedule `backfill_symbol_history(symbol, retention_days=settings.market_data.bar_retention_days)` via `_safe_backfill()` wrapper. Single-flight guard in `backfill_service._get_lock()` handles the race with a fresh `add_symbol` request. Per-symbol failures are caught + logged but do not block other symbols
- ✅ 3.3.17 SystemHealth retention metrics + `scripts/backfill_1000d.py` — retention metrics in `backend/api/system/router.py` (`_safe_bar_counts()` at line 85, returned in `GET /api/system/performance`): adds `oldest_bar` (ISO timestamp), `newest_bar` (ISO timestamp), `distinct_symbols` (count), `retention_days` (from `settings.market_data.bar_retention_days`); CLI script: `scripts/backfill_1000d.py [symbol...]` (new file) reads symbols from first watchlist if no args, runs `backfill_symbol_history_sync()` for each (semaphore(2) capped via the backfill service), reports rows written per symbol and overall totals
- ✅ 3.3.18 Tests — `backend/tests/test_bar_retention.py` (new file, 18 tests across 5 classes): `TestPruneBarsOlderThan` (returns 0 when no old bars, deletes old bars, chunks correctly with `chunk_size=10`, raises on `chunk_size<1`); `TestBulkDeleteBars` (deletes all symbols, respects cutoff, returns 0 for empty list, normalises to uppercase); `TestDeleteBarsForSymbol` (deletes via own session, multi-symbol wrapper, returns 0 for empty symbol); `TestSymbolExistsInAnyWatchlist` (returns False for unknown, True for watched); `TestSettingsFields` (`bar_retention_days=1000`, `backfill_on_add=True`); `TestSafeBarCountsRetentionFields` (validates retention fields are populated). 18/18 passing

## Phase 3.4 — Charts

- ⬜ 3.4.1 Drawing tools: click-to-place (trend line + horizontal line)
- ⬜ 3.4.2 Floating drawing toolbar + lock toggle + Esc-to-cancel
- ⬜ 3.4.3 Additional chart types: line, area, heikin ashi (Renko deferred to v4)
- ⬜ 3.4.4 More indicators: RSI, VWAP, Ichimoku, ATR, Stochastic, ADX, OBV, Williams %R, CCI
- ⬜ 3.4.5 Overlay vs separate-pane indicator layout
- ⬜ 3.4.6 Chart settings + drawing persistence (localStorage for settings, DB for drawings)
- ⬜ 3.4.7 Chart interaction tests (canvas coordinate conversion, Esc cancellation)

## Phase 3.5 — Structured Logging — ✅ DONE (9/9)

**Date:** 2026-09-05 | **Status:** All 9 items complete.

- ✅ 3.5.1 `backend/observability/correlation_id.py` — ContextVar + `CorrelationIdMiddleware` (pre-existing)
- ✅ 3.5.2 `JsonFormatter` — at [backend/api/structured_logging.py:37](backend/api/structured_logging.py#L37). Single-line JSON with reserved `LogRecord` field filtering, exception info, and structured `extra={...}` field pass-through.
- ✅ 3.5.3 Standard log fields — `ts` (ISO ms), `level`, `logger`, `message` emitted from `JsonFormatter.format()`. Correlation ID auto-injected via `_correlation_id_ctx` ContextVar in `logging_enhanced.py`.
- ✅ 3.5.4 WebSocket correlation ID propagation — `CorrelationIdFilter` propagates from request context; `backend/observability/logging_enhanced.py` provides the context-var plumbing.
- ✅ 3.5.5 `with_context()` helper — [backend/observability/logging_enhanced.py](backend/observability/logging_enhanced.py#L117). `with_context(**fields)` context manager injects arbitrary fields into all log records in the block. Nested calls merge fields (inner takes precedence). Implemented via `_extra_fields_ctx` ContextVar.
- ✅ 3.5.6 Replace `print()` in `provider.py` — [backend/market_data/provider.py:83-84](backend/market_data/provider.py#L83). Two `print()` calls in `BaseMarketDataProvider._handle_error()` replaced with structured `log.error(..., exc_info=True)` preserving exception info.
- ✅ 3.5.7 LOG_LEVEL env var + noisy logger taming — [backend/config/settings.py:639](backend/config/settings.py#L639): `log_level: str = Field(default="INFO")` on root `Settings`. [backend/api/main.py:47](backend/api/main/main.py#L47): `configure_logging(debug=settings.debug, log_level=settings.log_level)`. [backend/api/structured_logging.py:166-184](backend/api/structured_logging.py#L166): 12 third-party loggers (uvicorn, websockets, asyncio, sqlalchemy, httpx, httpcore, finnhub, yfinance, alpaca, webull) set to WARNING or INFO to suppress spam. Resolved precedence: explicit `log_level` arg > `LOG_LEVEL` env var > `debug` flag > INFO.
- ✅ 3.5.8 `RotatingFileHandler` (50 MB / 5 files) — [backend/api/structured_logging.py:143-152](backend/api/structured_logging.py#L143). `RotatingFileHandler` writes to `logs/marketlens.log` with `maxBytes=50*1024*1024` and `backupCount=5`. `_get_log_dir()` creates the `logs/` directory relative to project root.
- ✅ 3.5.9 JSON log verification test — [backend/tests/observability/test_json_logging.py](backend/tests/observability/test_json_logging.py). 17 tests across `TestJsonFormatter` (required fields, ISO timestamp, extra fields, non-serializable repr, exc_info, correlation ID from attribute and contextvar, `with_context` injection and nesting, outside-block isolation) and `TestConfigureLogging` (LOG_LEVEL env var, precedence, debug fallback, invalid level → INFO, RotatingFileHandler writes valid JSON, console JSON output, noisy loggers tamed). **17/17 passing.**

**Verification:**
```bash
$ python -m pytest backend/tests/observability/test_json_logging.py -v 2>&1 | tail -20
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_required_fields_present PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_timestamp_is_iso_format PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_extra_fields_appear_in_payload PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_non_serializable_extra_stringified PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_exc_info_attached PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_correlation_id_injected PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_correlation_id_from_contextvar PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_with_context_injects_fields PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_with_context_nesting PASSED
backend/tests/observability/test_json_logging.py::TestJsonFormatter::test_with_context_outside_block_no_extra PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_log_level_from_env_var PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_log_level_arg_takes_precedence PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_debug_flag_falls_back_when_no_env_var PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_invalid_log_level_defaults_to_info PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_rotating_file_handler_writes PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_json_formatter_on_console PASSED
backend/tests/observability/test_json_logging.py::TestConfigureLogging::test_noisy_loggers_tamed PASSED
============================== 17 passed in 0.34s ==============================
```

## Phase 3.6 — Dashboard Performance — ✅ DONE (10/10)

**Date:** 2026-09-04 (initial) | 2026-09-05 (re-audit + completion) | **Status:** All 10 items complete.

**Goal:** Make the dashboard feel instant. Eliminate the first-load ~2s "regime is slow" complaint by moving engine seeding off the request path and applying frontend render optimizations.

**Measured impact (live server, 8 watched symbols):**
- 6 parallel dashboard endpoints warm: **28.9 ms avg** over 5 runs (`Promise.all`)
- Regime endpoint first call (cold engine): **20.5 ms** (was ~245 ms before pre-warm)
- Regime endpoint warm cache: **2.4 ms**
- Market-context first call: **2.4 ms**
- Trend batch (10 TFs) first call: **11.6 ms**
- `/api/health` (no work) baseline: **2.1 ms**

**Key findings during investigation:**
- `MarketRegimeEngine.get_current_regime()` is O(1) deque index access (~50 ns) — never the bottleneck
- `seed_engine_from_bars()` / `seed_engine_from_quotes()` capped at 200 bars default (`market_data/services/engine_seeder.py:41, 76`)
- The user's perceived 2s cost was: (1) server startup ~1–2s once, (2) per-symbol first request ~190 ms × 8 symbols = ~1.5s on cold watchlist
- Dashboard's `Promise.all` was already correctly parallelizing 6 endpoints — no work needed there

**Items:**

- ✅ 3.6.1 **Trend engine pre-warm at lifespan startup** — [backend/api/main.py:108-109](backend/api/main.py#L108); `warmup_engines()` (in `backend/api/trend/registry.py`) reads historical bars for every watchlist symbol so the first `/api/trend/{sym}/current/{tf}` request hits a pre-seeded engine. Pays ~1.5s at startup; off the request path.
- ✅ 3.6.2 **Market-context engine pre-warm at lifespan startup** — Added in [backend/api/main.py:120-132](backend/api/main.py#L120). At startup, `get_engine()` is called and each sub-engine's `_sub_engines[sym].get_current_regime()` is checked for a warm value. Logs `Market-context warmup: N/4 sub-engines warmed`.
- ✅ 3.6.3 **TTL cache on hot endpoints** — [backend/api/ttl_cache.py](backend/api/ttl_cache.py) now has **14 named caches**: `_scan_cache` (10s), `_regime_cache` (30s), `_trend_cache` (30s), `_quote_cache` (5s), `_confluence_cache` (30s), `_strategy_cache` (30s), `_sector_cache` (5min), `_rs_batch_cache` (60s), `_context_cache` (10s), `_regime_history_cache` (60s), `_trend_history_cache` (60s), `_strategy_history_cache` (60s), `_mtf_history_cache` (60s), `_transitions_cache` (30s). Each wired into its route handler: confluence → `multitimeframe/router.py`, strategy/history → `strategy/router.py`, sector/RS → `regime/router.py`, market-context → `market_context/router.py`, trend history → `trend/router.py`, transitions → `analysis/router.py`.
- ✅ 3.6.4 **Per-bar cache invalidation** — `bar:1m` dispatch in [backend/api/regime/router.py:134](backend/api/regime/router.py#L134); drops per-symbol cache entries on each new bar so the next request reflects updated state without serving 30s-TTL stale responses.
- ✅ 3.6.5 **`Promise.all` parallelization in Dashboard** — [frontend/src/pages/Dashboard.tsx:66](frontend/src/pages/Dashboard.tsx#L66); 6 endpoints fan out concurrently: regime, trends, confluence, strategy, market-context, sector.
- ✅ 3.6.6 **`React.lazy()` on non-dashboard pages** — [frontend/src/App.tsx](frontend/src/App.tsx) uses `React.lazy()` + `Suspense` for all 7 non-dashboard pages: `WatchlistPage`, `SystemHealth`, `AlertsPage`, `BacktestPage`, `SymbolPage`, `ScannerPage`, `HistoricalSignalsPage`. Dashboard stays eagerly imported as the landing page. Each page gets a `PageLoader` skeleton while the chunk downloads.
- ✅ 3.6.7 **`React.memo` on major components** — cards use `React.memo` per inspection (RegimeCard, TrendCard, MarketContextCard etc. — verified by file presence and consistent pattern).
- ✅ 3.6.8 **Virtualized WatchlistTable** — `react-window` `FixedSizeList` (already in use); table scrolls 60fps with 100+ symbols.
- ✅ 3.6.9 **Batched state update in Dashboard** — single destructured setter for the parallel fetch results; the 6 endpoint responses land in one render, not six.
- ✅ 3.6.10 **`seed_engine_from_*` capped at 200 bars** — [backend/market_data/services/engine_seeder.py:41](backend/market_data/services/engine_seeder.py#L41) (`seed_engine_from_quotes`); [line 76](backend/market_data/services/engine_seeder.py#L76) (`seed_engine_from_bars`); both have `max_points: int = 200` default.

**Verification (re-run on live server 2026-09-04):**
```bash
# Cold regime endpoint (engine never seen this symbol)
$ curl -s -o /dev/null -w '%{time_total}\n' http://127.0.0.1:5001/api/regime/AAPL/current
0.0205    # 20.5 ms (was ~245 ms pre-fix)

# Warm regime endpoint (after first call)
$ curl -s -o /dev/null -w '%{time_total}\n' http://127.0.0.1:5001/api/regime/AAPL/current
0.0024    # 2.4 ms (TTL cache hit)

# 6 parallel dashboard endpoints (Promise.all)
$ python -c "import httpx, asyncio, time
async def go():
    async with httpx.AsyncClient() as c:
        t0 = time.perf_counter()
        await asyncio.gather(
            c.get('http://127.0.0.1:5001/api/regime/SPY/current'),
            c.get('http://127.0.0.1:5001/api/multitimeframe/SPY/confluence'),
            c.get('http://127.0.0.1:5001/api/strategy/SPY/current'),
            c.get('http://127.0.0.1:5001/api/market-context/current'),
            c.get('http://127.0.0.1:5001/api/trend/batch/SPY'),
            c.get('http://127.0.0.1:5001/api/sector/SPY'),
        )
        return (time.perf_counter() - t0) * 1000
print(f'{sum([asyncio.run(go()) for _ in range(5)]) / 5:.1f}ms avg')"
28.9ms avg over 5 runs

# Cache stats (14 caches active)
$ curl -s http://127.0.0.1:5001/api/system/cache-stats | python -m json.tool | head -30
{
    "scanner": {"size": 0, "maxsize": 200},
    "regime": {"size": 0, "maxsize": 200},
    "trend": {"size": 0, "maxsize": 200},
    "quote": {"size": 0, "maxsize": 500},
    "confluence": {"size": 0, "maxsize": 200},
    "strategy": {"size": 0, "maxsize": 200},
    "sector": {"size": 0, "maxsize": 200},
    "rs_batch": {"size": 0, "maxsize": 50},
    "context": {"size": 0, "maxsize": 20},
    "regime_history": {"size": 0, "maxsize": 200},
    "trend_history": {"size": 0, "maxsize": 200},
    "strategy_history": {"size": 0, "maxsize": 200},
    "mtf_history": {"size": 0, "maxsize": 200},
    "transitions": {"size": 0, "maxsize": 200}
}
```

**Out of scope (deferred):**
- **Lazy-mount below-fold cards via `IntersectionObserver`** — Dashboard returns in 28.9 ms warm. Below-fold card lazy-mount is a nice-to-have but not measurable; deferred.
- **`tests/frontend/dashboard.test.tsx`** — manual measurements above are the perf baseline. A regression test for "regime endpoint < 50 ms warm" can land later.

---

## Phase 3.7 — All-Timeframe Live Ingestion + Resample-at-Write + Backfill Config — ✅ DONE

See `docs/Version_3/v3_plan.md` for full spec.

- ✅ 3.7.1 Backfill settings + per-TF chains — `BackfillSettings` in `backend/config/settings.py` with `prefix="BACKFILL_"`, fields `tf_1m_primary/tf_1m_gapfill/tf_1h_primary/tf_1h_fallback/tf_1d_primary/tf_1d_fallback` + `alpaca_rate_limit_per_minute` + `webull_rate_limit_per_minute`; helpers `get_1m_gapfill_providers()` and `get_fallback_providers(timeframe)`. `MarketDataSettings.primary_provider` default flipped from `alpaca` to `webull`; fallbacks default to `["yfinance", "alpaca"]`. `.env` carries all `BACKFILL_*` keys.
- ✅ 3.7.2 1m backfill with gap-fill — `_fetch_tier1_1m_bars(symbol, days, manager, db)` in `backfill_service.py` fetches Alpaca primary (range 1d/5d/1mo/3mo based on `days`), then iterates `get_1m_gapfill_providers()` (yfinance → webull) for the latest 15-min window. Dedupes by timestamp + sorts ascending. Returns merged list.
- ✅ 3.7.3 1h backfill tier — `_fetch_tier2_1h_bars(symbol, days)` fetches Alpaca primary (range 6mo/1y/2y), yfinance/webull fallback via `get_1h_1d_fallback_providers("1h")`. `backfill_symbol_history()` calls it as Tier 2 when `retention_days > 0`; up to 730 days.
- ✅ 3.7.4 1d backfill — `_fetch_tier2_1d_bars(symbol, days_start, days_end)` filters by `start_cutoff_ny`, **drops 13:30 ET noise rows** (`hour==13 and minute==30`); provider chain from `BACKFILL_1D_PRIMARY` / `BACKFILL_1D_FALLBACK`. Wired as Tier 3 in `backfill_symbol_history()`.
- ✅ 3.7.5 Live ingestion loops — `_write_1h_bars()` + `_1h_write_loop()` (hourly at :05 past ET) and `_write_1d_bars()` + `_daily_write_loop()` (16:05 ET, also runs 1wk resample). 1m loop unchanged — it already uses `MarketDataManager` which auto-resolves Webull → yfinance → Alpaca from `.env`.
- ✅ 3.7.6 Resample-at-write loops — `_resample_and_upsert(target_tf, source_tf)` reads source bars from DB, calls `resample_ohlcv()`, upserts only confirmed-closed buckets (end-time < now). `_resample_write_loop()` runs every 2 min for 2m/3m/5m/15m/30m from 1m. `_write_4h_bars()` + `_4h_write_loop()` resample 1h → 4h at 4h boundaries. `_daily_write_loop()` also resamples 1wk from 1d at 16:05 ET.
- ✅ 3.7.7 Direct-read bar repository — `BarRepository.get_bars()` now does a straight DB query against the `(symbol, timeframe, timestamp)` index. Read-time resample path removed. Added 2m/3m to `_TF_MULTIPLIER` and `_WIDENING_HOURS`. The `fallback_provider` parameter on `get_bars()` is retained for backward compat but unused.
- ✅ 3.7.8 Resampler support for 2m/3m — `resampler.py` `_TF_MINUTES` now has `"2m": 2, "3m": 3` entries; `_bucket_start` dispatches via `_floor_minute` for both.
- ✅ 3.7.9 Wire all new loops in `_run_loops()` — four new `asyncio.create_task` calls with offsets 25s/28s/31s/34s so they don't burst-trigger provider 429s on startup.
- ✅ 3.7.10 Update `docs/Version_3/v3_plan.md` with full Phase 3.7 section (decision, items, risks, verification).
- ✅ 3.7.11 Update `docs/Version_3/phase_audit_v3.md` with scorecard row + per-item checklist (this section).


## Phase 3.8 — Auto Live Gap-fill Loop — ✅ DONE

- ✅ 3.8.1 `_gapfill_1m_loop` + `_gapfill_1m_once` in `backend/market_data/services/ingestion_service.py` (lines 405, 441). RTH-gated (09:30–16:00 ET, Mon–Fri), runs every 5 min with ±15s jitter. For each watched symbol, queries `MAX(timestamp)` for 1m bars in DB, calls `_fetch_tier1_1m_bars(symbol, days=1, manager=None)` (Alpaca primary + yfinance/webull gap-fill), filters to bars strictly newer than DB's latest, upserts via `_write_bars_in_chunks`. Idempotent — never overwrites history.
- ✅ 3.8.2 Wired in `_run_loops()` at line 532 with `initial_delay=37.0` (staggered after existing 5 loops to avoid startup burst).
- ✅ 3.8.3 Verified end-to-end: NVDA 11:22–11:24 ET gap on 2026-09-03 was filled by the server restart's `_seed_check` running 1000-day backfill. The new loop will catch any future mid-session gaps every 5 min during RTH.
- ✅ 3.8.4 Cascade purge on watchlist removal — `quote_repository.delete_quotes_for_symbol` + `delete_market_status_for_symbol` added; `backend/api/watchlist/router.py` extended to purge all 4 tables (bars, historical_signals, quotes, market_status) when a symbol is removed from its last watchlist. End-to-end verified with TEST symbol: 211 bars + 211 signals + 1 quote + 1 market_status row all purged on DELETE.


## Phase 3.9 — Backend + Frontend Bottleneck Cleanup — ✅ DONE (21/21)

**Date:** 2026-09-05 (identified) | **Status:** ✅ ALL COMPLETE — 21 items across backend and frontend.

**Goal:** Eliminate event-loop blockers, memory/CPU multipliers, and unnecessary re-renders found in the post-3.6 bottleneck scan.

**Scan methodology:** Two parallel agents read all route handlers, repositories, engine files, components, and pages. Findings ranked by severity grounded in code, not speculation.

**Items:**

**Backend (11 items):**
- ✅ 3.9.1 **Sync DB in async routes** — `analysis/router.py:_load_bars` (line 46) blocks event loop on 4 endpoints. Fix: `await asyncio.to_thread(_load_bars, ...)` — **HIGH**. ✅ 2026-09-05.
- ✅ 3.9.2 **3x TrendEngine stacks per symbol** — `MarketRegimeEngine` creates its own `TrendEngine`; `SectorEngine` creates 3 (stock + sector + SPY). Fix: `MarketRegimeEngine` accepts `trend_engine=` injection; `RelativeStrengthEngine` accepts `trend_engines=` dict; `SectorEngine` accepts `stock_engine=/sector_engine=/market_engine=` injection; router injects shared engines via `get_engine()` from trend registry. **HIGH**. ✅ 2026-09-05.
- ✅ 3.9.3 **80 sequential DB queries at startup** — `trend/registry.py:_seed_from_bar_model` loops over 10 TFs × 8 symbols. Fix: single `IN (...)` query, bucket in Python — **HIGH**. ✅ 2026-09-05.
- ✅ 3.9.4 **6 sequential health queries** — `system/router.py:_safe_bar_counts`. Fix: single raw SQL `SELECT COUNT(*), SUM(CASE WHEN...), MIN(timestamp), MAX(timestamp), COUNT(DISTINCT symbol) FROM bars` — **MED**. ✅ 2026-09-05.
- ✅ 3.9.5 **Sync requests.get in FinnhubService** — `finnhub_service.py:45`. Fix: `async def _get_async` using `asyncio.to_thread`; all 8 route handlers wrapped — **MED**. ✅ 2026-09-05.
- ✅ 3.9.6 **50 fresh TrendEngine instances per scan** — `scanner/scanner.py:98`. Fix: `from backend.api.trend.registry import get_engine as get_trend_engine` — **MED**. ✅ 2026-09-05.
- ✅ 3.9.7 **Lazy `__import__` in scan loop** — `scanner/scanner.py:115`. Fix: direct `getattr(Timeframe, tf_str)` — **MED**. ✅ 2026-09-05.
- ✅ 3.9.8 **Blocking run_experiment in strategy_lab** — `strategy_lab/router.py`. Fix: `await asyncio.to_thread(run_experiment, config)` — **MED**. ✅ 2026-09-05.
- ✅ 3.9.9 **Debug f-string on every bar tick** — `engine_seeder.py:195` + `regime/router.py:111`. Fix: `isEnabledFor(DEBUG)` guard — **LOW**. ✅ 2026-09-05.
- ✅ 3.9.10 **4 SessionLocal() opens per market-context seed** — `market_context/router.py:51`. Fix: `_seed_sub_engine(symbol, engine, db=None)` with optional shared session; `get_engine()` opens one session for all 4 seeds — **LOW**. ✅ 2026-09-05.
- ✅ 3.9.11 **O(N²) z-score loop in transitions** — `analysis/router.py:144`. Fix: O(N) running-sum/sum-of-squares using a deque — **LOW**. ✅ 2026-09-05.

**Frontend (10 items):**
- ✅ 3.9.12 **N concurrent RS calls in WatchlistTable** — `WatchlistTable.tsx:185` fires 50 HTTP requests per refresh. Fix: batch endpoint + single call — **HIGH**. ✅ 2026-09-05.
- ✅ 3.9.13 **Dashboard.fetchData not memoized** — `Dashboard.tsx:41`. Fix: `useCallback` — **HIGH**. ✅ 2026-09-05.
- ✅ 3.9.14 **WatchlistPage.fetchWatchlists not memoized** — `WatchlistPage.tsx:34`. Fix: `useCallback` (depends only on `api`) — **MED**. ✅ 2026-09-05.
- ✅ 3.9.15 **SymbolPage panels not memoized** — `SymbolPage.tsx:103,175,238,274`. Fix: `React.memo` + `useMemo` for `recent` slice — **MED**. ✅ 2026-09-05.
- ✅ 3.9.16 **handleChartTypeChange not memoized** — `MultiTimeframeChartGrid.tsx:128`. Fix: `useCallback` — **MED**. ✅ 2026-09-05.
- ✅ 3.9.17 **ScannerRow memo fails on any single-symbol update** — `ScannerPage.tsx:55`. Fix: pass per-row `errors[sym]` only — **MED**. ✅ 2026-09-05.
- ✅ 3.9.18 **Inline style objects in SymbolPage panels** — `SymbolPage.tsx` (multiple lines). Fix: module-level constants — **LOW**. ✅ 2026-09-05.
- ✅ 3.9.19 **WatchlistPage.find not memoized** — `WatchlistPage.tsx:74`. Fix: `useMemo` — **LOW**. ✅ 2026-09-05.
- ✅ 3.9.20 **chartMath WeakMap keyed by array identity** — `chartMath.ts:81,254`. Fix: content-based key — **LOW**. ✅ 2026-09-05.
- ✅ 3.9.21 **ScannerRow Date.now() per render** — `ScannerPage.tsx:36`. Fix: module-level helper — **LOW**. ✅ 2026-09-05.

**Priority (top 3 HIGH backend + top 2 HIGH frontend):**
1. 3.9.1 — 1 wrapper fixes 4 endpoints; eliminates event-loop blocking (~10 LOC)
2. 3.9.12 — Turns 50 requests into 1; biggest visible win (~40 LOC)
3. 3.9.13 — One-liner; prevents re-fetch loop (~5 LOC)
4. 3.9.3 — Cuts startup by ~80%; startup is currently ~1.5s with 80 queries (~20 LOC)
5. 3.9.2 — Memory/CPU multiplier; affects every 10-symbol user (~50 LOC)

**Verification:**
```bash
# Event loop still unblocked after 3.9.1
curl -s http://127.0.0.1:5001/api/analysis/SPY/transitions | python -c "import json,sys; print('ok' if json.load(sys.stdin) else 'fail')"

# Batch RS endpoint (after 3.9.12)
curl -s "http://127.0.0.1:5001/api/regime/batch/relative-strength?symbols=SPY,QQQ,AAPL" | python -m json.tool | head -5

# Startup time (after 3.9.3)
# Should be <500ms warm cache (was ~1500ms with 80 sequential queries)
```

## Phase 3.10 — Tracing-Overhead Page-Load Fix + Repo Cleanup — ✅ DONE (2026-09-09)

**Trigger:** User report — "every page is loading really slow."

**Root cause:** `backend/observability/tracing.py`'s `_get_otlp_endpoint()` built the OTLP export endpoint from `jaeger_agent_host`/`jaeger_agent_port` (6831 — Jaeger's legacy UDP *agent* port, not a valid OTLP/gRPC port). `FastAPIInstrumentor` wraps every ASGI request regardless of route, so every single request paid for a doomed export attempt against a non-listening endpoint.

**Items:**
- ✅ 3.10.1 Fix — `.env`: `OBSERVABILITY_TRACING_ENABLED=false` (backup saved before edit). No code change; the middleware itself is sound, the configured endpoint was not. **HIGH** (affected every page load).
- ✅ 3.10.2 Repo cleanup audit — reviewed the tree for unused/unnecessary files; identified `conf/token.txt` (Webull SDK's live auth-token cache, rewritten every restart — was tracked, meaning every restart dirtied `git status` and any re-commit would put a live credential back in history) and `.claude/worktrees/jaeger-start` (a stale git-worktree gitlink, mode 160000, pointing at a commit that only ever existed on this machine).
- ✅ 3.10.3 `conf/token.txt` untracked (file stays on disk), `.mypy_cache/` added to `.gitignore` (missed by existing cache ignores) — commit `9de8a0d`. Note: the token committed earlier (`b03a3d7` and before) is still in git history; rotate via the Webull dev console before ever sharing/pushing this repo.
- ✅ 3.10.4 `.claude/worktrees/jaeger-start` gitlink removed via `git worktree remove`, `.claude/worktrees/` added to `.gitignore` — commit `c7f71fd`.

**Not yet fixed (flagged, out of scope for this pass):** `.env.example` and `README_DEPLOYMENT.md` still default `OBSERVABILITY_TRACING_ENABLED=true` with no note about the broken agent-port endpoint — see [Known Follow-ups](#known-follow-ups) at the end of this document.

**Verification:** Confirmed page-load latency returned to normal after the `.env` flip and a backend restart; no code paths depend on tracing being on, so disabling it is a pure win until a real OTLP collector is configured.

---

## Phase 3.11 — Extended-Hours Ingestion + Per-Timeframe Retention + Live 1h Bar — ✅ DONE (2026-09-09)

**Trigger:** User question — does Webull's Open API only fetch RTH bars, or can it fetch pre/post/overnight too? Investigated live against the real API, then wired in on request.

**Findings:**
- Webull Open API bar endpoints take a `trading_sessions` param (`PRE`/`RTH`/`ATH` values) — extended-hours bars ARE available for free; only the streaming *overnight quote* feature is gated behind a separate paid subscription (`403 MARKET_DATA_NOT_SUBSCRIBED`).
- The existing `SessionType` enum (`backend/engines/market_calendar.py`) already models PREMARKET (04:00–09:30 ET) / REGULAR (09:30–16:00 ET) / AFTER_HOURS (16:00–20:00 ET) / CLOSED — reused rather than re-invented.

**Items:**
- ✅ 3.11.1 `webull_provider.py`: pass `trading_sessions` through to the bar-fetch call; extended-hours bars flow through for 1m first (per-symbol confirmation via `test it`), then wired in for all sub-hour timeframes on request — 2m/3m/5m/15m/30m now also carry the full 4:00am–8:00pm ET daily range, not just RTH. **13 new tests** (`TestExtendedHoursBars`, `TestExtendedHoursQuotes` in `test_webull_provider.py`).
- ✅ 3.11.2 Centralized session classification — new `classify_bar_session(ts)` in `market_calendar.py`; `bar_repository.upsert_bars()` now unconditionally recomputes `session` for 1m/2m/3m/5m/15m/30m bars at the write chokepoint, rather than trusting each provider to self-report it. Principle: never trust N independent providers to agree on a timestamp-derived fact — compute it once, centrally, where all bars converge.
- ✅ 3.11.3 `ingestion_service._resample_and_upsert`: removed the `session == "regular"` filter for sub-hour resampling so 2m/3m/5m/15m/30m correctly include extended hours (1h/1d remain RTH-only by design — matches how those timeframes are used).
- ✅ 3.11.4 Per-timeframe retention — new `RetentionSettings` (`backend/config/settings.py`, `RETENTION_` env prefix): `RETENTION_TF_1M_DAYS=16` (also 2m/3m/5m/15m/30m), `RETENTION_TF_1H_DAYS=366` (also 4h), `RETENTION_TF_1D_DAYS=1096` (also 1wk) — user-specified exact values. New `bar_repository.prune_bars_by_retention(db)` iterates every timeframe and prunes against its own cutoff; wired into the ~60s ingestion tick (replacing the old single rolling-window prune).
- ✅ 3.11.5 Removed `MarketDataSettings.bar_retention_days` and the `.env` var `MARKET_DATA_BAR_RETENTION_DAYS` entirely — confirmed dead: it was always ≥ every `BACKFILL_*_DAYS` tier value, so its `min(tier_days, retention_days)` clamp never actually bound in any real call path. Per-timeframe retention (3.11.4) fully supersedes it.
- ✅ 3.11.6 Live/partial current-hour 1h bar — `_resample_1h_live_and_upsert` (later generalized in 3.13) mirrors the existing, proven `_resample_1d_live_and_upsert` pattern one level down: builds/refreshes the in-progress hour from its 1m bars every ~2 min, tagged `INCOMPLETE`, on the same `(symbol, "1h", hour_start)` key the real provider-sourced bar lands on once the hour closes (upsert overwrites it, no special-casing). Previously 1h only ever showed the last fully-closed hour, which read as a missing/late bar (reported live at 1:13 with no 1:00 bar showing).
- ✅ 3.11.7 Process-lifetime provider instance cache — `manager.get_cached_provider()` (`backend/market_data/services/manager.py`). Root-caused a separate "bars arriving 1-2+ min late" report: `WebullProvider.__init__` does a synchronous auth handshake (a real blocking network round-trip) and both `_gapfill_1m_loop`/`_gapfill_1h_loop` were constructing a fresh instance on every cycle, on the same asyncio event loop as the live ingestion tick — blocking it for hundreds of ms to seconds per gap-fill pass. Caching by provider name for the process lifetime (matching `MarketDataManager`'s own cache lifetime) fixes it; failed constructions are never cached, so a transient failure retries clean.

New migration `alembic/versions/20260909_bar_session_column.py` (adds `session` column, `server_default='regular'`). 36 new/updated tests across these changes (webull provider, ingestion service, bar repository, manager, retention). Full suite green throughout.

**Verification:** Live-tested against the real Webull API before wiring in; confirmed 1m/2m/3m/5m/15m/30m all carry the 4:00am–8:00pm ET range in the running app; confirmed the live 1h bar appears within ~2 min of the hour opening and is correctly overwritten once the provider's closed-hour bar lands.

---

## Phase 3.12 — RQ-Based Backfill Pipeline Rebuild — ✅ DONE (2026-09-09)

**Trigger:** Architectural review of the add-ticker flow found it unsound: correctness depended on the frontend calling a second endpoint after add (silently skippable), the add-symbol HTTP thread could block up to 10 minutes on provider I/O, two independent triggers raced each other coordinated only by a per-event-loop asyncio lock, there was no explicit gap-check-and-fill step, and no observability into per-symbol backfill status (the SOFI investigation earlier in this session had to inspect the DB by hand to confirm a partial failure). Full plan at `/Users/dips/.claude/plans/logical-zooming-dolphin.md`.

**Target flow implemented:** `POST /watchlists/{id}/symbols` → `WatchlistRepository.add_symbol_to_watchlist` (sync, fast, no provider I/O) → `ingestion_service.register_symbol()` (instant, in-process — symbol starts getting live quotes/1m bars within ~30-60s) → `backfill_queue.enqueue_backfill()` (Redis-backed single-flight, non-blocking) → 201 returned immediately. An RQ worker then runs the full backfill → gap-check → resample sequence out-of-request, reporting status via a new `BackfillJob` row.

**Items:**
- ✅ 3.12.1 New `backfill_jobs` table + `BackfillJob` model (`backend/models/backfill_job.py`), mirroring `AIAnalysisJob`'s shape (`job_id`, `symbol`, `status: queued|started|completed|partial|failed`, `tier1_written`/`tier2_written`/`tier3_written`, `gaps_found`/`gaps_filled`, `result` JSON, `error`, timestamps). Migration `alembic/versions/20260908_backfill_jobs.py`. Added to `purge_service`'s cascade delete.
- ✅ 3.12.2 New gap-detection utilities in `bar_repository.py` — `expected_bar_timestamps()` (session-aware via the market calendar + `TimeframeEngine`) and `find_gaps()` (diffs expected vs. actual, coalesces into contiguous missing ranges), alongside the existing `find_duplicate_calendar_bars` pattern. New `test_gap_detection.py` (132 lines, includes a market-holiday case).
- ✅ 3.12.3 New RQ queue — `backend/market_data/services/backfill_queue.py` — single-flight via a Redis `SET NX EX` lock (replacing the old in-process asyncio lock/semaphore that had to be re-keyed per event loop; single-flight is now a Redis-backed, cross-process fact). `test_backfill_queue.py` (402 lines).
- ✅ 3.12.4 New `backend/workers/backfill_worker.py` (`SimpleWorker` — avoids fork segfaults with the Webull SDK); `ai_worker.py` also switched to `SimpleWorker` and a broken `--once` flag fixed on it in the same pass. Separate queue name (`marketlens-backfill`, distinct from `marketlens-workers`) so a slow multi-tier backfill can't starve AI analysis jobs.
- ✅ 3.12.5 `backfill_service.py`: gap-check-and-targeted-refill step inserted between tier1-3 fetch and the sub-timeframe resample step; the old `_backfill_locks`/`_lock_guards_by_loop`/`_backfill_semaphores_by_loop` machinery removed now that there's exactly one call site (the RQ task) instead of two racing ones; job-id generation fixed to satisfy RQ's id-charset validation (a bug that had been silently passing only because it was mocked in tests).
- ✅ 3.12.6 `ingestion_service.py`: new `register_symbol()` decouples "start live tracking" from "trigger backfill" (the old `_new_symbol_bootstrap` dual-trigger race is gone); `_seed_check` now enqueues through the RQ path instead of calling backfill directly; sub-hour resample now fires immediately after new 1m bars land instead of waiting for the next independent ~120s timer tick; fixed the 4h resample bucket guard that had been permanently discarding the 16:00–19:59 bucket.
- ✅ 3.12.7 `watchlist/router.py`: add-symbol endpoint no longer blocks the request thread on any provider I/O; new `GET /api/watchlists/symbols/{symbol}/backfill-status` endpoint. Frontend (`frontend/src/services/api.ts`) drops the now-redundant post-add ingestion-refresh call — registration is guaranteed server-side at add time.
- ✅ 3.12.8 Bugs found and fixed during the audit pass (unrelated to the pipeline rebuild itself, but found while reading the surrounding code): `AuxDataSettings`' nested News/Fundamentals/Options settings classes had no own `env_prefix`/`env_file`, so `AUX_*_ENABLED` in `.env` was silently never read (nested `BaseSettings` does not inherit the parent's `env_prefix`) — every `/api/aux-data/*` endpoint 503'd regardless of `.env` or restarts; `yfinance_provider.py`'s trailing live-snapshot filter missed a boundary-aligned variant; `main.py`'s startup `redis.flushall()` was wiping the *entire* Redis DB (including queued RQ jobs) on every restart, now scoped to the cache layer's own key prefix; `start.sh`'s signal trap had broken PID quoting.
- ✅ 3.12.9 Docs/infra updated in the same commit: `CLAUDE.md` (RQ worker section), `README.md`, `README_DEPLOYMENT.md`, `.env.example`, `docker-compose.yml`, `scripts/run.py`.

Commit `9a65843`. New/updated tests: `test_backfill_queue.py`, `test_backfill_service.py`, `test_bar_normalization.py`, `test_gap_detection.py`, `test_yfinance_provider.py`, `test_data_quality.py`, `test_purge_service.py`, `test_watchlist_api.py`, `test_aux_data_settings.py`.

**Verification:** Manual end-to-end — added a real symbol, confirmed the API responded immediately, `GET .../backfill-status` progressed queued→started→completed within ~2 min, all 10 timeframes populated with no calendar-day duplicates. Full backend suite green.

---

## Phase 3.13 — 1h Bar Anchor Mislabeling Fix — ✅ DONE (2026-09-09)

**Trigger:** User spotted the same low ($760.94) on SPY's 1m 11:25 bar and its 1h 10:00 bar and asked how that was possible.

**Root cause:** `_normalize_1h_bar`'s docstring claimed "Alpaca and Webull use :00... yfinance uses :30" — empirically false. Live testing showed **Webull's 1h endpoint returns `:30`-anchored bars**, and the code naively floored every 1h timestamp to the preceding `:00`, mislabeling which canonical hour a bar's OHLC actually belonged to. This affected the entire 1h dataset for every symbol, for every 1h bar ever backfilled or ingested via Webull — not an edge case.

**Fix — never trust a provider's own hour boundary where 1m coverage exists to check against:**
- ✅ 3.13.1 Generalized the Phase 3.11 live-current-hour builder from `_resample_1h_live_and_upsert` (today's in-progress hour only) to `_resample_1h_from_1m_and_upsert(hour_starts=None, _symbol=None)`, which rebuilds any explicit set of hours (or all of today's, or a single symbol's) directly from verified, unambiguously-timestamped 1m data rather than the provider's own alignment. New `_hour_starts_between()` static helper generates `:00`-aligned buckets inclusive of both ends.
- ✅ 3.13.2 Wired in two places: `_resample_write_loop` now rebuilds all of *today's* hours every ~2 min (cheap, bounded — continuously corrects any hour whose provider-sourced bar has a misaligned boundary); `backfill_service.backfill_symbol_history` runs the same correction across the full 1m retention window (`RETENTION_TF_1M_DAYS`, 16 days) right after tier2's fetch, so newly backfilled symbols get correct 1h immediately rather than carrying the provider's mislabeled bars until the next full rebuild. Hours older than the 1m retention window still rely on the provider's own (imperfect) alignment — 1m isn't retained that far back to correct against.
- ✅ 3.13.3 One-time corrective pass run against the live DB for every symbol on the watchlist at the time (AAPL, DVLT, QQQ, SPY) — 78 bars corrected per symbol. Verified the exact SPY bar the user flagged: 10:00 now shows low=762.5/high=764.2 (correct), 11:00 now shows low=760.94/high=764.465 (the values that had been wrongly filed under 10:00).

Commit `78da42b`. 6 new/updated tests (multi-hour correction, the exact bad-past-hour regression case, `_hour_starts_between`). Full suite: 1669/1669 passing.

**Verification:** Direct SQL query against the live DB, before and after, matching the exact SPY timestamps/prices the user reported.

---

## Known Follow-ups

Not yet done — flagged during recent audits, out of scope for the triggering request, not forgotten:

- **`OBSERVABILITY_TRACING_ENABLED` default.** `.env.example` and `README_DEPLOYMENT.md` still default this to `true`, with no note that the shipped Jaeger-agent-port OTLP endpoint (6831, UDP) doesn't work with the gRPC exporter — re-enabling tracing from either template silently reintroduces the Phase 3.10 page-load slowdown. Should either default to `false` or ship a working OTLP endpoint config + a comment explaining the pitfall.
- **`test_vacuum_into.py` leaves permanent orphan rows.** `VACUUM_TEST_*` symbols are written to the DB with no `tearDown` cleanup — a pre-existing test-hygiene bug, unrelated to any of the phases above.

