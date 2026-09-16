"""
Outcome tracking for the AI's own buy/sell trade_plan calls (2026-09-11).

``record_trade_plan()`` is called from a single choke point — the tail
of ``analyze_symbol()`` — for every actionable (buy/sell) ``TradePlan``
the AI proposes, regardless of caller (REST ``/api/ai/analyze``,
``AIAnalysisPanel``'s Re-run, background jobs, chat's reanalysis tool).
``hold``/``avoid`` plans are never captured — ``TradePlan.
_check_consistency`` already clears their entry/stop/targets, so
there's nothing to grade.

A background thread (mirrors ``backend/api/tape/registry.py``'s flush
loop) periodically grades every still-"open" row against daily bars
since it was proposed: a target hit before the stop -> "win"; the stop
hit first -> "loss"; neither within the time_horizon's holding window
-> "expired" (marked-to-market, kept out of the win-rate stat).

Gated end-to-end on ``settings.ai_trade_plan_tracking.enabled`` —
capture, grading, and ``get_track_record()`` are all no-ops when it's
off.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta
from statistics import fmean

from backend.config.settings import settings
from backend.database import SessionLocal
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)

# Calendar days from `created_at` before an unresolved plan is marked
# "expired" rather than left open forever.
_HOLDING_DAYS = {"scalp": 1, "swing": 10, "position": 60}
_DEFAULT_HOLDING_DAYS = 10


def record_trade_plan(symbol: str, parsed) -> None:
    """Persist one actionable (buy/sell) TradePlan for later grading.

    A no-op for hold/avoid (no actionable levels) or when tracking is
    off. The caller (analyze_symbol) wraps this in its own try/except —
    a capture failure must never surface as an analysis failure — so
    this function is intentionally a straight, un-guarded write.
    """
    plan = getattr(parsed, "trade_plan", None)
    if plan is None or plan.recommendation not in ("buy", "sell"):
        return
    if not settings.ai_trade_plan_tracking.enabled:
        return

    from backend.models.ai_trade_plan_outcome import AITradePlanOutcome

    db = SessionLocal()
    try:
        db.add(AITradePlanOutcome(
            symbol=symbol.upper(),
            recommendation=plan.recommendation,
            conviction=plan.conviction,
            time_horizon=plan.time_horizon,
            entry_zone_low=plan.entry_zone_low,
            entry_zone_high=plan.entry_zone_high,
            stop_loss=plan.stop_loss,
            targets_json=json.dumps(plan.targets),
            risk_reward=plan.risk_reward,
            provider=parsed.provider,
            model=parsed.model,
        ))
        db.commit()
    finally:
        db.close()


def _entry_mid(row) -> float | None:
    lo, hi = row.entry_zone_low, row.entry_zone_high
    if lo is not None and hi is not None:
        return (lo + hi) / 2
    return lo if lo is not None else hi


def _return_pct(row, entry_mid: float | None) -> float | None:
    if not entry_mid or row.resolved_price is None:
        return None
    sign = 1 if row.recommendation == "buy" else -1
    return round(sign * (row.resolved_price - entry_mid) / entry_mid, 4)


def _grade_row(db, row, today) -> bool:
    """Grade one open row in place (mutated, not committed here).

    Returns True if it resolved (win/loss/expired) this pass, False if
    it's still open and should be left alone.

    Fill-awareness (2026-09-16): a stop/target touch only resolves the
    row if price actually entered the entry zone on this bar or an
    earlier one — i.e. the position had been filled. A bar that gaps
    past the entry zone (e.g. a short whose open is already above the
    entry range and hits the stop) is NOT graded, because the stop only
    applies to an established position. The pre-existing conservative
    rule is preserved: when a single bar touches both the entry zone and
    both stop and target, the stop wins (loss).
    """
    from backend.repositories.bar_repository import get_bars

    targets: list[float] = json.loads(row.targets_json or "[]")
    entry_mid = _entry_mid(row)
    if row.entry_zone_low is not None and row.entry_zone_high is not None:
        entry_lo, entry_hi = row.entry_zone_low, row.entry_zone_high
    elif row.entry_zone_low is not None:
        entry_lo = entry_hi = row.entry_zone_low
    elif row.entry_zone_high is not None:
        entry_lo = entry_hi = row.entry_zone_high
    else:
        entry_lo = entry_hi = None

    def _touches_entry(bar) -> bool:
        # No entry zone recorded → assume filled (don't change behavior
        # for any row that lacks one). Otherwise the bar's [low, high]
        # range must overlap the entry zone.
        if entry_lo is None:
            return True
        return bar.low <= entry_hi and bar.high >= entry_lo

    bars = get_bars(db, row.symbol, "1d", from_ts=row.created_at)
    filled = False

    for bar in bars:
        # Only grade once the position has been filled on this bar or a
        # prior one. Bars that gap past the entry zone (no fill) are
        # skipped — a stop/target touch on an unfilled bar is not a real
        # resolution.
        if not filled:
            if not _touches_entry(bar):
                continue
            filled = True

        # Conservative: a single OHLC bar can't tell us which happened
        # first intraday, so a bar that touches both is treated as a
        # stop-out, not a win.
        if row.recommendation == "buy":
            if row.stop_loss is not None and bar.low <= row.stop_loss:
                row.status, row.resolved_price = "loss", row.stop_loss
            else:
                for i, t in enumerate(targets):
                    if bar.high >= t:
                        row.status, row.resolved_price, row.hit_target_index = "win", t, i
                        break
        else:  # sell
            if row.stop_loss is not None and bar.high >= row.stop_loss:
                row.status, row.resolved_price = "loss", row.stop_loss
            else:
                for i, t in enumerate(targets):
                    if bar.low <= t:
                        row.status, row.resolved_price, row.hit_target_index = "win", t, i
                        break
        if row.status != "open":
            row.resolved_at = now_ny()
            row.return_pct = _return_pct(row, entry_mid)
            return True

    holding_days = _HOLDING_DAYS.get(row.time_horizon, _DEFAULT_HOLDING_DAYS)
    if today - row.created_at.date() >= timedelta(days=holding_days):
        row.status = "expired"
        row.resolved_at = now_ny()
        if bars:
            row.resolved_price = bars[-1].close
            row.return_pct = _return_pct(row, entry_mid)
        return True
    return False


def _grade_once(db) -> int:
    """Grade every still-open row. Each row is isolated in its own
    try/except — one broken row (bad JSON, no bars for a delisted
    symbol, ...) never blocks the rest of the batch. Returns how many
    rows resolved this pass."""
    from backend.models.ai_trade_plan_outcome import AITradePlanOutcome

    today = now_ny().date()
    rows = db.query(AITradePlanOutcome).filter(AITradePlanOutcome.status == "open").all()
    resolved = 0
    for row in rows:
        try:
            if _grade_row(db, row, today):
                resolved += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("trade plan grading failed for outcome id=%s: %s", row.id, e)
    if resolved:
        db.commit()
    return resolved


def get_track_record(symbol: str, limit: int = 20) -> dict:
    """Best-effort win-rate summary of the AI's own resolved buy/sell
    calls on ``symbol``. ``{}`` when tracking is off or there's no
    resolved history yet — the caller (context.py) treats an empty
    dict as "drop this section", same as every other best-effort
    context block. Opens and closes its own session — same convention
    as this module's other entry points."""
    if not settings.ai_trade_plan_tracking.enabled:
        return {}

    from backend.models.ai_trade_plan_outcome import AITradePlanOutcome

    sym = symbol.upper()
    db = SessionLocal()
    try:
        rows = (
            db.query(AITradePlanOutcome)
            .filter(AITradePlanOutcome.symbol == sym, AITradePlanOutcome.status.in_(("win", "loss")))
            .order_by(AITradePlanOutcome.resolved_at.desc())
            .limit(limit)
            .all()
        )
        if not rows:
            return {}

        wins = [r for r in rows if r.status == "win"]
        losses = [r for r in rows if r.status == "loss"]
        open_count = (
            db.query(AITradePlanOutcome)
            .filter(AITradePlanOutcome.symbol == sym, AITradePlanOutcome.status == "open")
            .count()
        )
        win_returns = [r.return_pct for r in wins if r.return_pct is not None]
        loss_returns = [r.return_pct for r in losses if r.return_pct is not None]
        return {
            "sample_size": len(rows),
            "win_rate": round(len(wins) / len(rows), 2),
            "avg_return_win": round(fmean(win_returns), 4) if win_returns else None,
            "avg_return_loss": round(fmean(loss_returns), 4) if loss_returns else None,
            "open_count": open_count,
        }
    finally:
        db.close()


# --- Background grading thread — mirrors backend/api/tape/registry.py ---

_grading_thread: threading.Thread | None = None
_grading_stop = threading.Event()


def _grading_loop() -> None:
    while not _grading_stop.wait(settings.ai_trade_plan_tracking.grading_interval_seconds):
        db = SessionLocal()
        try:
            n = _grade_once(db)
            if n:
                logger.info("trade plan grading: resolved %d outcome(s)", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("trade plan grading pass failed: %s", e)
        finally:
            db.close()


def start_trade_plan_tracker() -> None:
    global _grading_thread
    if _grading_thread is not None and _grading_thread.is_alive():
        return
    _grading_stop.clear()
    _grading_thread = threading.Thread(
        target=_grading_loop, name="trade-plan-grading", daemon=True,
    )
    _grading_thread.start()


def stop_trade_plan_tracker() -> None:
    _grading_stop.set()
