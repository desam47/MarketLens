"""Grounded, provider-free Watchlist Intelligence aggregation.

The Scanner owns the expensive work of producing :class:`ScanResult` objects.
This module only reads those results (and, when the caller supplies them, local
session-price snapshots) to make a deterministic briefing.  It deliberately
does not initiate a scan, fetch a quote, or ask an AI model to infer a market
claim.  That keeps a Watchlist refresh to one scanner pass while making every
briefing section reproducible from its returned data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from backend.regime.sector_engine import SECTOR_MAP
from backend.scanner.ranking import RankingEngine

if TYPE_CHECKING:
    from backend.scanner.scanner import ScanResult


_TOP_N = 5
_VOLUME_SPIKE_RATIO = 1.5
_MTF_MIN_CONFIRMATIONS = 3


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _price_view(
    result: ScanResult,
    session_snapshots: Mapping[str, Mapping[str, Any]],
    *,
    session_scope: str,
) -> dict[str, Any]:
    """Return the price/change representation selected by the user.

    ``all`` deliberately uses the current scanner quote; one or more selected
    sessions use the local-bar snapshot returned by the existing session-price
    endpoint.  With no selected session we withhold price-direction rankings
    rather than silently falling back to a different scope.
    """
    if session_scope == "none":
        return {"price": None, "change_pct": None, "timestamp": None, "session": None}
    snapshot = session_snapshots.get(result.symbol.upper())
    if snapshot is not None:
        return {
            "price": _number(snapshot.get("price")),
            "change_pct": _number(snapshot.get("change_pct")),
            "timestamp": snapshot.get("timestamp"),
            "session": snapshot.get("session"),
        }
    quote = result.quote
    return {
        "price": _number(getattr(quote, "price", None)),
        "change_pct": _number(result.change_pct),
        "timestamp": getattr(quote, "timestamp", None) or result.timestamp,
        "session": "all" if session_scope == "all" else None,
    }


def _entry(
    result: ScanResult,
    session_snapshots: Mapping[str, Mapping[str, Any]],
    *,
    session_scope: str,
    metric: float | None = None,
    metric_label: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    view = _price_view(result, session_snapshots, session_scope=session_scope)
    return {
        "symbol": result.symbol.upper(),
        "price": view["price"],
        "change_pct": view["change_pct"],
        "timestamp": view["timestamp"].isoformat() if hasattr(view["timestamp"], "isoformat") else view["timestamp"],
        "session": view["session"],
        "score": round(float(result.calculate_total_score()), 4),
        "signals": list(result.signals or []),
        "metric": round(metric, 4) if metric is not None else None,
        "metric_label": metric_label,
        "details": dict(details or {}),
    }


def _relative_strength(result: ScanResult, benchmark_symbol: str | None = None) -> tuple[str, float] | None:
    values = {
        key.removeprefix("rs_pct_"): float(value)
        for key, value in result.indicator_values.items()
        if key.startswith("rs_pct_") and isinstance(value, (int, float))
    }
    if benchmark_symbol:
        benchmark = benchmark_symbol.upper()
        value = values.get(benchmark)
        return (benchmark, value) if value is not None else None
    if values:
        return max(values.items(), key=lambda item: item[1])
    fallback = _number(result.indicator_values.get("relative_strength"))
    return ("configured benchmark", fallback) if fallback is not None else None


def _mtf_counts(result: ScanResult) -> tuple[int, int]:
    bullish = bearish = 0
    for signal in result.trend_signals.values():
        if not isinstance(signal, Mapping) or _number(signal.get("confidence")) is None:
            continue
        if float(signal["confidence"]) < 0.5:
            continue
        direction = str(signal.get("direction", "")).lower()
        bullish += direction == "uptrend"
        bearish += direction == "downtrend"
    return bullish, bearish


def _directional_score(result: ScanResult) -> float | None:
    """Return a comparable directional score when scanner inputs exist."""
    scores = result.scores
    values = [
        float(scores[key])
        for key in ("momentum", "macd", "rsi")
        if isinstance(scores.get(key), (int, float))
    ]
    return sum(values) / len(values) if values else None


def build_watchlist_intelligence(
    results: list[ScanResult],
    *,
    watchlist_size: int,
    timeframe: str = "1d",
    session_scope: str = "all",
    session_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
    missing_session_symbols: list[str] | None = None,
    top_n: int = _TOP_N,
    benchmark_symbol: str | None = None,
) -> dict[str, Any]:
    """Create one exact, bounded briefing from already-computed scan data."""
    session_snapshots = {key.upper(): value for key, value in (session_snapshots or {}).items()}
    missing_session_symbols = sorted({symbol.upper() for symbol in (missing_session_symbols or [])})
    ready_count = len(results)
    status = "ready" if ready_count == watchlist_size else "partial" if ready_count else "warming"

    price_entries = [
        (result, _price_view(result, session_snapshots, session_scope=session_scope))
        for result in results
    ]
    bullish = sorted(
        ((result, view) for result, view in price_entries if (view["change_pct"] or 0) > 0),
        key=lambda item: float(item[1]["change_pct"]),
        reverse=True,
    )[:top_n]
    bearish = sorted(
        ((result, view) for result, view in price_entries if (view["change_pct"] or 0) < 0),
        key=lambda item: float(item[1]["change_pct"]),
    )[:top_n]

    breakouts = [
        result for result in results
        if result.indicator_values.get("breakout_20") is True or "BREAKOUT_20" in (result.signals or [])
    ]
    breakouts.sort(
        key=lambda result: _number(result.indicator_values.get("breakout_pct_20")) or 0,
        reverse=True,
    )

    volume_spikes = [
        result for result in results
        if (_number(result.indicator_values.get("volume_ratio")) or 0) >= _VOLUME_SPIKE_RATIO
    ]
    volume_spikes.sort(
        key=lambda result: _number(result.indicator_values.get("volume_ratio")) or 0,
        reverse=True,
    )

    relative_strength = [
        (result, value) for result in results if (value := _relative_strength(result, benchmark_symbol)) is not None
    ]
    relative_strength.sort(key=lambda item: item[1][1], reverse=True)

    mtf_alignment: list[tuple[ScanResult, int, int]] = []
    for result in results:
        bullish_count, bearish_count = _mtf_counts(result)
        if max(bullish_count, bearish_count) >= _MTF_MIN_CONFIRMATIONS:
            mtf_alignment.append((result, bullish_count, bearish_count))
    mtf_alignment.sort(key=lambda item: (max(item[1], item[2]), item[1] - item[2]), reverse=True)

    rankings = RankingEngine().rank(results, top_n=top_n)
    result_by_symbol = {result.symbol.upper(): result for result in results}
    deterioration = []
    for rank in rankings["biggest_deterioration"].entries:
        result = result_by_symbol.get(rank.symbol.upper())
        if result is not None:
            deterioration.append(_entry(
                result,
                session_snapshots,
                session_scope=session_scope,
                metric=rank.score,
                metric_label="deterioration score",
                details=rank.metrics,
            ))

    # A name can be relatively weakest even when every name is up on the
    # selected day and none qualifies for the explicitly bearish or
    # deteriorating buckets. Keep this as a separate fallback so callers can
    # distinguish relative weakness from an outright bearish move.
    weakest = []
    for result in results:
        score = _directional_score(result)
        if score is None:
            continue
        weakest.append(_entry(
            result,
            session_snapshots,
            session_scope=session_scope,
            metric=score,
            metric_label="directional score",
            details={"directional_score": score, "relative_only": True},
        ))
    weakest.sort(key=lambda item: float(item.get("metric") or 0))
    weakest = weakest[:top_n]

    # API-mode/cache snapshots can carry trend signals while omitting the
    # composite indicator scores. In that case, use the signed MTF direction
    # as the evidence-backed fallback instead of treating full coverage as an
    # empty answer.
    if not weakest:
        for result in results:
            bullish_count, bearish_count = _mtf_counts(result)
            if not bullish_count and not bearish_count:
                continue
            entry = _entry(
                result,
                session_snapshots,
                session_scope=session_scope,
                metric=float(bullish_count - bearish_count),
                metric_label="multi-timeframe direction score",
                details={
                    "bullish_timeframes": bullish_count,
                    "bearish_timeframes": bearish_count,
                    "relative_only": True,
                },
            )
            # This cache path has no composite score; do not render the
            # zero-value placeholder beside the meaningful MTF metric.
            entry["score"] = None
            weakest.append(entry)
        weakest.sort(key=lambda item: float(item.get("metric") or 0))
        weakest = weakest[:top_n]

    sector_groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    unmapped_sector_symbols = 0
    for result, view in price_entries:
        change_pct = _number(view["change_pct"])
        if change_pct is None:
            continue
        sector = SECTOR_MAP.get(result.symbol.upper())
        if sector is None:
            unmapped_sector_symbols += 1
            continue
        sector_groups[sector].append((result.symbol.upper(), change_pct))
    sector_rotation = [
        {
            "sector": sector,
            "average_change_pct": round(sum(change for _, change in members) / len(members), 4),
            "advancing": sum(change > 0 for _, change in members),
            "declining": sum(change < 0 for _, change in members),
            "symbols": sorted(symbol for symbol, _ in members),
        }
        for sector, members in sector_groups.items()
    ]
    sector_rotation.sort(key=lambda item: item["average_change_pct"], reverse=True)

    warnings: list[str] = []
    if status != "ready":
        warnings.append(f"Only {ready_count} of {watchlist_size} enabled symbols have a current scanner result.")
    if session_scope == "none":
        warnings.append("No market session is selected, so price-move rankings are withheld.")
    elif session_scope != "all" and missing_session_symbols:
        warnings.append(
            f"No selected-session bar is available for {len(missing_session_symbols)} symbol(s); their scanner snapshot may be used for non-price sections."
        )
    if unmapped_sector_symbols:
        warnings.append(
            f"Sector momentum covers the built-in sector map only; {unmapped_sector_symbols} symbol(s) with price data are unmapped."
        )

    return {
        "data_status": status,
        "watchlist_size": watchlist_size,
        "analyzed_symbols": ready_count,
        "timeframe": timeframe,
        "session_scope": session_scope,
        "price_basis": (
            "latest scanner quote and its scanner baseline"
            if session_scope == "all"
            else "latest selected-session 1-minute bar and its regular-session baseline"
            if session_scope != "none"
            else "no session selected"
        ),
        "missing_symbols": sorted({*missing_session_symbols, *(result.symbol.upper() for result in results if result.symbol.upper() not in session_snapshots and session_scope not in {"all", "none"})}),
        "top_bullish": [_entry(result, session_snapshots, session_scope=session_scope, metric=view["change_pct"], metric_label="change %") for result, view in bullish],
        "top_bearish": [_entry(result, session_snapshots, session_scope=session_scope, metric=view["change_pct"], metric_label="change %") for result, view in bearish],
        "breakouts": [_entry(result, session_snapshots, session_scope=session_scope, metric=_number(result.indicator_values.get("breakout_pct_20")), metric_label="20-bar breakout %") for result in breakouts[:top_n]],
        "deteriorating": deterioration,
        "weakest": weakest,
        "volume_spikes": [_entry(result, session_snapshots, session_scope=session_scope, metric=_number(result.indicator_values.get("volume_ratio")), metric_label="volume / trailing average") for result in volume_spikes[:top_n]],
        "relative_strength": [_entry(result, session_snapshots, session_scope=session_scope, metric=value[1], metric_label=f"relative strength vs {value[0]}", details={"benchmark": value[0]}) for result, value in relative_strength[:top_n]],
        "benchmark_symbol": benchmark_symbol.upper() if benchmark_symbol else None,
        "mtf_alignment": [_entry(result, session_snapshots, session_scope=session_scope, metric=float(max(bullish_count, bearish_count)), metric_label="confirmed timeframes", details={"direction": "bullish" if bullish_count > bearish_count else "bearish", "bullish_timeframes": bullish_count, "bearish_timeframes": bearish_count}) for result, bullish_count, bearish_count in mtf_alignment[:top_n]],
        "sector_rotation": sector_rotation[:top_n],
        "warnings": warnings,
    }


__all__ = ["build_watchlist_intelligence"]
