# MarketLens Architecture

## System Overview

MarketLens is a full-stack application with a FastAPI backend and React/TypeScript frontend. The backend exposes REST endpoints for market data, technical analysis, and watchlist management. The frontend is a standalone dashboard that communicates with the API.

```
Browser (React)  ──HTTP──►  FastAPI (uvicorn)
                                │
                    ┌───────────┼───────────┬──────────────┐
                    ▼           ▼           ▼              ▼
              Regime       Trend       Multi-TF       Strategy
              Engine      Engine       Engine         Engine
                    │           │           │              │
                    └───────────┴─────┬─────┴──────────────┘
                                        ▼
                               Scanner / Indicators
                                        │
                    ┌───────────────────┴──────────────┐
                    ▼                                  ▼
             Market Data Providers              SQLAlchemy Models
             (Yahoo Finance,                      (Quotes, Bars,
              Alpha Vantage)                      Watchlists)
                                        │
                                        ▼
                                  SQLite DB
```

## Backend Modules

### `backend/indicators/`

Pure Python technical indicator implementations. Each file is self-contained with no external dependencies. Interface follows `base_indicator.py`:

```python
class BaseIndicator(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def calculate(self, bars: List[Bar]) -> Any: ...

    @abstractmethod
    def update(self, bar: Bar) -> None: ...
```

Implemented indicators: EMA, SMA, SuperTrend, RSI, MACD, ROC, ADX, ATR, Volume SMA, Relative Volume, OBV, Bollinger Bands, Swing High, Swing Low.

### `backend/engines/`

Stateful analysis engines that consume bars and produce structured signals:

- **TrendEngine** (`engines/trend.py`) — Detects bull/bear/neutral trends using EMA crossovers and price-position heuristics. Produces `TrendSignal` with `direction`, `confidence` (0–100), and `message`.
- **RegimeEngine** (`engines/regime.py`) — Classifies market into Trending, Mean-Reverting, or Volatile using ADX + ATR + Bollinger Band width heuristics.
- **MultiTimeframeEngine** (`engines/multitimeframe.py`) — Aggregates signals across multiple timeframes using a weighted scoring scheme. Higher timeframes get higher weight.
- **StrategyEngine** (`engines/strategy.py`) — Combines trend + regime signals into buy/sell/hold recommendations with per-signal confidence scores.

### `backend/market_data/`

Provider abstraction layer. `provider.py` defines the `MarketDataProvider` abstract interface:

```python
class MarketDataProvider(ABC):
    async def get_quote(self, symbol: str) -> Quote: ...
    async def get_bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]: ...
    async def get_market_status(self, symbol: str) -> MarketStatus: ...
```

Implementations:
- `yfinance_provider.py` — Yahoo Finance (default fallback, no API key required)
- `alpha_vantage_provider.py` — Alpha Vantage (requires `ALPHA_VANTAGE_API_KEY`)

Providers are wired in `services/ingestion_service.py` with a fallback chain.

### `backend/scanner/`

`ScannerEngine` evaluates a list of symbols against configurable technical criteria and returns a ranked `ScanResult` per symbol. Each criterion is a callable `Callable[[List[Bar]], bool]`. Results include per-criterion and aggregate scores.

### `backend/models/`

SQLAlchemy ORM models backed by SQLite:

- `market_data_sql.py` — `QuoteModel`, `BarModel`, `MarketStatusModel`, `ProviderStatusModel`
- `watchlist.py` — `Watchlist`, `WatchlistSymbol`

All models inherit from `backend.database.Base` (SQLAlchemy 2.0 `DeclarativeBase`).

### `backend/api/`

FastAPI routers organized by feature:

- `regime/router.py` — `GET /api/regime/{symbol}`
- `trend/router.py` — `GET /api/trend/{symbol}`
- `multitimeframe/router.py` — `GET /api/multitimeframe/{symbol}`
- `strategy/router.py` — `GET /api/strategy/{symbol}`
- `market_data_routes.py` — `POST /api/market-data/quote`, `POST /api/market-data/bars`
- `watchlist/router.py` — Full CRUD for watchlists and symbols

Shared middleware:
- **CORS** — Configurable allowlist via `CORS_ALLOWED_ORIGINS` env var (never `*` in production)
- **Rate limiting** — In-memory token bucket on write endpoints (max 30 req/IP per 60s)
- **Structured logging** — JSON logs with correlation IDs

### `backend/repositories/`

Data access layer using SQLAlchemy sessions. `watchlist_repository.py` implements `WatchlistRepository` for all watchlist CRUD operations.

## Database Schema

```sql
-- quotes: latest price per symbol
quotes(id, symbol, price, bid, ask, volume, timestamp, provider, data_status)

-- bars: OHLCV candles
bars(id, symbol, timeframe, open, high, low, close, volume, timestamp, provider, data_status)

-- market_status: exchange open/close state
market_status(id, symbol, is_open, next_open, next_close, timezone, provider, timestamp)

-- provider_status: health tracking
provider_status(id, provider_name, is_healthy, latency_ms, rate_limit_remaining, last_success, error_message, timestamp)

-- watchlists: user-defined symbol lists
watchlists(id, name, description, created_at, updated_at)

-- watchlist_symbols: symbols within a watchlist
watchlist_symbols(id, watchlist_id, symbol, enabled, sort_order, notes, added_at)
```

Indexes on `(symbol, timestamp)`, `(symbol, timeframe, timestamp)`, `(provider_name, timestamp)` for efficient time-series queries.

## Frontend

React 18 + TypeScript SPA. Standalone from the backend — communicates only via the REST API. Key screens: Dashboard (symbol overview), Scanner (screener), Watchlists (symbol lists).

State management: React Query for server state, local component state for UI.

## Security Considerations

- **No secrets in source** — All secrets via environment variables. `.env.example` documents required vars without exposing values.
- **CORS allowlist** — Frontend origin must be listed in `CORS_ALLOWED_ORIGINS`. `*` logs a startup warning.
- **Rate limiting** — Write endpoints (POST/PUT/DELETE) limited to 30 req/IP per 60s. Read endpoints are not rate-limited.
- **No API keys in frontend** — Market data and AI calls go through the backend API, which holds keys server-side.
- **X-Forwarded-For** — Rate limiting uses `request.client.host` directly; if behind a reverse proxy, ensure the proxy sets `X-Forwarded-For` and FastAPI is configured accordingly.
