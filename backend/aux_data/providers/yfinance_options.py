"""
yfinance implementation of OptionsProvider.

Options chains are fetched from ``yf.Ticker.option_chain(date)`` which
returns a ``(calls_df, puts_df)`` tuple. Each DataFrame has the standard
yfinance schema:

  contractSymbol, strike, currency, lastPrice, change, percentChange,
  volume, openInterest, bid, ask, contractSize, expiration,
  lastTradeDate, impliedVolatility, inTheMoney, category

Greeks (delta/gamma/theta/vega/rho) are not exposed by the public
endpoint; they're left as ``None`` in the response. Put/call ratio,
average IV, total volume, and unusual-activity classification are all
computed from the raw DataFrames in this module.
"""

import logging

import pandas as pd

from backend.models.aux_data import (
    OptionContract,
    OptionsChain,
    OptionsResponse,
    OptionsType,
    UnusualActivity,
)
from backend.utils.timezone import now_ny

from ..provider import OptionsProvider

logger = logging.getLogger(__name__)


class YFinanceOptionsProvider(OptionsProvider):
    """Yahoo Finance options via yfinance."""

    def __init__(self) -> None:
        super().__init__("yfinance_options")

    def get_options(self, symbol: str, expiration: str | None = None) -> OptionsResponse:
        """Fetch options chain(s) for ``symbol``.

        If ``expiration`` is provided, return only that chain; otherwise
        return all available expirations (up to 4 to keep response size
        reasonable — most clients only display the front 1–3 anyway).
        """
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol.upper())
            all_expirations: tuple[str, ...] = tuple(ticker.options or [])

            target_expirations = (
                [expiration]
                if expiration and expiration in all_expirations
                else list(all_expirations[:4])
            )

            chains: list[OptionsChain] = []
            for exp in target_expirations:
                try:
                    chain = ticker.option_chain(exp)
                    chains.append(self._build_chain(symbol, exp, chain.calls, chain.puts))
                except Exception as exc:
                    logger.warning(
                        "yfinance option_chain failed for %s %s: %s",
                        symbol,
                        exp,
                        exc,
                    )
                    continue

            near_term_iv = self._compute_near_term_iv(chains)
            iv_rank = self._estimate_iv_rank(chains)

            self._mark_ok()
            return OptionsResponse(
                symbol=symbol.upper(),
                chains=chains,
                expirations=list(all_expirations),
                near_term_iv=near_term_iv,
                iv_rank=iv_rank,
                provider=self.name,
                timestamp=now_ny(),
            )

        except Exception as exc:
            logger.warning("YFinanceOptionsProvider failed for %s: %s", symbol, exc)
            self._mark_error(exc)
            return OptionsResponse(
                symbol=symbol.upper(),
                provider=self.name,
                timestamp=now_ny(),
            )

    # ------------------------------------------------------------------ helpers
    def _build_chain(
        self,
        symbol: str,
        expiration: str,
        calls_df: pd.DataFrame,
        puts_df: pd.DataFrame,
    ) -> OptionsChain:
        """Convert a (calls_df, puts_df) pair into an OptionsChain."""
        calls = [self._to_contract(row, OptionsType.CALL) for _, row in calls_df.iterrows()]
        puts = [self._to_contract(row, OptionsType.PUT) for _, row in puts_df.iterrows()]

        call_oi = sum(c.open_interest or 0 for c in calls)
        put_oi = sum(c.open_interest or 0 for c in puts)
        call_vol = sum(c.volume or 0 for c in calls)
        put_vol = sum(c.volume or 0 for c in puts)

        put_call_ratio = round(put_oi / call_oi, 4) if call_oi > 0 else None
        avg_iv_call = self._avg([c.implied_volatility for c in calls])
        avg_iv_put = self._avg([c.implied_volatility for c in puts])
        unusual = self._classify_unusual(call_vol, call_oi)

        return OptionsChain(
            symbol=symbol.upper(),
            expiration=expiration,
            calls=calls,
            puts=puts,
            put_call_ratio=put_call_ratio,
            total_call_volume=call_vol or None,
            total_put_volume=put_vol or None,
            avg_iv_call=avg_iv_call,
            avg_iv_put=avg_iv_put,
            unusual_activity=unusual,
        )

    @staticmethod
    def _to_contract(row: pd.Series, option_type: OptionsType) -> OptionContract:
        """Convert a single row from the yfinance DataFrame."""
        return OptionContract(
            strike=float(row.get("strike", 0.0) or 0.0),
            expiration=str(row.get("expiration", "")),
            option_type=option_type,
            bid=OptionContract.model_fields["bid"].annotation and _safe_float(row.get("bid")),
            ask=_safe_float(row.get("ask")),
            last=_safe_float(row.get("lastPrice")),
            volume=int(row.get("volume") or 0) or None,
            open_interest=int(row.get("openInterest") or 0) or None,
            implied_volatility=_safe_float(row.get("impliedVolatility")),
            in_the_money=bool(row.get("inTheMoney", False)),
        )

    @staticmethod
    def _avg(values: list[float | None]) -> float | None:
        cleaned = [v for v in values if v is not None]
        if not cleaned:
            return None
        return round(sum(cleaned) / len(cleaned), 4)

    @staticmethod
    def _classify_unusual(volume: int, oi: int) -> UnusualActivity:
        """Volume-vs-open-interest heuristic.

        Without historical data we can't compute a 30-day baseline; this
        rough rule is good enough for the spec — high volume with low
        OI suggests new positioning. Values are conservative.
        """
        if volume <= 0 or oi <= 0:
            return UnusualActivity.NORMAL
        ratio = volume / oi
        if ratio >= 5.0:
            return UnusualActivity.UNUSUAL
        if ratio >= 2.0:
            return UnusualActivity.HIGH
        if ratio >= 1.0:
            return UnusualActivity.ELEVATED
        return UnusualActivity.NORMAL

    @staticmethod
    def _compute_near_term_iv(chains: list[OptionsChain]) -> float | None:
        """Average IV of the first chain (assumed near-term)."""
        if not chains:
            return None
        first = chains[0]
        # Average of call and put IV for the nearest expiration.
        if first.avg_iv_call and first.avg_iv_put:
            return round((first.avg_iv_call + first.avg_iv_put) / 2, 4)
        return first.avg_iv_call or first.avg_iv_put

    @staticmethod
    def _estimate_iv_rank(chains: list[OptionsChain]) -> float | None:
        """Estimate IV rank 0–100 by averaging IV across all available chains.

        Without 52-week high/low IV history, the simplest defensible
        estimate is to map the average IV to a 0-100 scale using 0.5 as
        the lower anchor and 1.5 as the upper anchor (IV > 1.5 is rare
        and typically signals a crisis-level event).
        """
        all_ivs: list[float] = []
        for c in chains:
            for v in (c.avg_iv_call, c.avg_iv_put):
                if v is not None:
                    all_ivs.append(v)
        if not all_ivs:
            return None
        avg_iv = sum(all_ivs) / len(all_ivs)
        # Map [0.5, 1.5] → [0, 100], clamp outside the range.
        rank = (avg_iv - 0.5) / 1.0 * 100.0
        return round(max(0.0, min(100.0, rank)), 1)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None
