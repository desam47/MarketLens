"""Assemble a draft trade plan from independent stop/target sources.

A trade plan needs one entry, one stop and at least one target. The hard part
is not the arithmetic -- ``backend.ai.market_tools.build_trade_plan_tool``
already does that, routing every number through
``backend.ai.calculator.calculate`` -- it is deciding *where* the stop goes.
This module answers that from three independent angles and hands all of them
back so the trader compares rather than trusts one number:

  structural  nearest support/resistance from ``SupportResistanceEngine``
  volatility  ATR(14), plus the supertrend flip price as a trailing stop
  empirical   the adverse-excursion distribution of comparable past signals

A fourth source, the options-implied expected move, frames the plan against
what the market is pricing rather than proposing a stop of its own.

Read-only: nothing here saves a plan or places an order. The endpoint returns
candidates plus one pre-selected draft; the UI stays the decision-maker.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import date

from backend.utils.timezone import now_ny
from typing import Any, Literal

from sqlalchemy.orm import Session

from backend.indicators.atr import ATRIndicator
from backend.repositories.signal_repository import SignalRepository
from backend.services.excursion_stats import DEFAULT_MIN_SAMPLE, excursion_distribution

logger = logging.getLogger(__name__)

BAR_LIMIT = 500
ATR_PERIOD = 14

# A stop this many ATRs from entry sits outside ordinary bar-to-bar noise.
ATR_STOP_MULTIPLE = 2.0

# The adverse excursion most comparable signals stayed within -- a stop inside
# this is inside normal heat and will be taken out by a trade that still works.
STOP_PERCENTILE = "p75"

# The median run in favour: a target beyond this is above average, not typical.
TARGET_PERCENTILE = "p50"

MAX_TARGETS = 3

# Structural levels offered to the selector. Larger than MAX_TARGETS so the
# further-out swing highs and R2/R3 pivots remain available -- a stop placed
# outside the noise needs a target beyond the nearest level to pay for itself.
STRUCTURAL_TARGET_POOL = 12

# Below this, the trade is not worth taking: the selected stop is wide enough
# that the nearest structure cannot pay for it. Candidates below the bar are
# still returned for display -- they are just never auto-selected.
MIN_REWARD_RISK = 1.0

# The options expected move spans days, so it frames a plan but must not be
# auto-selected as a target on an intraday timeframe.
CONTEXT_ONLY_TARGET_SOURCES = frozenset({"options_implied"})

Direction = Literal["long", "short"]

DIRECTION_TREND_STATE: dict[str, str] = {"long": "bullish", "short": "bearish"}

_SUPPORT_TYPES = frozenset(
    {"swing_low", "pivot_s1", "pivot_s2", "pivot_s3", "pivot_pp", "consolidation_zone"}
)
_RESISTANCE_TYPES = frozenset(
    {"swing_high", "pivot_r1", "pivot_r2", "pivot_r3", "pivot_pp", "consolidation_zone"}
)


def _pct_from(entry: float, price: float) -> float:
    return round(abs(entry - price) / entry * 100, 4)


def _candidate(source: str, price: float, entry: float, rationale: str) -> dict[str, Any]:
    return {
        "source": source,
        "price": round(price, 4),
        "distance_pct": _pct_from(entry, price),
        "rationale": rationale,
    }


# --- Source A: structural ----------------------------------------------------


def _structural(bars: Sequence[dict], symbol: str, timeframe: str, direction: Direction) -> dict[str, Any]:
    """Nearest support/resistance from the S/R engine.

    Calls the engine directly rather than going through
    ``/api/analysis/{symbol}/price-range``: that endpoint narrows its response
    to the classic pivot table and discards the swing levels, which are exactly
    what a structural stop wants.
    """
    from backend.support_resistance import SupportResistanceEngine

    engine = SupportResistanceEngine(lookback_period=5, lookback_bars=len(bars))
    # Engine expects newest -> oldest, which is load_bars' own ordering.
    result = engine.detect(bars, symbol=symbol, timeframe=timeframe)
    entry = result.latest_close
    if entry is None:
        return {"available": False, "error": "No latest close in S/R scan."}

    levels = [
        {
            "price": lvl.price,
            "type": lvl.type.value,
            "strength": round(lvl.strength, 4),
            "touch_count": lvl.touch_count,
        }
        for lvl in result.levels
    ]
    stop_side = _SUPPORT_TYPES if direction == "long" else _RESISTANCE_TYPES
    target_side = _RESISTANCE_TYPES if direction == "long" else _SUPPORT_TYPES

    if direction == "long":
        stops = sorted(
            (level for level in levels if level["price"] < entry and level["type"] in stop_side),
            key=lambda level: entry - level["price"],
        )
        targets = sorted(
            (level for level in levels if level["price"] > entry and level["type"] in target_side),
            key=lambda level: level["price"] - entry,
        )
    else:
        stops = sorted(
            (level for level in levels if level["price"] > entry and level["type"] in stop_side),
            key=lambda level: level["price"] - entry,
        )
        targets = sorted(
            (level for level in levels if level["price"] < entry and level["type"] in target_side),
            key=lambda level: entry - level["price"],
        )

    return {
        "available": True,
        "latest_close": round(entry, 4),
        "level_count": len(levels),
        "nearest_stop_level": stops[0] if stops else None,
        # Keep the further-out levels too, not just the nearest few: a
        # noise-based stop can only be justified by a target far enough away to
        # pay for it, and on a liquid name the nearest swing high is often a
        # fraction of a percent off.
        "target_levels": targets[:STRUCTURAL_TARGET_POOL],
    }


# --- Source B: volatility ----------------------------------------------------


def _volatility(bars: Sequence[dict], symbol: str, timeframe: str) -> dict[str, Any]:
    """ATR(14) in dollars, plus the supertrend flip price when reachable.

    ATR is computed here rather than derived from the trend endpoint's
    ``band_distance_atr``: that division is undefined when price sits on the
    band. ``indicator_values["atr"]`` from the scanner is also unsuitable --
    it is a single-bar true range, not an average.
    """
    if len(bars) < ATR_PERIOD + 1:
        return {"available": False, "error": f"Need {ATR_PERIOD + 1} bars for ATR({ATR_PERIOD})."}

    # ATRIndicator walks forward using data[i-1]["close"], so it needs
    # oldest -> newest; load_bars hands back newest -> oldest.
    ascending = list(reversed(bars))
    values = ATRIndicator(ATR_PERIOD).calculate(ascending)
    if not values:
        return {"available": False, "error": "ATR returned no values."}

    atr = values[-1]
    result: dict[str, Any] = {
        "available": True,
        "atr_dollars": round(atr, 4),
        "atr_period": ATR_PERIOD,
        "atr_source": "atr_indicator_14",
        "stop_multiple": ATR_STOP_MULTIPLE,
        "flip_price": None,
    }

    # Best-effort cross-check: the supertrend flip is a ready-made trailing
    # stop, but it lives behind the trend engine and must never fail the plan.
    try:
        from backend.api.trend.registry import get_engine
        from backend.engines.timeframe import Timeframe

        signal = get_engine(symbol).get_current_trend(Timeframe(timeframe))
        supertrend = (getattr(signal, "key_levels", None) or {}).get("supertrend") or {}
        flip = supertrend.get("flip_price")
        if flip is not None:
            result["flip_price"] = round(float(flip), 4)
    except Exception as exc:  # noqa: BLE001
        logger.debug("supertrend flip unavailable for %s %s: %s", symbol, timeframe, exc)

    return result


# --- Source C: empirical -----------------------------------------------------


def _empirical(
    db: Session, symbol: str, timeframe: str, direction: Direction, min_sample: int
) -> dict[str, Any]:
    """Adverse/favorable excursion distribution of comparable past signals.

    When the symbol-specific slice is too thin, a pooled all-symbol baseline
    is attached under ``baseline`` so the UI can show what comparable setups
    across all tracked names look like without silently using a weak number.
    """
    repo = SignalRepository(db)
    trend_state = DIRECTION_TREND_STATE[direction]
    rows = repo.fetch_excursion_rows(symbol=symbol, timeframe=timeframe, trend_state=trend_state)
    stats = excursion_distribution(rows, min_sample=min_sample)
    result: dict[str, Any] = {"available": True, **stats}

    if not stats["sufficient"]:
        all_rows = repo.fetch_excursion_rows(symbol=None, timeframe=timeframe, trend_state=trend_state)
        all_stats = excursion_distribution(all_rows, min_sample=min_sample)
        if all_stats["sufficient"]:
            result["baseline"] = {
                "label": f"All symbols — {timeframe} {direction}",
                "sample_size": all_stats["sample_size"],
                "confidence": all_stats["confidence"],
                "adverse_excursion_pct": all_stats["adverse_excursion_pct"],
                "favorable_excursion_pct": all_stats["favorable_excursion_pct"],
            }

    return result


# --- Source D: options-implied ----------------------------------------------


def _options_implied(symbol: str, entry: float) -> dict[str, Any]:
    """Expected move from near-term IV -- context, not a stop of its own."""
    from backend.ai.calculator import CalculationRequest, calculate
    from backend.aux_data.services.manager import aux_data_manager

    if not aux_data_manager.options.is_enabled():
        return {"available": False, "error": "Options provider disabled."}

    response = aux_data_manager.get_options(symbol)
    iv = response.near_term_iv
    if iv is None:
        return {"available": False, "error": "No near-term IV available."}

    days = None
    for value in sorted(response.expirations):
        try:
            days = (date.fromisoformat(value) - now_ny().date()).days
        except ValueError:
            continue
        if days and days > 0:
            break
    if not days or days <= 0:
        return {"available": False, "error": "No future expiration available."}

    result = calculate(
        CalculationRequest(
            calculation="expected_move",
            new_value=entry,
            implied_volatility=iv,
            days_to_expiration=days,
        )
    )
    move = result.values.get("expected_move")
    lower = result.values.get("lower_bound")
    upper = result.values.get("upper_bound")
    if move is None or lower is None or upper is None:
        return {"available": False, "error": "Expected-move calculation returned no bounds."}

    return {
        "available": True,
        "iv_rank": response.iv_rank,
        "near_term_iv": iv,
        "days_to_expiration": days,
        "expected_move": round(move, 4),
        "lower_bound": round(lower, 4),
        "upper_bound": round(upper, 4),
    }


# --- Candidate assembly ------------------------------------------------------


def _collect_stops(sources: dict[str, Any], entry: float, direction: Direction) -> list[dict[str, Any]]:
    stops: list[dict[str, Any]] = []
    sign = -1 if direction == "long" else 1

    structural = sources["structural"]
    if structural.get("available") and structural.get("nearest_stop_level"):
        level = structural["nearest_stop_level"]
        stops.append(
            _candidate(
                "structural",
                level["price"],
                entry,
                f"Nearest {level['type'].replace('_', ' ')} "
                f"(strength {level['strength']:.2f}, {level['touch_count']} touches).",
            )
        )

    volatility = sources["volatility"]
    if volatility.get("available"):
        atr = volatility["atr_dollars"]
        stops.append(
            _candidate(
                "volatility",
                entry + sign * ATR_STOP_MULTIPLE * atr,
                entry,
                f"{ATR_STOP_MULTIPLE:g}x ATR({ATR_PERIOD}) of ${atr:.4f}.",
            )
        )
        if volatility.get("flip_price") is not None:
            flip = volatility["flip_price"]
            # Only meaningful while the flip sits on the protective side.
            if (direction == "long" and flip < entry) or (direction == "short" and flip > entry):
                stops.append(
                    _candidate("supertrend", flip, entry, "Supertrend flip price (trailing stop).")
                )

    empirical = sources["empirical"]
    adverse = empirical.get("adverse_excursion_pct") if empirical.get("available") and empirical.get("sufficient") else None
    if adverse:
        pct = adverse[STOP_PERCENTILE]
        stops.append(
            _candidate(
                "empirical",
                entry * (1 + sign * pct / 100),
                entry,
                f"{STOP_PERCENTILE} adverse excursion ({pct:.2f}%) over "
                f"{empirical['sample_size']} comparable signals.",
            )
        )

    return stops


def _collect_targets(sources: dict[str, Any], entry: float, direction: Direction) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    sign = 1 if direction == "long" else -1

    structural = sources["structural"]
    if structural.get("available"):
        for level in structural.get("target_levels") or []:
            targets.append(
                _candidate(
                    "structural",
                    level["price"],
                    entry,
                    f"{level['type'].replace('_', ' ')} (strength {level['strength']:.2f}).",
                )
            )

    empirical = sources["empirical"]
    favorable = empirical.get("favorable_excursion_pct") if empirical.get("available") and empirical.get("sufficient") else None
    if favorable:
        pct = favorable[TARGET_PERCENTILE]
        targets.append(
            _candidate(
                "empirical",
                entry * (1 + sign * pct / 100),
                entry,
                f"Median favourable excursion ({pct:.2f}%) over "
                f"{empirical['sample_size']} comparable signals.",
            )
        )

    options = sources["options_implied"]
    if options.get("available"):
        bound = options["upper_bound"] if direction == "long" else options["lower_bound"]
        targets.append(
            _candidate(
                "options_implied",
                bound,
                entry,
                f"Options-implied expected move over {options['days_to_expiration']}d.",
            )
        )

    return targets


def _protective(candidates: list[dict[str, Any]], entry: float, direction: Direction) -> list[dict[str, Any]]:
    return [
        c for c in candidates
        if (direction == "long" and c["price"] < entry) or (direction == "short" and c["price"] > entry)
    ]


def _beyond(candidates: list[dict[str, Any]], entry: float, direction: Direction) -> list[dict[str, Any]]:
    return [
        c for c in candidates
        if ((direction == "long" and c["price"] > entry) or (direction == "short" and c["price"] < entry))
        and c["source"] not in CONTEXT_ONLY_TARGET_SOURCES
    ]


def _dedupe(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One target per distinct price, so the reward:risk ladder stays meaningful."""
    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        if all(abs(candidate["price"] - other["price"]) > 1e-6 for other in kept):
            kept.append(candidate)
    return kept


def _select(
    stops: list[dict[str, Any]], targets: list[dict[str, Any]], entry: float, direction: Direction
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float | None]:
    """Widest stop, then the nearest targets that can pay for it.

    Stop placement is a question about invalidation, not about making the
    reward:risk look good -- the widest candidate is the one that sits outside
    every source's estimate of normal noise, so it is the one least likely to
    be taken out by a trade that would have worked. (Picking whichever stop
    maximises reward:risk always collapses to the tightest one, since risk is
    the denominator.)

    Targets are then filtered to those clearing ``MIN_REWARD_RISK`` against
    that stop. If none do, the trade does not pay for its own risk and no
    plan is returned.

    Returns the chosen stop, its qualifying targets, and the best reward:risk
    available against that stop -- reported even when nothing clears the gate,
    so the caller can say how far off the setup was.
    """
    valid_stops = _protective(stops, entry, direction)
    valid_targets = _dedupe(
        sorted(_beyond(targets, entry, direction), key=lambda t: t["distance_pct"])
    )
    if not valid_stops:
        return None, [], None

    stop = max(valid_stops, key=lambda s: s["distance_pct"])
    risk = abs(entry - stop["price"])
    if risk <= 0 or not valid_targets:
        return stop, [], None

    ratios = [(t, abs(t["price"] - entry) / risk) for t in valid_targets]
    best_ratio = max(ratio for _, ratio in ratios)
    qualifying = [t for t, ratio in ratios if ratio >= MIN_REWARD_RISK]
    return stop, qualifying[:MAX_TARGETS], best_ratio


async def build_draft_plan(
    db: Session,
    *,
    symbol: str,
    timeframe: str,
    direction: Direction,
    account_value: float | None = None,
    risk_percent: float | None = None,
    include_options: bool = True,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> dict[str, Any]:
    """Gather every source, assemble candidates, and build the selected plan.

    Each source is wrapped in an ``{available, error}`` envelope so one slow or
    failing provider degrades that section instead of the whole response.
    """
    from backend.analysis.series import load_bars

    sym = symbol.upper()
    warnings: list[str] = []

    bars = await asyncio.to_thread(load_bars, sym, timeframe, limit=BAR_LIMIT)
    if len(bars) < 20:
        return {
            "symbol": sym,
            "timeframe": timeframe,
            "direction": direction,
            "current_price": None,
            "latest_bar_timestamp": None,
            "latest_bar_data_status": None,
            "latest_bar_source": None,
            "sources": {},
            "candidate_stops": [],
            "candidate_targets": [],
            "selected": None,
            "plan": None,
            "warnings": [f"Only {len(bars)} bars stored for {sym} {timeframe}; need at least 20."],
        }

    async def guarded(name: str, fn, *args) -> tuple[str, dict[str, Any]]:
        try:
            return name, await asyncio.to_thread(fn, *args)
        except Exception as exc:  # noqa: BLE001
            logger.warning("trade plan source %s failed for %s %s: %s", name, sym, timeframe, exc)
            return name, {"available": False, "error": str(exc)}

    structural_name, structural = await guarded(
        "structural", _structural, bars, sym, timeframe, direction
    )
    entry = structural.get("latest_close") if structural.get("available") else None
    if entry is None:
        entry = float(bars[0]["close"])
        warnings.append("S/R scan unavailable; entry taken from the latest bar close.")

    pending = [
        guarded("volatility", _volatility, bars, sym, timeframe),
        guarded("empirical", _empirical, db, sym, timeframe, direction, min_sample),
    ]
    if include_options:
        pending.append(guarded("options_implied", _options_implied, sym, entry))

    sources: dict[str, Any] = {structural_name: structural}
    for name, payload in await asyncio.gather(*pending):
        sources[name] = payload
    sources.setdefault(
        "options_implied", {"available": False, "error": "Skipped (include_options=false)."}
    )

    empirical = sources["empirical"]
    if empirical.get("available") and not empirical.get("sufficient"):
        n = empirical["sample_size"]
        if n == 0:
            warnings.append(
                f"No {direction} signals with completed outcomes exist for {sym} {timeframe} "
                f"in the historical database — the trend engine may never have called this "
                f"direction on this symbol/timeframe, or outcomes haven't been recorded yet. "
                f"No empirical stop offered."
            )
        else:
            warnings.append(
                f"Only {n} comparable signals "
                f"(need {empirical['min_sample']}); no empirical stop offered."
            )

    stops = _collect_stops(sources, entry, direction)
    targets = _collect_targets(sources, entry, direction)
    stop, chosen_targets, best_ratio = _select(stops, targets, entry, direction)

    selected: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None

    if stop is None:
        warnings.append("No stop candidate on the protective side of entry; cannot build a plan.")
    elif not chosen_targets:
        achievable = (
            f"the best available is {best_ratio:.2f}:1"
            if best_ratio is not None
            else "no target sits beyond entry at all"
        )
        warnings.append(
            f"No target clears {MIN_REWARD_RISK:g}:1 against the {stop['distance_pct']:.2f}% "
            f"{stop['source']} stop -- {achievable}. The trade does not pay for its own risk "
            "here: price is too close to structure. Wait for a pullback, or use a timeframe "
            "whose levels sit further out."
        )
    else:
        selected = {
            "entry_zone_low": round(entry, 4),
            "entry_zone_high": round(entry, 4),
            "stop_price": stop["price"],
            "stop_source": stop["source"],
            "targets": [t["price"] for t in chosen_targets],
            "target_sources": [t["source"] for t in chosen_targets],
        }
        plan, plan_warning = _build_plan(
            sym, timeframe, direction, selected, account_value, risk_percent
        )
        if plan_warning:
            warnings.append(plan_warning)
        warnings.extend(_sizing_warnings(plan, account_value))

    return {
        "symbol": sym,
        "timeframe": timeframe,
        "direction": direction,
        "current_price": round(entry, 4),
        "latest_bar_timestamp": bars[0].get("timestamp"),
        "latest_bar_data_status": bars[0].get("data_status"),
        "latest_bar_source": bars[0].get("source"),
        "bars_used": len(bars),
        "sources": sources,
        "candidate_stops": stops,
        "candidate_targets": targets,
        "best_reward_risk": round(best_ratio, 4) if best_ratio is not None else None,
        "min_reward_risk": MIN_REWARD_RISK,
        "selected": selected,
        "plan": plan,
        "warnings": warnings,
    }


def _sizing_warnings(plan: dict[str, Any] | None, account_value: float | None) -> list[str]:
    """Flag a size the account cannot actually carry.

    A tight stop makes the risk-budget share count very large: risking 1% of
    100k over a 0.19% stop is a $526k position. The risk math is right, but an
    unmargined account cannot take the trade, and nothing else in the stack
    says so.
    """
    if not plan or not account_value:
        return []
    sizing = plan.get("position_size") or {}
    position_value = sizing.get("position_value")
    if not position_value or position_value <= account_value:
        return []
    return [
        f"Position value ${position_value:,.0f} exceeds account value "
        f"${account_value:,.0f} ({position_value / account_value:.1f}x) -- the risk budget "
        "allows more shares than the account can carry unmargined. Cap the size or widen "
        "the stop."
    ]


def _build_plan(
    symbol: str,
    timeframe: str,
    direction: Direction,
    selected: dict[str, Any],
    account_value: float | None,
    risk_percent: float | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run the shared plan builder, turning its refusals into warnings.

    ``build_trade_plan_tool`` raises when a stop or target sits on the wrong
    side of entry. Candidates are filtered before we get here, so a raise means
    a genuine inconsistency -- surface it rather than returning a 500.
    """
    from backend.ai.market_tools import TradePlanRequest, build_trade_plan_tool

    try:
        result = build_trade_plan_tool(
            TradePlanRequest(
                symbol=symbol,
                direction=direction,
                entry_zone_low=selected["entry_zone_low"],
                entry_zone_high=selected["entry_zone_high"],
                stop_price=selected["stop_price"],
                targets=selected["targets"],
                account_value=account_value,
                risk_percent=risk_percent,
                timeframe=timeframe,
            )
        )
    except ValueError as exc:
        return None, f"Plan builder rejected the selected levels: {exc}"
    return result.model_dump(mode="json"), None
