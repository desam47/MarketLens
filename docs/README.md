# MarketLens

A comprehensive market intelligence and quantitative research platform for analyzing securities, detecting trading opportunities, and running systematic strategies.

## Features

- **Multi-Timeframe Analysis** — Correlate signals across 1m, 5m, 15m, 1h, 4h, 1d, 1w timeframes
- **14 Technical Indicators** — EMA, SMA, SuperTrend, RSI, MACD, ROC, ADX, ATR, Volume SMA, Relative Volume, OBV, Bollinger Bands, Swing High/Low
- **Trend Detection Engine** — Identifies bull/bear/neutral trends with confidence scoring
- **Market Regime Classification** — Trending, mean-reverting, or volatile market detection
- **Stock Scanner** — Screen symbols by technical criteria with configurable scoring rules
- **Watchlist Management** — Organize symbols into tracked lists with per-symbol notes
- **AI-Powered Insights** — Optional OpenAI integration for narrative summaries
- **Multi-Provider Support** — Yahoo Finance (default), Alpha Vantage, extensible provider interface

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 18+

### Backend

```bash
cd backend
pip install -e .
python3 run.py
```

The API server starts on `http://localhost:8000`. API docs are at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dashboard starts on `http://localhost:3000`.

### Environment Variables

Copy `.env.example` to `.env` in the `backend/` directory:

```bash
cp .env.example backend/.env
```

Key variables:

| Variable | Default | Description |
|---|---|---|
| `DEBUG` | `false` | Enable verbose logging |
| `MARKET_DATA_PRIMARY_PROVIDER` | `alpha_vantage` | Primary market data source |
| `MARKET_DATA_FALLBACK_PROVIDERS` | `yahoo_finance` | Fallback providers (comma-separated) |
| `AI_ENABLED` | `false` | Enable AI narrative generation |
| `AI_API_KEY` | — | OpenAI API key (required if `AI_ENABLED=true`) |
| `DATABASE_URL` | `sqlite:///./marketlens.db` | Database connection string |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:5001` | Allowed CORS origins |

## Project Structure

```
MarketLens/
├── backend/
│   ├── api/              # FastAPI routes (regime, trend, multitimeframe, strategy, watchlist)
│   ├── config/           # Pydantic settings (environment variables)
│   ├── database.py       # SQLAlchemy engine + session management
│   ├── engines/          # Trend, Regime, Multi-Timeframe, Strategy engines
│   ├── indicators/       # 14 technical indicator implementations
│   ├── market_data/      # Provider interface + Yahoo Finance / Alpha Vantage adapters
│   ├── models/           # SQLAlchemy ORM models (quotes, bars, watchlists)
│   ├── repositories/     # Data access layer
│   ├── scanner/          # Stock screening engine
│   └── tests/            # Unit tests + known-broken skip registry
├── frontend/             # React/TypeScript dashboard
└── docs/                 # Architecture, providers, AI, testing, troubleshooting
```

## Running Tests

```bash
# All tests (103 total, ~50 are known-broken and skipped)
python3 backend/tests/run_tests.py

# With unittest directly
python3 -m unittest discover -s backend/tests -t .
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/system/status` | System configuration status |
| `GET` | `/api/regime/{symbol}` | Market regime classification |
| `GET` | `/api/trend/{symbol}` | Trend detection results |
| `GET` | `/api/multitimeframe/{symbol}` | Multi-timeframe correlation |
| `GET` | `/api/strategy/{symbol}` | Strategy signals |
| `POST` | `/api/market-data/quote` | Get current quote |
| `POST` | `/api/market-data/bars` | Get OHLCV bars |
| `GET` | `/api/watchlists` | List watchlists |
| `POST` | `/api/watchlists` | Create watchlist |
| `GET` | `/api/watchlists/{id}` | Get watchlist details |
| `PUT` | `/api/watchlists/{id}` | Update watchlist |
| `DELETE` | `/api/watchlists/{id}` | Delete watchlist |
| `POST` | `/api/watchlists/{id}/symbols` | Add symbol to watchlist |
| `DELETE` | `/api/watchlists/{id}/symbols/{symbol}` | Remove symbol |
