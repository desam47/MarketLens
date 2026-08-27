# MarketLens

Market Intelligence and Quantitative Research Platform.

## Stack

- **Frontend**: React + TypeScript SPA (`frontend/`)
- **Backend**: FastAPI (`backend/`)
- **Database**: SQLite (`marketlens.db`)

## Quick Start

### 1. Start the API

```bash
python3 -m uvicorn backend.api.main:app --host 127.0.0.1 --port 5001 --reload
```

API docs: http://localhost:5001/docs

### 2. Start the Frontend

```bash
cd frontend
npm install
npm start
```

Open: http://localhost:3000

### 3. Initialize the Database

```bash
python3 scripts/init_db.py
```

### 4. Seed Market Data

Before the dashboard shows useful data, fetch historical bars for a symbol:

```bash
curl -X POST "http://localhost:5001/api/market-data/history/AAPL?timeframe=1d&period=1y"
```

## Pages

| Page | Description |
|------|-------------|
| **Dashboard** | Market regime, multi-timeframe trends, confluence, strategy recommendation |
| **Watchlist** | Manage symbol watchlists |
| **Alerts** | Create price and signal alerts |
| **Backtest** | Replay scanner signals over historical bars and see forward returns |
| **System Health** | API status and health |

## API Base

The frontend uses `http://localhost:5001/api` by default. Configure with the `REACT_APP_API_URL` env var when the API runs elsewhere.

## Development

```bash
# Backend tests
pytest backend/tests/ -q

# Frontend build (production)
cd frontend && npm run build
```

## Architecture

```
frontend/src/
  pages/       — Dashboard, Watchlist, Alerts, Backtest, SystemHealth
  components/  — RegimeCard, TrendCard, AlertsCard, BacktestCard, etc.
  services/    — api.ts (typed API client)

backend/
  api/         — FastAPI routers (regime, trend, multitimeframe, alerts, backtest, …)
  models/      — SQLAlchemy models
  repositories/— Data access layer
  scanner/     — Signal generation engine
  backtesting/ — Historical signal replay engine
  alerts/      — Alert engine and condition evaluators
  indicators/  — Technical indicator calculations
```
