# Phase 2.1 — Improvement Roadmap

> **Date:** 2026-08-30
> **Source:** Full codebase audit covering 31104 lines of Python and 7522 lines of TypeScript.
> **Scope:** Concrete, prioritised improvements identified during a deep-dive cleanup pass.

This document captures a ranked list of improvements for MarketLens, organised by impact
and risk. Each entry includes the affected files, the rationale, and a recommended
implementation order.

---

## 1. Backend Improvements (High Impact)

### 1.1 Split oversized backend modules

Several Python modules have grown to the point where they bundle multiple unrelated
responsibilities. Splitting them improves readability, testability, and reduces merge
conflicts.

| File | Lines | Current responsibilities | Recommended split |
|---|---|---|---|
| `backend/market_data/services/manager.py` | 1345 | Provider registry, rate limiter wiring, circuit breaker, fallback orchestration | `manager.py` (orchestrator) + `circuit_breaker.py` (already exists in `market_data/`) + `provider_registry.py` |
| `backend/alerts/conditions.py` | 635 | All 5+ alert condition types (price, bar, indicator, sentiment, composite) | `conditions/base.py` + `conditions/price.py` + `conditions/bar.py` + `conditions/indicator.py` |
| `backend/backtesting/engine.py` | 717 | Run loop + equity curve + metrics + report rendering | `engine.py` (core) + `metrics.py` + `report.py` |
| `backend/config/settings.py` | 508 | All Pydantic settings in one file (DB, market data, AI, observability, alerts, webull, finnhub) | `settings/database.py` + `settings/market_data.py` + `settings/ai.py` + `settings/observability.py` + `__init__.py` (re-exports) |

**Why:**
- Easier to grep and navigate
- Smaller diffs when changing one concern
- Test files map 1:1 to source modules
- Reduces the risk of a circular import as settings grow

**Effort:** Medium (2–3 days). **Risk:** Low (pure refactor, no behavior change).

### 1.2 Add TTL caching to hot endpoints

Several endpoints recompute the same data on every request:

- `GET /api/scanner/scan` — recomputes filter pipeline for every request
- `GET /api/regime/{symbol}/current` — re-runs regime classification
- `GET /api/trend/{symbol}/current/{timeframe}` — re-runs trend engine
- `GET /api/market-data/quotes/{symbol}` — re-fetches from provider

**Fix:** Wrap these in a lightweight TTL cache (cachetools.TTLCache, in-memory) with
short TTLs:

| Endpoint | Recommended TTL |
|---|---|
| `/api/scanner/*` | 10 seconds |
| `/api/regime/{symbol}/current` | 30 seconds |
| `/api/trend/{symbol}/current/{tf}` | 30 seconds |
| `/api/market-data/quotes/{symbol}` | 5 seconds |
| `/api/ai/analyze` | 0 (never cache) |

**Why:** Most dashboards refresh on a 5–10s interval. A 5s TTL on quotes would cut
DB/provider load by ~50–80% for a typical session.

**Effort:** Low (1 day). **Risk:** Low (use a single wrapper function and apply selectively).

### 1.3 Convert heavy endpoints to `async def`

Most routes are `def` (synchronous). When the request blocks on `yfinance` or a DB
query, the entire event loop is blocked from serving other requests.

**Fix:** Convert to `async def` for endpoints that do I/O:

```python
# Before
@router.get("/quotes/{symbol}")
def get_quote(symbol: str):
    return provider.get_quote(symbol)

# After
@router.get("/quotes/{symbol}")
async def get_quote(symbol: str):
    return await asyncio.to_thread(provider.get_quote, symbol)
```

Start with the scanner endpoint, which fans out to many providers.

**Why:** Lets the server handle concurrent scans of multiple watchlists without one
slowing down another.

**Effort:** Medium (2 days). **Risk:** Low (only affects throughput, not correctness).

### 1.4 Add endpoint-level rate limits

`RedisRateLimiter` exists in `backend/api/rate_limit.py` but only protects the scanner
endpoint. Other expensive endpoints are unprotected:

- `POST /api/ai/analyze` — calls LLM ($$$)
- `POST /api/alerts/trigger` — fires DB writes
- `POST /api/backtest/run` — CPU heavy

**Fix:** Apply a rate limit decorator (10/min for AI, 30/min for alerts, 5/min for
backtest) using the existing Redis limiter.

**Effort:** Low (half day). **Risk:** Low (tunable per-endpoint).

### 1.5 Verify alpha_vantage provider is wired

`backend/market_data/providers/` has 3 providers. yahoo and webull are wired; check if
`alpha_vantage_provider.py` is actually used. If not, decide: implement and wire it,
or remove it.

**Effort:** Low (audit + decision). **Risk:** None.

---

## 2. Frontend Improvements (Medium Impact)

### 2.1 Split large React components

| Component | Lines | Recommended split |
|---|---|---|
| `frontend/src/components/CandlestickChart.tsx` | 681 | `ChartRenderer.tsx` (canvas/d3 logic) + `DrawingToolbar.tsx` (tools) + `VolumePanel.tsx` (sub-panel) |
| `frontend/src/components/AITemplatesPanel.tsx` | 612 | `TemplateList.tsx` + `TemplateForm.tsx` + `TemplatePreview.tsx` |
| `frontend/src/components/WatchlistTable.tsx` | 573 | `WatchlistTable.tsx` (orchestrator) + `WatchlistRow.tsx` (already partly exists) + `WatchlistToolbar.tsx` |
| `frontend/src/pages/SymbolPage.tsx` | 466 | Extract `SymbolHeader.tsx` (top bar) + each panel into its own file |

**Why:** Smaller components are easier to read, test, and lazy-load. The pages dir
becomes a router file; the components dir holds real, named, reusable units.

**Effort:** Medium (3 days). **Risk:** Low (incremental; behaviour identical).

### 2.2 Add loading skeletons

Replace `LoadingSpinner` with content-shaped skeletons:

```tsx
// Instead of: <LoadingSpinner />
<Skeleton rows={4} variant="card" />
<Skeleton rows={3} variant="row" />
```

The page layout reserves space → no layout shift when data arrives.

**Effort:** Low (1 day). **Risk:** None (visual only).

### 2.3 Add React error boundaries

A single `componentDidCatch` boundary around each page (e.g. `<SymbolPageBoundary>`)
prevents one broken panel from blanking the whole route.

**Effort:** Low (half day). **Risk:** None.

### 2.4 Virtualise the scanner results table

The `ScannerPage` results table currently renders all rows. For 50+ symbols in
production this becomes slow.

**Fix:** Use `react-window` (already a project dep, used in `WatchlistTable`) to
virtualise.

**Effort:** Low (half day). **Risk:** None.

### 2.5 Add unit tests for critical components

`CandlestickChart`, `WatchlistTable`, and `AITemplatesPanel` are state-heavy and have
no tests. Vitest + React Testing Library would catch regressions during the splits
above.

**Effort:** Medium (2 days). **Risk:** None.

---

## 3. Complete Phase 2 (from the prior plan)

Three items from the Phase 2 plan are still pending:

### 3.1 webpack-bundle-analyzer
- `@craco/craco` and `webpack-bundle-analyzer` not yet installed
- `craco.config.js` not yet created
- `package.json` scripts still use `react-scripts` directly

**Effort:** Low (half day). **Risk:** None.

### 3.2 Background AI processing (RQ)
- Backend model `AIAnalysisJob` exists
- Task function `analyze_symbol_async` partially scaffolded
- Frontend "Run in background" button not yet added to `AIAnalysisPanel`

**Effort:** Medium (1 day). **Risk:** Low.

### 3.3 Custom AI prompts (finish integration)
- Backend `AITemplate` model + CRUD endpoints exist
- `AITemplatesPanel.tsx` component exists
- The `analyze` endpoint accepts a `template_id` override
- **Missing:** Wire `AITemplatesPanel` into `SymbolPage` so users can pick a template
  before running analysis

**Effort:** Low (half day). **Risk:** Low.

---

## 4. Infrastructure & Tooling

### 4.1 Consolidate dependency declarations

`backend/requirements.txt` (Python deps) and `pyproject.toml` (tooling config) are
separate. Move Python deps to `pyproject.toml` `[project.dependencies]` for a
single source of truth. Then `requirements.txt` can be regenerated via
`pip-compile` or `uv pip compile`.

**Effort:** Low (1 hour). **Risk:** None.

### 4.2 Add API integration tests

98 unit tests exist, but no end-to-end test that starts the server and hits a real
endpoint. Add a `tests/integration/` suite that uses FastAPI TestClient to verify
the full request/response cycle for top endpoints.

**Effort:** Medium (2 days). **Risk:** None.

### 4.3 Add code coverage to CI

`.github/workflows/ci.yml` runs `pytest` but doesn't report coverage. Add `pytest-cov`
+ a coverage badge to the README.

**Effort:** Low (1 hour). **Risk:** None.

---

## 5. Recommended implementation order

A pragmatic 1-2 week sequence that minimises risk and maximises value:

| Day | Task | Rationale |
|---|---|---|
| 1 | Add TTL caching to hot endpoints (§1.2) | Biggest perf win, smallest diff |
| 1 | Wire webpack-bundle-analyzer (§3.1) | Quick win, surfaces bloat early |
| 1 | Wire `AITemplatesPanel` into `SymbolPage` (§3.3) | Closes a half-finished feature |
| 2 | Add endpoint rate limits (§1.4) | Protects the backend from runaway clients |
| 2 | Add loading skeletons (§2.2) | Pure UX polish |
| 2 | Add React error boundaries (§2.3) | Prevents whole-page crashes |
| 3 | Split `config/settings.py` (§1.1) | Foundational; makes future changes safer |
| 3 | Virtualise scanner results table (§2.4) | Performance for large watchlists |
| 4 | Split `market_data/services/manager.py` (§1.1) | Largest payoff for testability |
| 4 | Split `CandlestickChart.tsx` (§2.1) | Largest frontend component |
| 5 | Convert scanner endpoint to async (§1.3) | Performance, but slightly more risk |
| 5 | Complete RQ background AI (§3.2) | Closes last Phase 2 item |

**Total: ~5 working days for a single engineer.** Each step is independent and
revertable.

---

## 6. Out of scope (for this phase)

These improvements are valuable but are deferred to Phase 2.2 or later:

- Migrate SQLite to PostgreSQL (production data volume)
- Add a real auth layer (currently unauthenticated; OK for local use)
- Multi-user watchlist sharing
- Streaming market data via WebSocket (already partial in `realtime/`)
- Mobile-first responsive design for SymbolPage
- Internationalisation (i18n)
- Dark mode toggle (current styles are dark-only)

---

## 7. Summary

- **Backend**: Split 4 large modules + add caching + convert to async = 2× maintainability
- **Frontend**: Split 4 large components + add skeletons/boundaries = 30% perceived speedup
- **Phase 2**: Complete 3 half-finished items = feature completeness
- **Tooling**: Consolidate deps + add coverage = 5% setup time reduction

Total estimated effort: **~5 working days**. Risk: **low** (mostly refactors and
additive features). The application is already well-structured; these changes sharpen
it rather than rebuild it.
