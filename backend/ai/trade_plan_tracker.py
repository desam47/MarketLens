"""
Outcome tracking for explicitly confirmed buy/sell trade plans (2026-09-11).

``record_confirmed_trade_plan()`` is called by the explicit
``POST /api/ai/track-trade-plan`` action after the server revalidates the
setup against fresh context. Analysis, refresh, and background runs never
create outcome rows merely by producing a Buy/Sell proposal.
``hold``/``avoid`` plans are never captured — ``TradePlan.
_check_consistency`` already clears their entry/stop/targets, so
there's nothing to grade.

A background thread (mirrors ``backend/api/tape/registry.py``'s flush
loop) periodically grades every still-"open" row against 1-minute bars
after it was tracked on that day, then daily bars from the following day:
a target hit before the stop -> "win"; the stop hit first -> "loss";
neither within the time_horizon's holding window -> "expired"
(marked-to-market, kept out of the win-rate stat).

Gated end-to-end on ``settings.ai_trade_plan_tracking.enabled`` —
capture, grading, and ``get_track_record()`` are all no-ops when it's
off.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, time, timedelta
from statistics import fmean

from backend.config.settings import settings
from backend.database import SessionLocal
from backend.repositories.trade_plan_repository import TradePlanRepository
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)

# Calendar days from `created_at` before an unresolved plan is marked
# "expired" rather than left open forever.
_HOLDING_DAYS = {"scalp": 1, "swing": 10, "position": 60}
_DEFAULT_HOLDING_DAYS = 10


def record_confirmed_trade_plan(
    symbol: str,
    plan,
    *,
    provider: str = "user-confirmed",
    model: str = "AIAnalysisPanel",
    timeframe: str = "1d",
    analysis_id: str = "",
) -> tuple[object | None, bool]:
    """Persist a freshly server-validated plan after explicit confirmation.

    Returns ``(row, duplicate)``. The caller must validate the plan against
    fresh market context before calling this function. Open plans with the
    same actionable fields are treated as duplicates so repeated clicks or
    retries cannot create multiple grading records.
    """
    if plan is None or plan.recommendation not in ("buy", "sell"):
        return None, False
    if not settings.ai_trade_plan_tracking.enabled:
        return None, False

    targets_json = json.dumps([float(value) for value in plan.targets], separators=(",", ":"))
    db = SessionLocal()
    try:
        repo = TradePlanRepository(db)
        candidates = repo.find_open_duplicates(
            symbol, plan.recommendation, plan.conviction, plan.time_horizon, timeframe
        )
        for row in candidates:
            if (
                row.entry_zone_low == plan.entry_zone_low
                and row.entry_zone_high == plan.entry_zone_high
                and row.stop_loss == plan.stop_loss
                and row.targets_json == targets_json
            ):
                return row, True

        row = repo.create(
            symbol=symbol,
            recommendation=plan.recommendation,
            conviction=plan.conviction,
            time_horizon=plan.time_horizon,
            entry_zone_low=plan.entry_zone_low,
            entry_zone_high=plan.entry_zone_high,
            stop_loss=plan.stop_loss,
            targets_json=targets_json,
            risk_reward=plan.risk_reward,
            provider=provider,
            model=model,
            timeframe=timeframe,
            analysis_id=analysis_id,
        )
        return row, False
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

    filled = False
    latest_bar = None

    def _grade_bars(bars) -> bool:
        nonlocal filled, latest_bar
        for bar in bars:
            latest_bar = bar
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
                    for i, target in enumerate(targets):
                        if bar.high >= target:
                            row.status, row.resolved_price, row.hit_target_index = "win", target, i
                            break
            else:  # sell
                if row.stop_loss is not None and bar.high >= row.stop_loss:
                    row.status, row.resolved_price = "loss", row.stop_loss
                else:
                    for i, target in enumerate(targets):
                        if bar.low <= target:
                            row.status, row.resolved_price, row.hit_target_index = "win", target, i
                            break
            if row.status != "open":
                row.resolved_at = now_ny()
                row.return_pct = _return_pct(row, entry_mid)
                return True
        return False

    # Daily bars are stamped at midnight, so querying them from a plan tracked
    # at (say) 10:30 would silently skip that whole day. Use only bars after
    # the explicit confirmation on day D, then switch to daily bars at D+1.
    next_day = datetime.combine(row.created_at.date() + timedelta(days=1), time.min)
    intraday_bars = get_bars(
        db,
        row.symbol,
        "1m",
        from_ts=row.created_at,
        to_ts=next_day - timedelta(microseconds=1),
    )
    if _grade_bars(intraday_bars):
        return True

    daily_bars = get_bars(db, row.symbol, "1d", from_ts=next_day)
    if _grade_bars(daily_bars):
        return True

    holding_days = _HOLDING_DAYS.get(row.time_horizon, _DEFAULT_HOLDING_DAYS)
    if today - row.created_at.date() >= timedelta(days=holding_days):
        row.status = "expired"
        row.resolved_at = now_ny()
        # Fill-aware here too (2026-09-16): a plan whose entry zone was
        # never touched never became a real position, so marking it to
        # the last close and computing a return_pct as if one existed
        # is a phantom number — same fill gate as the win/loss branch
        # above, just applied to the "held the whole window and never
        # resolved" case instead of "resolved by stop/target".
        if filled and latest_bar is not None:
            row.resolved_price = latest_bar.close
            row.return_pct = _return_pct(row, entry_mid)
        return True
    return False


def _grade_once(db) -> int:
    """Grade every still-open row. Each row is isolated in its own
    try/except — one broken row (bad JSON, no bars for a delisted
    symbol, ...) never blocks the rest of the batch. Returns how many
    rows resolved this pass."""
    today = now_ny().date()
    rows = TradePlanRepository(db).get_open_rows()
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
    """Best-effort win-rate summary of explicitly tracked setups on
    ``symbol``. ``{}`` when tracking is off or there's no
    resolved history yet — the caller (context.py) treats an empty
    dict as "drop this section", same as every other best-effort
    context block. Opens and closes its own session — same convention
    as this module's other entry points.

    The ``all_time_win_count``/``all_time_loss_count``/
    ``all_time_expired_count``/``all_time_open_count`` buckets are
    pulled from a single grouped status-count query (one pass over the
    table, replacing a separate ``COUNT(*)`` subquery for open rows)
    over the symbol's ENTIRE history. ``sample_size``/``win_rate``/avg
    returns are a DIFFERENT, narrower window — only the most-recent
    ``limit`` resolved rows (the calibration sample the AI's confidence
    is damped against). These two windows are deliberately different
    questions ("how many opens do I have right now, all-time" vs. "how
    have my last N tracked setups done") — the ``all_time_`` prefix exists
    specifically so a consumer never assumes they reconcile (bug found
    2026-09-16: they didn't, and nothing in either field name said so).
    """
    if not settings.ai_trade_plan_tracking.enabled:
        return {}

    db = SessionLocal()
    try:
        repo = TradePlanRepository(db)
        rows = repo.get_resolved_rows(symbol, limit)
        if not rows:
            return {}

        status_counts = repo.get_status_counts(symbol)

        wins = [r for r in rows if r.status == "win"]
        losses = [r for r in rows if r.status == "loss"]
        win_returns = [r.return_pct for r in wins if r.return_pct is not None]
        loss_returns = [r.return_pct for r in losses if r.return_pct is not None]
        return {
            "sample_size": len(rows),
            "win_rate": round(len(wins) / len(rows), 2),
            "avg_return_win": round(fmean(win_returns), 4) if win_returns else None,
            "avg_return_loss": round(fmean(loss_returns), 4) if loss_returns else None,
            "all_time_win_count": status_counts.get("win", 0),
            "all_time_loss_count": status_counts.get("loss", 0),
            "all_time_expired_count": status_counts.get("expired", 0),
            "all_time_open_count": status_counts.get("open", 0),
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
        target=_grading_loop,
        name="trade-plan-grading",
        daemon=True,
    )
    _grading_thread.start()


def stop_trade_plan_tracker() -> None:
    _grading_stop.set()
