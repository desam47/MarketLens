# v2.1 Plan — Repository Cleanup, UX Polish, and Latent Bug Fixes

**Date:** 2026-08-30
**Phase:** v2.1 — Post-Phase-2 quality + cleanup pass
**Goal:** Reclaim disk space, fix latent bugs discovered during user testing, and polish watchlist UX. No new features; only remove, fix, or refactor.

---

## Current State (pre-v2.1)

The codebase at the start of v2.1 had:
- 452 MB `backend/` directory, of which 445 MB was Jaeger binary downloads (unused; Jaeger runs via Docker)
- 565 MB total of untracked-but-present junk files (binaries, debug scripts, orphan DBs, old logs)
- Two duplicate top-level docs (`API_SUMMARY.md`, `IMPLEMENTATION_SUMMARY.md`) that fully overlapped the new comprehensive `README.md`
- A latent SQLite CWD bug: `DATABASE_URL=sqlite:///./marketlens.db` resolved relative to CWD, so running `uvicorn` from `backend/` silently created a phantom empty DB
- A hardcoded fallback symbol list (`["SPY", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA", "META", "NFLX"]`) that masked the CWD bug by giving the user a working but incorrect dashboard
- A watchlist table with an "edit" modal that surfaced a notes field the user didn't want; the desired UX is a per-symbol enable/disable toggle

---

## Items to Implement

### 1. Repository cleanup — remove orphan binaries, debug scripts, and old logs

Delete the following (all unused, all untracked or stray):

**Jaeger binaries (559 MB):**
- `backend/jaeger-1.53.0-darwin-amd64/` (275 MB)
- `backend/jaeger.tar.gz` (124 MB)
- `backend/jaeger-2.20.0-darwin-amd64.tar.gz` (58 MB)
- `backend/jaeger-tools-2.20.0-darwin-amd64.tar.gz` (38 MB)
- `backend/jaeger-2.20.0-darwin-amd64.sha256sum.txt`
- `backend/jaeger-2.20.0-darwin-amd64.tar.gz.asc`
- `backend/jaeger-tools-2.20.0-darwin-amd64.sha256sum.txt`
- `backend/jaeger-tools-2.20.0-darwin-amd64.tar.gz.asc`

**Debug scripts:**
- `check_settings.py` — one-off Settings debug
- `debug_settings.py` — pydantic-settings debug
- `test_env.py` — .env load test
- `test_env2.py` — .env load test variant

**Orphan DB:**
- `test_strategy_lab.db` — no references in code or alembic migrations

**Old logs (root + backend):**
- `server.log`, `server_output.log`, `server.pid` (root)
- `backend/server.log`, `backend/server_output.log`, `backend/server.pid`
- `jaeger.log` (root + backend)

**Empty dirs:**
- `.benchmarks/` (root, frontend, backend) — pytest-benchmark recreates on demand; harmless

**Total reclaim: ~565 MB.**

### 2. Documentation consolidation

Delete the following — each is fully covered by the new comprehensive `README.md`:

- `API_SUMMARY.md` — endpoint list duplicated in `README.md` § API Reference
- `IMPLEMENTATION_SUMMARY.md` — early-build journal superseded by `README.md` § Project Structure
- `docs/README.md` — docs folder index, redundant with root README
- `docs/ARCHITECTURE.md` — diagram + module overview duplicated in `README.md` § Architecture
- `docs/AI.md` — AI config + providers duplicated in `README.md` § Key Services

**Keep** (genuinely deeper than README):
- `docs/MIGRATIONS.md`, `docs/TROUBLESHOOTING.md`, `docs/PROVIDERS.md`, `docs/TESTING.md`, `docs/PERFORMANCE_REPORT.md`
- `docs/prompts/`, `docs/Version_1/`, `docs/Version_2/` (historical artifacts)

### 3. Database path safety — fix CWD-dependent SQLite path

**Problem:** `DATABASE_URL=sqlite:///./marketlens.db` is a relative URL. When `uvicorn` is started from `backend/`, the DB is created at `backend/marketlens.db` (empty) which shadows the real `marketlens.db` at the project root.

**Three-layer fix:**

**Layer 1 — `.env` absolute path:**
```diff
-DATABASE_URL=sqlite:///./marketlens.db
+# Always use an absolute path so the DB is in the project root regardless of CWD.
+DATABASE_URL=sqlite:////Users/dips/projects/MarketLens/marketlens.db
```

**Layer 2 — `backend/database.py` startup safety check:**
```python
_db_url = settings.database.url
if _db_url.startswith("sqlite:///"):
    _db_path = Path(_db_url.replace("sqlite:///", ""))
    if not _db_path.is_absolute():
        raise RuntimeError(...)
    _db_canonical = _db_path.resolve()
    _root_canonical = _PROJECT_ROOT.resolve()
    if not str(_db_canonical).startswith(str(_root_canonical)):
        raise RuntimeError(...)
```

**Layer 3 — canonical startup script (`start.sh`):**
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001 --reload &
```

### 4. Remove hardcoded SPY fallback

**File:** `backend/market_data/services/ingestion_service.py`

**Before:**
```python
_DEFAULT_SYMBOLS = ["SPY", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA", "META", "NFLX"]

def _load_symbols_from_watchlist(self) -> list[str]:
    # ...
    if not symbols:
        return _DEFAULT_SYMBOLS  # masks misconfiguration
```

**After:**
```python
def _load_symbols_from_watchlist(self) -> list[str]:
    # iterate watchlists, return symbols from first populated one
    if watchlists:
        logger.info("Active watchlists exist but all are empty — no symbols to ingest")
    else:
        logger.info("No active watchlist found — no symbols to ingest")
    return []
```

### 5. Frontend default symbol from first populated watchlist

**File:** `frontend/src/App.tsx`

**Before:** `useState<string>('SPY')` — hardcoded.

**After:** `useEffect` fetches the first watchlist with symbols and sets the default:
```tsx
useEffect(() => {
  let cancelled = false;
  api.getWatchlists().then(async (watchlists) => {
    for (const wl of watchlists) {
      if (cancelled) break;
      const symbols = await api.getWatchlistSymbols(wl.id);
      if (symbols.length > 0) {
        if (!cancelled) setSymbol(symbols[0].symbol);
        break;
      }
    }
  }).catch(() => {});
  return () => { cancelled = true; };
}, []);
```

If no watchlist is populated, the dashboard stays on the placeholder `SPY` default but the backend will correctly return no data for it, so the user sees the real state of their setup.

### 6. Delete empty watchlist "Test Watchlist 2"

Confirmed empty via API; deleted via `DELETE /api/watchlists/{id}`. No user data lost.

### 7. Watchlist edit → enable/disable toggle

**File:** `frontend/src/components/WatchlistTable.tsx`

**Refactor scope:**
- Remove: `editingSymbol`, `editNotes`, `editEnabled`, `savingEdit` state; `handleEditSymbol`, `handleSaveEdit`; the full edit modal JSX (textarea + save/cancel buttons)
- Add: `togglingSymbol: string | null` state; `handleToggleSymbol(symbol)` with optimistic update + rollback
- Visual: ⏸ button when enabled, ▶ when disabled, "…" while toggling
- Add `.row-disabled` class to dim rows where `is_enabled=false`

**API call:** `api.updateWatchlistSymbol(watchlistId, symbol, { is_enabled: nextEnabled })`

**TypeScript:** `WatchlistScanResult` interface (in `frontend/src/services/api.ts`) gains `notes?: string | null` for legacy data shape; no new notes writes.

### 8. Git history pruning

`git gc --prune=now --aggressive` — verify if any reclamation possible. Document result in audit.

**Acceptable outcome:** if no unreachable objects exist (which they don't — the 560 MB Jaeger files were never committed, only present untracked), then no further action is possible.

---

## Out of Scope (deferred to v2.2)

- Splitting oversized backend modules (`manager.py`, `alerts/conditions.py`, `backtesting/engine.py`, `config/settings.py`) — see `v2.1/improvements.md` § 1.1
- TTL caching on hot endpoints — see `v2.1/improvements.md` § 1.2
- Async I/O on heavy endpoints — see § 1.3
- Endpoint-level rate limits — see § 1.4
- Frontend component splits (CandlestickChart, AITemplatesPanel, WatchlistTable, SymbolPage) — see § 2.1
- Loading skeletons, error boundaries, scanner virtualization — see § 2.2-2.4
- Unit tests for frontend components — see § 2.5
- Dependency consolidation, integration tests, coverage in CI — see § 4

These are tracked in the v2.1 improvement roadmap (`docs/Version_2/v2.1/improvements.md`) and form the v2.2 backlog.

---

## Test Strategy

v2.1 is cleanup + refactor only. No new behavior; no new tests. Existing test suite (1247 tests, 1245 passing) is the regression baseline.

**Validation per change:**
- After each file edit: `npm run typecheck` (frontend) and `python3 -c "from backend.X import Y"` (backend)
- After all changes: full `pytest` run, confirm 1245/1247 pass (same as v2 baseline)
- After `start.sh` rewrite: `bash start.sh` + `curl http://localhost:5001/api/health`

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Removing a file that is dynamically imported | Low | High | Grep for the filename in all source before deleting; confirmed zero matches for all 4 debug scripts |
| Absolute `.env` path breaks a developer's local setup | Medium | Low | Add comment in `.env.example` explaining why path is absolute; document in README § Configuration |
| Startup safety check rejects a legitimate external DB | Low | Medium | Check only blocks paths that resolve outside the project root; user can still set any absolute path inside the project |
| Watchlist toggle optimistic update race | Low | Low | Rollback on API error; the server is the source of truth on next fetch |
| `git gc --aggressive` increases pack size (repack overhead) | Low | None | Acceptable — confirms history is healthy |

---

## Implementation Order

1. **Cleanup** (1 hour) — remove 565 MB of files, no code changes, easy wins
2. **Documentation consolidation** (15 min) — delete 5 duplicate .md files
3. **DB path safety** (1 hour) — `.env` + `database.py` + `start.sh`
4. **Remove hardcoded SPY fallback** (15 min) — `ingestion_service.py`
5. **Frontend default symbol** (30 min) — `App.tsx` useEffect
6. **Delete empty watchlist** (5 min, requires user confirmation)
7. **Watchlist toggle refactor** (2 hours) — `WatchlistTable.tsx` (largest single change)
8. **Git gc** (5 min) — verify no reclamation possible, document

**Total: ~5 hours of work, all in one session.**

---

## Final Status

v2.1 ships:
- 565 MB reclaimed from orphan files
- 5 duplicate docs removed
- 1 latent CWD bug permanently fixed (3 layers)
- 1 hardcoded fallback removed
- 1 empty watchlist deleted
- 1 UX improvement (edit modal → inline toggle)

No new features. No test suite regression. 1245/1247 tests pass (unchanged from v2).
