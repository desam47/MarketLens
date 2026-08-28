"""
Application configuration settings
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketDataSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MARKET_DATA_", extra="ignore")
    primary_provider: str = Field(default="yahoo_finance")
    fallback_providers: list[str] = Field(default_factory=lambda: [])
    rate_limit_per_minute: int = Field(default=60)
    cache_ttl_seconds: int = Field(default=300)


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
    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")

    enabled: bool = Field(default=False)
    # Primary provider: one of {ollama, lm_studio, openai_compatible,
    # openai, anthropic, openrouter}.
    provider: str = Field(default="ollama")
    # Comma-separated fallback chain (e.g. "openrouter,openai"). Empty
    # by default; the manager treats the chain as an ordered list and
    # tries each in turn.
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

    def fallback_chain(self) -> list[str]:
        """Return the ordered list of fallback providers (excluding primary)."""
        return [p.strip() for p in self.fallback_providers.split(",") if p.strip()]

    def all_providers(self) -> list[str]:
        """Primary + fallbacks in order."""
        return [self.provider] + self.fallback_chain()


class WatchlistSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WATCHLIST_", extra="ignore")
    max_symbols_per_watchlist: int = Field(default=50)
    max_watchlists: int = Field(default=10)
    auto_save_enabled: bool = Field(default=True)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")
    url: str = Field(default="sqlite:///./marketlens.db")
    echo: bool = Field(default=False)
    pool_size: int = Field(default=5)


class CORSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORS_", extra="ignore")
    # Comma-separated list of allowed origins, e.g. "http://localhost:3000,https://app.example.com".
    # `*` is allowed in dev but logs a warning at startup because it permits any
    # browser to call authenticated endpoints if allow_credentials=True.
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5001"]
    )


class RateLimitSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_", extra="ignore")
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
    """
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    # EMA crossover (Phase 6 spec: "EMA structure")
    ema: float = 0.3
    # RSI overbought/oversold
    rsi: float = 0.2
    # MACD histogram sign
    macd: float = 0.2
    # ADX is used for *strength* (not direction), so this weight is
    # applied as a multiplier on confidence — it does not contribute to
    # the directional ``score``. Kept here so a single block of settings
    # exposes every component.
    adx: float = 0.15
    # Volume confirmation (relative to its own SMA)
    volume: float = 0.1
    # Momentum (ROC sign)
    momentum: float = 0.05
    # SuperTrend direction (close above/below the line)
    supertrend: float = 0.15
    # Market structure (Bollinger %B deviation from 0.5)
    bollinger: float = 0.0


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
    model_config = SettingsConfigDict(env_prefix="MTF_", extra="ignore")
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
    model_config = SettingsConfigDict(env_prefix="MARKET_CONTEXT_", extra="ignore")
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
    model_config = SettingsConfigDict(env_prefix="RELATIVE_STRENGTH_", extra="ignore")
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
    primary_provider: str = "yfinance_news"


class FundamentalsAuxSettings(_AuxProviderCategorySettings):
    primary_provider: str = "yfinance_fundamentals"


class OptionsAuxSettings(_AuxProviderCategorySettings):
    primary_provider: str = "yfinance_options"


class AuxDataSettings(BaseSettings):
    """Phase 18 — Optional auxiliary data providers.

    News, fundamentals, and options are independently enabled. When a
    category is ``enabled=False`` its endpoints return a 503 with a
    descriptive message and the rest of the app continues to work.
    Defaults are ``enabled=False`` for all three; the trend engine,
    scanner, and AI pipeline do not require any of them.
    """
    model_config = SettingsConfigDict(env_prefix="AUX_", extra="ignore")
    news: NewsAuxSettings = Field(default_factory=NewsAuxSettings)
    fundamentals: FundamentalsAuxSettings = Field(default_factory=FundamentalsAuxSettings)
    options: OptionsAuxSettings = Field(default_factory=OptionsAuxSettings)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    app_name: str = "MarketLens"
    app_version: str = "0.1.0"
    debug: bool = Field(default=False, validation_alias=AliasChoices("DEBUG", "debug"))
    host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("HOST", "host"))
    port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "port"))

    market_data: MarketDataSettings = Field(default_factory=MarketDataSettings)
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


# Global settings instance
settings = Settings()
