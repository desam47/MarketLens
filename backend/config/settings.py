"""
Application configuration settings
"""

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root is two levels up from this file (backend/config/settings.py).
# Used for env_file resolution so pydantic-settings reads .env regardless of CWD,
# and as the base for any relative paths in settings (e.g. the SQLite DB).
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Path to the canonical .env file at the project root. All settings classes
# point at this single file so there's one source of truth for local config.
_ENV_FILE = str(_PROJECT_ROOT / ".env")

# Hard-coded database URL. Computed from the settings file location, NOT from
# the process CWD. This is the permanent fix for the "watchlist disappears on
# restart from wrong CWD" bug — the path is resolved at import time and never
# changes for the lifetime of the process.
_DB_PATH = (_PROJECT_ROOT / "marketlens.db").resolve()
_DB_URL = f"sqlite:///{_DB_PATH}"


class RedisSettings(BaseSettings):
    """Redis configuration for caching and pub/sub."""
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="REDIS_", extra="ignore")
    url: str = Field(default="redis://localhost:6379/0")
    password: str | None = Field(default=None)
    # Cache TTL settings (in seconds)
    bar_data_ttl: int = Field(default=300)  # 5 minutes for bar data
    quote_ttl: int = Field(default=60)      # 1 minute for quotes
    # Cache size limits (maximum number of keys)
    max_bar_keys: int = Field(default=1000)
    max_quote_keys: int = Field(default=1000)
    # Enable/disable Redis caching
    enabled: bool = Field(default=False)


class BackgroundProcessingSettings(BaseSettings):
    """Phase 2.5 — Background AI analysis job queue (RQ)."""
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="BACKGROUND_", extra="ignore")
    enabled: bool = Field(default=True)
    # RQ queue name. Workers must be started with: rq worker --url redis://... <queue_name>
    queue_name: str = Field(default="marketlens-workers")
    # Separate queue for the symbol-history backfill pipeline — kept apart
    # from queue_name (AI analysis jobs) so a slow multi-tier backfill can't
    # starve AI analysis, and vice versa. Workers:
    # rq worker --url redis://... <backfill_queue_name>
    backfill_queue_name: str = Field(default="marketlens-backfill")
    # Result TTL in seconds — how long completed results stay in Redis before expiring.
    result_ttl: int = Field(default=3600)
    # Job TTL in seconds — jobs not started within this window are discarded.
    job_timeout: int = Field(default=600)


class NotificationSettings(BaseSettings):
    """Optional server-side delivery settings for alert notifications."""
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="NOTIFICATIONS_", extra="ignore")
    enabled: bool = Field(default=True)
    request_timeout: float = Field(default=5.0, ge=1.0, le=30.0)
    retry_max: int = Field(default=3, ge=0, le=10)
    retry_backoff_seconds: int = Field(default=30, ge=1, le=3600)
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from: str = Field(default="")


class FinnhubSettings(BaseSettings):
    """Finnhub free-tier provider configuration (v2.2).

    Free tier: 30 req/sec rate limit, IP-based (no API key required).
    Providing an API key upgrades to 60 req/sec.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="FINNHUB_", extra="ignore")
    enabled: bool = Field(default=False)
    api_key: str = Field(default="")
    rate_limit_per_minute: int = Field(default=1200)
    request_timeout: float = Field(default=10.0)


class WebullSettings(BaseSettings):
    """Webull Open API v3 provider configuration (v2.2).

    Provider is enabled only when WEBULL_ENABLED=true AND both
    WEBULL_APP_KEY and WEBULL_APP_SECRET are set. The token is held
    in-process memory only and refreshed automatically on expiry.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="WEBULL_", extra="ignore")
    enabled: bool = Field(default=False)
    app_key: str = Field(default="")
    app_secret: str = Field(default="")
    use_sandbox: bool = Field(default=True)
    rate_limit_per_minute: int = Field(default=120)
    request_timeout: float = Field(default=15.0)
    # --- MQTT streaming (2026-09-10) -----------------------------------
    # Push feed for L1 snapshots + trade ticks via the Webull SDK's
    # DataStreamingClient. Off by default; needs app_key/app_secret and a
    # Nasdaq Basic / Time & Sales entitlement.
    streaming_enabled: bool = Field(default=False)
    # A symbol whose last stream message is older than this is considered
    # "not live" — the polled quote loop covers it as a fallback.
    streaming_stale_seconds: float = Field(default=15.0)
    # Optional MQTT host override; empty = let the SDK's endpoint resolver
    # pick it from the region.
    streaming_mqtt_host: str = Field(default="")


class MarketDataSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="MARKET_DATA_", extra="ignore")
    # Phase 3.7: live ingestion chain — primary + fallbacks in priority order.
    # Defaults: webull (primary) → yfinance → alpaca. Configurable via
    # MARKET_DATA_PRIMARY_PROVIDER / MARKET_DATA_FALLBACK_PROVIDERS.
    primary_provider: str = Field(default="webull")
    fallback_providers: list[str] = Field(default_factory=lambda: ["yfinance", "alpaca"])
    # Global rate limit (used when no per-provider override is set).
    rate_limit_per_minute: int = Field(default=60)
    cache_ttl_seconds: int = Field(default=300)
    # Removed 2026-09-09 (MARKET_DATA_BAR_RETENTION_DAYS / bar_retention_days):
    # was only ever a fetch-depth cap (how far back an initial backfill
    # could reach), and by construction (1095 >= every BACKFILL_*_DAYS
    # tier value) it never actually clamped anything in any real call
    # path — dead weight, not real behavior. Storage retention (the
    # thing this name suggested it did) is RetentionSettings' job, per
    # timeframe — see that class's docstring.
    # Phase 3.3.14: When a symbol is freshly added to a watchlist, trigger
    # a background backfill of bar history. Set False to disable backfills.
    backfill_on_add: bool = Field(default=True)
    # Per-provider rate limit overrides keyed by provider name.
    # Values are sourced from MARKET_DATA_<PROVIDER_NAME>_RATE_LIMIT_PER_MINUTE.
    yahoo_finance_rate_limit_per_minute: int = Field(default=60)
    finnhub_rate_limit_per_minute: int = Field(default=1200)
    webull_rate_limit_per_minute: int = Field(default=100)  # ~200/min max sustained
    alpaca_rate_limit_per_minute: int = Field(default=60)


class BackfillSettings(BaseSettings):
    """Phase 3.7: per-timeframe backfill provider chains.

    Each timeframe (1m, 1h, 1d) has an independent primary + fallback
    chain. The 1m tier also has a separate ``gapfill`` chain for the
    ~15-minute lag at the tip of Alpaca's free-tier 1m data — yfinance
    or webull fills that window after the Alpaca fetch.

    Defaults (driven by `.env`):
      - 1m primary=alpaca,  gapfill=webull,   fallback=webull,yahoo_finance
      - 1h primary=alpaca,  fallback=webull,yahoo_finance
      - 1d primary=alpaca,  fallback=webull,yahoo_finance
    """
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_prefix="BACKFILL_",
        extra="ignore",
        populate_by_name=True,
    )

    # 1m: alpaca primary; gapfill providers fill the latest ~15 min lag.
    tf_1m_primary: str = Field(
        default="alpaca", validation_alias=AliasChoices("BACKFILL_1M_PRIMARY")
    )
    tf_1m_gapfill: str = Field(
        default="webull", validation_alias=AliasChoices("BACKFILL_1M_GAPFILL")
    )
    tf_1m_fallback: str = Field(
        default="webull,yahoo_finance",
        validation_alias=AliasChoices("BACKFILL_1M_FALLBACK"),
    )

    # 1h: alpaca primary; webull first, yfinance second.
    tf_1h_primary: str = Field(
        default="alpaca", validation_alias=AliasChoices("BACKFILL_1H_PRIMARY")
    )
    tf_1h_fallback: str = Field(
        default="webull,yahoo_finance",
        validation_alias=AliasChoices("BACKFILL_1H_FALLBACK"),
    )

    # 1d: yfinance primary; webull first, alpaca second.
    tf_1d_primary: str = Field(
        default="yfinance", validation_alias=AliasChoices("BACKFILL_1D_PRIMARY")
    )
    tf_1d_fallback: str = Field(
        default="webull,alpaca",
        validation_alias=AliasChoices("BACKFILL_1D_FALLBACK"),
    )

    # Per-tier backfill window (calendar days). Each is capped further by
    # the caller's requested retention (MARKET_DATA_BAR_RETENTION_DAYS,
    # or an explicit `days` override) via `min(tf_Xx_days, retention_days)`
    # in backfill_symbol_history — these settings just make the previously
    # hardcoded per-tier ceilings (15 / 365 / retention_days) tunable
    # independently of the overall retention policy.
    tf_1m_days: int = Field(
        default=15, validation_alias=AliasChoices("BACKFILL_1M_DAYS")
    )
    tf_1h_days: int = Field(
        default=365, validation_alias=AliasChoices("BACKFILL_1H_DAYS")
    )
    tf_1d_days: int = Field(
        default=1095, validation_alias=AliasChoices("BACKFILL_1D_DAYS")
    )

    def get_1m_gapfill_providers(self) -> list[str]:
        """Comma-separated list of 1m gapfill providers from the .env."""
        return [p.strip() for p in self.tf_1m_gapfill.split(",") if p.strip()]

    def get_1m_fallback_providers(self) -> list[str]:
        """Fallback provider names for 1m backfill from .env (after primary fails)."""
        return [p.strip() for p in self.tf_1m_fallback.split(",") if p.strip()]

    def get_fallback_providers(self, timeframe: str) -> list[str]:
        """Comma-separated list of fallback providers for ``timeframe`` (1m/1h/1d)."""
        if timeframe == "1m":
            return [p.strip() for p in self.tf_1m_fallback.split(",") if p.strip()]
        if timeframe in ("1h", "4h"):
            return [p.strip() for p in self.tf_1h_fallback.split(",") if p.strip()]
        if timeframe in ("1d", "1wk"):
            return [p.strip() for p in self.tf_1d_fallback.split(",") if p.strip()]
        return []

    def get_primary_provider(self, timeframe: str) -> str:
        """Primary provider name for ``timeframe`` (1m/1h/1d) from .env."""
        if timeframe == "1m":
            return self.tf_1m_primary.strip()
        if timeframe in ("1h", "4h"):
            return self.tf_1h_primary.strip()
        if timeframe in ("1d", "1wk"):
            return self.tf_1d_primary.strip()
        return "alpaca"  # safe default


class RetentionSettings(BaseSettings):
    """Per-timeframe bar storage retention (2026-09-09).

    Independent from ``BackfillSettings.tf_*_days`` (BACKFILL_*_DAYS) —
    how much history gets FETCHED on an initial backfill, not enforced
    as an ongoing DB constraint. (MARKET_DATA_BAR_RETENTION_DAYS /
    ``MarketDataSettings.bar_retention_days`` — the old single global
    prune window this superseded — was removed 2026-09-09: it never
    actually did anything in any real call path, see its removal note
    in MarketDataSettings.)

    This is the actual storage cap: bars older than the configured
    window for their own timeframe are deleted automatically by the
    rolling retention prune, which runs every ~60s ingestion tick (only
    when new bars were written that tick). 1m/2m/3m/5m/15m/30m are the
    high-volume timeframes (especially now that extended-hours 1m
    ingestion roughly doubles their daily row count) so they default to
    a short window; 1h/4h/1d/1wk are compact regardless of how long
    they're kept, so they default to much longer windows — this mirrors
    BACKFILL_1M/1H/1D_DAYS (15/365/1095) at +1 day, so nothing gets
    pruned right after backfill just fetched it.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="RETENTION_", extra="ignore")

    tf_1m_days: int = Field(default=16)
    tf_2m_days: int = Field(default=16)
    tf_3m_days: int = Field(default=16)
    tf_5m_days: int = Field(default=16)
    tf_15m_days: int = Field(default=16)
    tf_30m_days: int = Field(default=16)
    tf_1h_days: int = Field(default=366)
    tf_4h_days: int = Field(default=366)
    tf_1d_days: int = Field(default=1096)
    tf_1wk_days: int = Field(default=1096)

    # Append-only tables written on every ingestion tick and never read beyond the latest
    # rows (engine seeding, a limit-100 quote history, the current provider health): quotes
    # (~15k rows/day), provider_status (~2.7k/day) and market_status (~2k/day). Nothing
    # pruned them, so they just grew. A month is far more history than any reader uses.
    quotes_days: int = Field(default=30, ge=1)
    provider_status_days: int = Field(default=30, ge=1)
    market_status_days: int = Field(default=30, ge=1)
    # One BackfillJob row per enqueue; only the latest per symbol is ever read.
    backfill_jobs_days: int = Field(default=30, ge=1)

    def days_for(self, timeframe: str) -> int:
        """Retention window in days for ``timeframe``. Unknown timeframes
        fall back to the longest window (1096d) — safer to under-prune an
        unrecognized timeframe than silently delete it fast."""
        return {
            "1m": self.tf_1m_days,
            "2m": self.tf_2m_days,
            "3m": self.tf_3m_days,
            "5m": self.tf_5m_days,
            "15m": self.tf_15m_days,
            "30m": self.tf_30m_days,
            "1h": self.tf_1h_days,
            "4h": self.tf_4h_days,
            "1d": self.tf_1d_days,
            "1wk": self.tf_1wk_days,
        }.get(timeframe, self.tf_1wk_days)


class AlpacaSettings(BaseSettings):
    """Alpaca market data provider configuration (v3.6).

    Provides both REST API (quotes, bars, market status) and real-time
    WebSocket streaming for live bars/trades/quotes.

    **Data Tiers:**
      - Free tier (IEX): 200 req/min REST, free real-time data for US equities
      - Paid tier (SIP): higher rate limits + full SIP market data

    **Provider chain position:** Added as fallback after webull, yfinance.
    Enable via ALPACA_ENABLED=true with valid API credentials.

    **WebSocket:** The provider maintains a persistent WebSocket connection
    to Alpaca's streaming API. It starts on first subscription and stays
    connected as long as clients are subscribed.

    **Credentials:** ``api_key`` and ``secret_key`` are sourced from
    ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``. The provider is skipped
    (not registered) when ``enabled=false`` or credentials are absent.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="ALPACA_", extra="ignore")
    enabled: bool = Field(default=False)
    api_key: str = Field(default="")
    secret_key: str = Field(default="")
    # Paper trading (paper.alpaca.ai) by default; set to false for live trading.
    paper: bool = Field(default=True)
    # Data tier: "iex" (free) or "sip" (paid). Determines symbol availability
    # and rate limits. Auto-detected from credentials when possible.
    data_tier: str = Field(default="iex")
    request_timeout: float = Field(default=15.0)


class AISettings(BaseSettings):
    """Phase 15 — Optional AI provider configuration.

    ``enabled=False`` is the safe default: the quantitative engine
    continues to work end-to-end with AI completely off. When the
    caller opts in via ``AI_ENABLED=true``, the system reads the
    primary ``provider`` (ollama, lm_studio, openai_compatible,
    openai, anthropic, openrouter) plus a comma-separated
    ``fallback_providers`` chain. If a provider is unavailable, the
    next one in the chain is tried; if all are unavailable, calls
    return an ``UncertaintyResponse`` (Phase 16) rather than crashing.

    ``api_key`` is sourced from the environment (``AI_API_KEY``) and
    must never be sent to the frontend — see ``AIManager.safe_config()``.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AI_", extra="ignore")

    enabled: bool = Field(default=False)
    # Primary provider: one of {ollama, lm_studio, openai_compatible,
    # openai, anthropic, openrouter}.
    provider: str = Field(default="ollama")
    # Comma-separated fallback chain (e.g. "openrouter,openai"). Empty
    # by default; the manager treats the chain as an ordered list and
    # tries each in turn.
    #
    # An entry may also be "type:model" (e.g. "ollama:qwen3:14b" or
    # "openai_compatible:deepseek-v3") to pin that entry to a specific
    # model instead of the provider type's hardcoded default — this is
    # the ONLY way to choose a fallback's model; there is no separate
    # override field. It's also required to run a SECOND model through
    # the same provider type as another chain entry, since providers
    # are cached (and thus deduped) by their bare type name — repeating
    # a type with no ``:model`` suffix would just reuse the first
    # instance's model instead of trying a different one. A ``:model``
    # entry that names the same type as the primary provider reuses
    # the primary's base_url/api_key (same gateway, different model);
    # otherwise it falls through to that type's own defaults.
    fallback_providers: str = Field(default="")
    # Default model hint for the primary provider.
    model: str = Field(default="llama3.2")
    # OpenAI-compatible base URL (Ollama defaults to http://localhost:11434,
    # LM Studio to http://localhost:1234).
    base_url: str = Field(default="http://localhost:11434")
    # API key for the primary provider. Read from env via Pydantic.
    api_key: str | None = Field(default=None)
    # Per-call timeout in seconds. Health checks use a shorter timeout
    # to fail fast on unreachable local servers.
    timeout: float = Field(default=30.0)
    health_check_timeout: float = Field(default=2.0)
    # Sampling / output limits.
    max_tokens: int = Field(default=1000)
    temperature: float = Field(default=0.3)
    # O11: request structured JSON output from providers that support it.
    # When True, the completion request includes response_format forcing
    # JSON mode and the manager skips regex extraction during parsing.
    # Supported by: openai, openrouter, ollama (7B+ models), anthropic.
    # Ollama/LM Studio depend on the local model actually honouring the
    # response_format field — disable if your model ignores it.
    structured_output: bool = Field(default=True)

    # Universal AI Hub chat (2026-09-10). Max tickers one chat turn will
    # build full quant context for (extra named tickers are dropped with
    # a note). ``chat_symbol_ai_fallback`` allows one small extra
    # completion to resolve a company name -> ticker when the regex/
    # denylist extractor finds nothing.
    chat_max_tickers: int = Field(default=3)
    chat_symbol_ai_fallback: bool = Field(default=True)
    # Stream the chat reply token-by-token over SSE (perceived latency).
    # Off => the streaming endpoint still exists but emits the whole
    # reply in one chunk, so the frontend path is unchanged.
    chat_streaming: bool = Field(default=True)
    # Optional chain-entry override (same "type" / "type:model" format
    # as fallback_providers) tried before the default chain, for the
    # chat completion call only — every other AI feature (digest,
    # analyze_symbol, nl_search) is unaffected. Chat's reply contract is
    # unusually strict (a fixed JSON shape, action-tagging rules, a
    # destructive-action confirm gate) and a model that doesn't follow
    # instructions reliably here shows up as wrong/hallucinated
    # behavior a trader sees directly, not just a worse read — see the
    # _fallback_action / _fallback_confirmation regex safety nets in
    # backend/ai/chat.py, added because the default chain's model
    # didn't reliably comply on its own. Empty (default) means no
    # override — chat uses the same chain as everything else.
    chat_model: str = Field(default="")
    # The chat's run_backtest action tool (2026-09-11) — a fresh 6-month
    # backtest of the engine's own signals, on demand. Off by default:
    # a chat-triggered backtest is a real, rate-limited compute cost.
    backtest_tool_enabled: bool = Field(default=False)

    def fallback_chain(self) -> list[str]:
        """Return the ordered list of fallback providers (excluding primary).

        Entries may be bare provider types ("ollama") or "type:model"
        composites ("openai_compatible:deepseek-v3") — see
        ``fallback_providers`` for what the latter does. Returned
        as-is; ``AIManager`` is what parses the ``:model`` suffix.
        """
        return [p.strip() for p in self.fallback_providers.split(",") if p.strip()]

    def all_providers(self) -> list[str]:
        """Primary + fallbacks in order."""
        return [self.provider] + self.fallback_chain()


class WatchlistSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="WATCHLIST_", extra="ignore")
    max_symbols_per_watchlist: int = Field(default=50)
    max_watchlists: int = Field(default=10)
    auto_save_enabled: bool = Field(default=True)


class DatabaseSettings(BaseSettings):
    """Database configuration.

    The DB path is **hard-coded** to ``<project_root>/marketlens.db`` and
    cannot be overridden via ``DATABASE_URL`` in ``.env`` or the process
    environment. This is intentional: the previous design allowed
    ``DATABASE_URL`` to be set to a relative path, which silently created
    a new empty DB in whatever directory the server was started from,
    causing the "watchlist disappeared after restart from wrong dir" bug.

    If you need a different DB (tests, production Postgres), set the
    ``MARKETLENS_DB_OVERRIDE`` environment variable to the desired URL.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="DATABASE_", extra="ignore")
    # The URL is the hard-coded project-root path. The field validator below
    # forces this value, ignoring anything pydantic-settings read from env.
    url: str = ""  # Set by the validator below; never read from env.
    echo: bool = Field(default=False)
    pool_size: int = Field(default=5)

    @field_validator("url", mode="before")
    @classmethod
    def _force_project_root_db(cls, v: str | None) -> str:
        """Force the DB URL to the project-root path regardless of env.

        This is a permanent fix for the "watchlist disappears on restart
        from wrong dir" bug. The hard-coded path is computed from
        ``_PROJECT_ROOT`` at import time, so it cannot be misconfigured
        by the process CWD or by an accidental ``DATABASE_URL`` in ``.env``.

        To use a different DB (tests, production Postgres), set
        ``MARKETLENS_DB_OVERRIDE`` to the URL.
        """
        return os.environ.get("MARKETLENS_DB_OVERRIDE") or _DB_URL


class CORSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="CORS_", extra="ignore")
    # Comma-separated list of allowed origins, e.g. "http://localhost:3000,https://app.example.com".
    # `*` is allowed in dev but logs a warning at startup because it permits any
    # browser to call authenticated endpoints if allow_credentials=True.
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5001"]
    )


class RateLimitSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="RATE_LIMIT_", extra="ignore")
    # Max requests per IP per window for write/mutation endpoints (ingestion
    # start/stop, watchlist edits). Read endpoints are not rate-limited —
    # they're already throttled by the underlying engines' state.
    window_seconds: int = 60
    max_requests_per_window: int = 30


class TrendSignalWeights(BaseSettings):
    """Weights used by TrendEngine when scoring indicators (sum need not = 1).

    All eight Phase 6 components are represented here. Weights are
    normalized internally by ``TrendEngine._calculate_trend`` (it sums
    the weights of components that have a current value, so a component
    that hasn't warmed up yet is silently excluded from the average).

    Phase 6.1 weights: tuned to emphasize price-action indicators
    (EMA, MACD, SuperTrend) over oscillators (RSI), and to give ADX
    a real directional vote via DI+/DI- rather than only strength.
    Bollinger Bands is enabled (was 0.0) so the structure signal counts.
    """
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    # EMA crossover — the spine of trend direction.
    ema: float = 0.25
    # RSI: scaled continuous signal around the 50 midpoint, not binary.
    rsi: float = 0.10
    # MACD: continuous histogram, normalized by ATR.
    macd: float = 0.20
    # ADX: directional vote from DI+/DI-, weighted by trend strength.
    adx: float = 0.15
    # Volume confirmation (relative to its own SMA).
    relative_volume: float = 0.05
    # Momentum (ROC scaled continuous, normalized by ATR).
    momentum: float = 0.10
    # SuperTrend direction (close above/below the line).
    supertrend: float = 0.20
    # Market structure (Bollinger %B deviation from 0.5).
    bollinger: float = 0.10


class IndicatorDefaults(BaseSettings):
    """Default indicator periods used throughout the engine — Principle 12."""
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    # RSI — default 14-period, used on every timeframe.
    rsi_period: int = 14
    # MACD — default (12, 26, 9) is the de-facto standard across most platforms.
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # ADX — default 14-period Wilder smoothing.
    adx_period: int = 14
    # SuperTrend — ATR-based; defaults (10, 3.0) are the most common settings.
    supertrend_atr_period: int = 10
    supertrend_multiplier: float = 3.0
    # Bollinger Bands — default (20, 2.0) covers ~2 std-dev of a 20-bar window.
    bollinger_period: int = 20
    bollinger_std_dev: float = 2.0


class TrendSettings(BaseSettings):
    """Settings that control TrendEngine behaviour and weighting."""
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    # Score component weights (passed to TrendSignalWeights; sum not required to be 1).
    signal_weights: TrendSignalWeights = Field(default_factory=TrendSignalWeights)
    # Indicator defaults used when building per-timeframe indicator stacks.
    indicators: IndicatorDefaults = Field(default_factory=IndicatorDefaults)
    # Timeframe weights for TrendEngine.get_overall_trend.
    # Keys must match Timeframe enum values; sum is normalised internally.
    timeframe_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "1m": 0.1,
            "5m": 0.15,
            "15m": 0.2,
            "1h": 0.25,
            "4h": 0.15,
            "1d": 0.15,
        }
    )
    # Strategy version stamped onto every BacktestRun. Bump this when the
    # signal generation logic, scoring weights, or indicator defaults change
    # in a way that makes prior backtest results non-comparable. Phase 0
    # Principle 13: every important signal is reproducible and versioned.
    strategy_version: str = "v1.0"


class DataQualitySettings(BaseSettings):
    """Phase 0 Principle 15: data quality validated before analysis.

    Stale/duplicate/gap detection thresholds used by ``TrendEngine.update``
    before each indicator tick. ``max_tick_gap_seconds`` is the cutoff for
    flagging a gap in the incoming tick stream; ``stale_threshold_seconds``
    is the cutoff for flagging a tick as stale (older than the configured
    threshold at the time of arrival).
    """
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    max_tick_gap_seconds: float = 60.0   # > this gap between ticks => warn "gap"
    stale_threshold_seconds: float = 30.0  # ticks older than this => warn "stale"
    duplicate_price_tolerance: float = 1e-9  # |price - last_price| <= this => "duplicate"


class MultiTimeframeSettings(BaseSettings):
    """Phase 7: configurable multi-timeframe analysis.

    The legacy MultiTimeframeEngine had a hard-coded ``timeframe_weights``
    dict inside ``_calculate_overall_direction`` — a Phase 0 Principle 11
    violation. This block is the single source of truth: every per-TF
    weight the engine uses for the weighted confluence score is read from
    here. The two preset timeframes (day trading, swing) are exposed as
    module-level constants on the engine itself because they're
    configuration, not settings — they don't vary per environment.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="MTF_", extra="ignore")
    # Which preset to use when the engine is constructed without one.
    # "day_trading" (5m/15m/1h/4h/1d) or "swing" (15m/1h/4h/1d/1w).
    default_preset: str = "day_trading"
    # Per-timeframe weights for the overall confluence score. Sum is
    # normalized internally. Keys match Timeframe enum values.
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "1m": 0.05,
            "5m": 0.10,
            "15m": 0.15,
            "30m": 0.10,
            "1h": 0.20,
            "4h": 0.20,
            "1d": 0.15,
            "1wk": 0.05,
        }
    )


class MarketContextSettings(BaseSettings):
    """Phase 8: market-context analysis across SPY/QQQ/IWM/VIX.

    The market-wide regime is derived by aggregating the sub-regimes of
    these four indices. VIX is treated inversely (high VIX → risk-off).
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="MARKET_CONTEXT_", extra="ignore")
    # Indices to analyze for the market-wide regime aggregate.
    indices: tuple[str, ...] = ("SPY", "QQQ", "IWM", "^VIX")
    # Number of sub-regimes that must agree for a consensus regime.
    consensus_threshold: int = 3
    # VIX level below which VIX sub-engine signals RISK_ON.
    vix_risk_on_max: float = 15.0
    # VIX level above which VIX sub-engine signals RISK_OFF.
    vix_risk_off_min: float = 25.0
    # VIX % change in one bar that triggers TRANSITION.
    vix_spike_threshold: float = 0.05


class RelativeStrengthSettings(BaseSettings):
    """Phase 8: relative-strength classification against benchmarks.

    For each benchmark (SPY, QQQ, sector ETF) the engine computes the
    symbol's return delta over the lookback window and maps it to a
    classification band.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="RELATIVE_STRENGTH_", extra="ignore")
    # Comma-separated benchmark symbols used for relative-strength
    # computation. Defaults to the SPY/QQQ pair from the Phase 8 spec;
    # override via ``RELATIVE_STRENGTH_BENCHMARKS=SPY,QQQ,IWM``.
    benchmarks: str = Field(default="SPY,QQQ")
    # Number of trading days over which to compute relative performance.
    lookback_days: int = 20
    # Return delta above this threshold → STRONG_OUTPERFORMER.
    strong_outperformer_threshold: float = 0.05
    # Return delta above this threshold → OUTPERFORMER.
    outperformer_threshold: float = 0.01
    # Return delta below this threshold → UNDERPERFORMER.
    underperformer_threshold: float = -0.01
    # Return delta below this threshold → STRONG_UNDERPERFORMER.
    strong_underperformer_threshold: float = -0.05

    def benchmark_list(self) -> tuple[str, ...]:
        """Return the benchmark symbols as an ordered tuple.

        Empty entries (e.g. trailing comma) are dropped. Sector ETF is
        resolved separately by ``SectorEngine`` and is not part of this
        list.
        """
        return tuple(s.strip() for s in self.benchmarks.split(",") if s.strip())


class _AuxProviderCategorySettings(BaseSettings):
    """Shared fields for one Phase 18 provider category."""
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = Field(default=False)
    primary_provider: str  # must be overridden
    fallback_providers: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=20)
    request_timeout: float = Field(default=10.0)


class NewsAuxSettings(_AuxProviderCategorySettings):
    # Explicit env_prefix/env_file — a nested BaseSettings field does NOT
    # inherit its parent's env_prefix (AuxDataSettings' "AUX_" below); each
    # nested BaseSettings subclass resolves its own env vars independently,
    # per its OWN model_config. Without this, NewsAuxSettings() read with
    # env_prefix="" and env_file=None — no prefix (looking for a bare
    # ENABLED, not AUX_NEWS_ENABLED) and no .env file at all — so
    # AUX_NEWS_ENABLED=true in .env silently had zero effect and `enabled`
    # was permanently stuck at its Python default (False), 503-ing every
    # request regardless of .env or how many times the backend restarted
    # (found live 2026-09-09 — the user set AUX_NEWS_ENABLED=true,
    # restarted, and every /api/aux-data/* endpoint still 503'd).
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AUX_NEWS_", extra="ignore")
    # 2026-09-10: Finnhub /company-news is primary (broker-grade, keyed),
    # yfinance is the fallback. Finnhub raises on failure so the chain
    # advances; if FINNHUB_API_KEY is absent it raises immediately and
    # yfinance takes over.
    primary_provider: str = "finnhub_news"
    fallback_providers: list[str] = Field(default_factory=lambda: ["yfinance_news"])


class FundamentalsAuxSettings(_AuxProviderCategorySettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AUX_FUNDAMENTALS_", extra="ignore")
    # yfinance stays primary — its .info scrape fills the full
    # FundamentalsItem. webull_fundamentals is wired as a fallback but
    # Webull's get_financials_indicators only carries 8 per-share ratios
    # (see that provider's docstring), so it can't be a full primary
    # without fanning out to more endpoints.
    primary_provider: str = "yfinance_fundamentals"
    fallback_providers: list[str] = Field(default_factory=lambda: ["webull_fundamentals"])


class OptionsAuxSettings(_AuxProviderCategorySettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AUX_OPTIONS_", extra="ignore")
    primary_provider: str = "yfinance_options"


class AuxDataSettings(BaseSettings):
    """Phase 18 — Optional auxiliary data providers.

    News, fundamentals, and options are independently enabled. When a
    category is ``enabled=False`` its endpoints return a 503 with a
    descriptive message and the rest of the app continues to work.
    Defaults are ``enabled=False`` for all three; the trend engine,
    scanner, and AI pipeline do not require any of them.

    This class's own env_prefix="AUX_" below does NOT propagate to the
    news/fundamentals/options sub-models — each is independently a
    BaseSettings subclass with its own model_config (AUX_NEWS_ / AUX_
    FUNDAMENTALS_ / AUX_OPTIONS_ — see their class comments). This
    class-level prefix is inert (no scalar fields of its own to bind), kept
    only for consistency with every other settings class in this file.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AUX_", extra="ignore")
    news: NewsAuxSettings = Field(default_factory=NewsAuxSettings)
    fundamentals: FundamentalsAuxSettings = Field(default_factory=FundamentalsAuxSettings)
    options: OptionsAuxSettings = Field(default_factory=OptionsAuxSettings)


class SecuritySettings(BaseSettings):
    """Security header configuration.

    HTTP security headers are added to every response. Defaults are
    production-safe; HSTS is disabled by default because it must only
    be set on deployments that are reachable exclusively over HTTPS.
    Enable ``hsts_enabled=True`` only after HTTPS is confirmed end-to-end.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="SECURITY_", extra="ignore")

    # Strict-Transport-Security: only enable when HTTPS is enforced
    # across the entire deployment chain (load balancer → app).
    # max-age in seconds: 31536000 = 1 year, 63072000 = 2 years.
    hsts_enabled: bool = Field(default=False)
    hsts_max_age_seconds: int = Field(default=31536000)  # 1 year
    hsts_include_subdomains: bool = Field(default=True)
    hsts_preload: bool = Field(default=False)

    # Content-Security-Policy. The default below is intentionally strict
    # for a JSON API: scripts and styles are blocked, only same-origin
    # is allowed, forms and frames are denied. Override via
    # ``SECURITY_CSP_VALUE=...`` if you need a looser policy.
    csp_enabled: bool = Field(default=True)
    csp_value: str = Field(
        default="default-src 'none'; "
        "script-src 'none'; "
        "style-src 'none'; "
        "img-src 'none'; "
        "font-src 'none'; "
        "connect-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'none'; "
        "base-uri 'none';"
    )

    # Clickjacking / framing protection.
    x_frame_options: str = Field(default="DENY")

    # MIME-type sniffing protection (IE legacy, but harmless elsewhere).
    x_content_type_options: bool = Field(default=True)

    # Referrer header on cross-origin requests.
    referrer_policy: str = Field(default="strict-origin-when-cross-origin")

    # Disable Google FLoC tracking.
    permissions_policy: str = Field(
        default="accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    )


class ObservabilitySettings(BaseSettings):
    """Observability and monitoring configuration.

    Configuration for distributed tracing, metrics collection, and
    structured logging. All settings can be overridden via environment
    variables with the OBSERVABILITY_ prefix.
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="OBSERVABILITY_", extra="ignore")

    # Tracing configuration
    tracing_enabled: bool = Field(default=False)
    # Jaeger agent host and port for trace export
    jaeger_agent_host: str = Field(default="localhost")
    jaeger_agent_port: int = Field(default=4317)

    # Metrics configuration
    metrics_enabled: bool = Field(default=True)
    # Enable detailed SQLAlchemy query logging in metrics
    sqlalchemy_query_logging: bool = Field(default=False)

    # Logging configuration
    structured_logging_enabled: bool = Field(default=True)
    # Log level for structured logging when not in debug mode
    structured_log_level: str = Field(default="INFO")


def _version_factory() -> str:
    """Default factory for ``Settings.app_version``.

    Reads the git commit hash (and optional tag) from ``version.txt`` at
    the project root.  The pre-commit hook writes this file on every
    commit, so the value always reflects the source-tree state the
    server is running from.  Falls back to "dev" when the file is
    missing (e.g. running from a tarball release without git).
    """
    try:
        from backend.version import get_version
        return get_version()
    except Exception:
        return "dev"


class DigestSettings(BaseSettings):
    """Version 4, AI feature 2 — daily/session AI digest scheduling."""
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AI_DIGEST_", extra="ignore")
    enabled: bool = Field(default=True)
    # Two fixed daily slots (ET), matching the "premarket"/"close" session
    # names used throughout backend.ai.digest / the /api/ai/digest/* routes.
    premarket_hour: int = Field(default=8, ge=0, le=23)
    premarket_minute: int = Field(default=30, ge=0, le=59)
    close_hour: int = Field(default=16, ge=0, le=23)
    close_minute: int = Field(default=15, ge=0, le=59)
    # How many top bullish/bearish movers get an AI blurb per digest —
    # bounds AI call volume/latency; everything else in the payload
    # (regime, RSI extremes, MTF counts) is pure quant, no AI cost.
    top_movers_count: int = Field(default=5, ge=1, le=20)


class TapeSettings(BaseSettings):
    """Time & Sales tape analytics (2026-09-10).

    A rolling per-symbol engine over the Webull trade-tick stream:
    signed volume / buy-sell pressure, tape speed, block detection. Off
    unless ``TAPE_ENABLED=true`` (and the Webull MQTT stream running).
    """
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="TAPE_", extra="ignore")
    enabled: bool = Field(default=False)
    window_seconds: int = Field(default=60, ge=5)
    long_window_seconds: int = Field(default=300, ge=30)
    fast_window_seconds: int = Field(default=15, ge=1)
    bucket_seconds: int = Field(default=1, ge=1)
    # A print is a "block" if its notional (price*size) OR raw size
    # clears either threshold.
    block_notional: float = Field(default=250_000.0, gt=0)
    block_size: int = Field(default=10_000, gt=0)
    # signed-volume z-score above which pressure is "heavy_buy"/"heavy_sell".
    heavy_pressure_z: float = Field(default=2.0, gt=0)
    retention_days: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def _check_window_ordering(self) -> "TapeSettings":
        """TapeEngine assumes fast_w <= main_w <= long_w (nested windows —
        get_snapshot() filters main from long_ and fast from main; the
        pressure z-score also assumes signed_v is summed over a window no
        wider than the history pruning retains). A misconfiguration here
        wasn't rejected before, so e.g. TAPE_WINDOW_SECONDS > TAPE_LONG_
        WINDOW_SECONDS silently deflated tape_speed/tape_accel with no
        error — fail fast at startup instead.
        """
        if not (self.fast_window_seconds <= self.window_seconds <= self.long_window_seconds):
            raise ValueError(
                "TapeSettings windows must satisfy "
                "fast_window_seconds <= window_seconds <= long_window_seconds "
                f"(got fast={self.fast_window_seconds}, window={self.window_seconds}, "
                f"long={self.long_window_seconds})"
            )
        return self


class AITradePlanTrackingSettings(BaseSettings):
    """Outcome tracking for the AI's own buy/sell trade_plan calls
    (2026-09-11). Off unless ``AI_TRADE_PLAN_TRACKING_ENABLED=true`` —
    when off, analyze_symbol() captures nothing, no background grading
    thread runs, and context.py's track_record section stays empty.
    """
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_prefix="AI_TRADE_PLAN_TRACKING_", extra="ignore",
    )
    enabled: bool = Field(default=False)
    # How often the background grading pass re-checks open plans against
    # fresh daily bars. Grading only matters once a day closes, so this
    # is deliberately generous, not tight.
    grading_interval_seconds: float = Field(default=1800.0, ge=60.0)


class NudgeSettings(BaseSettings):
    """Proactive chat nudges (2026-09-15) — the universal AI Hub chat is
    otherwise 100% reactive. Off unless ``AI_NUDGES_ENABLED=true``; when
    off, ``backend.ai.nudges.NudgeService`` never starts a loop."""
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="AI_NUDGES_", extra="ignore")
    enabled: bool = Field(default=True)
    poll_interval_seconds: float = Field(default=45.0, ge=5.0)
    # |signed_total_score| a watched symbol must cross (not just sit
    # at) to earn a "big move" nudge — see backend/scanner/filters.py's
    # TrendScoreGt/Lt, where 50 is already used elsewhere as "strong."
    # This is deliberately higher: a nudge interrupts, a screen result
    # doesn't.
    score_threshold: float = Field(default=60.0, gt=0)
    # Minimum time before the same symbol can trigger another "big
    # move" nudge, so a score oscillating around the threshold doesn't
    # spam the thread.
    cooldown_seconds: float = Field(default=1800.0, ge=60.0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    app_name: str = "MarketLens"
    # ``app_version`` is written by the pre-commit hook to version.txt at
    # the repo root.  ``get_version()`` falls back to "dev" when the file
    # is absent, so the server always starts with a valid version string.
    app_version: str = Field(default_factory=_version_factory)
    debug: bool = Field(default=False, validation_alias=AliasChoices("DEBUG", "debug"))
    # Log level for the root logger (DEBUG/INFO/WARNING/ERROR/CRITICAL).
    # Takes precedence over the DEBUG flag. Reads from LOG_LEVEL env var.
    log_level: str = Field(default="INFO")
    host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("HOST", "host"))
    port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "port"))

    market_data: MarketDataSettings = Field(default_factory=MarketDataSettings)
    backfill: BackfillSettings = Field(default_factory=BackfillSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    finnhub: FinnhubSettings = Field(default_factory=FinnhubSettings)
    webull: WebullSettings = Field(default_factory=WebullSettings)
    alpaca: AlpacaSettings = Field(default_factory=AlpacaSettings)
    ai: AISettings = Field(default_factory=AISettings)
    watchlist: WatchlistSettings = Field(default_factory=WatchlistSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    cors: CORSSettings = Field(default_factory=CORSSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    trend: TrendSettings = Field(default_factory=TrendSettings)
    data_quality: DataQualitySettings = Field(default_factory=DataQualitySettings)
    multitimeframe: MultiTimeframeSettings = Field(default_factory=MultiTimeframeSettings)
    market_context: MarketContextSettings = Field(default_factory=MarketContextSettings)
    relative_strength: RelativeStrengthSettings = Field(default_factory=RelativeStrengthSettings)
    aux_data: AuxDataSettings = Field(default_factory=AuxDataSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    background: BackgroundProcessingSettings = Field(default_factory=BackgroundProcessingSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    ai_digest: DigestSettings = Field(default_factory=DigestSettings)
    tape: TapeSettings = Field(default_factory=TapeSettings)
    ai_trade_plan_tracking: AITradePlanTrackingSettings = Field(
        default_factory=AITradePlanTrackingSettings,
    )
    ai_nudges: NudgeSettings = Field(default_factory=NudgeSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


# Global settings instance
settings = Settings()
