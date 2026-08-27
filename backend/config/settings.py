"""
Application configuration settings
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketDataSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MARKET_DATA_", extra="ignore")
    primary_provider: str = Field(default="alpha_vantage")
    fallback_providers: list[str] = Field(default_factory=lambda: ["yahoo_finance"])
    rate_limit_per_minute: int = Field(default=5)
    cache_ttl_seconds: int = Field(default=300)


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")
    enabled: bool = Field(default=False)
    provider: str = Field(default="openai")
    model: str = Field(default="gpt-3.5-turbo")
    api_key: str | None = Field(default=None)
    max_tokens: int = Field(default=1000)
    temperature: float = Field(default=0.7)


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


# Global settings instance
settings = Settings()
