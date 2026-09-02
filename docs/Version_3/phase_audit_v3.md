# Version 3 Phase Audit

**Last updated:** 2026-09-02
**Scope:** Database backup/optimization, Charts, Structured logging, Dashboard rebuild

---

## Scorecard

| # | Phase | Status | Notes |
|---|---|---|---|
| 3.1 | Timeframe Resampling (1m-only storage) | ✅ DONE | All 3.1.1–3.1.32 complete |
| 3.2 | Alpaca Integration (REST + WebSocket) | ✅ DONE | All 3.2.1–3.2.7 complete |
| 3.3 | Database Backup & Optimization | 🟡 PLANNED | Not started |
| 3.4 | Charts (drawing v1, line/area/HA, indicators) | 🟡 PLANNED | Not started |
| 3.5 | Structured Logging (JSON formatter, rotation) | 🟡 PLANNED | correlation_id.py done (3.5.1); 3.5.2 pending |
| 3.6 | Dashboard Performance (lazy-load, memo, virtualize) | 🟡 PLANNED | Not started |

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

## Phase 3.3 — Database Backup & Optimization

- ⬜ 3.3.1 `backend/database/wal_streamer.py` — WALStreamer (Litestream-style local WAL streaming)
- ⬜ 3.3.2 `GET /api/system/backup-status` endpoint
- ⬜ 3.3.3 Index audit — `EXPLAIN QUERY PLAN` on top queries + Alembic migration
- ⬜ 3.3.4 Query optimization — N+1 fixes, eager loading
- ⬜ 3.3.5 `VACUUM INTO` + `ANALYZE` on schedule (post-snapshot, off-line)
- ⬜ 3.3.6 Database tab in SystemHealth page (file size, row counts, WAL health)
- ⬜ 3.3.7 `tests/database/test_wal_streamer.py`

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

## Phase 3.6 — Dashboard Performance

- ⬜ 3.6.1 Lazy-load below-fold Dashboard cards via `useIntersectionLazy` hook
- ⬜ 3.6.2 React.memo audit — MarketContextCard, AlertsCard, TopMoversCard
- ⬜ 3.6.3 Virtualize AlertsCard (>30 rows) and HistoricalSignalCard (>50 rows)
- ⬜ 3.6.4 `tests/frontend/dashboard.test.tsx` (virtualization thresholds, lazy-load)

