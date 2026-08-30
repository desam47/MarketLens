# Phase 2.2 — Finnhub Free Tier Integration

**Plan date:** 2026-08-29
**Phase:** Finnhub Free Tier Integration (continuing Phase 2.2 — Multiple Data Source Support)
**Goal:** Add Finnhub as a third market data provider (quote/bar failover), and expose Finnhub's free-tier endpoints as new API routes: company profile, key metrics, financials, news, analyst recommendations, and insider sentiment.
**Free tier:** 30 req/sec rate limit. No API key required for basic access (rate-limited by IP).

---

## What Already Exists

- `MarketDataProvider` ABC with 7 methods: `get_quote`, `get_bar`, `get_latest_bar`, `get_historical_bars`, `get_batch_quotes`, `get_market_status`, `get_provider_status`, `get_capabilities`, `is_available`
- `YFinanceProvider` — primary provider, free, no auth, uses curl_cffi
- `WebullProvider` — secondary provider, OAuth auth, already wired in
- `MarketDataManager` — manages provider chain with circuit breakers + per-provider rate limits
- `_PROVIDER_CLASSES` registry in `manager.py` — `yahoo_finance`, `webull`
- `MarketDataSettings` — `primary_provider` + `fallback_providers` list
- No company profile, metrics, financials, news, or recommendations endpoints exist

---

## Items to Implement

### Phase 2.2.1 — FinnhubProvider (quote/bar failover)

**File:** `backend/market_data/providers/finnhub_provider.py`

Extends `BaseMarketDataProvider`. Implements the `MarketDataProvider` ABC.

**Endpoints consumed:**
| Method | Finnhub Endpoint | Returns |
|---|---|---|
| `get_quote` | `GET https://finnhub.io/api/v1/quote?symbol={sym}` | Current price, O/H/L/C, previous close, timestamp |
| `get_historical_bars` | `GET https://finnhub.io/api/v1/stock/candle?symbol={sym}&resolution={res}&from={ts}&to={ts}` | OHLCV candles (resolution: 1, 5, 15, 30, 60, D, W, M) |
| `get_batch_quotes` | Loops `get_quote` per symbol | `dict[symbol, Quote]` |
| `get_market_status` | `GET https://finnhub.io/api/v1/market-status?exchange=US` | Market open/closed status |

**Rate limit:** 30 req/sec free tier. Use `max_per_minute=1800` as the conservative ceiling.

**Error handling:**
- HTTP 429 → `RuntimeError` (circuit breaker tracks it)
- HTTP 4xx → `RuntimeError`
- HTTP 5xx → `RuntimeError`
- Empty JSON response → `ValueError`

**Resolution mapping** (Finnhub candle `resolution` → our timeframe):
```python
FINNHUB_RESOLUTION_MAP = {
    "1m": "1",   "5m": "5",  "15m": "15", "30m": "30", "60m": "60",
    "1h":  "60", "1d": "D",  "5d": "D",   "1wk": "W",  "1mo": "M",
}
```

**No auth required** — free tier uses IP-based rate limiting. No API key needed.

**Note:** The candle endpoint uses Unix timestamps for `from`/`to`. For range-based requests, compute `to = now`, `from = now - range_to_seconds(range_)`.

**Circuit breaker config:** `failure_threshold=5`, `recovery_timeout=60s` (same as Webull).

---

### Phase 2.2.2 — FinnhubSettings

**File:** `backend/config/settings.py`

Add `FinnhubSettings` class:
```python
class FinnhubSettings(BaseSettings):
    """Finnhub free-tier provider configuration (v2.2).
    Free tier: 30 req/sec rate limit, IP-based (no API key required).
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="FINNHUB_", extra="ignore")
    enabled: bool = Field(default=False)
    api_key: str = Field(default="")  # Optional — improves rate limit to 60 req/sec
    rate_limit_per_minute: int = Field(default=1800)  # Conservative: 30/sec × 60s
    request_timeout: float = Field(default=10.0)
```

Add to root `Settings`: `finnhub: FinnhubSettings = Field(default_factory=FinnhubSettings)`.

Add to `MarketDataSettings`: `finnhub_rate_limit_per_minute: int = Field(default=1800)`.

---

### Phase 2.2.3 — Provider Registry Update

**File:** `backend/market_data/services/manager.py`

Add `finnhub` to `_PROVIDER_CLASSES`:
```python
_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {
    "yahoo_finance": YFinanceProvider,
    "webull": WebullProvider,
    "finnhub": FinnhubProvider,  # v2.2 Finnhub free tier
}
```

Only instantiate Finnhub if `FINNHUB_ENABLED=true`. Unknown names are logged and skipped (already the behavior).

**Provider chain order — Finnhub as primary:**

The fallback chain is re-ordered so Finnhub is the **primary** provider for quotes/bars, with Yahoo Finance and Webull as fallbacks:
```python
primary_provider: str = Field(default="finnhub")
fallback_providers: list[str] = Field(default_factory=lambda: ["yahoo_finance", "webull"])
```

Chain after change:
```
get_quote("AAPL")
  → finnhub (CLOSED) → success → return
  → finnhub (OPEN) → skip → yahoo_finance (CLOSED) → success → return
  → yahoo_finance (OPEN/FAIL) → skip → webull (CLOSED) → success → return
  → all failed → RuntimeError
```

Rationale for Finnhub primary:
- Finnhub has a structured, consistent API that normalizes well across symbols
- Yahoo Finance sometimes returns inconsistent or missing data for delisted/unusual tickers
- If Finnhub is rate-limited or down, Yahoo Finance and Webull provide reliable fallback
- The circuit breaker per provider means Finnhub failures are isolated and don't affect Yahoo/Webull traffic

Note: If `FINNHUB_ENABLED=false`, the manager falls back to `yahoo_finance` as the next available provider.

---

### Phase 2.2.4 — New Finnhub-Specific Models

**File:** `backend/models/finnhub.py` (new file)

New Pydantic models for Finnhub-specific data:

```python
class CompanyProfile(BaseModel):
    """Company profile from Finnhub /stock/profile2"""
    country: str | None
    currency: str | None
    exchange: str | None
    ipo: str | None
    market_capitalization: float | None
    name: str | None
    ticker: str | None
    finnhubIndustry: str | None  # Finnhub's industry field
    logo: str | None
    weburl: str | None

class CompanyMetrics(BaseModel):
    """Key metrics from Finnhub /stock/metric"""
    symbol: str
    # Valuation
    pe_basic_eps: float | None  # P/E basic earnings per share
    peg: float | None  # Price/Earnings to Growth
    # Dividends
    dividend_yield_annual: float | None
    # Price
    beta: float | None
    high_52w: float | None
    low_52w: float | None
    # Per-share
    eps_basic: float | None  # EPS basic TTM
    eps_diluted: float | None
    book_value_per_share: float | None
    cash_share: float | None  # Cash per share
    debt_equity: float | None
    # Margins
    gross_margin: float | None
    net_margin: float | None
    operating_margin: float | None
    # Shares
    shares_outstanding: int | None
    # Analyst
    target_price: float | None
    recommendation: str | None  # buy/hold/sell

class CompanyFinancials(BaseModel):
    """Financial data from Finnhub /stock/financials"""
    symbol: str
    # Income statement
    total_revenue: float | None
    cost_of_revenue: float | None
    gross_profit: float | None
    operating_expense: float | None
    operating_income: float | None
    net_income: float | None
    eps: float | None
    # Balance sheet
    total_assets: float | None
    total_liabilities: float | None
    total_equity: float | None
    # Cash flow
    operating_cash_flow: float | None
    investing_cash_flow: float | None
    financing_cash_flow: float | None
    free_cash_flow: float | None

class NewsItem(BaseModel):
    """A single news article from Finnhub /company-news or /market-news"""
    id: int
    symbol: str | None  # Populated for company-news; None for market-news
    category: str | None
    datetime: datetime
    headline: str
    image: str | None
    related: str | None  # Comma-separated related tickers
    source: str | None
    summary: str | None
    url: str | None

class AnalystRecommendation(BaseModel):
    """Analyst consensus from Finnhub /stock/recommendation"""
    symbol: str
    buy: int
    hold: int
    sell: int
    strong_buy: int
    strong_sell: int
    period: str  # e.g. "2024-Q1"
    buy_pct: float | None  # Computed: buy / total
    sell_pct: float | None  # Computed: sell / total

class InsiderSentiment(BaseModel):
    """Insider sentiment from Finnhub /stock/insider-sentiment"""
    symbol: str
    name: str | None
    sector: str | None
    market_cap: float | None
    change: float | None  # Shares bought/sold (positive=buy, negative=sell)
    sentiment: float | None  # -1 to 1 (buy/sell weighted)
    year: int
    month: int
```

---

### Phase 2.2.5 — Finnhub API Service Layer

**File:** `backend/market_data/services/finnhub_service.py` (new file)

A thin wrapper around the Finnhub REST API. Not a `MarketDataProvider` — this is a service class that the API routes call directly.

```python
class FinnhubService:
    def __init__(self, api_key: str | None = None, timeout: float = 10.0):
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY", "")
        self.timeout = timeout
        self.base_url = "https://finnhub.io/api/v1"

    def _get(self, endpoint: str, params: dict) -> dict:
        """Make authenticated GET request. Rate-limit errors raise RuntimeError."""
        ...

    # ── Company data ────────────────────────────────────────────────
    def get_company_profile(self, symbol: str) -> CompanyProfile: ...
    def get_company_metrics(self, symbol: str) -> CompanyMetrics: ...
    def get_company_financials(self, symbol: str) -> CompanyFinancials: ...
    def get_analyst_recommendations(self, symbol: str) -> list[AnalystRecommendation]: ...
    def get_insider_sentiment(self, symbol: str, from_date: date, to_date: date) -> list[InsiderSentiment]: ...

    # ── News ────────────────────────────────────────────────────────
    def get_company_news(self, symbol: str, from_date: date, to_date: date) -> list[NewsItem]: ...
    def get_market_news(self, category: str = "general") -> list[NewsItem]: ...

    # ── Reference ────────────────────────────────────────────────────
    def get_peers(self, symbol: str) -> list[str]: ...
```

**Rate limit handling:** HTTP 429 → `RuntimeError("Finnhub rate limited")` so the circuit breaker tracks it.

---

### Phase 2.2.6 — Finnhub API Routes

**Files:** `backend/api/finnhub/router.py` (new), `backend/api/finnhub/__init__.py` (new)

**Module:** `backend/api/finnhub/`

New API router included in `main.py`:
```python
from backend.api.finnhub.router import router as finnhub_router
# ...
app.include_router(finnhub_router)
```

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| GET | `/api/finnhub/company/{symbol}` | Company profile |
| GET | `/api/finnhub/metrics/{symbol}` | Key metrics |
| GET | `/api/finnhub/financials/{symbol}` | Income statement, balance sheet, cash flow |
| GET | `/api/finnhub/news/{symbol}` | Company-specific news (query params: `from`, `to`) |
| GET | `/api/finnhub/market-news` | General market news (query param: `category`) |
| GET | `/api/finnhub/recommendations/{symbol}` | Analyst buy/hold/sell consensus |
| GET | `/api/finnhub/insider/{symbol}` | Insider sentiment (query params: `from`, `to`) |
| GET | `/api/finnhub/peers/{symbol}` | Industry peer symbols |

**Response caching:** All Finnhub endpoints use `Cache-Control: max-age=3600` (1 hour) — Finnhub free tier is rate-limited, and company data doesn't change minute-to-minute.

**Health check:** GET `/api/finnhub/health` — calls `get_company_profile("AAPL")` to verify connectivity.

**Error handling:**
- 404 if symbol not found in Finnhub response
- 503 if Finnhub rate-limited (HTTP 429)
- Generic 500 for upstream errors

---

### Phase 2.2.7 — Update .env.example

Add Finnhub section:
```bash
# ---------------------------------------------------------------------------
# Finnhub Provider (v2.2 — optional, free tier)
# ---------------------------------------------------------------------------
FINNHUB_ENABLED=false
# API key is optional — free tier uses IP-based rate limiting (30 req/sec).
# Providing an API key upgrades to 60 req/sec.
FINNHUB_API_KEY=
FINNHUB_RATE_LIMIT_PER_MINUTE=1800
# Per-provider rate limits (override global MARKET_DATA_RATE_LIMIT_PER_MINUTE)
MARKET_DATA_FINNHUB_RATE_LIMIT_PER_MINUTE=1800
```

---

### Phase 2.2.8 — Tests

**New test files:**
| File | Tests | Coverage |
|---|---|---|
| `test_finnhub_provider.py` | ~15 | `get_quote`, `get_historical_bars`, `get_batch_quotes`, `get_market_status`, `is_available`, error handling (429, 4xx, 5xx, empty) |
| `test_finnhub_service.py` | ~12 | `get_company_profile`, `get_company_metrics`, `get_company_financials`, `get_news`, `get_recommendations`, `get_insider_sentiment`, rate-limit error, API key in params |
| `test_finnhub_router.py` | ~10 | HTTP status codes, response shapes, caching headers, error responses |

**Mock strategy:** All tests use `responses` library or `unittest.mock.patch` — no real Finnhub API calls in tests.

**Test fixtures:**
- Mock JSON responses matching Finnhub's actual response shapes (copy from docs)
- Verify API key is passed in query params when set
- Verify HTTP 429 raises `RuntimeError`
- Verify cache headers on API routes

---

## Files to Create

| File | Purpose |
|---|---|
| `backend/market_data/providers/finnhub_provider.py` | Quote/bar/candle provider |
| `backend/models/finnhub.py` | Pydantic models for Finnhub data |
| `backend/market_data/services/finnhub_service.py` | Finnhub REST API service |
| `backend/api/finnhub/__init__.py` | Module init |
| `backend/api/finnhub/router.py` | API routes |
| `backend/tests/market_data/test_finnhub_provider.py` | Provider unit tests |
| `backend/tests/market_data/test_finnhub_service.py` | Service unit tests |
| `backend/tests/api/test_finnhub_router.py` | API endpoint tests |

## Files to Modify

| File | Change |
|---|---|
| `backend/config/settings.py` | Add `FinnhubSettings`, add to `MarketDataSettings`, add to root `Settings` |
| `backend/market_data/services/manager.py` | Add `finnhub` to `_PROVIDER_CLASSES`, update `fallback_providers` default |
| `backend/api/main.py` | Include `finnhub_router` |
| `.env.example` | Add Finnhub section |

---

## Provider Chain After Integration

```
get_quote("AAPL")
  → finnhub (CLOSED) → success → return
  → finnhub (OPEN) → skip → yahoo_finance (CLOSED) → success → return
  → yahoo_finance (OPEN/FAIL) → skip → webull (CLOSED) → success → return
  → all failed → RuntimeError
```

Finnhub is the **primary** quote/bar provider, with Yahoo Finance and Webull as fallbacks. The per-provider circuit breaker ensures a failing provider doesn't cascade — a Finnhub outage falls through cleanly to Yahoo, and only as a last resort hits Webull.

---

## Security Constraints

- `FINNHUB_API_KEY` never logged (check before logging)
- Finnhub API key never in API responses
- HTTP errors from Finnhub → generic error message to client, detail to logs
- Rate limit (HTTP 429) → `RuntimeError` so circuit breaker opens

---

## Success Criteria

1. `test_finnhub_provider.py` — 100% pass, no real HTTP calls
2. `test_finnhub_service.py` — 100% pass, mocks verified against Finnhub response shapes
3. `test_finnhub_router.py` — 100% pass, cache headers verified
4. All Finnhub API routes return correct response shapes
5. `GET /api/finnhub/company/AAPL` returns `CompanyProfile` with name, industry, logo, etc.
6. `GET /api/finnhub/metrics/AAPL` returns `CompanyMetrics` with P/E, EPS, dividend yield, beta, 52w high/low
7. `GET /api/finnhub/news/AAPL?from=2024-01-01&to=2024-12-31` returns `list[NewsItem]`
8. Finnhub disabled by default (`FINNHUB_ENABLED=false`)
9. All 1211 existing tests still pass
