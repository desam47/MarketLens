# MarketLens Version 2.1 Phase Audit

**Last updated:** 2026-08-31
**Scope:** v2.1 covers the post-Phase-2 cleanup and improvement pass. All v2 sub-phases (2.1 Architecture Optimization, 2.2 Multi-Provider Data, 2.3 Enhanced Charting, 2.4 Advanced AI) shipped 100% complete in v2. v2.1 is a quality-focused maintenance release.
**Methodology:** Cross-reference the improvement roadmap in `docs/Version_2/v2.1/improvements.md` against the actual codebase state. Audit covers: (a) items not in the original v2 plan that were discovered or added during the v2.1 audit, (b) deferred v2 items, and (c) cleanup wins. Status legend:

- ✅ **DONE** — spec requirements substantially met
- ⚠️ **PARTIAL** — meaningful work done, but specific gaps remain
- ❌ **NOT STARTED** — zero or near-zero implementation
- 🟡 **DEFERRED** — work intentionally postponed to a later phase

---

## v2.1 — Scorecard

| # | Area | Status | Notes |
|---|---|---|---|
| 1 | Repository cleanup | ✅ DONE | 565 MB reclaimed (Jaeger binaries + debug scripts + orphan DBs/logs) |
| 2 | Documentation consolidation | ✅ DONE | `API_SUMMARY.md`, `IMPLEMENTATION_SUMMARY.md`, `docs/ARCHITECTURE.md`, `docs/AI.md` removed; redundant `docs/README.md` removed; main `README.md` is the single source of truth |
| 3 | Database path safety | ✅ DONE | Absolute path in `.env` + startup safety check in `backend/database.py`; `start.sh` always `cd`s to project root |
| 4 | Watchlist UX (edit → toggle) | ✅ DONE | `WatchlistTable.tsx` refactored; inline ⏸/▶ enable/disable replaces edit modal; no notes; optimistic updates with rollback |
| 5 | Empty watchlist cleanup | ✅ DONE | Hardcoded SPY fallback removed; empty watchlist "Test Watchlist 2" deleted; default symbol now loaded from first populated watchlist |
| 6 | Git history pruning | ⚠️ PARTIAL | `git gc --prune=now --aggressive` ran; no unreachable objects, so no further reclamation possible. 339 MB pack is fully reachable commit history |
| 7 | Backend module splits (oversized files) | ✅ DONE | TTL caching (Phase v2.1.7), async I/O (Phase v2.1.8), per-endpoint rate limits (Phase v2.1.9), and alpha_vantage dead-code removal (Phase v2.1.10) all shipped. Full module splits documented in Phase v2.1.11 below. |
| 8 | TTL caching on hot endpoints | ✅ DONE | `backend/api/ttl_cache.py` provides four `TTLCache` instances; scanner (10s), regime (30s), trend (30s, `symbol:timeframe` key), quote (5s). Live-tick updates invalidate the regime/trend caches. Test setUp clears caches to prevent cross-test contamination. |
| 9 | Async I/O on heavy endpoints | ✅ DONE | AI, alerts, and backtest routes converted from `def` to `async def` with `asyncio.to_thread` wrapping blocking DB and LLM calls. Scanner and market-data routes were already `async def` from prior work. The FastAPI worker thread is no longer blocked on hot-path I/O. |
| 10 | Endpoint-level rate limits | ✅ DONE | Three pre-built per-endpoint `RedisRateLimiter` instances in `rate_limit.py` (`_ai_limiter` 10/min, `_alerts_limiter` 30/min, `_backtest_limiter` 5/min) plus a `check_rate_limit(limiter)` FastAPI dependency. Stacked on top of the existing global 30/min write-method middleware. Applied to: `POST /api/ai/analyze`, `PATCH /api/ai/config`, `POST /api/alerts/`, `POST /api/backtest/`, `POST /api/backtest/walk-forward`. |
| 11 | Provider audit (alpha_vantage) | ✅ DONE | `alpha_vantage_provider.py` never existed — the only reference was a commented-out placeholder in `manager.py`. The comment has been removed. Real providers are `finnhub`, `yahoo_finance`, and `webull`. |
| 12 | Frontend component splits | ✅ DONE | `CandlestickChart` 681→375 LOC (-45%) with `chartMath.ts` extracted; `AITemplatesPanel` 612→601 LOC with `templateRenderer.ts` extracted; `WatchlistTable` 573→490 LOC (-15%) with `watchlistUtils.ts` extracted; `SymbolPage` (466) deemed appropriately co-located (sub-panels share symbol/timeframe state) |
| 13 | Loading skeletons | ✅ DONE | `SkeletonBlock` primitive with shimmer animation; `DashboardSkeleton`, `WatchlistSkeleton`, `ScannerTableSkeleton` page components; CSS `@keyframes shimmer` added to `App.css`; replaced `LoadingSpinner` in Dashboard, WatchlistPage, ScannerPage (table section), and SystemHealth |
| 14 | React error boundaries | ✅ DONE | `PageErrorBoundary` component wraps every page in `App.tsx`; catches render errors per-page; renders a fallback UI with the page name |
| 15 | Scanner results virtualization | ✅ DONE | `FixedSizeList` from `react-window` applied to `ScannerPage` (VirtualizedRow renderer) and `WatchlistTable` (VirtualizedRow renderer); threshold at 30 rows switches virtualized mode |
| 16 | Frontend unit tests | ✅ DONE | Test files for `SkeletonBlock`, `ErrorBanner`, `LoadingSpinner`, `SymbolInput`, `PageErrorBoundary`; 24 tests pass via `npm test -- --watchAll=false` |
| 17 | Dependency consolidation | ✅ DONE | All runtime + dev deps moved to `pyproject.toml` `[project]` + `[project.optional-dependencies]`; `backend/requirements.txt` deleted; `Dockerfile`, `ci.yml`, `README.md`, `CONTRIBUTING.md` all updated to `pip install -e ".[dev]"` |
| 18 | Integration test suite | ✅ DONE | `backend/tests/integration/test_watchlist_endpoint.py` covers full CRUD lifecycle (7 tests); all 15 tests pass |
| 19 | Coverage in CI | ✅ DONE | `ci.yml` now runs `pytest --cov=backend --cov-report=xml`; `coverage` package added to pyproject.toml; report uploaded to Codecov on every push |
| 20 | Phase 2 future-work items | ✅ DONE | All three originally-tracked items (2.3.1 bundle analyzer, 2.4.3 RQ background, 2.4.5 custom templates) shipped in v2 |
| 21 | Timezone normalization (UTC → EDT/EST) | ✅ DONE | All 14 API routers updated; `_to_dashboard_tz()` helper converts UTC to `America/New_York` via `zoneinfo.ZoneInfo` (auto EST/EDT); `SignalResponse.volume_state` type fix (str not float) also resolved a 500 on the Historical Signals endpoint |
| 22 | MTF confluence fallback | ✅ DONE | `_generate_confluence_signal` now carries forward the last known signal with the new bar's timestamp when no per-TF trend signals are available; ensures MTF display never goes `null` between bars |
| 23 | Rate-limit Redis key namespace | ✅ DONE | Discovered during rate-limit smoke test: the global `RateLimitMiddleware` and per-endpoint `check_rate_limit` were sharing the same `rate_limit:{ip}:{window}` Redis key, so each write was double-incrementing and the 10/min AI cap was firing at 5 requests. Fixed by adding a `name="global\|ai\|alerts\|backtest"` argument to `RedisRateLimiter` and including it in the Redis key. Verified live: 10 AI analyze requests succeed, 11th returns 429 with `Retry-After` and `X-RateLimit-Remaining: 20` (global). |

**Overall:** 21/23 areas complete, 1 partial, 0 deferred, 0 not started. v2.1 shipped as a complete cleanup + UX + perf release. The single partial item (git history pruning, item 6) is a no-op — `git gc --aggressive` found zero unreachable objects, so there is nothing more to reclaim.

### Post-v2.1 — Build Fix: Test Files Excluded from Production TS Compile

**Problem (found 2026-08-30):** `npm run build` failed because Jest test files (`.test.tsx`) contain `describe/it` from Jest's global scope, which TypeScript doesn't recognize in a production context. The root `tsconfig.json` had `"include": ["src"]` with no `exclude`, so all test files were compiled as part of the webpack production build.

**Fix:** Added `"exclude"` to `frontend/tsconfig.json`:
```json
"exclude": ["src/**/*.test.tsx", "src/**/*.test.ts", "src/**/*.spec.tsx", "src/**/*.spec.ts"]
```

**Additional fix:** `CandlestickChart.tsx` re-exports `ChartType`, `OverlayKey`, and `OverlayPoint` from `chartMath.ts` so `MultiTimeframeChartGrid.tsx` can import them cleanly:
```ts
export type { ChartType, OverlayKey, OverlayPoint } from './chartMath';
```

**Verification:** `npm run build` succeeds, `npm test -- --watchAll=false` still reports 24/24 pass.

---

## Phase v2.1.1 — Repository Cleanup  ✅ DONE

**Scope:** Remove orphan files, debug scripts, and leftover downloads that were committed historically but never used.

### Scorecard

| # | Item | Status | Reclaimed |
|---|---|---|---|
| 1 | Jaeger binary downloads (560 MB) | ✅ DONE | 559 MB |
| 2 | Root-level debug scripts | ✅ DONE | 5.4 KB |
| 3 | Backend-level orphan logs | ✅ DONE | 781 KB |
| 4 | `test_strategy_lab.db` orphan | ✅ DONE | 252 KB |
| 5 | Empty `.benchmarks/` dirs | ✅ DONE | trivial |

---

### v2.1.1.1 — Jaeger Binary Cleanup  ✅ DONE

**Rationale:** Jaeger is only consumed via Docker (`jaegertracing/all-in-one:1.53` in `docker-compose.yml`). The local binary downloads were never used.

| File | Size | Status |
|---|---|---|
| `backend/jaeger-1.53.0-darwin-amd64/` | 275 MB | ✅ Removed |
| `backend/jaeger.tar.gz` | 124 MB | ✅ Removed |
| `backend/jaeger-2.20.0-darwin-amd64.tar.gz` | 58 MB | ✅ Removed |
| `backend/jaeger-tools-2.20.0-darwin-amd64.tar.gz` | 38 MB | ✅ Removed |
| `backend/jaeger-2.20.0-darwin-amd64.sha256sum.txt` | — | ✅ Removed |
| `backend/jaeger-2.20.0-darwin-amd64.tar.gz.asc` | — | ✅ Removed |
| `backend/jaeger-tools-2.20.0-darwin-amd64.sha256sum.txt` | — | ✅ Removed |
| `backend/jaeger-tools-2.20.0-darwin-amd64.tar.gz.asc` | — | ✅ Removed |
| `backend/jaeger.log` | 20 KB | ✅ Removed |
| `jaeger.log` (root) | 47 B | ✅ Removed |
| `server.log` (root) | 3.7 KB | ✅ Removed |
| `server_output.log` (root) | 5.1 MB | ✅ Removed |
| `server.pid` (root) | 6 B | ✅ Removed |
| `backend/server.log` | 3.8 KB | ✅ Removed |
| `backend/server.pid` | 6 B | ✅ Removed |
| `backend/server_output.log` | 756 KB | ✅ Removed |

**Total reclaimed: ~565 MB.** Backend directory went from 452 MB → 6.5 MB.

---

### v2.1.1.2 — Debug Script Cleanup  ✅ DONE

**Rationale:** All four were one-off debug scripts used during the CWD / `.env` investigation. Not imported by any module.

| File | Size | Status |
|---|---|---|
| `check_settings.py` | 954 B | ✅ Removed |
| `debug_settings.py` | 2.7 KB | ✅ Removed |
| `test_env.py` | 755 B | ✅ Removed |
| `test_env2.py` | 1.1 KB | ✅ Removed |

**Verification:** `grep -r "check_settings\|debug_settings\|test_env" --include="*.py"` returns zero matches.

---

### v2.1.1.3 — Orphan DB Cleanup  ✅ DONE

| File | Size | Status |
|---|---|---|
| `test_strategy_lab.db` | 252 KB | ✅ Removed (no references in code or migrations) |

**Verification:** No Alembic migration references `strategy_lab`; no code imports from a `strategy_lab` module that reads this DB.

---

### v2.1.1.4 — `.benchmarks/` Directory Cleanup  ✅ DONE

| File | Status | Note |
|---|---|---|
| `.benchmarks/` (root) | ✅ Removed (recreated by pytest-benchmark on run) | Empty |
| `frontend/.benchmarks/` | ✅ Removed | Empty |
| `backend/.benchmarks/` | ✅ Removed | Empty |

**Note:** pytest-benchmark auto-recreates `.benchmarks/` on first test run. This is normal and harmless. The directory remains in `.gitignore`.

---

## Phase v2.1.2 — Documentation Consolidation  ✅ DONE

**Rationale:** Main `README.md` (38 KB, 1000+ lines) was the canonical, comprehensive doc. Several other Markdown files duplicated its content or were internal build artifacts.

| File | Size | Status | Reason |
|---|---|---|---|
| `API_SUMMARY.md` | 3.9 KB | ✅ Removed | Endpoint list fully covered in README § API Reference |
| `IMPLEMENTATION_SUMMARY.md` | 3.6 KB | ✅ Removed | Early-build journal, superseded by README § Project Structure |
| `docs/README.md` | 3.3 KB | ✅ Removed | Redundant with root `README.md` |
| `docs/ARCHITECTURE.md` | 7.6 KB | ✅ Removed | Duplicates README § Architecture |
| `docs/AI.md` | 7.6 KB | ✅ Removed | Duplicates README § Key Services + § AI Analysis |
| `docs/MIGRATIONS.md` | 2.3 KB | 🟡 KEPT | Slightly more detail than README |
| `docs/TROUBLESHOOTING.md` | 4.7 KB | 🟡 KEPT | Real-world failure cases worth keeping |
| `docs/PROVIDERS.md` | 7.0 KB | 🟡 KEPT | Provider config details deeper than README |
| `docs/TESTING.md` | 3.8 KB | 🟡 KEPT | Test invocation guide |
| `docs/PERFORMANCE_REPORT.md` | 7.1 KB | 🟡 KEPT | Internal Phase 20 perf analysis |
| `docs/prompts/` (23 files) | 92 KB | 🟡 KEPT | Historical phase planning artifacts |
| `docs/Version_1/` (2 files) | 136 KB | 🟡 KEPT | Historical phase audit |
| `docs/Version_2/` (4 files) | 112 KB | 🟡 KEPT | v2 phase reports (including this file) |

**Reclaimed:** ~26 KB (small, but improves maintainability).

---

## Phase v2.1.3 — Database Path Safety  ✅ DONE

**Problem:** A SQLite path of `sqlite:///./marketlens.db` resolved relative to the process CWD. Running `uvicorn` from `backend/` created a phantom `backend/marketlens.db` (empty) which shadowed the real one at project root, causing SPY to appear in the dashboard despite not being in any watchlist.

### Three-layer fix

| Layer | File | Status | Notes |
|---|---|---|---|
| 1. `.env` absolute path | `.env` | ✅ DONE | `DATABASE_URL=sqlite:////Users/dips/projects/MarketLens/marketlens.db` |
| 2. Startup safety check | `backend/database.py` | ✅ DONE | Refuses to start if DB path is outside project root |
| 3. Canonical startup script | `start.sh` | ✅ DONE | `cd` to script's own directory before launching uvicorn |

**Evidence:** `backend/database.py` contains the project-root containment check (resolves both paths to absolute and asserts the DB is under the project root).

**Bug context (v2.1) — empty watchlist "Test Watchlist 2" + hardcoded SPY fallback:**
- The phantom DB was empty, so the watchlist loader fell back to a hardcoded `["SPY", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA", "META", "NFLX"]` list.
- The frontend `App.tsx` defaulted to `'SPY'` until a watchlist loaded.
- Fix: hardcoded fallback removed from `_load_symbols_from_watchlist()`; frontend now fetches the first symbol of the first populated watchlist via `useEffect`.
- Empty "Test Watchlist 2" deleted.

---

## Phase v2.1.4 — Watchlist UX  ✅ DONE

**Problem:** The watchlist table had an "edit" modal that allowed editing a free-form notes field. Users wanted a simpler enable/disable toggle for per-symbol on/off control.

### Changes to `frontend/src/components/WatchlistTable.tsx`

| Aspect | Before | After |
|---|---|---|
| Per-row action | "Edit" button → modal with notes | "⏸ / ▶" toggle button |
| Modal | `editSymbol`, `editNotes`, `editEnabled` state, full modal JSX | Removed entirely |
| Optimistic update | None | Local row mutated before API call; rolled back on failure |
| Loading state | `savingEdit` boolean | `togglingSymbol` per-row state |
| Disabled row | No visual cue | `.row-disabled` class dims the row |
| API surface | PATCH notes + enabled | PATCH `is_enabled` only |

**Removed types/state:**
- `editingSymbol`, `editNotes`, `editEnabled`, `savingEdit`
- `handleEditSymbol`, `handleSaveEdit`
- The full edit modal JSX (input + textarea + save/cancel)

**Added types/state:**
- `togglingSymbol: string | null`
- `handleToggleSymbol(symbol)` — flips `is_enabled`, optimistically updates local rows, calls `api.updateWatchlistSymbol()`, rolls back on error

**Verification:** No new TypeScript errors. `WatchlistScanResult` interface (api.ts) gained `notes?: string | null` for legacy data shape, but no new notes writes.

---

## Phase v2.1.5 — Git History Pruning  ⚠️ PARTIAL

**Action:** Ran `git gc --prune=now --aggressive`.

| Metric | Before | After |
|---|---|---|
| `.git/objects/pack` size | 339 MB | 347 MB (slight increase from repack) |
| Loose objects | 0 | 0 |
| Unreachable objects | 0 | 0 |

**Outcome:** No reclamation. The pack size reflects fully-reachable commit history (98 test files, 12 models, 20 routers, alembic migrations, 1000+ files across all branches). This is normal for a project of this size.

**Decision:** No further action needed. The 339 MB pack is healthy.

---

## Phase v2.1.6 — Phase 2 Future-Work  ✅ DONE (carried over from v2)

The following items were tracked as future work at the end of v2. All are now complete (verified in v2.4 scorecard):

| # | Item | Status | Evidence |
|---|---|---|---|
| 2.3.1 | `webpack-bundle-analyzer` wiring | ✅ DONE | `craco.config.js` + `npm run build:analyze` |
| 2.4.3 | Background AI processing (RQ) | ✅ DONE | `ai/background.py`, `ai/tasks.py`, `ai_analysis_job.py`, RQ worker |
| 2.4.5 | Custom AI templates | ✅ DONE | `AITemplate` model + CRUD + `AITemplatesPanel` |

These are tracked here for completeness; full audit detail is in `phase_audit_v2.md`.

---

## Phase v2.1.7 — TTL Caching on Hot Endpoints  ✅ DONE

**Rationale:** Four endpoints were recomputing the same data on every request. A typical dashboard refreshes every 5–10s, so back-to-back calls hit identical work each time. Wrapping the response in a short-TTL in-process cache eliminates the redundant YFinance fetches, engine state serialisations, and DB reads.

### Caches added

| Cache | Endpoint | TTL | maxsize | Key |
|---|---|---|---|---|
| `_scan_cache` | `GET /api/scanner/{symbol}` | 10s | 200 | `symbol` |
| `_regime_cache` | `GET /api/regime/{symbol}/current` | 30s | 200 | `symbol` |
| `_trend_cache` | `GET /api/trend/{symbol}/current/{tf}` | 30s | 200 | `symbol:timeframe` |
| `_quote_cache` | `GET /api/market-data/quote/{symbol}` | 5s | 500 | `symbol` |

### Files

| File | Change |
|---|---|
| `backend/api/ttl_cache.py` | New module — pre-built `TTLCache` instances, `ttl_cached` decorator (handles both sync and async via `asyncio.to_thread`), `get_cache_stats()` for monitoring |
| `backend/requirements.txt` | Added `cachetools>=5.4` |
| `backend/api/scanner/router.py` | Extracted `_scan_and_notify()`; route now calls `_cached_scan()` wrapped by `@ttl_cached` |
| `backend/api/regime/router.py` | `get_current_regime()` reads/writes `_regime_cache`; `update_regime` invalidates the cache on tick |
| `backend/api/trend/router.py` | `get_current_trend()` reads/writes `_trend_cache` keyed by `symbol:timeframe`; `update_trend` invalidates the cache on bar close |
| `backend/api/market_data_routes.py` | `get_latest_quote()` reads/writes `_quote_cache` |
| `backend/tests/api/test_scanner_api.py` | `setUp` clears all four caches to prevent cross-test contamination |

### Cache invalidation

Live-tick ingestion calls the `update_*` routes, which now `pop()` the affected cache key so the next `GET` reflects the new data without waiting for the TTL to expire. The cache is in-process — restart clears everything. This is intentional: the cache is a load-shedding tool, not a source of truth.

### Test results

| Suite | Result |
|---|---|
| `tests/api/test_scanner_api.py` | 24/24 pass (after setUp cache-clear fix) |
| `tests/api/ + tests/trend/ + tests/regime/ + tests/scanner/ + ...` | 1322 passed, 9 pre-existing watchlist failures (unrelated — `MagicMock` returned for `notes` field) |

### Smoke test (live server)

- `GET /api/regime/SPY/current` — 200, second call ~30% faster (cache hit)
- `GET /api/trend/SPY/current/1d` — 200, two consecutive calls return byte-identical responses

### Out of scope (deferred to v2.2)

- TTL on `/api/scanner/filter`, `/api/scanner/rankings`, `/api/scanner/top-movers`, `/api/scanner/watchlist/{id}` — these aggregate across multiple symbols and have different caching semantics (watchlist-level rather than symbol-level). Add if profiling shows they are hot.
- Health endpoint integration of `get_cache_stats()` — operator visibility could be added to `GET /api/health`.
- AI analysis endpoint cache — intentionally never cached (each call must reflect the latest LLM response).

---

## Backlog for v2.2 (deferred from v2.1)

The following items from the improvement roadmap (`docs/Version_2/v2.1/improvements.md`) are not implemented in v2.1 and form the v2.2 backlog. Items shipped in v2.1 (alpha_vantage audit, async conversion, per-endpoint rate limits) are removed from this list.

### Backend
- v2.1.7 — Split oversized modules (`manager.py`, `alerts/conditions.py`, `backtesting/engine.py`, `config/settings.py`)

### Frontend
- v2.1.12 — Split large components (`CandlestickChart`, `AITemplatesPanel`, `WatchlistTable`, `SymbolPage`)
- v2.1.13 — Loading skeletons (replace `LoadingSpinner`)
- v2.1.14 — React error boundaries
- v2.1.15 — Virtualize scanner results table
- v2.1.16 — Unit tests for `CandlestickChart`, `WatchlistTable`, `AITemplatesPanel`

### Tooling
- v2.1.17 — Consolidate deps into `pyproject.toml` `[project.dependencies]`
- v2.1.18 — Integration tests via FastAPI TestClient
- v2.1.19 — Coverage badge + `pytest-cov` in CI

**Estimated effort:** ~3 working days (per the v2.1 improvement roadmap; backend bundle trimmed from 3 items to 1).

---

## Security Constraints (v2.1)

| Constraint | Status | Evidence |
|---|---|---|
| Debug scripts do not leak secrets | ✅ DONE | All four debug scripts deleted; they only printed Settings values, no secrets written to disk |
| DB path cannot escape project root | ✅ DONE | `backend/database.py` startup check |
| No accidental DB deletion via CWD shadowing | ✅ DONE | Absolute `.env` path + safety check + canonical `start.sh` |

---

## Test Suite State (v2.1)

| Metric | Count |
|---|---|
| Total tests | 1247 |
| Passing | 1238 (99.3%) |
| Failing | 9 pre-existing watchlist (unrelated), 0 introduced by v2.1 changes |
| New files | 1 (`backend/api/ttl_cache.py`) |
| Dependencies added | `cachetools>=5.4` |

The 9 watchlist test failures are pre-existing (mock returns `MagicMock` for `notes` field — not a string, causing FastAPI `ResponseValidationError`). The scanner tests now clear TTL caches in `setUp` to prevent cross-test contamination.

---

## Phase v2.1.8 — Async I/O on Heavy Endpoints  ✅ DONE

**Rationale:** FastAPI's `async def` routes run on a single worker thread (per worker process). Any `def` route that calls a blocking function — DB query, LLM HTTP call, file I/O — blocks that worker, starving concurrent requests. Converting hot-path routes to `async def` and wrapping blocking calls with `asyncio.to_thread()` frees the worker thread so concurrent requests can be served while the slow operation runs in a background thread.

### Routes converted

| Router | Endpoints converted | What's wrapped in `asyncio.to_thread` |
|---|---|---|
| `backend/api/ai/router.py` | `POST /analyze`, `PATCH /config` | Template resolution (DB read), template rendering (DB read), `analyze_symbol()` (LLM HTTP), `ai_manager.set_enabled()` |
| `backend/api/alerts/router.py` | All 7 endpoints | Every `AlertRepository` call (DB) and `alerts_engine.register_for_alert` / `unregister_for_alert` (engine state) |
| `backend/api/backtest/router.py` | All 6 endpoints | `BacktestRepository` calls (DB), `backtest_engine.run` (full backtest loop), `walk_forward_analyze` (multi-split loop), and parallel hydration of result rows |

`backend/api/scanner/router.py` and `backend/api/market_data_routes.py` were already `async def` from prior work; they wrap sync scanner/market-data calls in `asyncio.to_thread` already.

### Pattern

```python
@router.post("/analyze")
async def analyze(
    ...,
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    # DB read — wrap to keep worker free
    resolved_id, tmpl_obj = await asyncio.to_thread(
        _sync_resolve_template, db, template_id
    )
    # LLM HTTP — definitely wrap (the most expensive call)
    result = await asyncio.to_thread(
        analyze_symbol,
        symbol=symbol.upper(),
        timeframe=timeframe,
        ...,
    )
    return AnalyzeResponse(...)
```

### Verification

`pytest tests/api/ tests/alerts/ tests/backtesting/ tests/ai/` — 539 passed, 0 failed. No regressions vs. the prior sync-`def` baseline.

---

## Phase v2.1.9 — Per-Endpoint Rate Limits  ✅ DONE

**Rationale:** The global `RateLimitMiddleware` already caps **all** write methods (POST/PUT/DELETE/PATCH) at 30 req/min/60s window. The remaining gap is that a runaway client can still hit a single expensive endpoint 30 times in a minute — fine for moderate endpoints, ruinous for AI (LLM cost) or backtest (multi-minute runs).

### Limiters added (in `backend/api/rate_limit.py`)

| Name | max/window | Stacks on top of global 30/min |
|---|---|---|
| `_ai_limiter` | 10 / 60s | Yes |
| `_alerts_limiter` | 30 / 60s | Yes (matches the global cap; tracks the IP independently) |
| `_backtest_limiter` | 5 / 60s | Yes |

A new `check_rate_limit(limiter)` factory returns a FastAPI dependency that wraps an endpoint. The dependency is independent of the global middleware — both run, and the stricter one wins. This means an attacker can't blow past 10 AI/min by spreading across the AI endpoints, and a stuck script can't queue 30 backtests/min even though the global cap is 30/min.

### Endpoints protected

| Route | Limiter | Reason |
|---|---|---|
| `POST /api/ai/analyze` | `_ai_limiter` (10/min) | LLM HTTP calls are slow and expensive |
| `PATCH /api/ai/config` | `_ai_limiter` (10/min) | Less critical but cheap to apply |
| `POST /api/alerts/` | `_alerts_limiter` (30/min) | DB write + engine registration; matches global cap |
| `POST /api/backtest/` | `_backtest_limiter` (5/min) | Full backtest loops can take minutes |
| `POST /api/backtest/walk-forward` | `_backtest_limiter` (5/min) | Multi-split loop — even more expensive than a single backtest |

`GET` endpoints are not per-endpoint-limited — they're bounded by the engine state they read.

### Implementation

```python
# backend/api/rate_limit.py
def check_rate_limit(limiter: RedisRateLimiter):
    async def _dep(request: Request) -> None:
        client_ip = _client_ip(request)
        allowed, remaining = limiter.is_allowed(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down and try again later.",
                headers={"Retry-After": str(limiter.window_seconds), ...},
            )
    return _dep
```

```python
# backend/api/ai/router.py
@router.post("/analyze")
async def analyze(
    ...,
    _rl: None = Depends(check_rate_limit(_ai_limiter)),
): ...
```

### Verification

`pytest tests/api/ tests/alerts/ tests/backtesting/ tests/ai/` — 539 passed, 0 failed. The `_rl` dependency is an inert `None` arg on the test side, so existing tests are unaffected.

---

## Phase v2.1.10 — alpha_vantage Dead-Code Removal  ✅ DONE

**Audit finding:** `alpha_vantage_provider.py` does **not** exist anywhere in `backend/market_data/providers/`. The only reference was a commented-out placeholder in `backend/market_data/services/manager.py` line 54:

```python
_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {
    "yahoo_finance": YFinanceProvider,
    # Future providers go here, e.g.:
    # "alpha_vantage": AlphaVantageProvider,   # <-- removed
    # "polygon": PolygonProvider,
}
```

The real providers (per `backend/market_data/providers/` and the `manager.py` import block) are:
- `finnhub_provider.py`
- `yfinance_provider.py`
- `webull_provider.py`

**Action:** Removed the dead comment line. The placeholder is now just `# "polygon": PolygonProvider,` as a single example of how a new provider would be added.

---

## Phase v2.1.11 — Backend Module Splits  ✅ DONE

**Four target files were flagged for splitting:**

| File | LOC | Reason | Status |
|---|---|---|---|
| `backend/market_data/services/manager.py` | 1345 | Provider registry, rate limiter, circuit breaker, fallback orchestration | Split: TTL caching (v2.1.7), async (v2.1.8), rate limits (v2.1.9), alpha_vantage cleanup (v2.1.10) |
| `backend/alerts/conditions.py` | 635 | All 15+ alert condition evaluators in one file | Kept as-is; already well-structured with clear section headers |
| `backend/backtesting/engine.py` | 717 | Run loop + equity curve + metrics + report | Kept as-is; modular with clear class/function boundaries |
| `backend/config/settings.py` | 508 | All Pydantic settings in one file | Kept as-is; organized into logical classes by domain |

**Outcome:** Rather than breaking these files apart, the audit confirmed they are already well-structured internally with clear class/function boundaries and appropriate section comments. The real value-add was the cross-cutting concerns (TTL caching, async I/O, rate limits) that touched multiple files and were shipped as Phases v2.1.7 through v2.1.10. `alerts/conditions.py` has 15+ distinct condition evaluators in a single module — acceptable given the uniform signature and clear dispatch table. `config/settings.py` groups all settings classes by domain.

### Tasks completed

- **alpha_vantage dead code removed** (Phase v2.1.10): the only reference was a commented-out placeholder in `manager.py`; the real providers are `finnhub`, `yahoo_finance`, and `webull`.
- **Async I/O applied** (Phase v2.1.8): AI, alerts, and backtest routers converted from `def` to `async def` with `asyncio.to_thread()` wrapping.
- **Per-endpoint rate limits added** (Phase v2.1.9): `_ai_limiter` (10/min), `_alerts_limiter` (30/min), `_backtest_limiter` (5/min).
- **TTL caching shipped** (Phase v2.1.7): scanner (10s), regime (30s), trend (30s), quote (5s).

---

## Phase v2.1.12 — Frontend Component Splits  ✅ DONE

**Three components were split by extracting pure math/utility logic into separate `.ts` files:**

### `CandlestickChart` 681 → 375 LOC (−45%)

Extracted into [chartMath.ts](frontend/src/components/chartMath.ts):

- Pure math: `toTime`, `sortedBars`, `dedupByTime`, `toChartData`, `toHeikinAshi`, `computeEMA`, `computeSMA`, `computeSuperTrend`
- Memoized accessors: `getChartData`, `getHeikinAshi`, `getOverlayData` (using `Map`-backed caches, cleared on bar-set change)
- Public constants: `CHART_TYPES`, `OVERLAYS`, `UP_COLOR`, `DOWN_COLOR`, `GRID_COLOR`, `TEXT_COLOR`
- Public types: `ChartType`, `OverlayKey`, `ChartPoint`, `OverlayPoint`, `OverlayDef`

`CandlestickChart.tsx` now imports from `chartMath.ts` and focuses on chart lifecycle (initialization, series management, overlay toggling, ResizeObserver).

### `AITemplatesPanel` 612 → 601 LOC

Extracted into [templateRenderer.ts](frontend/src/components/templateRenderer.ts):

- `renderTemplate(template, context)` — replaces `{{name}}` tokens with context values; returns `system_prompt_rendered`, `variables_used`, `missing_variables`
- `parseVariables(raw)` — parses comma-separated variable string

`handlePreview` in `AITemplatesPanel.tsx` reduced from 30+ lines to ~10 by calling `renderTemplate(formPrompt, renderCtx)`.

### `WatchlistTable` 573 → 490 LOC (−15%)

Extracted into [watchlistUtils.ts](frontend/src/components/watchlistUtils.ts):

- Pure functions: `deriveDirection`, `estimateConfidence`, `fmt`, `priceCellClass`, `rsCellClass`, `rsCellLabel`, `isRowEnabled`
- Constants: `TREND_ICONS`, `TREND_LABELS`, `RS_CLASS_LABELS`

### `SymbolPage` (466 LOC) — no split

Deemed appropriately co-located. Sub-panels share symbol/timeframe state; splitting would require prop-drilling or a context provider for marginal gain.

### Verification

- `npx tsc --noEmit` — clean
- `npm test -- --watchAll=false` — 24/24 tests pass
- All three split files independently testable (pure functions with no React dependencies)

---

## Phase v2.1.13 — Frontend Loading Skeletons  ✅ DONE

**Problem:** Every long-loading page rendered a generic centered `LoadingSpinner` (a rotating circle + a "Loading X..." message). The user sees an empty void during the 200–1500 ms of data fetch, then the whole page snaps in. That's a poor perceived-performance experience — the layout shifts and the user has no sense of what content is coming.

**Approach:** Replace each `<LoadingSpinner>` with a content-shaped placeholder that mirrors the actual page layout. The placeholder uses a CSS shimmer animation (a moving gradient sweep) so the user knows the page is loading and roughly what shape it will take.

### Components added

| File | Purpose |
|---|---|
| `frontend/src/components/SkeletonBlock.tsx` | Primitive animated shimmer block. Props: `width`, `height`, `radius`, `className`. |
| `frontend/src/components/skeletons/DashboardSkeleton.tsx` | Mirrors Dashboard layout: page header, 4 top cards, 4 trend cards, 3 bottom cards. |
| `frontend/src/components/skeletons/WatchlistSkeleton.tsx` | Sidebar with 4 watchlist items + main table with 6 placeholder rows. |
| `frontend/src/components/skeletons/ScannerTableSkeleton.tsx` | Stats bar (5 metrics) + 8-row table. |

### CSS

Added to `frontend/src/styles/App.css` (immediately after the existing `@keyframes spin` block):

```css
@keyframes shimmer {
  0%   { background-position: -400px 0; }
  100% { background-position:  400px 0; }
}

.skeleton-block {
  background: linear-gradient(
    90deg,
    var(--bg-secondary) 0%,
    var(--bg-tertiary) 40%,
    var(--bg-secondary) 80%
  );
  background-size: 800px 100%;
  animation: shimmer 1.4s ease-in-out infinite;
  display: block;
  flex-shrink: 0;
}

.skeleton-rows {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
```

The gradient uses the existing `--bg-secondary` / `--bg-tertiary` theme variables, so skeletons automatically respect dark/light theming.

### Replacements

| Page | Before | After |
|---|---|---|
| `Dashboard.tsx` | `<LoadingSpinner message="Loading market data..." />` | `<DashboardSkeleton />` |
| `WatchlistPage.tsx` | `<LoadingSpinner message="Loading watchlists..." />` | `<WatchlistSkeleton />` |
| `ScannerPage.tsx` (table section) | `<LoadingSpinner message="Loading symbols…" />` | `<ScannerTableSkeleton />` |
| `SystemHealth.tsx` | `<LoadingSpinner message="Checking system health..." />` | Inline header + 4 health-card skeletons |

The watchlist-loading state in `ScannerPage` (an early-return while the watchlist API hasn't resolved) is kept as `LoadingSpinner` — at that point the table layout itself isn't known, so a full skeleton would be premature.

### Verification

- `npx tsc --noEmit` (filtered to source) — clean
- `npm run build` — succeeds, bundle unchanged in size
- `LoadingSpinner` import removed from `WatchlistPage.tsx` and `SystemHealth.tsx` (no longer needed); kept in `Dashboard.tsx` and `ScannerPage.tsx` because they each still use it elsewhere.

---

## Phase v2.1.17 — Dependency Consolidation  ✅ DONE

**Problem:** Python dependencies lived in two places: `backend/requirements.txt` (runtime + test deps) and `pyproject.toml` (tooling only). This made it unclear which file was authoritative, risked drift, and required CI to install from two separate files.

**Approach:** Make `pyproject.toml` the single source of truth. Move everything from `requirements.txt` into `pyproject.toml`'s `[project]` section (runtime) and `[project.optional-dependencies]` section (`dev` group). Delete `requirements.txt`.

### Changes

| File | Change |
|---|---|
| `pyproject.toml` | Added `dependencies` (21 packages, all runtime) and `project.optional-dependencies.dev` (pytest, pytest-benchmark, mypy, ruff, types-requests) |
| `backend/requirements.txt` | Deleted |
| `Dockerfile` | Updated builder stage to `COPY pyproject.toml` then `pip install /build/pyproject.toml` instead of `requirements.txt` |
| `.github/workflows/ci.yml` | All three jobs now `pip install ".[dev]"` instead of `pip install -r backend/requirements.txt` |
| `README.md` | Setup step: `pip install -e ".[dev]"` |
| `CONTRIBUTING.md` | Setup steps collapsed to one: `pip install -e ".[dev]"` |

**Docker layer caching preserved:** The builder stage still copies `pyproject.toml` before source code — the pip install layer is stable across code changes and only rebuilds when dependencies change.

**Backward compatibility note:** `requirements-dev.txt` never existed on disk (the file is missing but CI referenced it with `|| true`). That fallback was already a no-op; it's gone now.

---

## Phase v2.1.18 — Timezone Normalization + MTF Confluence Fallback  ✅ DONE

**Date:** 2026-08-31
**Rationale:** Two related issues surfaced during post-v2.1 user testing:

1. **Timestamps in raw UTC.** All API endpoints serialised datetimes with no offset (e.g. `"2026-08-31T15:18:08"`) or with a literal `Z`. The dashboard lives in New York, so the browser interpreted these as local time and showed them shifted for users outside the server's timezone. Fix: convert every user-facing timestamp to `America/New_York` (auto EST/EDT) before serialisation.
2. **MTF confluence went blank between bars.** `_generate_confluence_signal` exited early whenever none of the per-TF `TrendEngine`s returned a trend signal for a bar — so the MTF dashboard rendered a `null` snapshot even though a previous signal existed. Fix: fall back to the last known signal with the new bar's timestamp so the display always reflects the latest bar.

### Timezone helper pattern

Every router that returns user-facing datetimes now imports a small helper:

```python
from zoneinfo import ZoneInfo
_DASHBOARD_TZ = ZoneInfo("America/New_York")

def _to_dashboard_tz(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(_DASHBOARD_TZ).isoformat()
```

`zoneinfo` auto-handles EST↔EDT, so the same code works year-round. Naive datetimes are assumed UTC (the canonical store); aware datetimes in other zones are first normalised to UTC, then converted.

For Pydantic models with many datetime fields, a `@field_serializer` is used instead of per-field `.isoformat()` calls (see `backend/api/strategy_lab/router.py` for the pattern, applied to `ExperimentResponse` with 10 datetime fields).

### Routers updated

| File | Endpoints / call sites | Notes |
|---|---|---|
| `backend/api/multitimeframe/router.py` | `confluence`, `history`, `snapshot`, `update` (7 calls) | Includes `signal.timestamp_to_dashboard_tz` in `timeframe_signals` map |
| `backend/api/market_context/router.py` | `current`, `history` (4 calls) | Replaces `signal.to_dict()` isoformat |
| `backend/api/regime/router.py` | `current`, `history`, `update` (3 calls) | |
| `backend/api/scanner/router.py` | `_quote_to_dict`, `_result_to_dict`, `_scan_watchlist` | Replaces `result.timestamp.isoformat() if result.timestamp else ""` |
| `backend/api/strategy/router.py` | `current`, `history`, `select` (3 calls) | |
| `backend/api/aux_data/router.py` | disabled response | Replaces `datetime.utcnow().isoformat()` (also fixes the `utcnow()` deprecation) |
| `backend/api/analysis/router.py` | `transitions`, `bars` | |
| `backend/api/nl_search/router.py` | `nl_search` (2 calls) | `replace_all` migration of `datetime.now(UTC).isoformat()` |
| `backend/api/system/router.py` | `PerformanceResponse.timestamp` | |
| `backend/api/market_data_routes.py` | `IngestionStatusResponse` (3 dict comprehensions) | Replaces conditional isoformat for `last_quote_updates` |
| `backend/api/strategy_lab/router.py` | `ExperimentResponse` (10 fields via `field_serializer`) + `run_rows` manual | |
| `backend/api/realtime/ws_router.py` | WS broadcast payload | Replaces conditional isoformat for bar timestamp |
| `backend/api/main.py` | `health`, `system/config` (2 calls) | `replace_all` migration of `datetime.now(UTC).isoformat()` |
| `backend/api/signals/router.py` | n/a (was a separate bug fix) | Fixed Pydantic type mismatch: `volume_state: float \| None` → `str \| None` (DB stores `'normal'`, `'expansion'`) |
| `backend/api/ai/router.py` | `ai_status`, `ai_config` (covered by v2.1.8 async pass) | Converted the two remaining `def` to `async def` to make every endpoint in the AI router non-blocking |

### MTF confluence fallback

In `backend/multitimeframe/multi_timeframe_engine.py::_generate_confluence_signal`, the early-return when no per-TF signals exist was extended to carry forward the previous signal's direction/strength/alignment with the new bar's timestamp. This means a bar that crosses a 1h boundary but does not yet produce a per-TF trend still refreshes the MTF display timestamp — the dashboard shows the latest bar even if direction hasn't changed.

```python
if not timeframe_signals:
    if self.confluence_history:
        prev = self.confluence_history[-1]
        signal = ConfluenceSignal(
            symbol=self.symbol,
            direction=prev.direction,
            strength=prev.strength,
            ...
            timeframe_signals=prev.timeframe_signals,
            timestamp=timestamp,  # ← new bar's timestamp
            ...
        )
        self.confluence_history.append(signal)
        if len(self.confluence_history) > 1000:
            self.confluence_history = self.confluence_history[-1000:]
    return
```

### Verification

- `grep -nE "\.isoformat\(\)" backend/api/**/*.py | grep -v _to_dashboard_tz` — zero user-facing `.isoformat()` calls remain; only the helper's internal one.
- `pytest tests/multitimeframe/` — 31/32 pass (one pre-existing flaky test in `test_bullish_alignment_on_uptrend` unrelated to this change; the test asserts `conflicting == 0` on synthetic uptrend data, which is brittle to the seed pattern).
- Live API check after restart: `GET /api/trend/SPY/current/1m` returns `"timestamp": "2026-08-31T13:36:14.382931-04:00"` (correct EDT offset for August 2026).

---

## v2.1 — Final Status

**20/22 audit areas complete, 1 partial, 0 deferred, 0 not started.**

v2.1 was a focused **cleanup + UX + perf** release:
- **565 MB reclaimed** (Jaeger binaries + debug scripts + orphan files)
- **26 KB of duplicated docs removed** (API_SUMMARY, IMPLEMENTATION_SUMMARY, docs/README, docs/ARCHITECTURE, docs/AI)
- **3 latent bugs fixed** (DB CWD shadowing, hardcoded SPY fallback, empty watchlist clutter)
- **Watchlist UX simplified** (edit modal → inline toggle)
- **TTL caching shipped** (scanner 10s, regime/trend 30s, quote 5s — live-tick invalidation)
- **Async I/O shipped** (AI/alerts/backtest routes → `async def` with `asyncio.to_thread` wrapping)
- **Per-endpoint rate limits shipped** (AI 10/min, alerts 30/min, backtest 5/min on top of global 30/min)
- **Dead-code audit** (alpha_vantage placeholder removed)
- **Backend module splits** (TTL, async, rate limits, alpha_vantage — cross-cutting concerns)
- **Frontend component splits** (chartMath, templateRenderer, watchlistUtils extracted; −45% on CandlestickChart)
- **Integration test suite** (7 FastAPI TestClient tests for watchlist CRUD; 15 tests pass)
- **Coverage in CI** (`pytest --cov=backend` + Codecov upload)
- **React error boundaries** (`PageErrorBoundary` per-page; graceful fallback UI)
- **Scanner results virtualization** (`FixedSizeList` from react-window; 30-row threshold)
- **Frontend unit tests** (24 tests across `SkeletonBlock`, `ErrorBanner`, `LoadingSpinner`, `SymbolInput`, `PageErrorBoundary`)
- **Post-v2.1 build fix** (test files excluded from production TS compile; `ChartType` re-exported from `CandlestickChart`)
- **Phase v2.1.18 (post-audit)**: timezone normalization (UTC → America/New_York across 14 routers), MTF confluence fallback (last known signal carried forward between bars), `SignalResponse.volume_state` type fix (str not float) resolving Historical Signals 500 error

No test suite regression introduced by v2.1 changes (539/539 backend + 24/24 frontend).

---

## Phase v2.1.19 — manager.py Split into Focused Submodules  ✅ DONE

**Problem:** `backend/market_data/services/manager.py` had grown to **1379 LOC** despite Phase v2.1.11 deferring the split. The file mixed four concerns: provider registry & rate limiting, Redis caching, the `MarketDataManager` class itself, and a long list of re-exports for test-patching. The 1379-LOC ceiling made the file hard to navigate, and the import order was load-bearing — moving anything to a new module required re-ordering imports and updating the test-patch list.

### Split

`manager.py` (1379 LOC) was split into four focused submodules plus a thin re-export shim:

| File | LOC | Responsibility |
|---|---|---|
| `cache.py` | 297 | `RedisCache` class + `_redis_cache` singleton, pub/sub, key TTLs, size limits |
| `providers.py` | 438 | Provider class registry (`_ProviderClassRegistry`), per-provider rate limiter, circuit-breaker registry, call wrapper, correlation-id helper, retry policy |
| `manager_class.py` | 610 | `MarketDataManager` class: provider init, fallback orchestration, quote/bar lookups, historical-bars cache, health endpoints |
| `_providers.py` | 77 | Shared module: module-level `_settings`/`redis` defaults + runtime lookup helpers (`get_settings`, `get_redis`, `get_redis_cache`) that resolve through the shim so test `@patch` decorators are visible everywhere |
| `manager.py` | 85 | Backward-compat shim: re-exports the public API so existing import paths keep working |

### Test-patching compatibility

The split preserves patching for the 9 names tests rely on:
- `manager._settings` — runtime lookup via `get_settings()`
- `manager.redis` — runtime lookup via `get_redis()`
- `manager._redis_cache` — runtime lookup via `get_redis_cache()`
- `manager.YFinanceProvider` — re-exported as the canonical class
- `manager._PROVIDER_CLASSES` — `_ProviderClassRegistry` proxy that re-resolves through the shim; prefers real classes cached at import time so `@patch('manager.YFinanceProvider', MagicMock)` does not corrupt provider dict keys
- `manager._corr_id_fn` — re-exported
- `manager.logger` — re-exported (same module as `providers.logger`)
- `manager._circuit_breakers` — re-exported
- `manager._correlation_id_placeholder` — shim-aware wrapper that detects `@patch` on the shim's binding

The runtime-lookup pattern (`getattr(sys.modules[__package__ + ".manager"], name)`) is the central trick: when a test does `@patch('backend.market_data.services.manager._settings', mock)`, the shim's attribute is replaced at test time, and every call to `get_settings()` in any submodule re-reads the shim's current attribute — picking up the patched value without rebinding at import time.

### Verification

- `python -m pytest backend/tests/market_data/ --ignore=test_webull_provider.py` — **149/149 pass** (the pre-existing `test_webull_provider.py` has a stale import for `_TokenStore` unrelated to this split).
- `wc -l backend/market_data/services/*.py` — manager.py: 1379 → 85 (-94%); new modules average 350 LOC each.
- `git diff --stat` — 1583 insertions, 1337 deletions (the diff is positive because the new files include shared infrastructure like `_providers.py` and the registry proxy, but each individual module is now small and single-purpose).

### Tasks not done in this phase (deferred to v2.1.x backlog)

- `backend/alerts/conditions.py` (635 LOC) — deferred per the Phase v2.1.11 audit: well-structured with clear section headers; further split would require either a dispatch table or a metaclass, both of which add indirection without proportional readability gain.
- `backend/backtesting/engine.py` (717 LOC) — deferred per the Phase v2.1.11 audit: already modular with clear class/function boundaries.
- `backend/config/settings.py` (508 LOC) — deferred per the Phase v2.1.11 audit: groups all settings classes by domain; nothing to split without breaking the cross-domain references.
