# MarketLens

**Market Intelligence and Quantitative Research Platform** — real-time market regime detection, multi-timeframe trend analysis, confluence scoring, strategy recommendation, scanner rankings, alerts, backtesting, and optional AI-powered analysis.

> Current version: v3.2.0 · Python 3.12 · FastAPI · React 18 · SQLite

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Data Models](#data-models)
- [Configuration](#configuration)
- [Key Services](#key-services)
- [Frontend](#frontend)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## Overview

MarketLens is a full-stack application that:

1. **Ingests market data** from Webull (primary), Alpaca, Yahoo Finance, and Finnhub for any equity or ETF symbol
2. **Backfills full history** automatically when a ticker is added — 1m (15d), 1h (full), 1d (3 years)
3. **Computes technical indicators** — RSI, MACD, ATR, ADX, volume metrics, Bollinger Bands, EMA/SMA
4. **Detects market regime** — bull/bear/neutral/trend-confirmation using rule-based engines
5. **Evaluates trends** across 8 timeframes (1m → weekly) with multi-timeframe confluence scoring
6. **Generates signals** — bullish/bearish/breakout/breakdown/divergence signals
7. **Ranks symbols** — composite score combining trend strength, momentum, volatility, and volume
8. **Recommends strategies** — day trading, swing trading, breakout, mean reversion, etc.
9. **Manages watchlists** — track any number of symbols, per-symbol notes, bulk import/export
10. **Fires alerts** — price, % change, RSI threshold, and regime-change triggers
11. **Backtests strategies** — replay scanner signals over historical bars to measure forward returns
12. **Scans markets** — real-time scanner with WebSocket updates, named rankings, top movers
13. **Optional AI analysis** — plug in any Ollama-compatible LLM for natural-language market commentary

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        React SPA (localhost:3000)                    │
│   Dashboard · Symbol · Watchlist · Scanner · Alerts · Backtest       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │  HTTP / WebSocket
┌────────────────────────────────▼────────────────────────────────────┐
│                    FastAPI (localhost:5001)                          │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ API Routers  │  │ Middleware   │  │ Background Services      │   │
│  │              │  │ CORS         │  │ IngestionService (thread)│   │
│  │ regime/      │  │ Rate limit   │  │  · 1m live loop (~60s)  │   │
│  │ trend/       │  │ Cache        │  │  · 1h write loop (:02)  │   │
│  │ scanner/     │  │ Correlation  │  │  · 1h gapfill loop      │   │
│  │ alerts/      │  │ Security hdr │  │  · 1d write loop        │   │
│  │ watchlist/   │  └──────────────┘  │ BackfillService (on add)│   │
│  │ backtest/    │                    │ AlertsEngine            │   │
│  │ ai/          │                    │ TrendEngines (per-sym)  │   │
│  │ nl_search/   │                    │ ScannerEngine          │   │
│  │ signals/     │                    │ RegimeEngines          │   │
│  │ strategy/    │                    │ BacktestEngine         │   │
│  │ multitimeframe│                   └──────────────────────────┘   │
│  │ market_context│                                                   │
│  │ + more...    │  ┌──────────────┐  ┌──────────────────────────┐   │
│  └──────────────┘  │ Data Layer   │  │ Market Data Providers    │   │
│                    │ SQLAlchemy   │  │ Webull  (primary)        │   │
│                    │ Repository   │  │ Alpaca  (gap-fill, 16:00)│   │
│                    │ pattern      │  │ Yahoo Finance (curl_cffi)│   │
│                    └──────────────┘  │ Finnhub (fundamentals)   │   │
│                                      │ Redis   (cache + pub/sub) │   │
│                                      └──────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │              SQLite (marketlens.db) at project root              │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 · FastAPI 0.141 · SQLAlchemy 2.0 · Pydantic v2 |
| Frontend | React 18 · TypeScript 4 · CRA (Create React App) 5 |
| Database | SQLite (default) · PostgreSQL (via `DATABASE_URL`) |
| Caching | Redis (optional) — in-process dict fallback |
| AI | Ollama-compatible REST API (disabled by default) |
| Observability | OpenTelemetry · Jaeger · structured logging |
| Tooling | Ruff · mypy · pytest · Alembic migrations |

---

## Project Structure

```
MarketLens/
├── .env                        # Environment config (gitignored — never commit keys)
├── .env.example                # Template for .env
├── .gitignore
├── alembic/                    # DB migration scripts
│   ├── alembic.ini
│   └── versions/
├── backend/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config/
│   │   └── settings.py         # All pydantic-settings; single source of truth
│   ├── database.py             # Engine, SessionLocal, Base, DB safety checks
│   ├── alerts/                 # Alert engine + condition evaluators
│   ├── ai/                     # Prompt builder, Ollama client, background tasks
│   ├── aux_data/               # Earnings, IPO calendar, sector data providers
│   ├── backtesting/            # Walk-forward + event-driven backtest engine
│   ├── config/
│   ├── divergence/             # Bullish/bearish divergence detector
│   ├── engines/                # Timeframe, volatility, volume engines
│   ├── indicators/             # Technical indicator calculations
│   ├── market_data/
│   │   ├── providers/          # Yahoo Finance, Finnhub provider adapters
│   │   └── services/
│   │       ├── ingestion_service.py   # Background thread — live quote/bar fetch
│   │       └── engine_seeder.py      # Pre-load engines from DB quotes
│   ├── models/                 # SQLAlchemy ORM models (all tables)
│   ├── multitimeframe/         # MTF confluence engine
│   ├── nl_search/              # Natural-language chart annotation search
│   ├── observability/          # Correlation IDs, structured logging, tracing
│   ├── regime/                 # MarketRegimeEngine, RelativeStrengthEngine
│   ├── repositories/           # Data access layer (watchlist, alerts, etc.)
│   ├── scanner/
│   │   ├── scanner.py          # Core scan logic — indicators + scoring
│   │   ├── filters.py          # Bullish/bearish/breakout filter registry
│   │   └── ranking.py          # RankingEngine — composite score computation
│   ├── services/
│   ├── strategy/               # Strategy selection engine
│   ├── support_resistance/     # S/R level detection
│   ├── symbols/                # Symbol validation + lookup
│   ├── trend/                  # TrendEngine per symbol + timeframe
│   ├── transitions/            # Regime transition detection
│   ├── utils/
│   ├── workers/                # RQ job workers (Phase 2.5)
│   └── api/                    # FastAPI routers (all endpoints)
│       ├── main.py             # App entry point, middleware, router includes
│       ├── dependencies.py     # get_db(), get_current_user() stubs
│       ├── alerts/
│       ├── ai/                 # /api/ai — analyze, jobs
│       ├── ai_templates/       # /api/ai/templates — custom prompt templates
│       ├── analysis/           # /api/analysis — transitions, S/R, divergences
│       ├── aux_data/
│       ├── backtest/           # /api/backtest — runs, trades
│       ├── cache.py            # In-memory response cache middleware
│       ├── custom_indicators/  # /api/custom_indicators
│       ├── drawing_tools/      # /api/drawing-tools
│       ├── finnhub/            # /api/finnhub — company fundamentals
│       ├── market_context/     # /api/market-context
│       ├── market_data_routes.py # /api/market-data — ingestion + quotes
│       ├── multitimeframe/     # /api/multitimeframe — confluence, history
│       ├── nl_search/
│       ├── rate_limit.py       # RedisRateLimiter middleware
│       ├── realtime/           # /api/realtime — WebSocket bar streaming
│       ├── regime/             # /api/regime
│       ├── scanner/            # /api/scanner — scan, rankings, top-movers
│       │   ├── router.py       # REST endpoints
│       │   └── ws_router.py    # WebSocket endpoint
│       ├── signals/            # /api/signals — history, research, backfill
│       ├── strategy/           # /api/strategy
│       ├── strategy_lab/       # /api/strategy-lab — experiments + compare
│       ├── system/             # /api/system — health, config, metrics
│       ├── trend/              # /api/trend
│       ├── watchlist/          # /api/watchlists — CRUD + symbol management
│       ├── dependencies.py
│       └── structured_logging.py
├── docs/                      # Phase audits and migration guides
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── App.tsx            # Root — page routing + global symbol state
│   │   ├── components/        # 28 React components (cards, panels, charts)
│   │   ├── hooks/             # useMarketStream, useScannerStream (WebSocket)
│   │   ├── pages/             # 8 pages (Dashboard, Symbol, Watchlist, etc.)
│   │   ├── services/
│   │   │   └── api.ts         # Typed API client (~1500 lines)
│   │   └── styles/
│   │       └── App.css         # All custom styles
│   ├── package.json
│   └── craco.config.js        # CRA webpack override (for bundle analyzer)
├── scripts/
│   ├── init_db.py             # Bootstrap tables + seed default AI template
│   └── run.py
├── tests/                     # pytest suite
├── pyproject.toml             # Ruff + mypy config
└── start.sh                   # One-command startup script
```

---

## Quick Start

### 1. Prerequisites

- Python **3.12+**
- Node.js / npm
- Redis: `brew install redis && redis-server` — technically optional (the app degrades gracefully without it: in-process rate limiting, no caching), but required for two real features: AI analysis jobs and ticker backfill (adding a symbol to a watchlist) are both queued through it via RQ, and silently never run without a worker consuming them (see step 4a below).
- (Optional) Ollama for AI: `brew install ollama && ollama serve`

### 2. Install dependencies

```bash
# Backend
pip install -e ".[dev]"

# Frontend
cd frontend && npm install && cd ..
```

### 3. Initialize the database

```bash
python3 scripts/init_db.py
```

This creates all tables in `marketlens.db` and seeds the default AI prompt template.

### 4. Start the backend

```bash
./start.sh
# OR manually (always from project root):
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001 --reload
```

`./start.sh` also starts the background workers (step 4a) automatically. If
you used the manual uvicorn command instead, start them yourself — without
this, AI analysis and ticker backfill (step 6) will queue but never run:

```bash
rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-workers   # AI analysis jobs
rq worker --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker marketlens-backfill  # ticker backfill (run twice for more throughput)
```

`--worker-class rq.worker.SimpleWorker` is required, not optional — RQ's
default worker forks a process per job, and this project's webull provider
SDK reproducibly segfaults the forked child.

Interactive docs: http://localhost:5001/docs

### 5. Start the frontend

```bash
cd frontend && npm start
```

Open: http://localhost:3000

### 6. Add symbols to a watchlist

The ingestion service reads symbols from your watchlist. Add symbols first:

```bash
# Create a watchlist
curl -X POST "http://localhost:5001/api/watchlists/" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Watchlist", "description": "Tech stocks"}'

# Add symbols
curl -X POST "http://localhost:5001/api/watchlists/1/symbols" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL"}'

curl -X POST "http://localhost:5001/api/watchlists/1/symbols" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "TSLA"}'

# Start ingestion (fetches live quotes + bars for all watchlist symbols)
curl -X POST "http://localhost:5001/api/market-data/ingestion/start"
```

Adding a symbol returns immediately — live quotes/1m bars start within
seconds, and a full historical backfill (all 10 timeframes, tiered
1m/1h/1d fetch + gap-check-and-fill + resample) runs in the background via
a worker (step 4a). Poll its progress:

```bash
curl "http://localhost:5001/api/watchlists/symbols/AAPL/backfill-status"
```

The dashboard will now load market data for AAPL and TSLA.

---

## API Reference

All endpoints are under `/api/`. Base URL: `http://localhost:5001/api`.

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/system/status` | Service name, version, active providers |
| GET | `/system/config` | Live env vars (provider, AI enabled) |
| GET | `/system/performance` | Memory + CPU metrics |
| POST | `/system/memory_profile` | Trigger memory profiling |
| GET | `/system/metrics` | Prometheus-format metrics |

### Market Data & Ingestion

| Method | Path | Description |
|--------|------|-------------|
| POST | `/market-data/ingestion/start` | Start background ingestion thread |
| POST | `/market-data/ingestion/stop` | Stop ingestion |
| POST | `/market-data/ingestion/toggle` | Toggle ingestion on/off |
| GET | `/market-data/ingestion/status` | Ingestion running state |
| POST | `/market-data/ingestion/symbols` | Set which symbols to ingest |
| POST | `/market-data/ingestion/symbols/refresh` | Reload symbols from watchlist |
| POST | `/market-data/ingestion/timeframes` | Set timeframes to ingest |
| GET | `/market-data/quote/{symbol}` | Latest quote for symbol |
| GET | `/market-data/quote/{symbol}/history` | Recent quote history |
| GET | `/market-data/bar/{symbol}/{timeframe}` | Latest bar for symbol + timeframe |
| GET | `/market-data/bars/{symbol}` | Latest bar for all timeframes |
| GET | `/market-data/status/{symbol}` | Data freshness per timeframe |
| GET | `/market-data/providers` | Provider capabilities + status |

### Watchlists

| Method | Path | Description |
|--------|------|-------------|
| GET | `/watchlists/` | List all active watchlists |
| POST | `/watchlists/` | Create a new watchlist |
| GET | `/watchlists/{id}` | Get a watchlist |
| PUT | `/watchlists/{id}` | Update a watchlist |
| DELETE | `/watchlists/{id}` | Delete a watchlist (soft) |
| GET | `/watchlists/{id}/symbols` | List symbols in a watchlist |
| POST | `/watchlists/{id}/symbols` | Add a symbol |
| DELETE | `/watchlists/{id}/symbols/{symbol}` | Remove a symbol (hard delete) |
| PUT | `/watchlists/{id}/symbols/{symbol}/enable` | Enable a symbol |
| PUT | `/watchlists/{id}/symbols/{symbol}/disable` | Disable a symbol |
| PATCH | `/watchlists/{id}/symbols/{symbol}` | Update symbol notes/enabled state |
| PUT | `/watchlists/{id}/symbols/reorder` | Reorder symbols |
| GET | `/watchlists/{id}/symbols/search?q=` | Search symbols by ticker substring |
| POST | `/watchlists/{id}/import` | Bulk import symbols (returns imported/skipped/errors) |
| GET | `/watchlists/{id}/export?format=json\|csv` | Export all symbols |

### Scanner

| Method | Path | Description |
|--------|------|-------------|
| POST | `/scanner/filter` | Scan a list of symbols with optional filters |
| GET | `/scanner/filter-types` | List available filter names |
| POST | `/scanner/rankings` | Scan symbols and return named rankings |
| GET | `/scanner/rankings/categories` | List ranking category definitions |
| GET | `/scanner/top-movers?direction=bullish\|bearish&limit=N` | Top movers from first populated watchlist |
| GET | `/scanner/{symbol}` | Full scan for a single symbol |
| GET | `/scanner/{symbol}/cached` | Last cached scan result (fast) |
| GET | `/scanner/signals/{symbol}` | Computed signal list for a symbol |
| GET | `/scanner/watchlist/{id}` | Full ranked scan for a watchlist |
| GET | `/scanner/watchlist/{id}/rankings` | Named rankings for a watchlist |
| GET | `/scanner/watchlist/{id}/top` | Top-N symbols by score from a watchlist |

### Regime

| Method | Path | Description |
|--------|------|-------------|
| GET | `/regime/{symbol}/current` | Current regime + data freshness |
| GET | `/regime/{symbol}/history` | Historical regime transitions |
| POST | `/regime/{symbol}/update` | Force recalculate regime |
| GET | `/regime/{symbol}/relative-strength` | RS vs SPY/QQQ |
| GET | `/regime/{symbol}/sector` | Sector + relative performance |

### Trend

| Method | Path | Description |
|--------|------|-------------|
| GET | `/trend/{symbol}/current/{timeframe}` | Current trend for a timeframe |
| GET | `/trend/{symbol}/history/{timeframe}` | Historical trend values |
| POST | `/trend/{symbol}/update/{timeframe}` | Force recalculate trend |

### Multi-Timeframe

| Method | Path | Description |
|--------|------|-------------|
| GET | `/multitimeframe/presets` | Named MTF preset configurations |
| GET | `/multitimeframe/{symbol}/confluence` | MTF confluence score + breakdown |
| GET | `/multitimeframe/{symbol}/history` | Historical confluence |
| GET | `/multitimeframe/{symbol}/snapshot` | Current state across all timeframes |
| GET | `/multitimeframe/{symbol}/snapshot/history` | Historical snapshots |
| POST | `/multitimeframe/{symbol}/update` | Force recalculate MTF state |

### Strategy

| Method | Path | Description |
|--------|------|-------------|
| GET | `/strategy/{symbol}/current` | Recommended strategy for symbol |
| GET | `/strategy/{symbol}/history` | Historical strategy recommendations |
| POST | `/strategy/{symbol}/select` | Manually select a strategy |

### Signals

| Method | Path | Description |
|--------|------|-------------|
| GET | `/signals/` | List historical signals (paginated) |
| GET | `/signals/{id}` | Get a specific signal |
| GET | `/signals/symbol/{symbol}/latest` | Latest signal for a symbol |
| POST | `/signals/record` | Record a new signal |
| POST | `/signals/backfill` | Backfill signals for a date range |
| DELETE | `/signals/old?before=` | Delete signals older than date |
| GET | `/signals/research/regime-performance` | Signal win rate by regime |
| GET | `/signals/research/count-by-regime` | Signal count by regime type |

### Analysis

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analysis/{symbol}/transitions` | Regime transition history |
| GET | `/analysis/{symbol}/support-resistance` | S/R levels |
| GET | `/analysis/{symbol}/divergences` | Bullish/bearish divergences |
| GET | `/analysis/{symbol}/bars` | OHLCV bars for charting |

### Market Context

| Method | Path | Description |
|--------|------|-------------|
| GET | `/market-context/current` | Broad market (SPY/QQQ/IWM/VIX) snapshot |
| GET | `/market-context/history` | Historical market context |

### Alerts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/alerts/` | List all alerts |
| POST | `/alerts/` | Create an alert |
| GET | `/alerts/{id}` | Get alert details |
| PUT | `/alerts/{id}` | Update an alert |
| DELETE | `/alerts/{id}` | Delete an alert |
| GET | `/alerts/active` | List recently triggered alert triggers |
| GET | `/alerts/{id}/triggers` | Trigger history for an alert |

### Backtest

| Method | Path | Description |
|--------|------|-------------|
| GET | `/backtest/` | List all backtest runs |
| POST | `/backtest/` | Run a backtest |
| GET | `/backtest/{id}` | Get a backtest result |
| GET | `/backtest/{id}/trades` | Get trades from a backtest |
| DELETE | `/backtest/{id}` | Delete a backtest run |
| POST | `/backtest/walk-forward` | Run walk-forward analysis |

### AI Analysis

| Method | Path | Description |
|--------|------|-------------|
| POST | `/ai/analyze` | Run AI analysis for a symbol |
| GET | `/ai/status` | Provider status (enabled/disabled) |
| GET | `/ai/config` | Current AI config |
| PATCH | `/ai/config` | Update AI config |
| POST | `/ai/jobs` | Submit background analysis job |
| GET | `/ai/jobs/{id}` | Get background job status + result |

### AI Templates

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ai/templates` | List templates (active only by default) |
| POST | `/ai/templates` | Create a template |
| GET | `/ai/templates/{id}` | Get a template |
| GET | `/ai/templates/default` | Get the default template |
| GET | `/ai/templates/{id}/preview?symbol=&timeframe=` | Preview rendered template |
| PATCH | `/ai/templates/{id}` | Update a template |
| DELETE | `/ai/templates/{id}` | Delete a template |

### Finnhub (Fundamentals)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/finnhub/health` | Finnhub provider health |
| GET | `/finnhub/company/{symbol}` | Company profile |
| GET | `/finnhub/metrics/{symbol}` | Key financial metrics |
| GET | `/finnhub/financials/{symbol}` | Income, balance, cash flow |
| GET | `/finnhub/news/{symbol}` | News for symbol |
| GET | `/finnhub/market-news` | General market news |
| GET | `/finnhub/recommendations/{symbol}` | Analyst recommendations |
| GET | `/finnhub/insider/{symbol}` | Insider transactions |
| GET | `/finnhub/peers/{symbol}` | Peer companies |

### Strategy Lab

| Method | Path | Description |
|--------|------|-------------|
| POST | `/strategy-lab/` | Run an experiment |
| GET | `/strategy-lab/` | List experiments |
| GET | `/strategy-lab/{id}` | Get experiment |
| GET | `/strategy-lab/{id}/runs` | All runs for an experiment |
| POST | `/strategy-lab/compare` | Compare multiple experiments |
| DELETE | `/strategy-lab/{id}` | Delete an experiment |

### Custom Indicators

| Method | Path | Description |
|--------|------|-------------|
| GET | `/custom-indicators/` | List custom indicators |
| POST | `/custom-indicators/` | Create a custom indicator |
| GET | `/custom-indicators/{id}` | Get indicator |
| GET | `/custom-indicators/by-slug/{slug}` | Get by slug |
| PATCH | `/custom-indicators/{id}` | Update indicator |
| DELETE | `/custom-indicators/{id}` | Delete indicator |
| POST | `/custom-indicators/compute/{id}` | Compute indicator value |

### Drawing Tools

| Method | Path | Description |
|--------|------|-------------|
| GET | `/drawing-tools/` | List drawing tools |
| POST | `/drawing-tools/` | Create a drawing |
| GET | `/drawing-tools/{id}` | Get a drawing |
| PATCH | `/drawing-tools/{id}` | Update a drawing |
| DELETE | `/drawing-tools/{id}` | Delete a drawing |
| DELETE | `/drawing-tools/` | Delete all drawings |

### Auxiliary Data

| Method | Path | Description |
|--------|------|-------------|
| GET | `/aux-data/earnings` | Upcoming earnings calendar |
| GET | `/aux-data/ipo` | Upcoming IPOs |
| GET | `/aux-data/sectors` | Sector performance data |
| GET | `/aux-data/market-movers` | Today's top market movers |

### Real-Time (WebSocket)

| Type | Path | Description |
|------|------|-------------|
| WS | `/realtime/ws?watch=` | Stream live bars for symbols |

---

## Data Models

All models live in `backend/models/`. SQLAlchemy 2.0 declarative base.

### watchlists / watchlist_symbols

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| name | String(100) | Watchlist name |
| description | Text | Optional |
| is_active | Boolean | Soft delete flag |
| created_at | DateTime | |
| updated_at | DateTime | Auto-update |

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| watchlist_id | Integer FK | → watchlists.id |
| symbol | String(10) | Uppercase ticker |
| is_enabled | Boolean | Include in scans |
| added_at | DateTime | |
| position | Integer | Display order |
| notes | Text | User annotations |

### alerts / alert_triggers

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| symbol | String(10) | Target symbol |
| alert_type | String(20) | price / pct_change / rsi / regime |
| condition | String(10) | above / below / cross_up / cross_down |
| threshold | Float | |
| is_active | Boolean | |
| created_at | DateTime | |

### backtest_runs / backtest_trades

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| name | String(100) | |
| symbol | String(10) | |
| timeframe | String(10) | e.g. "1d" |
| strategy | String(50) | |
| start_date | DateTime | |
| end_date | DateTime | |
| initial_capital | Float | |
| final_capital | Float | |
| total_return | Float | % |
| sharpe_ratio | Float | |
| max_drawdown | Float | % |
| win_rate | Float | |
| created_at | DateTime | |

### bars

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| symbol | String(10) | |
| timeframe | String(10) | |
| open | Float | |
| high | Float | |
| low | Float | |
| close | Float | |
| volume | Integer | |
| timestamp | DateTime | |
| provider | String(20) | |

### quotes

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| symbol | String(10) | |
| price | Float | |
| bid | Float | |
| ask | Float | |
| volume | Integer | |
| timestamp | DateTime | |
| provider | String(20) | |

### historical_signals

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| symbol | String(10) | |
| signal_type | String(50) | bullish / bearish / breakout / ... |
| timeframe | String(10) | |
| price | Float | Price at signal time |
| score | Float | Confidence 0-100 |
| regime | String(20) | Market regime at signal time |
| metadata | JSON | Additional context |
| created_at | DateTime | |

### ai_templates

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| name | String(100) | |
| description | Text | |
| system_prompt | Text | System prompt (max 10k chars) |
| user_instructions | Text | |
| variables_json | Text | JSON list of variables |
| is_active | Boolean | |
| is_default | Boolean | |
| is_system | Boolean | Cannot delete |
| created_at | DateTime | |
| updated_at | DateTime | |

### ai_analysis_jobs

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| job_id | String(100) | RQ job ID |
| symbol | String(10) | |
| timeframe | String(10) | |
| template_id | Integer FK | → ai_templates.id (nullable) |
| status | String(20) | queued / started / finished / failed |
| result | Text | JSON result |
| error | Text | Error message |
| created_at | DateTime | |
| completed_at | DateTime | |

### custom_indicators

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| name | String(100) | |
| slug | String(100) | URL-safe name |
| formula | Text | Indicator formula |
| description | Text | |
| is_active | Boolean | |
| created_at | DateTime | |

### drawing_tools

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| symbol | String(10) | |
| chart_type | String(50) | horizontal_line / trend_line / fib / rect / ... |
| data | JSON | Tool coordinates + styling |
| timeframe | String(10) | |
| created_at | DateTime | |

### experiments (Strategy Lab)

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| name | String(100) | |
| description | Text | |
| config | JSON | Experiment parameters |
| created_at | DateTime | |

---

## Configuration

All configuration is driven by environment variables via `pydantic-settings`. The canonical source is `.env` at the project root. See `.env` for all options.

### Key Environment Variables

```bash
# ── Market Data ─────────────────────────────────────────────────────────────
MARKET_DATA_PRIMARY_PROVIDER=webull          # webull | alpaca | yahoo_finance | finnhub
MARKET_DATA_FALLBACK_PROVIDERS=["alpaca","yahoo_finance","finnhub"]
MARKET_DATA_CACHE_TTL_SECONDS=120
MARKET_DATA_BAR_RETENTION_DAYS=1095          # ~3 trading years
MARKET_DATA_BACKFILL_ON_ADD=true             # auto-backfill when ticker added

# ── Backfill Provider Chains ─────────────────────────────────────────────────
BACKFILL_1M_PRIMARY=webull
BACKFILL_1M_GAPFILL=yahoo_finance
BACKFILL_1M_FALLBACK=alpaca
BACKFILL_1H_PRIMARY=webull
BACKFILL_1H_FALLBACK=alpaca,yahoo_finance    # alpaca fills 16:00 ET close bar
BACKFILL_1D_PRIMARY=webull
BACKFILL_1D_FALLBACK=webull

# ── Webull ───────────────────────────────────────────────────────────────────
WEBULL_ENABLED=true
WEBULL_APP_KEY=<your_key>
WEBULL_APP_SECRET=<your_secret>

# ── Alpaca ───────────────────────────────────────────────────────────────────
ALPACA_ENABLED=true
ALPACA_API_KEY=<your_key>
ALPACA_SECRET_KEY=<your_secret>
ALPACA_PAPER=true
ALPACA_DATA_TIER=iex                         # iex (free) | sip (paid)

# ── Finnhub ──────────────────────────────────────────────────────────────────
FINNHUB_ENABLED=true
FINNHUB_API_KEY=<your_key>

# ── AI (optional) ────────────────────────────────────────────────────────────
AI_ENABLED=false
AI_PROVIDER=ollama
AI_MODEL=llama3.2
AI_BASE_URL=http://localhost:11434

# ── Redis ───────────────────────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0
REDIS_ENABLED=true                           # false = in-process dict fallback

# ── Rate Limiting ───────────────────────────────────────────────────────────
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_REQUESTS_PER_WINDOW=100

# ── Watchlists ───────────────────────────────────────────────────────────────
WATCHLIST_MAX_SYMBOLS_PER_WATCHLIST=50
WATCHLIST_MAX_WATCHLISTS=10
```

### Database Path Safety

`database.py` contains a startup safety check: if `DATABASE_URL` resolves to a path **outside** the project root, the server fails immediately with a clear error. This prevents the "empty DB shadowing populated DB" class of bug.

**Always run the backend from the project root** (use `./start.sh`), or ensure `DATABASE_URL` is an absolute path in `.env`.

---

## Key Services

### IngestionService (`backend/market_data/services/ingestion_service.py`)

Runs in a background thread. Manages four continuous loops for all symbols in active watchlists:

| Loop | Fires | Fetches | Window |
|---|---|---|---|
| `_1m_loop` | ~every 60s | 1m bars from primary provider | latest only |
| `_1h_write_loop` | hourly at :02 ET | 1h bars via BACKFILL_1H chain | 5d |
| `_gapfill_1h_loop` | periodically | 1h bars (catches 16:00 close bar) | 5d |
| `_1d_write_loop` | daily | 1d bars via BACKFILL_1D chain | 30d |

After each 1m write → resamples **2m/3m/5m/15m/30m** in-process.
After each 1h write → resamples **4h** in-process.
After each 1d write → resamples **1wk** in-process.

### BackfillService (`backend/market_data/services/backfill_service.py`)

One-shot historical fetch triggered when a ticker is added to a watchlist (`MARKET_DATA_BACKFILL_ON_ADD=true`). Provider chains are configured via `BACKFILL_1M/1H/1D_PRIMARY` and `BACKFILL_1M/1H/1D_FALLBACK` env vars.

| Timeframe | Default chain | Typical result |
|---|---|---|
| 1m | webull → yahoo_finance → alpaca | ~6,000 bars (15d) |
| 1h | webull → alpaca → yahoo_finance | ~1,200 bars (webull cap) + gap-fill |
| 1d | webull → webull | ~750 bars (3 years, retention cap) |

Derived timeframes (2m/3m/5m/15m/30m/4h/1wk) are aggregated in-process after base bars are saved.

### ScannerEngine (`backend/scanner/scanner.py`)

Computes indicators, generates signals, and scores symbols. The singleton `market_scanner` holds per-symbol `ScanResult` dicts.

Key indicators: RSI(14), MACD(12,26,9), ATR(14), ADX(14), Bollinger Bands(20,2), volume SMA, price momentum, trend alignment score.

### RegimeEngine (`backend/regime/market_regime_engine.py`)

Rule-based engine classifying market state as:
- **bull** — price above 20 EMA + rising ADX
- **bear** — price below 20 EMA + falling ADX
- **neutral** — price near EMA + flat ADX
- **bull_confirmed** / **bear_confirmed** — confirmed by RSI and volume filters

### TrendEngine (`backend/trend/trend_engine.py`)

Per-symbol + per-timeframe engine. Tracks EMA crossovers, ADX strength, and trend direction. Updated on every new bar. Provides both current trend and historical trend series.

### AlertsEngine (`backend/alerts/engine.py`)

Loaded at startup. Evaluates all active alerts on every new quote. Fires `AlertTrigger` rows when conditions are met. Alert types: `price_above`, `price_below`, `pct_change`, `rsi_above`, `rsi_below`, `regime_change`.

### BacktestEngine (`backend/backtesting/engine.py`)

Event-driven backtester. Replays historical bars and scanner signals. Computes equity curve, Sharpe ratio, max drawdown, win rate, and per-trade P&L.

---

## Frontend

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Market regime, MTF trends, confluence, strategy, top movers |
| Symbol | `/symbol` | Deep-dive on a single symbol |
| Watchlist | `/watchlist` | Manage watchlists + symbol scanning |
| Scanner | `/scanner` | Live scanner with WebSocket updates |
| Alerts | `/alerts` | Create and manage alerts |
| Backtest | `/backtest` | Run and compare backtests |
| Historical Signals | `/signals` | Signal history + research |
| System Health | `/health` | Provider status, config, memory |

### Key Components

| Component | Purpose |
|-----------|---------|
| `RegimeCard` | Color-coded regime display with freshness indicator |
| `TrendCard` | Single timeframe trend with EMA visualization |
| `ConfluenceCard` | MTF alignment gauge + preset selector |
| `StrategyCard` | Recommended strategy with regime context |
| `MarketContextCard` | SPY/QQQ/IWM/VIX benchmark display |
| `TopMoversCard` | Bullish/bearish movers from watchlist |
| `WatchlistTable` | Virtualized watchlist table with inline scanning |
| `CandlestickChart` | TradingView Lightweight Charts integration |
| `MTFScoreGrid` | Per-timeframe score heatmap |
| `AIAnalysisPanel` | LLM-powered market commentary |
| `AlertsCard` | Alert management panel |

### API Client (`frontend/src/services/api.ts`)

~1500-line typed API client. All methods return typed Promises. Key methods:

```typescript
api.getRegime(symbol)                    // MarketRegimeEngine state
api.getTrends(symbol, timeframes)       // Per-TF trend data
api.getConfluence(symbol, preset)       // MTF confluence
api.getStrategy(symbol)                 // Strategy recommendation
api.getMarketContext()                  // SPY/QQQ/IWM/VIX snapshot
api.getTopMovers(direction, limit)     // Bullish/bearish movers
api.getWatchlistScan(watchlistId)      // Full watchlist ranked scan
api.analyzeSymbol(symbol, timeframe)    // AI analysis (if enabled)
api.createAlert(alert)                  // Create a price/signal alert
api.runBacktest(config)                 // Trigger a backtest
```

### Bundle Analyzer

```bash
cd frontend && npm run build:analyze
```
Opens `stats.html` showing the JavaScript bundle composition.

---

## Development

### Database Migrations

```bash
# Detect drift between models and DB
alembic check

# Generate a new migration
alembic revision --autogenerate -m "Add notes column to watchlist_symbols"

# Apply migrations
alembic upgrade head

# Roll back
alembic downgrade -1
```

### Running Tests

```bash
# All tests
pytest backend/tests/ -q

# Specific test file
pytest backend/tests/api/test_watchlist_router.py -v

# With coverage
pytest backend/tests/ --cov=backend --cov-report=term-missing
```

### Linting

```bash
ruff check backend/
ruff check backend/ --fix     # auto-fix where possible
```

### Type Checking

```bash
mypy backend/
```

### Git Hooks & Versioning

After cloning, install the pre-commit hook so `version.txt` is stamped with the
current semantic version on every commit.  The version is read by
`backend/config/settings.py` and exposed as the `version` field in
`/api/health` and `/api/system/status`, and shown on the System Health page.

```bash
ln -sf scripts/git-hooks/pre-commit .git/hooks/pre-commit
```

**Create a new release by tagging a commit:**

```bash
git tag v2.2.0          # or v1.0.0, v3.0.0, etc.
git push origin v2.2.0  # push the tag to the remote
```

**Stamped format:**

| Commit state                         | `version.txt` contents        |
| ------------------------------------ | ----------------------------- |
| HEAD is exactly on a tag (`v2.1.0`)  | `v2.1.0`                      |
| 3 commits past `v2.1.0` (hash 54448ba) | `v2.1.0+3.g54448ba`         |
| No version tag in history yet        | `v0.0.0+g.54448ba`            |

The hash suffix (`g54448ba`) is the short git commit so you always know
which commit the running server is from.

### Adding a New API Router

1. Create `backend/api/your_feature/router.py` with a `APIRouter`:
   ```python
   from fastapi import APIRouter
   router = APIRouter(prefix="/api/your-feature", tags=["your-feature"])
   ```
2. Add endpoints with `@router.get`, `@router.post`, etc.
3. Include it in `backend/api/main.py`:
   ```python
   from backend.api.your_feature.router import router as your_feature_router
   app.include_router(your_feature_router)
   ```
4. Add a corresponding frontend API method in `frontend/src/services/api.ts`.

---

## Troubleshooting

### "SPY showing on dashboard but I don't have it in my watchlist"

This happens when the backend runs from the `backend/` directory instead of the project root, causing it to open an empty `backend/marketlens.db` instead of the populated one at the project root. The ingestion service falls back to an empty list (no SPY) when no watchlist has symbols, so SPY shouldn't appear — but if you see it, check:

1. Run `lsof -i :5001` to confirm only one backend instance
2. Check `curl http://localhost:5001/api/scanner/top-movers?direction=bullish` — if it returns SPY, ingestion is reading from the wrong DB
3. Fix: always start with `./start.sh` or `python3 -m uvicorn backend.api.main:app ...` from the project root
4. Delete the stray `backend/marketlens.db` file if it exists

### "Empty watchlist shows up in sidebar"

Run `DELETE FROM watchlists WHERE is_active=0` or use the UI to delete it. The `is_active` flag is a soft delete.

### "Ingestion service not fetching data"

1. Check `curl http://localhost:5001/api/market-data/ingestion/status`
2. Ensure symbols are in a watchlist: `curl http://localhost:5001/api/watchlists/`
3. Start ingestion: `curl -X POST http://localhost:5001/api/market-data/ingestion/start`
4. Check logs for provider errors: `tail -f /tmp/backend.log`

### "AI analysis not working"

1. Verify `AI_ENABLED=true` in `.env`
2. Check Ollama is running: `curl http://localhost:11434/api/tags`
3. Pull the model: `ollama pull llama3.2`
4. Check AI status: `curl http://localhost:5001/api/ai/status`

### "Frontend can't reach backend"

1. Confirm backend is running: `curl http://localhost:5001/api/health`
2. Check `REACT_APP_API_BASE_URL` in `.env` — must be `http://localhost:5001/api`
3. Rebuild frontend if the env var changed: `cd frontend && npm run build`
