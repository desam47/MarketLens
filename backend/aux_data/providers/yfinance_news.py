"""
yfinance implementation of NewsProvider.

News is fetched from ``yf.Ticker.news``. As of yfinance 0.2.66 each
article is a nested ``{"id": ..., "content": {...}}`` envelope (the
flat ``title``/``publisher``/``providerPublishTime``/``relatedTickers``
shape from older yfinance versions is gone — Yahoo restructured this
endpoint upstream, unrelated to anything in this codebase). The
article's real fields live under ``content``: ``title``,
``provider.displayName`` (was ``publisher``), ``pubDate`` as an ISO
8601 string (was ``providerPublishTime`` as unix-ms). There is no
``relatedTickers``-equivalent in the new shape, so relevance always
falls back to the same neutral 0.5 the old code used when that field
was absent.
"""
import logging
from datetime import datetime

from backend.utils.timezone import now_ny

from backend.models.aux_data import NewsItem, NewsResponse

from ..provider import NewsProvider

logger = logging.getLogger(__name__)


class YFinanceNewsProvider(NewsProvider):
    """Yahoo Finance news via yfinance."""

    def __init__(self) -> None:
        super().__init__("yfinance_news")

    def get_news(self, symbol: str, limit: int = 20) -> NewsResponse:
        """Fetch recent news for ``symbol`` via yfinance."""
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol.upper())
            raw = ticker.news or []

            items: list[NewsItem] = []
            for article in raw[:limit]:
                # Nested shape (yfinance >= ~0.2.4x): the real fields
                # live under "content". Fall back to the article dict
                # itself so an older/differently-shaped payload still
                # degrades gracefully instead of producing all-blank
                # items.
                content = article.get("content") or article

                try:
                    pub_raw = content.get("pubDate")
                    pub_dt = (
                        datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                        if pub_raw
                        else now_ny()
                    )
                except Exception:
                    pub_dt = now_ny()

                provider_info = content.get("provider") or {}
                source = (
                    provider_info.get("displayName")
                    if isinstance(provider_info, dict)
                    else None
                ) or content.get("publisher") or "Unknown"

                # No related-tickers concept exists in the new payload
                # shape — always the same neutral score the old code
                # used whenever that data was unavailable.
                rel = 0.5

                url_info = content.get("canonicalUrl") or content.get("clickThroughUrl")
                article_url = (
                    url_info.get("url")
                    if isinstance(url_info, dict)
                    else url_info
                ) or content.get("link")

                items.append(
                    NewsItem(
                        headline=content.get("title", ""),
                        source=source,
                        timestamp=pub_dt,
                        symbol=symbol.upper(),
                        relevance=rel,
                        url=article_url or None,
                    )
                )

            self._mark_ok()
            return NewsResponse(
                symbol=symbol.upper(),
                items=items,
                provider=self.name,
                timestamp=now_ny(),
            )

        except Exception as exc:
            logger.warning("YFinanceNewsProvider failed for %s: %s", symbol, exc)
            self._mark_error(exc)
            return NewsResponse(
                symbol=symbol.upper(),
                items=[],
                provider=self.name,
                timestamp=now_ny(),
            )
