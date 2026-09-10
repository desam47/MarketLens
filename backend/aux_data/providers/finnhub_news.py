"""
Finnhub implementation of NewsProvider (2026-09-10).

Uses the free ``/company-news`` endpoint (plain HTTP, same style as
``backend/market_data/providers/finnhub_provider.py``). On ANY failure
this provider **raises** so ``AuxDataManager`` falls through to the
yfinance provider — the manager only advances the chain on an exception,
not on an empty response.
"""
import logging
from datetime import datetime, timedelta, timezone

import requests

from backend.config.settings import settings
from backend.models.aux_data import NewsItem, NewsResponse
from backend.utils.timezone import now_ny

from ..provider import NewsProvider

logger = logging.getLogger(__name__)

_URL = "https://finnhub.io/api/v1/company-news"
_LOOKBACK_DAYS = 7


class FinnhubNewsProvider(NewsProvider):
    def __init__(self) -> None:
        super().__init__("finnhub_news")
        self._api_key = settings.finnhub.api_key
        self._timeout = settings.finnhub.request_timeout

    def get_news(self, symbol: str, limit: int = 20) -> NewsResponse:
        sym = symbol.upper()
        if not self._api_key:
            self._mark_error(RuntimeError("no FINNHUB_API_KEY"))
            raise RuntimeError("FinnhubNewsProvider: no API key configured")

        today = datetime.now(timezone.utc).date()
        params = {
            "symbol": sym,
            "from": (today - timedelta(days=_LOOKBACK_DAYS)).isoformat(),
            "to": today.isoformat(),
            "token": self._api_key,
        }
        try:
            r = requests.get(_URL, params=params, timeout=self._timeout)
            if r.status_code == 429:
                raise RuntimeError("Finnhub rate limited (HTTP 429)")
            if not r.ok:
                raise RuntimeError(f"Finnhub company-news HTTP {r.status_code}: {r.text[:200]}")
            rows = r.json()
        except Exception as exc:
            logger.warning("FinnhubNewsProvider failed for %s: %s", sym, exc)
            self._mark_error(exc)
            raise

        if not isinstance(rows, list):
            self._mark_error(RuntimeError("unexpected payload"))
            raise RuntimeError("FinnhubNewsProvider: non-list company-news payload")

        rows.sort(key=lambda a: a.get("datetime", 0), reverse=True)
        items: list[NewsItem] = []
        for a in rows[:limit]:
            headline = (a.get("headline") or "").strip()
            if not headline:
                continue
            ts_raw = a.get("datetime")
            ts = (
                datetime.fromtimestamp(ts_raw, tz=timezone.utc)
                if isinstance(ts_raw, (int, float)) and ts_raw > 0
                else now_ny()
            )
            related = str(a.get("related") or "").upper().split(",")
            items.append(NewsItem(
                headline=headline,
                source=a.get("source") or "Finnhub",
                timestamp=ts,
                symbol=sym,
                relevance=0.75 if sym in related else 0.55,
            ))

        self._mark_ok()
        return NewsResponse(symbol=sym, items=items, provider=self.name, timestamp=now_ny())
