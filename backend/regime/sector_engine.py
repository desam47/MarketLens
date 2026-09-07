"""
Sector Engine — maps a symbol to its sector and computes alignment score.

Phase 8 spec: symbol → sector → sector ETF. Compare stock trend,
sector trend, and market (SPY) trend to produce an alignment score.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from ..trend.trend_engine import TrendEngine

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Static mapping: symbol → sector name
# ------------------------------------------------------------------

SECTOR_MAP: dict[str, str] = {
    # Technology
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Technology",
    "GOOG": "Technology",
    "NVDA": "Technology",
    "AMD": "Technology",
    "INTC": "Technology",
    "META": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "NFLX": "Communication Services",
    # Healthcare
    "JNJ": "Healthcare",
    "UNH": "Healthcare",
    "PFE": "Healthcare",
    "ABBV": "Healthcare",
    "MRK": "Healthcare",
    "LLY": "Healthcare",
    # Financials
    "JPM": "Financials",
    "BAC": "Financials",
    "WFC": "Financials",
    "GS": "Financials",
    "MS": "Financials",
    "V": "Financials",
    "MA": "Financials",
    # Energy
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "SLB": "Energy",
    # Consumer
    "WMT": "Consumer Staples",
    "PG": "Consumer Staples",
    "KO": "Consumer Staples",
    "PEP": "Consumer Staples",
    "COST": "Consumer Staples",
    # Industrials
    "CAT": "Industrials",
    "BA": "Industrials",
    "HON": "Industrials",
    "GE": "Industrials",
    "RTX": "Industrials",
    # Materials
    "LIN": "Materials",
    "APD": "Materials",
    "SHW": "Materials",
    # Utilities
    "NEE": "Utilities",
    "DUK": "Utilities",
    "SO": "Utilities",
    # Real Estate
    "AMT": "Real Estate",
    "PLD": "Real Estate",
    "EQIX": "Real Estate",
    # Communication Services
    "CMCSA": "Communication Services",
    "DIS": "Communication Services",
    # Consumer Discretionary
    "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary",
    # Broad market ETFs used as benchmarks
    "SPY": "Broad Market",
    "QQQ": "Technology",
    "IWM": "Small Cap",
    "^VIX": "Volatility",
}


# ------------------------------------------------------------------
# Sector → sector ETF mapping
# ------------------------------------------------------------------

SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Broad Market": "SPY",
    "Small Cap": "IWM",
    "Volatility": "^VIX",
}


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

class AlignmentLevel(StrEnum):
    PERFECT = "perfect"      # all 3 agree
    MAJORITY = "majority"    # 2/3 agree
    SPLIT = "split"          # 1/3
    CONFLICTING = "conflicting"  # no agreement


@dataclass
class SectorSignal:
    """Phase 8: sector alignment signal for a single symbol."""
    symbol: str
    sector: str
    sector_etf: str | None
    stock_trend: str          # TrendDirection.value string
    sector_trend: str        # TrendDirection.value string
    market_trend: str         # TrendDirection.value string
    alignment_score: float   # 0.0..1.0
    alignment_level: str     # AlignmentLevel.value
    contributing_factors: dict[str, Any]
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "sector_etf": self.sector_etf,
            "stock_trend": self.stock_trend,
            "sector_trend": self.sector_trend,
            "market_trend": self.market_trend,
            "alignment_score": round(self.alignment_score, 3),
            "alignment_level": self.alignment_level,
            "contributing_factors": self.contributing_factors,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# ------------------------------------------------------------------
# SectorEngine
# ------------------------------------------------------------------

class SectorEngine:
    """
    Runs three TrendEngines (stock, sector ETF, SPY) and produces
    an alignment score.

    Alignment score:
      1.0  — all 3 trends point the same direction
      0.5  — 2/3 agree
      0.0  — fully conflicting or insufficient data
    """

    def __init__(self, symbol: str,
                 stock_engine: TrendEngine | None = None,
                 sector_engine: TrendEngine | None = None,
                 market_engine: TrendEngine | None = None):
        self.symbol = symbol.upper()
        self.sector = SECTOR_MAP.get(self.symbol, "Unknown")
        self.sector_etf = SECTOR_ETFS.get(self.sector)

        # Phase 3.9.2: accept injected engines to share with other callers
        # (e.g. the regime engine). All three engine types (stock, sector
        # ETF, SPY) are looked up via the shared registry so any other
        # component that also needs SPY or XLK gets the same instance.
        self._stock_eng = stock_engine if stock_engine is not None else TrendEngine(self.symbol)
        self._sector_eng = sector_engine if sector_engine is not None else (
            TrendEngine(self.sector_etf) if self.sector_etf else None
        )
        self._market_eng = market_engine if market_engine is not None else TrendEngine("SPY")

        self._signals: list[SectorSignal] = []

    def update(self,
               price: float,
               volume: float,
               timestamp: datetime,
               provider: str = "internal") -> None:
        """Feed a price tick into the appropriate TrendEngine."""
        self._stock_eng.update(price, volume, timestamp, provider)
        if self._sector_eng:
            self._sector_eng.update(price, volume, timestamp, provider)
        self._market_eng.update(price, volume, timestamp, provider)

    def update_all(self,
                   prices: dict[str, float],
                   volume: float,
                   timestamp: datetime) -> None:
        """Feed prices for all three engines at once."""
        for sym, price in prices.items():
            if sym == self.symbol:
                self._stock_eng.update(price, volume, timestamp, "internal")
            elif self._sector_eng and sym == self.sector_etf:
                self._sector_eng.update(price, volume, timestamp, "internal")
            elif sym == "SPY":
                self._market_eng.update(price, volume, timestamp, "internal")

    def get_current_signal(self,
                           timestamp: datetime | None = None) -> SectorSignal:
        """Compute and return the current sector signal."""
        ts = timestamp or datetime.now(timezone.utc)

        stock_trend = self._get_trend_str(self._stock_eng)
        sector_trend = self._get_trend_str(self._sector_eng) if self._sector_eng else "unknown"
        market_trend = self._get_trend_str(self._market_eng)

        score, level, factors = self._compute_alignment(
            stock_trend, sector_trend, market_trend
        )

        signal = SectorSignal(
            symbol=self.symbol,
            sector=self.sector,
            sector_etf=self.sector_etf,
            stock_trend=stock_trend,
            sector_trend=sector_trend,
            market_trend=market_trend,
            alignment_score=score,
            alignment_level=level,
            contributing_factors=factors,
            timestamp=ts,
        )

        self._signals.append(signal)
        return signal

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_trend_str(self, eng: TrendEngine | None) -> str:
        """Extract direction string from a TrendEngine's overall trend."""
        if eng is None:
            return "unknown"
        trend = eng.get_overall_trend()
        if trend is None:
            return "unknown"
        return trend.direction.value

    def _compute_alignment(
        self,
        stock: str,
        sector: str,
        market: str,
    ) -> tuple[float, str, dict[str, Any]]:
        """Compute alignment score + level from three direction strings."""
        factors: dict[str, Any] = {}
        unknown_count = sum(1 for v in (stock, sector, market) if v == "unknown")

        if unknown_count >= 2:
            factors["reason"] = "insufficient_data"
            return 0.0, AlignmentLevel.CONFLICTING.value, factors

        directions = [v for v in (stock, sector, market) if v != "unknown"]

        # Count agreement
        up = sum(1 for d in directions if d in ("uptrend", "strong_uptrend"))
        down = sum(1 for d in directions if d in ("downtrend", "strong_downtrend"))

        if up == len(directions):
            factors["agreement"] = "all_uptrend"
            return 1.0, AlignmentLevel.PERFECT.value, factors
        if down == len(directions):
            factors["agreement"] = "all_downtrend"
            return 1.0, AlignmentLevel.PERFECT.value, factors

        # 2/3 agreement
        if up >= 2:
            factors["agreement"] = "majority_uptrend"
            return 0.67, AlignmentLevel.MAJORITY.value, factors
        if down >= 2:
            factors["agreement"] = "majority_downtrend"
            return 0.67, AlignmentLevel.MAJORITY.value, factors

        # Fully split
        factors["agreement"] = "fully_split"
        return 0.0, AlignmentLevel.CONFLICTING.value, factors

    def get_signal_history(self, limit: int | None = None) -> list[SectorSignal]:
        if limit is None:
            return self._signals.copy()
        return self._signals[-limit:] if len(self._signals) > limit else self._signals.copy()
