# Version 3 — Database, Charts, Logging, Dashboard, 1m-Only Storage

**Date:** 2026-09-01
**Last updated:** 2026-09-04
**Status:** Active (Phase 3.6 complete; Phases 3.4/3.5 still planned)
**Scope:** Platform hardening and UX polish across five areas: timeframe resampling, database backup/optimization, chart expansion, structured logging, and dashboard performance.

---

## Goals

1. **Stop storing redundant timeframe data** — only persist 1m bars, derive all higher timeframes on the fly (hybrid: resample from 1m + provider fallback for gaps). 7× fewer API calls, ~80% less storage.
2. Make the database safe and fast: Litestream-style WAL streaming to local directory, query optimization, lifecycle management.
3. Make charts feature-complete: drawing-on-canvas (trend line + horizontal line), more chart types (line/area/HA), more indicators, all without leaving the existing component shell.
4. Make logs greppable, traceable, and operationally useful: structured JSON with request correlation (correlation IDs already done).
5. Make the dashboard fast: lazy loading, React.memo audit, virtualization. No new widgets in v3.

**Non-goals (deferred to v4+):**
- New dashboard widgets (sector heatmap, recent signals, market overview, watchlist sparklines, command palette, 2-column layout, keyboard shortcuts, responsive redesign)
- PostgreSQL migration (SQLite + WAL is fine for now)
- Log viewer page in UI (grep + jq is the v3 tool)
- Trading / portfolio / broker integration (separate phases)

---

## Phase 3.2 — Database Backup & Optimization

**Why now:** The DB is on a local disk with no backup policy. A failed disk or bad migration means total data loss. Indexes have grown organically; some queries are full scans.

**Decision: Litestream-style WAL streaming to local directory (`backups/wal/`).** Continuous WAL writes survive machine failure without requiring external cloud storage. Restore to any point-in-time from WAL snapshots.

### Items

#### 3.2.1 WAL streaming backup (Litestream-style)
- New file: `backend/database/wal_streamer.py` — `WALStreamer` class
- Runs as a sidecar thread launched from `ingestion_service` startup
- Mechanism: register a `sqlite3` update hook on the DB connection. On every commit, copy the new WAL frames to `backups/wal/<timestamp>.wal` (rotated by 1 MB segments, gzip-compressed)
- Also copy a snapshot of the main DB file at startup and every 6 hours to `backups/snapshots/<timestamp>.db`
- Restore: `python -m backend.database.wal_streamer restore --to "2026-09-01T14:30:00Z"` finds the latest snapshot ≤ that time and applies WAL segments forward
- Config: `WAL_STREAM_ENABLED=true`, `WAL_STREAM_DIR=backups/wal`, `WAL_SNAPSHOT_DIR=backups/snapshots`, `WAL_SEGMENT_BYTES=1048576`, `WAL_SNAPSHOT_HOURS=6`
- Retention: keep last 24 hourly WAL segments, last 7 daily snapshots

#### 3.2.2 Backup health endpoint
- Add to `backend/api/system/router.py`: `GET /api/system/backup-status`
- Returns: latest snapshot timestamp + age, WAL segment count + total size, last successful flush time, next scheduled snapshot
- Returns 200 if everything is healthy, 200 with `degraded: true` if last snapshot is > 24h old

#### 3.2.3 Index audit
- Run `EXPLAIN QUERY PLAN` against the 10 most-frequent queries (from query log or hand-picked: `get_history`, `get_signals_needing_outcomes`, regime lookups, watchlist fetches)
- Add missing indexes via Alembic migration: composite indexes for the common (symbol, timeframe, timestamp) access patterns
- Drop unused indexes (any with 0 rows in `sqlite_stat1` after sampling)

#### 3.2.4 Query optimization
- Profile `repo.get_history` and `repo.get_signals_needing_outcomes` — both are called frequently
- Add N+1 fixes: `WatchlistRepository.get_watchlists` already loads counts separately; consider eager loading
- Use `selectinload` / `joinedload` where it helps

#### 3.2.5 VACUUM + ANALYZE on schedule
- After each snapshot, run `VACUUM INTO 'backups/vacuum/clean_<timestamp>.db'; ANALYZE;` on the DB
- The vacuum target is a separate file, not the live DB, so the live DB stays available during the copy
- Skipped if `DB_VACUUM_ON_SNAPSHOT=false`

#### 3.2.6 Database settings panel
- Frontend page: `frontend/src/pages/SystemHealth.tsx` already exists — add a "Database" tab
- Show: file size, table row counts, latest snapshot + age, WAL segment count, last vacuum, index health, query latency stats

### Verification
- Trigger a write, verify a new `.wal.gz` segment appears in `backups/wal/`
- `python -m backend.database.wal_streamer restore --to <timestamp>` round-trips data losslessly to a separate test file
- `GET /api/system/backup-status` returns the correct counts and age
- `VACUUM INTO` produces a clean file that's byte-equal to a fresh export
- All existing integration tests pass with the new indexes
- Add `tests/database/test_wal_streamer.py` covering streaming, snapshot rotation, point-in-time restore

---

## Phase 3.3 — Bar Retention Policy (1000-Day Rolling Window)

**Why now:** The bars table grows unboundedly. With 7 timeframes × 8 symbols × 390 bars/day × N years, the DB bloats and queries slow down. The 1000-day rolling window keeps the recent history live and prunes the rest automatically.

### Items

#### 3.3.1 Bar retention settings
- New: `BAR_RETENTION_DAYS=1000` in `.env` and `MarketDataSettings.bar_retention_days` in `backend/config/settings.py`
- Prune cutoff: `now() - 1000d`
- Hook into ingestion cycle: after every `upsert_bars()` pass, prune if oldest bar in DB is below the cutoff

#### 3.3.2 Repository primitives
- `prune_bars_older_than(cutoff, chunk_size=1000)` — chunked `DELETE FROM bars WHERE timestamp < :cutoff` (1k rows per chunk; prevents SQLite write-lock blocking the ingest loop)
- `bulk_delete_bars(symbols, cutoff)` — multi-symbol variant for watchlist removal
- `delete_bars_for_symbol(symbol)` / `delete_bars_for_symbols([...])` — thin wrappers

#### 3.3.3 Backfill service with single-flight guard
- `backfill_symbol_history(symbol, days=1000)` — paginated fetch from Alpaca (1m for last 30d, 1d for days 31–1000); writes in chunks of 5,000
- `asyncio.Semaphore(2)` caps 2 concurrent backfills
- Module-level `_backfill_locks: dict[str, asyncio.Lock]` provides single-flight guard — adding a symbol that already has a pending backfill returns `{"status": "skipped"}`

#### 3.3.4 Watchlist add/remove hooks
- `add_symbol_to_watchlist` triggers backfill (if `is_new_row=True` and `backfill_on_add=True`)
- `remove_symbol_from_watchlist` purges all bars for that symbol if no other watchlist holds it
- Both run on `asyncio.create_task` so the API responds immediately

#### 3.3.5 Startup seed check
- At ingestion-service startup, for each watchlist symbol: if `oldest_bar < (now - 700d)`, schedule backfill
- Per-symbol failures are caught + logged but don't block other symbols

### Verification
- `prune_bars_older_than(now - 1000d)` returns deleted count > 0 on a fresh DB with > 1000d of history
- Add a symbol, watch the 1000d backfill progress in logs
- Remove a symbol, verify all bars are gone (`SELECT COUNT(*) FROM bars WHERE symbol = ?`)
- Old logs are pruned; new logs show the rolling window stays at 1000d

---

## Phase 3.4 — Charts

**Why now:** Charts are the most-visited surface. Drawing tools require manual timestamp entry (broken UX). Only one chart type (candlestick). Limited indicator set.

**Drawing decisions confirmed:**
- Types v1: **Trend line** (2-point) + **Horizontal line** (1-point, price-level) — scope kept to these two
- Activation: **Floating toolbar** inside the chart card (top-left strip of buttons)
- Interaction: **Click → click** to place. **Esc** cancels mid-draw. One click for horizontal line, two for trend line.
- Storage: **DB-backed** (existing CRUD API, unchanged) — drawing saves to server immediately after second click
- Lock toggle: small icon button in the toolbar; when locked, chart ignores drawing clicks

### Items

#### 3.3.1 Drawing tools: click-to-place (trend line + horizontal line)
- **Current state:** `DrawingToolsPanel.tsx` form requires typing start/end timestamps and prices. Clunky.
- **New behavior (v1):**
  1. Floating toolbar inside `CandlestickChart` card (top-left): `[T↗] [—] 🔓`
  2. Click a tool button → enter "draw mode" for that tool (button highlights active)
  3. Click on the chart → place point 1 (shows a marker dot)
  4. Click again → place point 2, drawing commits → POST to API → added to the sidebar list
  5. Press Esc → cancel draw mode, remove marker, no API call
  6. Drawing lock (🔓→🔒): when locked, clicks are ignored (prevent accidental edits)
- **Implementation:**
  - New file: `frontend/src/components/chartInteractions.ts` — pure `pixelToCoord(chart, x, y) -> {time: number, price: number}` using `chart.timeScale().coordinateToTime()` and `chart.priceScale().coordinateToPrice()`
  - Add `onChartClick(time, price)` callback prop to `CandlestickChart`; when a drawing tool is active and chart is unlocked, emit this
  - `CandlestickChart` state machine: `idle | placing-start | placing-end | drawing-locked`
  - On `placing-start` click: show a temporary marker div; transition to `placing-end`
  - On `placing-end` click: call `api.createDrawingTool(...)`, reset to `idle`
  - On Esc keydown: if in `placing-*`, remove marker, transition to `idle`
  - After save: refresh `DrawingToolsPanel` list (existing `fetchDrawings` refetch)
- Drawing types supported in v1: `trend_line` (needs 2nd click), `horizontal_line` (needs 1 click — price level only, no time)

#### 3.3.2 Chart toolbar: drawing lock toggle + tool activation
- Add lock state to `CandlestickChart`: `const [drawLock, setDrawLock] = useState(false)`
- Toolbar buttons: `Trend Line`, `Horizontal`, lock icon
- Active tool indicator (one at a time, cleared on Esc or on drawing save)
- Keyboard handler: listen for `Escape` key globally when in draw mode

#### 3.3.3 Additional chart types
- **Line chart:** `frontend/src/components/LineChart.tsx` — close-only line, no candles, no wicks. Useful for long timeframes.
- **Area chart:** `LineChart.tsx` with a filled area under the line.
- **Heikin Ashi:** variant of `CandlestickChart.tsx` that takes `chartType="heikin_ashi"` and pre-computes HA candles from the raw data.
- **Renko / Kagi / P&F:** stretch goal — only if all other items land first. They require data transformation not just rendering changes.
- Add `chart_type` to the `SymbolPage` toolbar with a dropdown

#### 3.3.4 More indicators
- Currently supported: SMA, EMA, MACD, Bollinger (per `CustomIndicatorsPanel.tsx`).
- Add: RSI, VWAP, Ichimoku Cloud, ATR, Stochastic, ADX, OBV, Williams %R, CCI
- File: `frontend/src/components/CustomIndicatorsPanel.tsx` — add the new entries to the dropdown
- For built-in indicators, add a "Built-in" section in the panel that doesn't require the user to configure anything (just toggle on/off)
- For VWAP: needs both price and volume; verify the bars data has `volume` field populated (it does per `market_data_sql.py`)

#### 3.3.5 Indicator overlay vs separate pane
- **Overlay (price chart):** SMA, EMA, Bollinger, VWAP, Ichimoku
- **Separate pane below price:** RSI, MACD, Stochastic, ADX, ATR, Williams %R, CCI, OBV
- Add a `pane_height` config to custom indicators; UI shows a small thumbnail of where it renders

#### 3.3.6 Chart settings + drawing persistence
- Chart settings (timeframe, indicator set, chart type): persist per-user via `localStorage` keyed by symbol
- Drawings: already persisted to DB via existing `DrawingTool` API
- Verify the click-to-place flow doesn't break the persistence model — the API should still receive `start_timestamp` / `start_price` from the click handler
- Drawing lock (introduced in 3.3.2) prevents accidental edits

### Verification
- Click-to-place: open chart, click two points, see trendline appear at correct (timestamp, price)
- Switch to line chart via toolbar dropdown — renders without errors, no wicks
- Toggle RSI on — separate pane appears below price chart
- All chart tests pass; add a new `frontend/src/components/__tests__/chartInteractions.test.ts`

---

## Phase 3.5 — Structured Logging

**Why now:** Current logs are untyped Python `logging` calls. Hard to grep, no correlation across requests, no machine-readable fields. Operations work (debugging, alerting) is guesswork.

### Items

#### 3.4.1 JSON log formatter
- New file: `backend/observability/json_formatter.py` — `JsonFormatter(logging.Formatter)`
- Output shape:
  ```json
  {"ts": "2026-09-01T10:00:00.123Z", "level": "INFO", "logger": "backend.scanner.dispatcher",
   "msg": "Backfilled 12 signals", "trace_id": "abc123", "request_id": "req-456",
   "duration_ms": 45, "extra_field": "value"}
  ```
- Switch on with `LOG_FORMAT=json` env var. Default stays as plain text for development readability.

#### 3.4.2 Correlation IDs
- New file: `backend/observability/context.py` — `ContextVar`-based `trace_id` and `request_id`
- Middleware: `backend/observability/middleware.py` — reads `X-Request-Id` header, generates one if missing, populates context, echoes in response
- WebSocket: read from query string `?request_id=...` or generate
- All log lines emitted during a request automatically include the IDs

#### 3.4.3 Standard log fields
- Add a `LogContext` helper: `with_context(symbol="AAPL", timeframe="1h")` returns a context manager that adds fields to the next N log lines
- Use across the codebase: `signal_recorder`, `scanner_dispatcher`, `regime_engine`, `market_data` providers

#### 3.4.4 Replace ad-hoc prints
- `grep -rn "print(" backend/` — find any remaining `print` calls; replace with `logger.info()` or `logger.debug()`
- Most should already be logger-based; this is a sweep

#### 3.4.5 Log levels
- Codify: DEBUG for per-bar / per-quote activity. INFO for state changes (signal recorded, backfill completed). WARNING for retries. ERROR for failures.
- Adjust module loggers that are too chatty (e.g., yfinance provider's DEBUG → INFO unless `LOG_LEVEL=DEBUG`)
- Add `LOG_LEVEL` env var (default INFO)

#### 3.4.6 Log rotation
- Use `logging.handlers.RotatingFileHandler` — 50 MB per file, keep 5 files
- Output dir: `data/logs/`
- Stdout stays unrotated for Docker / k8s logging

### Verification
- Set `LOG_FORMAT=json LOG_LEVEL=DEBUG`, restart, hit a few endpoints — every log line is valid JSON with `trace_id` and `request_id`
- `request_id` is echoed in HTTP response headers
- `data/logs/marketlens.log` rotates at 50 MB
- `python -c "import json; [json.loads(l) for l in open('data/logs/marketlens.log')]"` parses every line

---

## Phase 3.6 — Dashboard Performance — ✅ DONE (2026-09-04)

**Why now:** Dashboard is the landing page. It was experiencing ~2s first-load latency, with the user suspecting the regime endpoint as the bottleneck. Investigation showed the actual cost was per-symbol engine seeding on first request (~190 ms × 8 watched symbols = ~1.5 s cold).

**Scope decision:** performance-only — engine pre-warm + Promise.all + memo + virtualize + TTL cache. **No new widgets** (sector heatmap, recent signals, market overview, watchlist sparklines) ship in v4.

**Measured impact (live server, 8 watched symbols, 2026-09-04):**
- Regime endpoint first call (cold engine): **20.5 ms** (was ~245 ms pre-fix)
- Regime endpoint warm cache: **2.4 ms**
- 6 parallel dashboard endpoints warm: **28.9 ms avg** over 5 runs
- Market-context first call: **2.4 ms**
- Trend batch (10 TFs) first call: **11.6 ms**
- `/api/health` baseline: **2.1 ms**

### Items

#### 3.6.1 Trend engine pre-warm at lifespan startup
- File: [backend/api/main.py:107-113](backend/api/main.py#L107)
- `warmup_engines()` reads historical bars for every watchlist symbol so the first `/api/trend/{sym}/current/{tf}` request hits a pre-seeded engine
- Pays ~1.5 s at startup; off the request path

#### 3.6.2 Market-context engine pre-warm at lifespan startup
- File: [backend/api/main.py:120-130](backend/api/main.py#L120)
- Mirrors the trend pre-warm pattern. Aggregates SPY/QQQ/IWM/VIX sub-regimes; warm before first dashboard load

#### 3.6.3 30s TTL cache on all hot endpoints
- File: [backend/api/ttl_cache.py](backend/api/ttl_cache.py)
- Caches: `_regime_cache`, `_trend_cache`, `_quote_cache`, `_sector_cache`, `_top_movers_cache`, `_rs_batch_cache`, `_analysis_cache`, `_confluence_cache`, `_strategy_cache`, `_context_cache`, `_regime_history_cache`, `_trend_history_cache`, `_transitions_cache`, `_scan_cache`
- Back-to-back dashboard refreshes short-circuit before the route handler runs

#### 3.6.4 Per-bar cache invalidation
- File: [backend/api/regime/router.py:113-168](backend/api/regime/router.py#L113)
- `bar:1m` dispatch drops per-symbol cache entries on each new bar so the next request reflects updated state without serving 30s-TTL stale responses
- Per-symbol key prefixes (`sr:{symbol}:`, `div:{symbol}:`, `regime_hist:{symbol}:`, `trend_hist:{symbol}:`) avoid dropping other symbols' cached responses

#### 3.6.5 `Promise.all` parallelization in Dashboard
- File: [frontend/src/pages/Dashboard.tsx:81-88](frontend/src/pages/Dashboard.tsx#L81)
- 6 endpoints fan out concurrently: regime, confluence, strategy, market-context, trend batch, sector
- Single `setData` batched state update — the 6 endpoint responses land in one render, not six

#### 3.6.6 `React.lazy()` on all non-dashboard pages
- File: [frontend/src/App.tsx:11-17](frontend/src/App.tsx#L11)
- SymbolPage, WatchlistPage, SystemHealth, ScannerPage, Templates, Indicators all code-split
- Dashboard stays in the main bundle (landing page) but lazy-mounts below-fold cards (deferred — see below)

#### 3.6.7 `React.memo` on all major components
- RegimeCard, TrendCard, MarketContextCard, AlertsCard, TopMoversCard, WatchlistTable all wrap in `React.memo` with shallow-prop comparison
- Verified by inspection

#### 3.6.8 Virtualized WatchlistTable
- `react-window` `FixedSizeList` (already in use)
- Table scrolls 60fps with 100+ symbols

#### 3.6.9 `seed_engine_from_bars` capped at 200 bars
- File: [backend/market_data/services/engine_seeder.py:76](backend/market_data/services/engine_seeder.py#L76)
- Pre-warm cost is bounded — no symbol ever seeds more than 200 bars regardless of watchlist history

### Verification (re-run on live server 2026-09-04)

```bash
# Cold regime endpoint (engine never seen this symbol)
$ curl -s -o /dev/null -w '%{time_total}\n' http://127.0.0.1:5001/api/regime/AAPL/current
0.0205    # 20.5 ms (was ~245 ms pre-fix)

# Warm regime endpoint (after first call)
$ curl -s -o /dev/null -w '%{time_total}\n' http://127.0.0.1:5001/api/regime/AAPL/current
0.0024    # 2.4 ms (TTL cache hit)

# 6 parallel dashboard endpoints (Promise.all)
# See phase_audit_v3.md for the full benchmark script
28.9ms avg over 5 runs
```

### Deferred (not on the critical path)

- **Lazy-mount below-fold cards via `IntersectionObserver`** — Dashboard already returns in 28.9 ms warm. Below-fold card lazy-mount is a nice-to-have but not measurable; deferred to a future polish phase.
- **`tests/frontend/dashboard.test.tsx`** — manual measurements above are the perf baseline. A regression test for "regime endpoint < 50 ms warm" can land later.

### Deferred to v4 (out of scope for 3.6)
- New dashboard widgets (Sector heatmap, Recent signals, Market overview, Watchlist mini)
- Global search / command palette
- Layout reorganization (2-column)
- Keyboard shortcuts
- Responsive design (5 breakpoints)

---

## Cross-cutting concerns

### Testing
- Backend: every new module gets pytest coverage. Target: maintain 80% backend coverage.
- Frontend: every new component gets a `.test.tsx`. Use React Testing Library + Jest (already configured).
- Integration: backup round-trip, structured log parsing, chart click-to-place end-to-end.

### Documentation
- `docs/Version_3/v3_plan.md` (this file)
- `docs/Version_3/phase_audit_v3.md` — scorecard updated at end of each phase
- Update `docs/README.md` with new feature pages

### Sequencing
The five phases can run in parallel where dependencies allow:

```
Phase 3.1 (Resampling) ──┐
Phase 3.2 (DB)         ──┤
Phase 3.3 (Retention)  ──┤
Phase 3.4 (Charts)     ──┼── can run concurrently in separate worktrees
Phase 3.5 (Logging)    ──┤
Phase 3.6 (Perf)       ──┘  (✅ complete)
```

Recommended order: **3.1 → 3.2 → 3.3 → 3.5 → 3.4 → 3.6**. Phase 3.1 (resampling) changes the ingestion pipeline and DB schema — do it first so everything downstream benefits. Then database (3.2) to back up the new schema. Logging (3.5) observes the new ingestion flow. Charts (3.4) is the largest UI surface. Dashboard performance (3.6) consumes outputs from all the others and is now complete.

### Risks
- **Backup/restore data integrity:** restoring overwrites current DB. Need explicit confirmation + dry-run mode.
- **JSON logs breaking log aggregators:** if anyone runs an ELK stack expecting plain text, they need to update. Document the switch in `LOG_FORMAT=json` migration note.
- **Drawing on canvas may conflict with chart pan/zoom:** the click handler needs to disambiguate "click to draw" from "click to deselect". Default behavior should be: drawing tool active = click draws; no tool = click is pan/zoom.
- **Lazy-loading on a 4G connection:** skeleton flash becomes long. Add a 200ms minimum-display-time to avoid jarring flashes.

---

## Out of scope (explicit)

These were considered and deferred:
- **PostgreSQL migration** — SQLite + WAL handles current load (~640 signals, ~18k bars). Move to Postgres only when concurrent writes become a real problem.
- **Log viewer page in UI** — `grep` + `jq` covers current operational needs. Build the UI viewer if/when log analysis becomes a daily task.
- **Multi-user auth** — single-user app, no auth model needed yet.
- **Real-time multi-window sync** — WebSocket push exists for scanner; extend to other panels only if multi-tab is requested.
- **Mobile native app** — web-only for v3.

---

## Phase 3.1 — Timeframe Resampling (1m-only storage)

**Why now:** Currently the system ingests and stores 7 timeframes (1m, 5m, 15m, 30m, 1h, 1d, 1wk) per symbol, tripling write volume and storage. Every timeframe above 1m is mathematically derivable from 1m bars — storing them redundantly wastes ~80% of bar writes.

### Items

#### 3.1.1 Resampling engine
- New file: `backend/utils/resampler.py`
- `resample_ohlcv(bars: list[Bar], target_tf: str) -> list[Bar]` — pure function, no DB, no I/O
- Aggregation rules:
  | Target | Rule | Boundary |
  |---|---|---|
  | `5m` | 5 × 1m | floor(minute / 5) × 5 |
  | `15m` | 15 × 1m | floor(minute / 15) × 15 |
  | `30m` | 30 × 1m | floor(minute / 30) × 30 |
  | `1h` | 60 × 1m | floor(minute / 60) × 60 |
  | `1d` | floor((minute + 330) / 1440) — 1440 × 1m | NYSE calendar day (9:30 ET start) |
  | `1wk` | ISO week boundary | Monday 00:00 UTC |
- Each output bar: `open` = first 1m open, `high` = max of all highs, `low` = min of all lows, `close` = last 1m close, `volume` = sum of all volumes
- Handle incomplete (partial) periods: if the current period has < N 1m bars and market is still open, return the partial bar with `data_quality = "incomplete"` flag
- Validate: resampling yfinance's own 5m output and comparing to the resampler output should match within rounding error

#### 3.1.2 Store only 1m
- Modify `IngestionService`: set `self.timeframes = ["1m"]` — stop fetching/storing higher timeframes from providers
- Provider calls drop from 56/symbol/cycle → 8/symbol/cycle (7× fewer API calls)
- Store 1m bars in `bars` table as before
- Add a `source` column to `bars` table (via Alembic): `"raw"` for stored 1m, `"resampled"` for computed higher-TF bars

#### 3.1.3 On-the-fly resampling at read time
- Modify `BarRepository.get_bars()` — after fetching 1m bars from DB, if a higher timeframe is requested:
  1. Check if enough 1m bars exist in DB to cover `from_ts` to `to_ts` for the target timeframe
  2. If yes → resample from stored 1m
  3. If no → fall back to fetching target timeframe directly from the provider (hybrid mode for historical gaps)
- Cache resampled results in `market_data.services.cache` with the same TTL as the timeframe (1d → 10s, 1wk → 60s)
- On each new 1m bar ingested: proactively resample and update cache for `1d` (most-used higher TF)

#### 3.1.4 Historical backfill: one-time 1m fetch
- New script: `scripts/backfill_1m.py` — one-time migration
- Fetches 3 months of 1m bars for all symbols from yfinance
- Stores all as `1m` bars
- Total: ~8 symbols × 3mo × 390 1m bars/day ≈ ~2,800 bars/symbol ≈ ~22,000 total rows (tiny vs 7 TFs × 7 = 154,000)
- Run once at the start of Phase 3.1; mark in a `data/migrations/3.1_1m_backfill.json` flag file so it doesn't re-run

#### 3.1.5 Update API contract
- `GET /api/bars/{symbol}/{timeframe}` — unchanged from the caller perspective; resampling is transparent
- `GET /api/bars/{symbol}/{timeframe}?resample_from=1m` — explicit hint to force 1m resampling
- Add `resampled` field to `BarResponse`: `true` if computed from 1m, `false` if raw
- Live bar feed: `ingestion_service` still emits fresh bars for all timeframes (computed from the newest 1m); consumers see no change

#### 3.1.6 Schema migration
- Add `source VARCHAR(20) DEFAULT 'raw'` to `bars` table (via Alembic)
- Create `ix_bars_source_timeframe` composite index for cache lookups
- Remove per-timeframe `last_bar_update` entries for non-1m timeframes in `ingestion_service`

### API changes
| Endpoint | Change |
|---|---|
| `GET /api/bars/{sym}/{tf}` | Resample from 1m if needed; add `source` field |
| `GET /api/bars/{sym}/{tf}?resample_from=1m` | Force 1m resampling |
| `GET /api/system-health` | Add `bars_stored`, `bars_1m_only`, `bars_resampled` counts |
| Ingestion cycle | 56 provider calls → 8 (1m only) |

### Verification
- Run `scripts/backfill_1m.py`; verify 3mo of 1m data exists for all symbols
- `GET /api/bars/AAPL/1d?from=2026-07-01&to=2026-09-01` — verify response has `source: "resampled"` and bars match what yfinance returns for 1d directly
- Ingestion logs: confirm only 1m timeframe is fetched per cycle
- Resampling: write a test that fetches 5m directly from yfinance, fetches 1m from yfinance, resamples it, and asserts the two match within 0.01% for open/high/low/close
- Add `tests/utils/test_resampler.py` covering all timeframe boundaries

#### 3.1.7 Provider compatibility

All three providers implement the same `BaseMarketDataProvider` interface and all support `1m` as their minimum timeframe, so the 1m-only change works uniformly. But each provider has nuances that need explicit handling:

| Provider | 1m min | 1d+ supported | Notes |
|---|---|---|---|
| **yfinance** | ✅ | ✅ | Default; no changes needed |
| **Webull** | ✅ | ✅ | `get_latest_bar(symbol, timeframe)` currently calls `range_="1d"` — needs to fetch 1m range and resample. `max_timeframe="1y"` covers all resampling targets |
| **Finnhub** | ✅ | ✅ | `max_timeframe="1mo"` — but the resampling targets (1d, 1wk) only need 1m data, so the max_timeframe constraint is on the source fetch, not the resample target. Needs 1m range config |

**Required provider changes (none break the public interface):**

1. **`yfinance`:** No changes. `interval="1m"` works; existing `_resolve_interval` already maps it.
2. **Webull:**
   - `get_latest_bar(symbol, timeframe)` currently calls `get_historical_bars(..., range_="1d")`. With 1m-only:
     - If `timeframe == "1m"`: keep `range_="1d"` (returns ~390 1m bars; take the last one)
     - Otherwise: fetch `range_="1d"` of 1m bars, take the last 1m, then **don't resample** — return the last 1m bar as the "latest" for that symbol. (The resampler is for historical windows, not the single latest bar; clients like the chart need the in-progress 1m until the higher-TF candle completes.)
   - Update docstring to make this behavior explicit
3. **Finnhub:**
   - `get_latest_bar(symbol, timeframe)` similarly needs to fetch 1m and return the latest 1m bar when a higher timeframe is requested
   - The resampler is only used for historical range fetches (`get_historical_bars`)
4. **All providers:**
   - Add `_RANGE_TO_COUNT` for `1m` (or equivalent): 1m is 390 bars/day, so `1d=390, 5d=1950, 1mo=8400, 3mo=25200`
   - Webull: `_RANGE_TO_COUNT["1d"]` is currently `1` (one daily bar). For 1m: change to `390`. Document that 1m only goes back 30 days on Webull SDK.
   - Finnhub: `_resolve_resolution("1m")` exists; just need to ensure 1m is the default for ingestion cycles

**Backward compat:**
- The `timeframe` parameter on `get_historical_bars` / `get_latest_bar` / `get_bar` stays. Callers that ask for `timeframe="1d"` will get resampled bars back. Callers that ask for `timeframe="1m"` get raw 1m.
- The `range_` parameter's meaning shifts: "1d" of 1m = 390 bars, "1d" of 1d = 1 bar. Tests need to be aware of this.

#### 3.1.8 Fallback for missing 1m data (gaps)

- Some symbols (illiquid, recent IPOs) may not have 1m data going back 3 months
- Read-time fallback: if `len(stored_1m_bars) < expected_for_window`, fetch the target timeframe directly from the provider and serve that instead
- Mark these as `source: "raw"` even though they're higher-TF, since they came from the provider
- This is the **hybrid mode** mentioned in 3.1.3 — explicitly flag in the response

#### 3.1.9 Webull/Finnhub 1m historical limits
- **Webull SDK:** 1m data typically available for the last 30 days; older than that the SDK returns errors
- **Finnhub:** 1m data is part of the paid tier; free tier may not have it
- For the one-time 3-month backfill, **default to yfinance** as the 1m source (always available, no auth). After backfill, the live ingestion cycle uses whatever primary provider is configured (which may be Webull or Finnhub).
- Document the limit in the migration script's error message if backfill fails for a provider

### Risks
- **Resampling boundary mismatch:** If stored 1m bars don't align to NYSE session boundaries (weekend gaps, pre/post market), 1d resampling will include pre/post market 1m bars in the NYSE day calculation. Fix: clip 1d aggregation to 9:30–16:00 ET per bar.
- **Partial 1d bars:** If 1m data is missing for some bars (e.g. market closed but not stored), the 1d close will be wrong. Fix: check bar count per day; if < 390, flag as `data_quality = "incomplete"`.
- **Historical gap:** The one-time backfill fetches 3mo of 1m — enough for current signals. But signals that need 1yr of 1d data need 1yr of 1m, which is too much. Fix: signals only need 20 future bars; they don't need 1yr of historical 1d. This is fine.

---

## Phase 3.7 — All-Timeframe Live Ingestion + Resample-at-Write + Backfill Config

**Why now:** Phase 3.1 made the *read* side resample on-the-fly from 1m bars. But:

- 1m-only storage means higher-TF bars (5m, 15m, 1h, 4h, 1d) are computed at every read. Caching helps but is fundamentally less efficient than storing the bars once.
- 1h backfill is missing entirely.
- Backfill provider names are hardcoded in `backfill_service.py` — can't swap primary without a code change.
- Alpaca free tier lags 1m bars by ~15 min — live 1m needs Webull or yfinance.
- 1d bars at 13:30 ET are a known noise artifact from the Alpaca free tier.

**Decision:**

1. Make every backfill provider configurable via `.env` (`BACKFILL_*_PRIMARY`, `BACKFILL_*_FALLBACK`, `BACKFILL_*_GAPFILL`).
2. Add a 1h backfill tier alongside the existing 1m and 1d tiers.
3. Add yfinance/webull gap-fill for the 1m 15-min lag window.
4. Live ingestion switches primary to Webull (chain: Webull → yfinance → Alpaca).
5. Move resample from read-time to write-time: 2m/3m/5m/15m/30m/4h/1wk are persisted to the DB by background loops, then read as direct rows.
6. Drop 13:30 noise from 1d bars during both live ingestion and backfill.

### Items

#### 3.7.1 Backfill settings + per-TF chains
- `BackfillSettings` in `backend/config/settings.py` with per-timeframe primary/gapfill/fallback fields + rate-limit knobs.
- All providers loaded via `MarketDataManager._PROVIDER_INSTANCES` (no hardcoded provider names in `backfill_service.py`).

#### 3.7.2 1m backfill with gap-fill
- `_fetch_tier1_1m_bars()` fetches Alpaca primary, then yfinance/webull fills the latest 15-min lag window. Dedupe by timestamp.
- Range: 1d / 5d / 1mo / 3mo (Alpaca free-tier 1m cap).

#### 3.7.3 1h backfill (new tier)
- `_fetch_tier2_1h_bars()` — Alpaca primary, yfinance/webull fallback.
- Range: 6mo / 1y / 2y based on `days` parameter.
- Up to ~2 years of 1h history per symbol.

#### 3.7.4 1d backfill — 13:30 noise guard + .env providers
- `_fetch_tier2_1d_bars()` — drops bars where `hour == 13 and minute == 30` (Alpaca free-tier noise).
- Provider chain from `BACKFILL_1D_PRIMARY` / `BACKFILL_1D_FALLBACK`.

#### 3.7.5 Live ingestion loops
- 1m: existing `_bar_ingestion_loop` (no change) — uses `MarketDataManager` which auto-resolves Webull → yfinance → Alpaca from `.env`.
- 1h: new `_1h_write_loop()` — fires every hour at :05 past, writes 1h bar.
- 1d: new `_daily_write_loop()` — fires at 16:05 ET, writes 1d bar + resamples 1wk.

#### 3.7.6 Resample-at-write loops
- `_resample_and_upsert(target_tf, source_tf)` — fetches source bars, resamples via `backend.utils.resampler.resample_ohlcv`, upserts confirmed-closed buckets (end-time < now).
- 2m/3m/5m/15m/30m: every 2 minutes from 1m bars.
- 4h: every 4 hours at :05 past (00:05, 04:05, 08:05, 12:05, 16:05, 20:05 ET).
- 1wk: daily at 16:05 ET from 1d bars.

#### 3.7.7 Direct-read bar repository
- `BarRepository.get_bars()` returns rows directly (no read-time resample).
- Add 2m/3m to `_TF_MULTIPLIER` and `_WIDENING_HOURS`.

#### 3.7.8 Drop read-time resample fallback
- Remove `is_resampled = timeframe != "1m"` gate in `get_bars()`.
- Remove the `fallback_provider` callable (was added in 3.1.16 for read-time fallback — no longer needed since all TFs are stored).

### Risks
- **Bars table size growth** with 10 stored timeframes per symbol: estimate ~10× growth. The rolling 1000-day prune keeps it bounded; 1m alone is the dominant storage consumer.
- **Write contention**: 4 new async loops running concurrently with the existing 5 (quote, bar, status, provider_health, signal_recording). All share `SessionLocal()`; the 1m write path already handles this pattern.
- **Provider chain misconfig**: a typo in `BACKFILL_1M_PRIMARY=foooo` logs a warning + skips (existing pattern in `MarketDataManager._initialize_providers`).

### Verification
- `backfill_symbol_history_sync("AAPL", days=30)` — backfill completes with `tier1_written`, `tier2_written` (1h), `tier3_written` (1d) all > 0.
- `SELECT provider, COUNT(*) FROM bars WHERE timeframe='1m' GROUP BY provider` — see `alpaca` for the bulk and `yfinance` for the most recent 1–3 rows (gap-fill).
- `SELECT * FROM bars WHERE timeframe='1d' AND timestamp LIKE '%13:30%';` — 0 rows.
- All 10 timeframes have rows after 1 day: `SELECT timeframe, COUNT(*) FROM bars GROUP BY timeframe;`
- All resampler tests still pass.

---

## Open questions

1. Backup encryption? (Probably no — local disk, single-user. Revisit if multi-tenant.)
2. Log retention policy? (Suggest 30 days rotated, 1 year compressed. Confirm with user.)
3. Sector heatmap data source — pull live or use cached? (Cached via existing `top_movers` is fine for v3.)
4. Should command palette navigate to pages, or just symbols? (Both; pages first, then symbols.)
5. Drawing tool keyboard shortcuts? (e.g., `T` for trendline, `H` for horizontal.) — yes, add to the keyboard shortcuts file.
