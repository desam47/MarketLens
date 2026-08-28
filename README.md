# MarketLens

**Market Intelligence and Quantitative Research Platform** — market–regime detection, multi‑timeframe trend & confluence analysis, strategy recommendation, alerts, and backtesting.

## Overview

MarketLens is a full‑stack application that ingests market data (Yahoo Finance), computes technical indicators, detects the current market regime, evaluates trends across multiple timeframes, and recommends strategies. A FastAPI backend serves a typed API consumed by a React + TypeScript SPA.

## Stack

- **Backend**: Python 3.12 · FastAPI · SQLAlchemy 2.0 · Pydantic v2
- **Frontend**: React + TypeScript (Vite) SPA in `frontend/`
- **Database**: SQLite by default (`marketlens.db`); PostgreSQL supported via env config
- **Config**: pydantic‑settings, driven by environment (`backend/config/settings.py`)
- **Migrations**: Alembic (`alembic/`, `alembic.ini`)
- **Tooling**: Ruff (lint) · mypy (type check) · pytest (tests)

> **Note**: The AI analysis layer (`AI_ENABLED`) and the optional LLM path are present but disabled by default (`AI_ENABLED=false`).

## Prerequisites

- Python **3.12+**
- Node.js / npm (for the frontend)
- (Optional) An LLM API key if you enable `AI_ENABLED=true`

## Quick Start

### 0. Configure environment

```bash
cp .env.example .env
# edit .env to override any defaults (see "Configuration" below)
```

### 1. Install dependencies

```bash
# Backend
pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

### 2. Initialize & migrate the database

```bash
python3 scripts/init_db.py
alembic upgrade head
```

The commented `# marketlens.db` lines in `.gitignore` keep local SQLite data out of version control.

### 3. Start the API

```bash
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001 --reload
```

Interactive docs: http://localhost:5001/docs · API base: `http://localhost:5001/api`

### 4. Start the frontend

```bash
cd frontend
npm start
```

Open: http://localhost:3000

### 5. Seed market data

Once the app is running, fetch historical bars for a symbol so the dashboard shows data:

```bash
curl -X POST "http://localhost:5001/api/market-data/history/AAPL?timeframe=1d&period=1y"
```

## Pages

| Page | Description |
|------|-------------|
| **Dashboard** | Market regime, multi‑timeframe trends, confluence, strategy recommendation |
| **Watchlist** | Manage symbol watchlists (CRUD + reorder) |
| **Alerts** | Create price and signal alerts |
| **Backtest** | Replay scanner signals over historical bars and see forward returns |
| **System Health** | API status and configuration |

## API

The frontend uses `http://localhost:5001/api` by default. Override with `REACT_APP_API_URL` when the API runs elsewhere.

Core route groups:

| Prefix | Purpose |
|--------|---------|
| `/api/regime/{symbol}` | Current & historical market regime, manual update |
| `/api/trend/{symbol}` | Current & historical trend per timeframe |
| `/api/multitimeframe/{symbol}` | MTF confluence & history |
| `/api/strategy/{symbol}` | Recommended strategy + history |
| `/api/market-data` | History ingestion, provider status |
| `/api/watchlists` | Watchlist CRUD & symbol management |
| `/api/health`, `/api/system/status` | Health & configuration |

> See **API_SUMMARY.md** for a full endpoint reference.

## Configuration

Application behaviour is configured through environment variables (see `.env.example`). Key groups:

- **App / server**: `DEBUG`, `HOST`, `PORT`
- **Database**: `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE`
- **CORS**: `CORS_ALLOWED_ORIGINS`
- **Rate limiting**: `RATE_LIMIT_WINDOW_SECONDS`, `RATE_LIMIT_MAX_REQUESTS_PER_WINDOW`
- **Market data**: `MARKET_DATA_PRIMARY_PROVIDER`, fallback providers, cache TTL
- **AI (optional)**: `AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`
- **Watchlists**: max symbols / watchlists, auto‑save

## Development

```bash
# Apply / roll back schema migrations
alembic upgrade head
alembic check            # detect drift between models and DB
# See docs/MIGRATIONS.md for the full workflow.

# Backend tests
pytest backend/tests/ -q

# Lint (config in pyproject.toml)
ruff check backend/

# Type check (mypy config in pyproject.toml; lenient mode)
mypy backend/

# Frontend production build
cd frontend && npm run build
```

## Architecture

```
backend/
  api/           — FastAPI routers (regime, trend, multitimeframe, alerts, backtest, scanner, strategy_lab, nl_search, …)
  models/        — SQLAlchemy models
  repositories/  — Data access layer
  services/      — Business logic
  scanner/       — Signal generation engine
  backtesting/   — Historical signal replay engine
  alerts/        — Alert engine & condition evaluators
  indicators/    — Technical indicator calculations
  engines/       — Provider engines (rule‑based, model‑based, …)
  market_data/   — Data ingestion & caching (provider abstraction)
  ai/            — Optional LLM analysis layer

frontend/
  pages/         — Dashboard, Watchlist, Alerts, Backtest, SystemHealth
  components/    — RegimeCard, TrendCard, ConfluenceCard, StrategyCard, …
  services/      — api.ts (typed API client)
```

## Docs

- **API_SUMMARY.md** — endpoint reference and design notes
- **IMPLEMENTATION_SUMMARY.md** — what was built and fixes applied
- **docs/** — phase audits and migration guides (e.g. `PHASE_AUDIT.md`, `MIGRATIONS.md`)
- **scripts/** — `init_db.py` (database bootstrap), and other tooling