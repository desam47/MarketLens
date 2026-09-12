"""
Phase 16 — Structured context builder for the AI analysis prompt.

The context is a JSON-serialisable dict that captures everything the
quantitative engine already knows about a symbol. Per the spec:

> AI must NEVER directly calculate raw indicators if the application
> already has the calculation. Instead send structured context.

The builder reads from the existing engine singletons and bundles
the data the prompt template will turn into a natural-language
question. If any of the required components are unavailable (e.g.
a cold-start symbol with no bars), ``build_context()`` raises
``InsufficientDataError`` so the caller can return an
``UncertaintyResponse`` rather than asking the AI to hallucinate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.scanner.scanner import market_scanner


class InsufficientDataError(RuntimeError):
    """Raised when the quant engine doesn't have enough data to ask
    the AI a question. The caller should return an uncertainty
    response, never an AI answer."""


@dataclass
class AnalysisContext:
    """All structured inputs to the AI prompt.

    Field names map directly to the JSON keys the prompt template
    expects (see :mod:`backend.ai.prompt`).
    """

    symbol: str
    timeframe: str
    price: float | None
    timestamp: str | None
    data_status: str  # "live" | "stale" | "unknown"
    timeframe_scores: dict[str, Any] = field(default_factory=dict)
    trend_state: dict[str, Any] = field(default_factory=dict)
    market_structure: dict[str, Any] = field(default_factory=dict)
    market_regime: dict[str, Any] = field(default_factory=dict)
    relative_strength: dict[str, Any] = field(default_factory=dict)
    sector_alignment: dict[str, Any] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    momentum: dict[str, Any] = field(default_factory=dict)
    support_resistance: dict[str, Any] = field(default_factory=dict)
    trend_transition: dict[str, Any] = field(default_factory=dict)
    historical_signal_stats: dict[str, Any] = field(default_factory=dict)
    news: list[dict[str, Any]] = field(default_factory=list)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    divergence: dict[str, Any] = field(default_factory=dict)
    tape: dict[str, Any] = field(default_factory=dict)
    track_record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price": self.price,
            "timestamp": self.timestamp,
            "data_status": self.data_status,
            "timeframe_scores": self.timeframe_scores,
            "trend_state": self.trend_state,
            "market_structure": self.market_structure,
            "market_regime": self.market_regime,
            "relative_strength": self.relative_strength,
            "sector_alignment": self.sector_alignment,
            "volume": self.volume,
            "momentum": self.momentum,
            "support_resistance": self.support_resistance,
            "trend_transition": self.trend_transition,
            "historical_signal_stats": self.historical_signal_stats,
            "news": self.news,
            "fundamentals": self.fundamentals,
            "divergence": self.divergence,
            "tape": self.tape,
            "track_record": self.track_record,
        }


def _safe_call(fn, *args, default=None, **kwargs):
    """Call a quant engine function and return its result, swallowing
    any exception. Used so that a single broken sub-engine doesn't
    take the whole context down — the AI just sees a partial picture."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return default


def _regime_value(enum_or_str) -> str:
    """Coerce a regime enum or string to a plain string."""
    if hasattr(enum_or_str, "value"):
        return enum_or_str.value
    return str(enum_or_str)


def _sig_to_dict(sig) -> dict[str, Any]:
    """Serialize a signal dataclass to a dict, stripping private fields."""
    if sig is None:
        return {}
    result = {}
    for k, v in vars(sig).items():
        if k.startswith("_"):
            continue
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        elif hasattr(v, "value"):  # enum
            result[k] = v.value
        else:
            result[k] = v
    return result


def build_context(
    symbol: str,
    timeframe: str = "1d",
    *,
    include_news: bool = True,
    include_fundamentals: bool = True,
    include_divergence: bool = True,
) -> AnalysisContext:
    """Gather a structured context dict for ``symbol``.

    ``timeframe`` is the primary analysis window. Cross-timeframe
    scores come from the scanner's MTF result; everything else is
    taken from the most recent engine state.

    ``include_news``/``include_fundamentals``/``include_divergence``
    default to ``True`` for a single-symbol analysis call, but can be
    turned off by callers that build context for many symbols at once
    (e.g. a digest iterating the whole watchlist) to skip the extra
    aux-data I/O per symbol.

    Raises ``InsufficientDataError`` when there's no quote and no
    trend signal — the caller should return an uncertainty response.
    """
    sym = symbol.upper()
    tf = timeframe.upper()

    # --- 1. Single-symbol scan (complete snapshot including MTF scores) ---
    try:
        scan = market_scanner.scan_symbol(sym)
    except Exception as e:
        raise InsufficientDataError(f"scan failed for {sym}: {e}") from e

    if scan is None:
        raise InsufficientDataError(f"no scan result for {sym}")

    quote = scan.quote
    # quote is a Pydantic model (or None)
    if quote is not None:
        price = quote.price
        ts = quote.timestamp
    else:
        price = None
        ts = scan.timestamp

    if price is None:
        raise InsufficientDataError(f"no price available for {sym}")

    # --- 2. MTF trend signals ---
    mtf = scan.trend_signals or {}
    timeframe_scores: dict[str, Any] = {}
    for tf_key, tsig in mtf.items():
        # trend_signals is a dict-of-dicts in the scanner API
        if hasattr(tsig, "direction"):
            timeframe_scores[tf_key] = {
                "direction": _regime_value(tsig.direction),
                "strength": _regime_value(tsig.strength),
                "confidence": round(float(tsig.confidence), 2),
            }
        elif isinstance(tsig, dict):
            timeframe_scores[tf_key] = {
                "direction": _regime_value(tsig.get("direction", "unknown")),
                "strength": _regime_value(tsig.get("strength", "unknown")),
                "confidence": round(float(tsig.get("confidence", 0)), 2),
            }

    # Primary signal for the requested timeframe
    primary_sig = mtf.get(tf)
    if primary_sig is None:
        primary_sig = next(iter(mtf.values()), None)

    trend_state: dict[str, Any] = {}
    if primary_sig is not None:
        if hasattr(primary_sig, "direction"):
            trend_state = {
                "direction": _regime_value(primary_sig.direction),
                "strength": _regime_value(primary_sig.strength),
                "confidence": round(float(primary_sig.confidence), 2),
            }
        elif isinstance(primary_sig, dict):
            trend_state = {
                "direction": _regime_value(primary_sig.get("direction", "unknown")),
                "strength": _regime_value(primary_sig.get("strength", "unknown")),
                "confidence": round(float(primary_sig.get("confidence", 0)), 2),
            }

    # --- 3. Market regime (global) ---
    market_regime: dict[str, Any] = {}
    try:
        from backend.regime.market_regime_engine import MarketRegimeEngine

        engine = MarketRegimeEngine(sym)
        regime_sig = engine.get_current_regime()
        if regime_sig is not None:
            market_regime = _sig_to_dict(regime_sig)
    except Exception:  # noqa: BLE001
        pass

    # --- 4. Relative strength (per-symbol, one per benchmark) ---
    rs_list: list[dict[str, Any]] = []
    try:
        from backend.regime.relative_strength_engine import RelativeStrengthEngine

        rs_engine = RelativeStrengthEngine(sym)
        signals = rs_engine.get_signals()
        rs_list = [_sig_to_dict(s) for s in signals]
    except Exception:  # noqa: BLE001
        pass

    # Pick the primary benchmark (SPY) for the display
    primary_rs: dict[str, Any] = {}
    for rs in rs_list:
        if rs.get("benchmark") in ("SPY", "QQQ"):
            primary_rs = rs
            break
    if not primary_rs and rs_list:
        primary_rs = rs_list[0]

    # --- 5. Sector alignment ---
    # Found live 2026-09-10: SectorEngine(sym) with no injected engines
    # builds three brand-new, never-fed TrendEngine instances (stock,
    # sector ETF, SPY) from scratch — zero seed data, zero ticks — so
    # get_overall_trend() is always None and every alignment came back
    # "unknown"/"insufficient_data" regardless of how much real trend
    # data actually existed. SectorEngine's own docstring says exactly
    # this: "accept injected engines to share with other callers ...
    # looked up via the shared registry so any other component that
    # also needs SPY or XLK gets the same instance" — but neither real
    # call site in the app (this one, and backend/api/regime/router.py)
    # was actually doing that injection. Fixed by pulling the same
    # shared, DB-seeded TrendEngine singletons every other feature
    # already uses (backend.api.trend.registry.get_engine — the exact
    # registry the trend_transition section above already imports).
    sector_alignment: dict[str, Any] = {}
    try:
        from backend.api.trend.registry import get_engine as get_trend_engine_for_sector
        from backend.regime.sector_engine import SECTOR_ETFS, SECTOR_MAP, SectorEngine

        sector_name = SECTOR_MAP.get(sym, "Unknown")
        sector_etf = SECTOR_ETFS.get(sector_name)
        sector_engine = SectorEngine(
            sym,
            stock_engine=get_trend_engine_for_sector(sym),
            sector_engine=get_trend_engine_for_sector(sector_etf) if sector_etf else None,
            market_engine=get_trend_engine_for_sector("SPY"),
        )
        sector_sig = sector_engine.get_current_signal()
        if sector_sig is not None:
            sector_alignment = _sig_to_dict(sector_sig)
    except Exception:  # noqa: BLE001
        pass

    # --- 6. Volume / momentum from indicator values ---
    ind = scan.indicator_values or {}
    volume: dict[str, Any] = {
        "rvol": ind.get("rvol"),
        "volume_state": ind.get("volume_state"),
    }
    momentum: dict[str, Any] = {
        "rsi": ind.get("rsi"),
        "macd_hist": ind.get("macd_hist"),
    }

    # --- 7. Support / resistance ---
    # Found live 2026-09-10: this imported a `support_resistance_engine`
    # singleton from a module that doesn't exist
    # (backend.support_resistance.support_resistance_engine) and called
    # a `.detect_levels(sym, timeframe)` method that doesn't exist
    # either — the real module is backend.support_resistance.sr_engine
    # (re-exported as backend.support_resistance.SupportResistanceEngine),
    # a class with a `.detect(bars, symbol, timeframe)` method that
    # returns a flat `.levels` list, not separate `.supports`/
    # `.resistances` attributes. Same failure class as the
    # trend_transition bug fixed elsewhere in this file: a broad except
    # silently swallowed the AttributeError/ImportError every time, so
    # `support_resistance` had been an empty dict in every AI context
    # ever built. Fixed by calling the engine the way the existing,
    # working `/api/analysis/{symbol}/price-range` endpoint
    # does (backend/api/analysis/router.py) — same bar source, same
    # engine construction — then bucketing the flat level list into
    # supports/resistances by price relative to the latest close
    # (a level below current price is support, above is resistance;
    # `.levels` is pre-sorted strongest-first so each bucket keeps
    # that relative order).
    sr: dict[str, Any] = {}
    try:
        from backend.analysis.series import load_bars as _load_bars_for_sr
        from backend.analysis.series import load_reference_bars as _load_reference_bars_for_sr
        from backend.support_resistance import SupportResistanceEngine

        sr_bars = _load_bars_for_sr(sym, timeframe, limit=200)
        if len(sr_bars) >= 20:
            # Calendar-anchored levels (today/prev-day/this-week/prev-week/
            # 52-week high & low) must come from a dedicated daily series,
            # not `sr_bars` (which is at `timeframe`'s own granularity) —
            # see SupportResistanceEngine.detect()'s `reference_bars` param
            # and backend/api/analysis/router.py's identical fetch. Without
            # this, AI chat/analysis reported different "today's high" etc.
            # depending on which timeframe last populated the context.
            reference_bars = _load_reference_bars_for_sr(sym)
            sr_engine = SupportResistanceEngine(lookback_period=5, lookback_bars=200)
            sr_result = sr_engine.detect(
                sr_bars, symbol=sym, timeframe=timeframe, reference_bars=reference_bars
            )
            latest_close = sr_result.latest_close
            if latest_close is not None:
                supports: list[dict[str, Any]] = []
                resistances: list[dict[str, Any]] = []
                for level in sr_result.levels:
                    entry = {
                        "price": round(level.price, 2),
                        "strength": round(float(level.strength), 2),
                    }
                    (supports if level.price <= latest_close else resistances).append(entry)
                sr = {
                    "supports": supports[:3],
                    "resistances": resistances[:3],
                }
    except Exception:  # noqa: BLE001
        pass

    # --- 8. Most recent trend transition ---
    # Found live 2026-09-09: this section (and the identical pattern in
    # nl_search/executor.py's JustTransitionedFilter) imported a
    # `trend_transition_engine` singleton and called `.get_history(...)`
    # on it — neither exists. `backend.transitions.trend_transition_engine`
    # defines only the `TrendTransitionEngine` class (`.detect()`/
    # `.latest()`, which take a raw scores sequence), no module-level
    # instance and no `get_history` method. The broad except below
    # silently swallowed the resulting ImportError/AttributeError, so
    # `trend_transition` has been an empty dict in every AI analysis
    # ever produced. Fixed by using the engine the way it's actually
    # designed to be used: pull the already-warmed TrendEngine's own
    # score history (no new bar fetch) and detect the latest
    # transition on it directly.
    transition: dict[str, Any] = {}
    try:
        from backend.api.trend.registry import get_engine as get_trend_engine
        from backend.engines.timeframe import Timeframe
        from backend.transitions.trend_transition_engine import (
            TrendTransitionEngine,
        )

        tf_enum = Timeframe(tf.lower())
        hist = get_trend_engine(sym).trend_history.get(tf_enum, [])
        if len(hist) > 6:
            scores = [s.score for s in hist]
            timestamps = [s.timestamp for s in hist]
            t = TrendTransitionEngine(window=5, min_delta=10.0).latest(
                scores, timestamps=timestamps, symbol=sym, timeframe=timeframe
            )
            if t is not None:
                transition = t.to_dict()
    except Exception:  # noqa: BLE001
        pass

    # --- 9. Historical signal statistics (Phase 13) ---
    signal_stats: dict[str, Any] = {}
    try:
        from backend.services.signal_recorder import signal_recorder

        stats = signal_recorder.get_stats(symbol=sym, timeframe=timeframe)
        if stats:
            signal_stats = {
                "total_signals": stats.get("total", 0),
                "avg_return_5b": stats.get("avg_return_5b"),
                "avg_return_10b": stats.get("avg_return_10b"),
                "win_rate": stats.get("win_rate"),
            }
    except Exception:  # noqa: BLE001
        pass

    # --- 10. News (Phase 18 aux-data, real provider, previously never
    # reached the AI) ---
    news: list[dict[str, Any]] = []
    if include_news:
        try:
            from backend.aux_data.services.manager import aux_data_manager
            from backend.utils.timezone import now_ny, to_ny

            news_resp = aux_data_manager.get_news(sym, limit=5)
            # Found live 2026-09-10: news_resp.timestamp comes from
            # now_ny(), which is naive-by-convention (this project's
            # own rule: naive datetimes are always NY local, UTC ones
            # are always aware — see backend/utils/timezone.py). But
            # each article's own item.timestamp is UTC-aware (straight
            # from the provider). Subtracting a naive datetime from an
            # aware one raises TypeError, silently swallowed by the
            # except below — so age_hours computation was throwing on
            # the FIRST article every single call, and since that
            # happened before anything got appended, `news` came back
            # [] on every request regardless of how much real news
            # existed (confirmed live: /api/aux-data/news/AAPL had 10
            # real headlines while build_context()['news'] was always
            # empty). Fixed by converting each item's timestamp to
            # naive NY before comparing, per the project's own
            # convention, instead of reusing the response wrapper's
            # already-naive timestamp's (irrelevant) tzinfo.
            now = now_ny()
            for item in news_resp.items[:5]:
                item_ny = to_ny(item.timestamp)
                age_hours = (
                    round((now - item_ny).total_seconds() / 3600.0, 1)
                    if item_ny is not None else None
                )
                news.append({
                    "headline": item.headline,
                    "source": item.source,
                    "relevance": round(float(item.relevance), 2),
                    "age_hours": age_hours,
                })
        except Exception:  # noqa: BLE001
            pass

    # --- 11. Fundamentals (Phase 18 aux-data) — a curated subset, not
    # every field, to keep the prompt focused ---
    fundamentals: dict[str, Any] = {}
    if include_fundamentals:
        try:
            from backend.aux_data.services.manager import aux_data_manager

            f = aux_data_manager.get_fundamentals(sym).data
            fundamentals = {
                k: v
                for k, v in {
                    "sector": f.sector,
                    "industry": f.industry,
                    "market_cap": f.market_cap,
                    "pe_ratio": f.pe_ratio,
                    "eps_growth": f.eps_growth,
                    "debt_to_equity": f.debt_to_equity,
                    "analyst_target": f.analyst_target,
                    "recommendation": f.recommendation,
                    "beta": f.beta,
                }.items()
                if v is not None
            }
        except Exception:  # noqa: BLE001
            pass

    # --- 12. Divergence (Phase 9 engine, previously never reached the
    # AI) — most recent divergence only, mirrors trend_transition's
    # "latest one" convention ---
    divergence: dict[str, Any] = {}
    if include_divergence:
        try:
            from backend.analysis.series import (
                bar_dicts_to_arrays,
                load_bars,
                macd_histogram_series,
                rsi_series,
            )
            from backend.divergence import DivergenceEngine

            bars = load_bars(sym, timeframe, limit=200)
            if len(bars) >= 30:
                arrays = bar_dicts_to_arrays(bars)
                closes = arrays["closes"]
                rsi = rsi_series(closes, period=14)
                macd = macd_histogram_series(closes, fast=12, slow=26, signal=9)
                found = DivergenceEngine(pivot_lookback=2, max_pivots_apart=80).detect(
                    arrays["highs"], arrays["lows"], closes,
                    volumes=arrays["volumes"], rsi=rsi, macd=macd,
                    timestamps=arrays["timestamps"], symbol=sym, timeframe=timeframe,
                )
                if found:
                    divergence = found[-1].to_dict()
        except Exception:  # noqa: BLE001
            pass

    # --- 13. Tape / order flow (2026-09-10) — a compact view of the
    # recent trade tape. Raw aggregation, not an engine-derived quant
    # number, so the AI may cite it directly (framed as "recent tape").
    tape: dict[str, Any] = {}
    try:
        from backend.config.settings import settings as _settings

        if _settings.tape.enabled:
            from backend.api.tape.registry import get_tape_engine

            s = get_tape_engine(sym).get_snapshot()
            if s.get("trade_count"):
                tape = {
                    "pressure": s["pressure"],
                    "signed_volume_1m": s["signed_volume"],
                    "buy_ratio_1m": s["buy_ratio"],
                    "tape_speed_per_s": s["tape_speed"],
                    "tape_accel": s["tape_accel"],
                    "block_count_5m": s["block_count_5m"],
                }
    except Exception:  # noqa: BLE001
        pass

    # --- 14. Track record (2026-09-11) — the AI's OWN past buy/sell
    # calls on this ticker, graded against what happened. Not an
    # engine-derived quant number either, but it must be framed
    # honestly (small samples early on) — see CHAT_SYSTEM_PROMPT /
    # SYSTEM_PROMPT for the exact wording rule.
    track_record: dict[str, Any] = {}
    try:
        from backend.config.settings import settings as _settings

        if _settings.ai_trade_plan_tracking.enabled:
            from backend.ai.trade_plan_tracker import get_track_record

            track_record = get_track_record(sym)
    except Exception:  # noqa: BLE001
        pass

    return AnalysisContext(
        symbol=sym,
        timeframe=timeframe,
        price=price,
        timestamp=str(ts) if ts else None,
        data_status="live" if quote else "stale",
        timeframe_scores=timeframe_scores,
        trend_state=trend_state,
        market_structure={
            "total_score": round(scan.calculate_total_score(), 2),
            "dimension_scores": {k: round(v, 1) for k, v in (scan.scores or {}).items()},
            "signals": list(scan.signals or []),
        },
        market_regime=market_regime,
        relative_strength=primary_rs,
        sector_alignment=sector_alignment,
        volume=volume,
        momentum=momentum,
        support_resistance=sr,
        trend_transition=transition,
        historical_signal_stats=signal_stats,
        news=news,
        fundamentals=fundamentals,
        divergence=divergence,
        tape=tape,
        track_record=track_record,
    )
