"""
Webull implementation of FundamentalProvider (2026-09-10).

``fundamentals.get_financials_indicators(symbol, "US_STOCK")`` returns

    {"currency": "US",
     "values": {
        "<metric_key>": [
            {"fiscal_year": 2026, "fiscal_period": 3, "value": "2.0244"},
            ...
        ],
        ...
     }}

Confirmed live (2026-09-10) — this endpoint only carries 8 per-share
ratio / margin metrics: ``cap_surplus_ps``, ``debt_to_assets``,
``diluted_eps_incl_extra``, ``naps`` (net asset per share),
``net_margin``, ``ocf_ps``, ``roa``, ``roe``. It does NOT provide market
cap, P/E, revenue, net income, dividend yield, beta or the 52-week
range, so it can't fully populate ``FundamentalsItem`` on its own —
yfinance stays the primary fundamentals source and this provider is
wired as a fallback. Bringing it forward would mean fanning out to
``get_financials_income`` / ``get_financials_balance_sheet`` + a
snapshot for price (3-4 calls/request against a rate-limited key).

We take the most recent quarter per metric (``_latest``). If nothing
numeric resolves the provider raises, so ``AuxDataManager`` advances the
chain (it only falls through on an exception, never on an empty item).
"""

import logging

from pydantic import ValidationError

from backend.models.aux_data import FundamentalsItem, FundamentalsResponse
from backend.utils.timezone import now_ny

from ..provider import FundamentalProvider

logger = logging.getLogger(__name__)

# FundamentalsItem field -> Webull metric key(s), from the confirmed
# get_financials_indicators key set. Only `eps` maps cleanly; the rest
# of that endpoint's metrics (roe / roa / net_margin / naps / ocf_ps /
# debt_to_assets / cap_surplus_ps) have no FundamentalsItem home.
_METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "eps": ("diluted_eps_incl_extra", "basic_eps_incl_extra"),
}


def _latest(series) -> float | None:
    """Pick the newest {fiscal_year, fiscal_period, value} entry -> float."""
    if not isinstance(series, list) or not series:
        return None
    try:
        best = max(
            (e for e in series if isinstance(e, dict) and e.get("value") not in (None, "", "-")),
            key=lambda e: (e.get("fiscal_year", 0), e.get("fiscal_period", 0)),
            default=None,
        )
        if best is None:
            return None
        v = float(best["value"])
        return None if v != v else v
    except (TypeError, ValueError):
        return None


class WebullFundamentalsProvider(FundamentalProvider):
    def __init__(self) -> None:
        super().__init__("webull_fundamentals")

    def _provider(self):
        from backend.market_data.services.manager import get_cached_provider

        provider = get_cached_provider("webull")
        if provider is None or not hasattr(provider, "get_financial_indicators"):
            raise RuntimeError("Webull provider unavailable (not enabled / not credentialed)")
        return provider

    def get_fundamentals(self, symbol: str) -> FundamentalsResponse:
        from backend.market_data.services.providers import _call_provider

        sym = symbol.upper()
        values: dict = {}
        issuer: str | None = None
        errors = []
        try:
            provider = self._provider()
            # Routed through _call_provider (not provider._data_client
            # directly) so fundamentals calls share Webull's per-provider
            # rate limiter/circuit breaker with quotes/bars/tape ticks,
            # instead of bypassing it.
            try:
                resp = _call_provider(provider, "get_financial_indicators", sym)
                if getattr(resp, "status_code", 200) == 200:
                    body = resp.json()
                    values = body.get("values", body) if isinstance(body, dict) else {}
            except Exception as e:  # noqa: BLE001
                errors.append(f"indicators: {e}")
            try:
                resp = _call_provider(provider, "get_fund_brief", sym)
                if getattr(resp, "status_code", 200) == 200:
                    b = resp.json()
                    issuer = b.get("issuer") or b.get("name") if isinstance(b, dict) else None
            except Exception as e:  # noqa: BLE001
                errors.append(f"brief: {e}")
        except Exception as exc:
            self._mark_error(exc)
            raise

        if not values and not issuer:
            exc = RuntimeError("Webull fundamentals: " + ("; ".join(errors) or "empty response"))
            self._mark_error(exc)
            raise exc

        kwargs: dict = {
            "symbol": sym,
            "provider": self.name,
            "timestamp": now_ny(),
            "company_name": issuer,
        }
        for field, keys in _METRIC_KEYS.items():
            for k in keys:
                if k in values:
                    val = _latest(values[k])
                    if val is not None:
                        kwargs[field] = val
                        break

        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        kwargs.setdefault("symbol", sym)

        numeric = sum(
            1 for k, v in kwargs.items() if k != "timestamp" and isinstance(v, (int, float))
        )
        if numeric == 0:
            exc = RuntimeError(
                f"Webull fundamentals: no usable numeric fields "
                f"(metric keys present: {sorted(values)[:16]})"
            )
            self._mark_error(exc)
            raise exc

        item = self._build_resilient(kwargs)
        self._mark_ok()
        return FundamentalsResponse(symbol=sym, data=item, provider=self.name, timestamp=now_ny())

    @staticmethod
    def _build_resilient(kwargs: dict) -> FundamentalsItem:
        try:
            return FundamentalsItem(**kwargs)
        except ValidationError as e:
            bad = {err["loc"][0] for err in e.errors() if err.get("loc")}
            cleaned = {k: (None if k in bad else v) for k, v in kwargs.items()}
            try:
                return FundamentalsItem(**cleaned)
            except ValidationError:
                return FundamentalsItem(symbol=str(kwargs["symbol"]))
