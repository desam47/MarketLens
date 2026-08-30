# v2.2 Plan — Multiple Data Source Support with Reliability Patterns

**Date:** 2026-08-29
**Phase:** Multiple Data Source Support (Webull + reliability patterns)
**Goal:** Add Webull as a second market data provider, implement circuit breakers, per-provider rate limits, and enhanced provider observability.

---

## Current Architecture

The system already has:
- `MarketDataProvider` ABC with 9 methods
- `YFinanceProvider` — free, no auth, uses curl_cffi for Yahoo Finance chart API
- `_PROVIDER_CLASSES` registry with a Webull slot commented out (`manager.py:52`)
- `MarketDataSettings` with `MARKET_DATA_PRIMARY_PROVIDER` + `MARKET_DATA_FALLBACK_PROVIDERS`
- Per-provider rate limiter `_PerProviderRateLimiter` (global, not per-provider)
- Tenacity retry: 3 attempts, exponential backoff (0.5s → 1s → 2s)
- No circuit breaker
- `ProviderStatus` model: is_healthy, latency_ms, rate_limit_remaining, last_success, error_message, timestamp

---

## Items to Implement

### 1. Circuit Breaker (`backend/market_data/circuit_breaker.py`)
Per-provider circuit breaker state machine:
- **CLOSED**: normal operation, calls pass through
- **OPEN**: after N consecutive failures (configurable threshold, default 5), calls fail fast with `CircuitBreakerOpen`
- **HALF_OPEN**: after timeout (default 60s), allows 1 test call through
- Tracks consecutive failures, last_failure_time, state
- Thread-safe (lock per breaker)
- Decorator: `@circuit_breaker(provider_name, breaker)` wrapping `_call_provider`
- State transitions logged at INFO level
- `get_state()` for observability

### 2. Per-Provider Rate Limit Configuration
Extend `MarketDataSettings` to support per-provider overrides via env:
- `MARKET_DATA_YAHOO_RATE_LIMIT_PER_MINUTE` (default: 60)
- `MARKET_DATA_WEBULL_RATE_LIMIT_PER_MINUTE` (default: 120)
- `_PerProviderRateLimiter.acquire(provider_name)` already takes provider name — just wire the per-provider limit from settings
- Keep global fallback for unknown providers

### 3. WebullProvider (`backend/market_data/providers/webull_provider.py`)
- Extends `BaseMarketDataProvider`
- Auth: OAuth 2.0 device flow using `WEBULL_APP_KEY` + `WEBULL_APP_SECRET`
- Token stored in memory (refresh on expiry)
- Endpoints:
  - Quote: `/quote/{symbol}` (market data, no auth required)
  - Historical bars: `/bars/{symbol}` (requires auth for some data)
- Normalize to `Bar` / `Quote` models
- Handle Webull-specific errors (rate limit → circuit breaker, auth → log warning, invalid symbol → return empty)
- Never log APP_KEY or APP_SECRET
- `get_capabilities()`: batch quotes = False, historical = True
- Webull requires a brokerage account — provider is enabled only when `WEBULL_ENABLED=true` AND credentials are set

### 4. Webull Settings
Add to `backend/config/settings.py`:
```python
class WebullSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEBULL_", extra="ignore")
    enabled: bool = Field(default=False)
    app_key: str = Field(default="")
    app_secret: str = Field(default="")
    rate_limit_per_minute: int = Field(default=120)
    request_timeout: float = Field(default=15.0)
```

### 5. Provider Health Enhancement
Enhanced `ProviderStatus` tracking per provider:
- Track consecutive failures (for circuit breaker state)
- Track circuit breaker state per provider
- Add `circuit_breaker_state: str` to response
- Expose via `GET /api/market-data/providers` (already exists, just extend response)
- Add per-provider metrics to Prometheus (consecutive_failures, circuit_state, rate_limit_hits)

### 6. Provider Request/Response Logging
Add structured logging for provider calls:
- Log correlation ID, provider name, method, symbol, latency_ms, success/failure
- Never log response body (could contain sensitive data)
- Log at DEBUG level (not INFO — would be noisy)
- Include in provider health endpoint as `last_error` / `error_count`

### 7. Update Registry
Add `webull` to `_PROVIDER_CLASSES` in `manager.py`:
```python
_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {
    "yahoo_finance": YFinanceProvider,
    "webull": WebullProvider,
}
```
Only instantiate Webull if `WEBULL_ENABLED=true` and credentials are non-empty. Unknown names logged and skipped (already the case).

### 8. Update .env.example
Add Webull section:
```
# ---------------------------------------------------------------------------
# Webull Provider (v2.2 — optional)
# ---------------------------------------------------------------------------
WEBULL_ENABLED=false
WEBULL_APP_KEY=
WEBULL_APP_SECRET=
WEBULL_RATE_LIMIT_PER_MINUTE=120
# Per-provider rate limits (override global MARKET_DATA_RATE_LIMIT_PER_MINUTE)
MARKET_DATA_WEBULL_RATE_LIMIT_PER_MINUTE=120
```

### 9. Tests
- `test_circuit_breaker.py` — state machine transitions, thread safety, failure counting
- `test_webull_provider.py` — quote, bars, auth, error handling
- `test_manager_with_circuit_breaker.py` — manager wraps providers with breakers, fallback when OPEN
- `test_per_provider_rate_limit.py` — per-provider limits from settings

---

## Files to Create
- `backend/market_data/circuit_breaker.py`
- `backend/market_data/providers/webull_provider.py`
- `backend/tests/market_data/test_circuit_breaker.py`
- `backend/tests/market_data/test_webull_provider.py`

## Files to Modify
- `backend/config/settings.py` — add `WebullSettings`, extend `MarketDataSettings`
- `backend/market_data/services/manager.py` — wire circuit breakers, per-provider rate limits
- `backend/market_data/providers/yfinance_provider.py` — no changes needed (interface already complete)
- `backend/observability/prometheus.py` — add per-provider metrics
- `backend/api/market_data/router.py` — extend provider health response
- `.env.example` — add Webull section

## Security Constraints
- APP_KEY and APP_SECRET never logged (check before logging)
- Credentials never in API responses (`safe_config` pattern)
- Webull token stored in memory only (not persisted)
- Auth errors return generic message to client, full detail to logs

## Fallback Behavior
```
get_quote("AAPL")
  → yahoo_finance (CLOSED) → success → return
  → yahoo_finance (OPEN) → skip → webull (CLOSED) → success → return
  → all OPEN/FAILED → return None
```
Circuit breaker state shown in `/api/market-data/providers`.

## Success Criteria
1. `test_circuit_breaker.py` — 100% pass (state machine tested)
2. `test_webull_provider.py` — mocked HTTP, no real Webull calls in tests
3. `test_manager_with_circuit_breaker.py` — OPEN state skips provider, falls through
4. `test_per_provider_rate_limit.py` — per-provider limits from env
5. All 1135 existing tests still pass
6. Webull provider disabled by default (`WEBULL_ENABLED=false`)
