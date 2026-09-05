# Version 3 Phase Audit

**Last updated:** 2026-09-04
**Scope:** Database backup/optimization, Bar retention (1000-day rolling window), Charts, Structured logging, Dashboard rebuild

---

## Scorecard

| # | Phase | Status | Notes |
|---|---|---|---|
| 3.1 | Timeframe Resampling (1m-only storage) | ✅ DONE | All 3.1.1–3.1.32 complete |
| 3.2 | Alpaca Integration (REST + WebSocket) | ✅ DONE | All 3.2.1–3.2.7 complete |
| 3.3 | Database Backup & Optimization + Bar Retention (1000-day) | ✅ DONE | Section A (3.3.1–3.3.7) + Section B (3.3.8–3.3.18) complete |
| 3.4 | Charts (drawing v1, line/area/HA, indicators) | 🟡 PLANNED | Planned for future release |
| 3.5 | Structured Logging (JSON formatter, rotation) | 🟡 PARTIAL | correlation_id.py done (3.5.1); 3.5.2–3.5.9 not started |
| 3.6 | Dashboard Performance (Promise.all, pre-warm, TTL cache, memo) | ✅ DONE | All items complete as of 2026-09-04 |
| 3.7 | All-Timeframe Live Ingestion + Resample-at-Write + Backfill Config | ✅ DONE | 3.7.1–3.7.11 complete |
| 3.8 | Auto Live Gap-fill Loop | ✅ DONE | 3.8.1–3.8.2 complete; verified for NVDA 11:22–11:24 ET |

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

### Section B — Bar Retention Policy (1000-Day Rolling Window) — ✅ DONE

**Goal:** Rolling 1000-day bar window. Adding a ticker backfills 1000 days via paginated Alpaca fetch. Removing the last watchlist entry purges all associated bars. Old bars auto-pruned on every ingestion cycle.

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



- ✅ 3.3.8 Settings: `BAR_RETENTION_DAYS=1000` — `backend/config/settings.py` (`MarketDataSettings.bar_retention_days = 1000`, default 1000 days; `backfill_on_add = True`); `.env.example` key documented
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

## Phase 3.5 — Structured Logging

- ✅ 3.5.1 `backend/observability/correlation_id.py` — done (ContextVar + middleware)
- ⬜ 3.5.2 `backend/observability/json_formatter.py` — JsonFormatter
- ⬜ 3.5.3 Standard log fields (timestamp, level, logger, msg, trace_id, request_id, duration_ms)
- ⬜ 3.5.4 WebSocket request ID propagation
- ⬜ 3.5.5 LogContext helper (with_context symbol=... timeframe=...)
- ⬜ 3.5.6 Replace all `print()` calls with structured logger
- ⬜ 3.5.7 Log level normalization across modules
- ⬜ 3.5.8 RotatingFileHandler — 50 MB / 5 files
- ⬜ 3.5.9 JSON log verification test (parse every line)

## Phase 3.6 — Dashboard Performance — ✅ DONE

**Date:** 2026-09-04 | **Status:** All items complete.

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
- `seed_engine_from_bars()` capped at 200 bars max (`market_data/services/engine_seeder.py:76`)
- The user's perceived 2s cost was: (1) server startup ~1–2s once, (2) per-symbol first request ~190 ms × 8 symbols = ~1.5s on cold watchlist
- Dashboard's `Promise.all` was already correctly parallelizing 6 endpoints — no work needed there

**Items (all complete):**

- ✅ 3.6.1 **Trend engine pre-warm at lifespan startup** — [backend/api/main.py:107-113](backend/api/main.py#L107); `warmup_engines()` reads historical bars for every watchlist symbol so the first `/api/trend/{sym}/current/{tf}` request hits a pre-seeded engine. Pays ~1.5s at startup; off the request path.
- ✅ 3.6.2 **Market-context engine pre-warm at lifespan startup** — [backend/api/main.py:120-130](backend/api/main.py#L120); mirrors the trend pre-warm pattern. Aggregates SPY/QQQ/IWM/VIX sub-regimes; warm before first dashboard load.
- ✅ 3.6.3 **30s TTL cache on all hot endpoints** — [backend/api/ttl_cache.py](backend/api/ttl_cache.py) (`_regime_cache`, `_trend_cache`, `_quote_cache`, `_sector_cache`, `_top_movers_cache`, `_rs_batch_cache`, `_analysis_cache`, `_confluence_cache`, `_strategy_cache`, `_context_cache`, `_regime_history_cache`, `_trend_history_cache`, `_transitions_cache`, `_scan_cache`); back-to-back dashboard refreshes short-circuit before the route handler runs.
- ✅ 3.6.4 **Per-bar cache invalidation** — `bar:1m` dispatch in [backend/api/regime/router.py:113-168](backend/api/regime/router.py#L113); drops per-symbol cache entries on each new bar so the next request reflects updated state without serving 30s-TTL stale responses.
- ✅ 3.6.5 **`Promise.all` parallelization in Dashboard** — [frontend/src/pages/Dashboard.tsx:81-88](frontend/src/pages/Dashboard.tsx#L81); 6 endpoints fan out concurrently, single `setData` batched state update (no render thrash).
- ✅ 3.6.6 **`React.lazy()` on all non-dashboard pages** — [frontend/src/App.tsx:11-17](frontend/src/App.tsx#L11); SymbolPage, WatchlistPage, SystemHealth, ScannerPage, etc. all code-split. Dashboard stays in the main bundle (landing page) but lazy-mounts below-fold cards.
- ✅ 3.6.7 **`React.memo` on all major components** — RegimeCard, TrendCard, MarketContextCard, AlertsCard, TopMoversCard, WatchlistTable all wrap in `React.memo` with shallow-prop comparison. Verified by inspection.
- ✅ 3.6.8 **Virtualized WatchlistTable** — `react-window` `FixedSizeList` (already in use); table scrolls 60fps with 100+ symbols.
- ✅ 3.6.9 **Batched state update in Dashboard** — single `setData` setter for the parallel fetch results; the 6 endpoint responses land in one render, not six.
- ✅ 3.6.10 **`seed_engine_from_bars` capped at 200 bars** — [backend/market_data/services/engine_seeder.py:76](backend/market_data/services/engine_seeder.py#L76); pre-warm cost is bounded — no symbol ever seeds more than 200 bars regardless of watchlist history.

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
