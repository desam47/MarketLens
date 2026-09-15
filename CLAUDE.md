# CLAUDE.md — MarketLens

## Working Directory

**Always work from `/Users/dips/projects/MarketLens/`.** The backend is a Python package rooted at `backend/`. All `cd` commands and relative path references must be resolved from this directory.

```bash
# Always start here
cd /Users/dips/projects/MarketLens/
```

## Starting the Backend

```bash
cd /Users/dips/projects/MarketLens/
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001
```

The database path is absolute in `.env` (`DATABASE_URL=sqlite:////Users/dips/projects/MarketLens/marketlens.db`). The server must be started from the project root, otherwise Alembic migrations and relative path resolution may behave unexpectedly.

`./start.sh` and `scripts/run.py` start the backend + frontend + RQ background workers together — prefer those over the bare uvicorn command above unless you specifically want the API alone.

## Background Workers (RQ)

Two features depend on a running RQ worker, not just the API process: AI analysis jobs (`POST /api/ai/jobs`) and ticker backfill (adding a symbol to a watchlist). Both just enqueue a Redis job and return immediately — nothing processes that job without a worker running. `./start.sh`/`scripts/run.py` start these automatically (soft-fail if `rq`/Redis aren't available); if you start the backend directly with the bare uvicorn command above, start the workers too:

```bash
rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-workers    # AI analysis jobs
rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-backfill   # ticker backfill (one worker — run 2 only if you accept more Webull 429 pressure)
```

Requires `REDIS_ENABLED=true` in `.env` and a running Redis instance. Without a worker running, added tickers get a `BackfillJob` row stuck at `status: "queued"` forever (check via `GET /api/watchlists/symbols/{symbol}/backfill-status`) — the symbol still gets live quotes/1m bars (that's independent of backfill), it just never gets historical bars.

## Database

- **Location:** `/Users/dips/projects/MarketLens/marketlens.db`
- **Schema migrations:** `alembic/` — always run `alembic upgrade head` after creating a migration.
- **`DATABASE_URL`** in `.env` uses an absolute path. If setting it from scratch, use:
  ```
  DATABASE_URL=sqlite:////Users/dips/projects/MarketLens/marketlens.db
  ```
  Never use a relative path like `sqlite:///./marketlens.db` — the validator resolves it to the project root anyway, but the absolute form is clearer.

## File Creation Rules

**Every file must be created inside `/Users/dips/projects/MarketLens/`.** Check the working directory before creating or modifying files. If creating a script, migration, or data file, ensure the output path is inside the project tree.

Common pitfalls:
- SQLite DBs: always use the absolute path from `.env`, not a relative path.
- Log files: the `webull_trade_sdk` package hard-codes a relative log filename. This is patched in `backend/market_data/providers/webull_provider.py` to redirect to `logs/webull_trade_sdk.log` inside the project. Do not remove this patch.
- Any third-party library that writes files to the current working directory: configure its log/data path to the project root before use.

## Alembic Migrations

```bash
# Create a migration
alembic revision --autogenerate -m "description"

# Apply
alembic upgrade head

# Check current version
alembic current
```

## Key Paths

| Purpose | Path |
|---|---|
| Backend API | `backend/api/` |
| Models | `backend/models/` |
| Repositories | `backend/repositories/` |
| Frontend React | `frontend/` |
| Migrations | `alembic/versions/` |
| Logs | `logs/` (created on first webull use) |
| Database | `marketlens.db` (in project root) |
