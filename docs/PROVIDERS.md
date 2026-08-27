# Market Data Providers

MarketLens uses a pluggable provider architecture. All providers implement the `MarketDataProvider` abstract interface and can be mixed in a fallback chain.

## Interface

```python
class MarketDataProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    async def get_quote(self, symbol: str) -> Quote: ...
    async def get_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> List[Bar]: ...
    async def get_market_status(self, symbol: str) -> MarketStatus: ...
```

## Available Providers

### Yahoo Finance (default fallback)

- **Requires API key**: No
- **Rate limits**: ~2,000 requests/hour per IP
- **Data types**: Quotes, OHLCV bars (1m, 5m, 15m, 1h, 4h, 1d, 1wk, 1mo), market status
- **Class**: `backend.market_data.providers.yfinance_provider.YahooFinanceProvider`

Yahoo Finance is the default fallback provider. It requires no configuration but is subject to IP-based rate limiting. Use it as the primary provider for development and light production use.

### Alpha Vantage

- **Requires API key**: Yes (`ALPHA_VANTAGE_API_KEY`)
- **Free tier**: 25 requests/day, 5 req/min
- **Premium tier**: Higher limits
- **Data types**: Quotes, intraday bars, daily bars (1d, weekly, monthly)
- **Class**: `backend.market_data.providers.alpha_vantage_provider.AlphaVantageProvider`

Alpha Vantage provides professional-grade market data with a free tier. Configure via environment variables:

```bash
MARKET_DATA_PRIMARY_PROVIDER=alpha_vantage
ALPHA_VANTAGE_API_KEY=your_api_key_here
```

## Provider Chain

The ingestion service constructs a fallback chain from the `MARKET_DATA_PRIMARY_PROVIDER` and `MARKET_DATA_FALLBACK_PROVIDERS` settings:

```python
# Example: use Alpha Vantage primarily, fall back to Yahoo Finance
MARKET_DATA_PRIMARY_PROVIDER=alpha_vantage
MARKET_DATA_FALLBACK_PROVIDERS=yahoo_finance
```

When a provider fails (network error, rate limit, missing data), the next provider in the chain is tried. All failures are logged to `provider_status` table.

## Implementing a New Provider

1. Create `backend/market_data/providers/my_provider.py`:
   ```python
   from backend.market_data.provider import MarketDataProvider, ProviderCapabilities

   class MyProvider(MarketDataProvider):
       @property
       def name(self) -> str:
           return "my_provider"

       @property
       def capabilities(self) -> ProviderCapabilities:
           return ProviderCapabilities(
               quotes=True,
               intraday_bars=True,
               daily_bars=True,
               market_status=True,
           )
   ```

2. Register it in `backend/market_data/providers/__init__.py`:
   ```python
   from .my_provider import MyProvider
   PROVIDER_MAP = {
       "my_provider": MyProvider,
       ...
   }
   ```

3. Set as primary or fallback:
   ```bash
   MARKET_DATA_PRIMARY_PROVIDER=my_provider
   ```

## Data Quality

The system validates incoming data for:

- **Stale data** — Quotes older than 15 minutes are flagged with `data_status = "stale"`. Bars with timestamps in the future are rejected.
- **Invalid OHLC** — High < Low, or Close outside [Open, High, Low] range are flagged.
- **Duplicate candles** — Identical `(symbol, timeframe, timestamp)` bars are deduplicated (first wins).
- **Missing candles** — Gap detection logs a warning; the scanner does not error on missing data.
- **Timezone correctness** — All timestamps are stored as UTC-aware datetimes. Ingestion converts provider-local timestamps before storage.

## Error Handling

Provider failures are tracked in the `provider_status` table:

```sql
SELECT provider_name, is_healthy, error_message, last_success
FROM provider_status
ORDER BY timestamp DESC
LIMIT 10;
```

A provider is considered unhealthy after 3 consecutive failures. Health checks are retried every 60 seconds.
