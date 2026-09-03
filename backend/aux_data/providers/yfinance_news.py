"""
yfinance implementation of NewsProvider.

News is fetched from ``yf.Ticker.news`` which returns a list of articles
with ``title``, ``publisher``, ``providerPublishTime`` and related symbols.
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
                try:
                    pub_ms = article.get("providerPublishTime", 0)
                    pub_dt = (
                        datetime.fromtimestamp(pub_ms, tz=now_ny().astimezone().tzinfo)
                        if pub_ms
                        else now_ny()
                    )
                except Exception:
                    pub_dt = now_ny()

                # relevance = number of related tickers that match the symbol,
                # capped at 1.0. If no related-tickers data, use a default of 0.5.
                related = article.get("relatedTickers") or []
                rel = min(len([t for t in related if t.upper() == symbol.upper()]) / max(len(related), 1), 1.0)
                if not related:
                    rel = 0.5  # unavailable — assign a neutral middle score

                items.append(
                    NewsItem(
                        headline=article.get("title", ""),
                        source=article.get("publisher", "Unknown"),
                        timestamp=pub_dt,
                        symbol=symbol.upper(),
                        relevance=rel,
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
