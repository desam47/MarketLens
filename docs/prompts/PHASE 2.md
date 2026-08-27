# PHASE 2 — MARKET DATA ARCHITECTURE

Implement the market-data provider system.

Do NOT implement trend calculations yet.

Create a provider interface similar to:

MarketDataProvider

It must support capabilities such as:

- historical bars

- latest quote

- latest bar

- batch quotes

- market status

- provider health

Create normalized internal models:

Quote

Bar

MarketStatus

ProviderStatus

ProviderCapabilities

Every normalized object should contain appropriate metadata:

- symbol

- timestamp

- timeframe

- provider

- data status

DATA STATUS MUST distinguish:

LIVE

DELAYED

HISTORICAL

STALE

ERROR

Create:

MarketDataManager

Responsibilities:

- provider selection

- provider priority

- fallback

- retries

- rate-limit handling

- caching hooks

- provider health

- normalization

INITIAL PROVIDER

Add one suitable free provider.

Do NOT use Webull as the default.

Webull must not be required.

Create provider configuration like:

market_data:

  primary_provider: <free-provider>

  fallback_providers:

    - <fallback>

providers:

  <provider>:

    enabled: true

  webull:

    enabled: false

Do not require paid API credentials.

IMPORTANT

The rest of the application must only communicate with MarketDataManager or MarketDataProvider interfaces.

The trend engine must never import the provider implementation.

Add provider tests.

Test:

- valid quote

- valid bar

- invalid data

- stale data

- provider failure

- fallback behavior

- normalization

After implementation, verify the application can retrieve historical data for a test symbol.

Then STOP.

