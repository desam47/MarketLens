"""
Alert evaluation engine.

Process-wide singleton that evaluates alert rules against fresh data and
persists trigger events. Two activation paths:

1. **Signal path** — ``evaluate_scan_result(result)`` is called by the
   scanner router after every full scan. The result already carries the
   computed signal list; the engine checks it against every enabled
   ``signal_equals`` alert for the scanned symbol.

2. **Price path** — ``_on_quote(price=...)`` is registered with
   ``engine_registry`` for each symbol that has a price-based alert
   enabled. ``engine_registry`` calls it on every tick from ingestion.

Both paths call ``_try_fire()`` which handles dedup, the trigger insert,
and, later, notification dispatch.
"""
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from backend.database import SessionLocal
from backend.market_data.services.engine_seeder import engine_registry
from backend.models import Alert, AlertTrigger

from .conditions import (
    build_alignment_payload,
    build_breakdown_payload,
    build_breakout_payload,
    build_trend_payload,
    build_volume_payload,
    evaluate,
)

logger = logging.getLogger(__name__)

# How long (seconds) to suppress re-fires of the same alert for the
# same symbol. 3600 = 1 hour. A value of 0 disables dedup.
DEDUP_WINDOW_SECONDS = 3600


class AlertsEngine:
    """Process-wide alert evaluation engine.

    Thread-safe for calls from both the asyncio event loop (via the
    engine_registry callback) and the sync scanner path.
    """

    def __init__(self) -> None:
        # symbol.upper() -> list of alert IDs registered for price callbacks.
        self._price_alert_ids: dict[str, list[int]] = defaultdict(list)
        # (alert_id, symbol) -> last fired timestamp (Unix float).
        # Cleared at startup, repopulated on each fire.
        self._fired_at: dict[tuple[int, str], float] = {}
        # Cache of loaded enabled alerts: alert_id -> Alert (populated at startup).
        self._alerts_cache: dict[int, Alert] = {}
        # Guard for all mutable state.
        self._lock = threading.Lock()
        self._started = False

    # --- Public API (called from router and startup) --------------------

    def startup(self) -> None:
        """Load enabled alerts from DB and register quote callbacks for price alerts.

        Idempotent — safe to call from tests or multiple times.
        """
        with self._lock:
            if self._started:
                self._reload()
                return
            self._started = True
            self._reload()

    def _reload(self) -> None:
        """Re-read enabled alerts and update all in-memory state.

        Called at startup and when the engine needs to pick up changes
        made outside the CRUD API (e.g. manual DB edits).
        """
        db = SessionLocal()
        try:
            alerts = db.query(Alert).filter(Alert.is_enabled).all()
        finally:
            db.close()

        # Rebuild the alerts cache.
        self._alerts_cache = {a.id: a for a in alerts}

        # Price-based conditions: fire on every quote tick.
        PRICE_CONDITIONS = ("price_above", "price_below", "pct_change_above")
        # Bar-based conditions: fire on every bar (trend, alignment, volume, etc.).
        BAR_CONDITIONS = (
            "trend_crosses_above_70", "trend_crosses_below_70",
            "trend_direction_changes", "trend_strengthens", "trend_weakens",
            "full_timeframe_alignment", "timeframe_conflict",
            "volume_expansion", "breakout", "breakdown", "divergence",
            "market_regime_change",
        )

        current_price_symbols = set(self._price_alert_ids.keys())
        current_bar_symbols = set(self._bar_alert_ids.keys()) if hasattr(self, "_bar_alert_ids") else set()
        new_price_symbols: set[str] = set()
        new_bar_symbols: set[str] = set()
        # Track which symbols we've already registered in this reload pass to
        # avoid duplicate registrations when multiple alerts share the same symbol.
        _price_registered: set[str] = set()
        _bar_registered: set[str] = set()

        for alert in alerts:
            sym = alert.symbol.upper()
            if alert.condition_type in PRICE_CONDITIONS:
                new_price_symbols.add(sym)
                if sym not in self._price_alert_ids or alert.id not in self._price_alert_ids[sym]:
                    self._price_alert_ids.setdefault(sym, []).append(alert.id)
                    if sym not in current_price_symbols and sym not in _price_registered:
                        engine_registry.register("quote", sym, self._on_quote)
                        _price_registered.add(sym)
            elif alert.condition_type in BAR_CONDITIONS:
                new_bar_symbols.add(sym)
                if not hasattr(self, "_bar_alert_ids"):
                    self._bar_alert_ids = defaultdict(list)
                if sym not in self._bar_alert_ids or alert.id not in self._bar_alert_ids[sym]:
                    self._bar_alert_ids.setdefault(sym, []).append(alert.id)
                    if sym not in current_bar_symbols and sym not in _bar_registered:
                        engine_registry.register("bar", sym, self._on_bar)
                        _bar_registered.add(sym)

        # Unregister price callbacks for symbols with no price alerts.
        for sym in current_price_symbols - new_price_symbols:
            if sym in self._price_alert_ids:
                engine_registry.unregister("quote", sym, self._on_quote)
                del self._price_alert_ids[sym]

        # Unregister bar callbacks for symbols with no bar alerts.
        if hasattr(self, "_bar_alert_ids"):
            for sym in current_bar_symbols - new_bar_symbols:
                if sym in self._bar_alert_ids:
                    engine_registry.unregister("bar", sym, self._on_bar)
                    del self._bar_alert_ids[sym]

        logger.info(f"AlertsEngine loaded {len(alerts)} enabled alerts "
                    f"({len(new_price_symbols)} price symbols, {len(new_bar_symbols)} bar symbols)")

    def evaluate_scan_result(self, result) -> None:
        """Check every enabled signal_equals alert for this symbol.

        Called from the scanner router after each full scan. ``result``
        is a ScanResult with a .signals list and a .symbol field.
        """
        if not self._started:
            return
        sym = result.symbol.upper()
        with self._lock:
            # Snapshot the current alert IDs so we can release the lock.
            candidates = [
                a for a in self._alerts_cache.values()
                if a.symbol.upper() == sym
                and a.condition_type == "signal_equals"
                and a.is_enabled
            ]

        for alert in candidates:
            price = result.quote.price if result.quote else None
            self._try_fire(alert, price, extra_value=result.signals)

    def register_for_alert(self, alert: Alert) -> None:
        """Register a callback when a new alert is created.

        Called from the alerts router's POST handler so the engine starts
        evaluating the new alert immediately, without waiting for the
        next startup reload.
        """
        PRICE_CONDITIONS = ("price_above", "price_below", "pct_change_above")
        BAR_CONDITIONS = (
            "trend_crosses_above_70", "trend_crosses_below_70",
            "trend_direction_changes", "trend_strengthens", "trend_weakens",
            "full_timeframe_alignment", "timeframe_conflict",
            "volume_expansion", "breakout", "breakdown", "divergence",
            "market_regime_change",
        )
        if alert.condition_type not in PRICE_CONDITIONS + BAR_CONDITIONS:
            return
        sym = alert.symbol.upper()
        with self._lock:
            self._alerts_cache[alert.id] = alert
            if alert.condition_type in PRICE_CONDITIONS:
                already_registered = sym in self._price_alert_ids
                self._price_alert_ids.setdefault(sym, []).append(alert.id)
                if not already_registered:
                    engine_registry.register("quote", sym, self._on_quote)
            elif alert.condition_type in BAR_CONDITIONS:
                if not hasattr(self, "_bar_alert_ids"):
                    self._bar_alert_ids = defaultdict(list)
                already_registered = sym in self._bar_alert_ids
                self._bar_alert_ids.setdefault(sym, []).append(alert.id)
                if not already_registered:
                    engine_registry.register("bar", sym, self._on_bar)

    def unregister_for_alert(self, alert: Alert) -> None:
        """Remove a callback when an alert is deleted or disabled.

        Called from the alerts router's DELETE / PUT (is_enabled=False) handlers.
        """
        PRICE_CONDITIONS = ("price_above", "price_below", "pct_change_above")
        BAR_CONDITIONS = (
            "trend_crosses_above_70", "trend_crosses_below_70",
            "trend_direction_changes", "trend_strengthens", "trend_weakens",
            "full_timeframe_alignment", "timeframe_conflict",
            "volume_expansion", "breakout", "breakdown", "divergence",
            "market_regime_change",
        )
        if alert.condition_type not in PRICE_CONDITIONS + BAR_CONDITIONS:
            return
        sym = alert.symbol.upper()
        with self._lock:
            self._alerts_cache.pop(alert.id, None)
            if alert.condition_type in PRICE_CONDITIONS:
                ids = self._price_alert_ids.get(sym)
                if ids and alert.id in ids:
                    ids.remove(alert.id)
                if not self._price_alert_ids.get(sym):
                    self._price_alert_ids.pop(sym, None)
                    engine_registry.unregister("quote", sym, self._on_quote)
            elif alert.condition_type in BAR_CONDITIONS:
                if hasattr(self, "_bar_alert_ids"):
                    ids = self._bar_alert_ids.get(sym)
                    if ids and alert.id in ids:
                        ids.remove(alert.id)
                    if not self._bar_alert_ids.get(sym):
                        self._bar_alert_ids.pop(sym, None)
                        engine_registry.unregister("bar", sym, self._on_bar)

    # --- Price callback (called from engine_registry, any thread) -------

    def _on_quote(self, symbol: str, price: float, volume: float = 0,
                  timestamp=None, **_: object) -> None:
        """Called by engine_registry on every fresh quote for registered symbols.

        Checks price-based alert conditions and fires matching alerts.
        """
        if not self._started:
            return
        sym = symbol.upper()
        with self._lock:
            alert_ids = list(self._price_alert_ids.get(sym, []))
            alerts = {aid: a for aid, a in self._alerts_cache.items() if aid in alert_ids}

        if not alerts:
            return

        # Compute the percent change value for pct_change_above alerts.
        pct_change: float | None = self._compute_pct_change(sym, price)

        for alert in alerts.values():
            if alert.condition_type == "price_above":
                self._try_fire(alert, price, extra_value=price)
            elif alert.condition_type == "price_below":
                self._try_fire(alert, price, extra_value=price)
            elif alert.condition_type == "pct_change_above":
                if pct_change is not None:
                    self._try_fire(alert, pct_change, extra_value=pct_change)

    # --- Bar callback (called from engine_registry, any thread) -----------

    def _on_bar(self, symbol: str, timeframe: str, price: float, volume: float = 0,
                timestamp=None, **_: object) -> None:
        """Called by engine_registry on every completed bar for registered symbols.

        Checks trend, alignment, volume, breakout/breakdown, and divergence
        alert conditions.
        """
        if not self._started:
            return
        sym = symbol.upper()
        tf = timeframe or "1d"

        with self._lock:
            alert_ids = list(getattr(self, "_bar_alert_ids", {}).get(sym, []))
            alerts = {aid: a for aid, a in self._alerts_cache.items() if aid in alert_ids}

        if not alerts:
            return

        # Pre-compute payloads for each condition group.
        trend_payload = build_trend_payload(sym, tf)
        alignment_payload = build_alignment_payload(sym)
        volume_payload = build_volume_payload(sym, tf)
        breakout_payload = build_breakout_payload(sym, tf)
        breakdown_payload = build_breakdown_payload(sym, tf)

        for alert in alerts.values():
            ct = alert.condition_type
            if ct in ("trend_crosses_above_70", "trend_crosses_below_70",
                      "trend_direction_changes", "trend_strengthens", "trend_weakens"):
                self._try_fire(alert, price, extra_value=trend_payload)
            elif ct in ("full_timeframe_alignment", "timeframe_conflict"):
                self._try_fire(alert, price, extra_value=alignment_payload)
            elif ct == "volume_expansion":
                self._try_fire(alert, price, extra_value=volume_payload)
            elif ct == "breakout":
                self._try_fire(alert, price, extra_value=breakout_payload)
            elif ct == "breakdown":
                self._try_fire(alert, price, extra_value=breakdown_payload)
            elif ct == "divergence":
                from .conditions import build_divergence_payload
                div_payload = build_divergence_payload(sym, tf)
                self._try_fire(alert, price, extra_value=div_payload)
            elif ct == "market_regime_change":
                from .conditions import build_regime_change_payload
                regime_payload = build_regime_change_payload(sym)
                self._try_fire(alert, price, extra_value=regime_payload)

    # --- Core trigger logic --------------------------------------------

    def _try_fire(self, alert: Alert, price: float | None, *,
                   extra_value: object) -> bool:
        """Evaluate an alert's condition; persist a trigger if it fires.

        Returns True if the alert fired, False otherwise.
        Dedup is applied per (alert_id, symbol) within DEDUP_WINDOW_SECONDS.
        """
        # Evaluate the condition.
        if not evaluate(alert.condition_type, alert.parameter, extra_value):
            return False

        # Check dedup window.
        key = (alert.id, alert.symbol.upper())
        with self._lock:
            last = self._fired_at.get(key, 0)
            if time.time() - last < DEDUP_WINDOW_SECONDS:
                return False
            self._fired_at[key] = time.time()

        # Persist the trigger.
        self._persist_trigger(alert, price, extra_value)
        return True

    def _persist_trigger(self, alert: Alert, price: float | None,
                         extra_value: object) -> None:
        """Insert an AlertTrigger row into the DB.

        Runs in a worker thread so it doesn't block the event loop or
        the caller.
        """
        db = SessionLocal()
        try:
            # Build the message string.
            if alert.condition_type == "signal_equals":
                observed = str(list(extra_value) if extra_value else [])
                message = f"{alert.symbol}: {alert.parameter} signal detected"
            elif alert.condition_type in ("price_above", "price_below"):
                observed = str(extra_value)
                message = f"{alert.symbol}: {alert.condition_type.replace('_', ' ')} {alert.parameter} (now {extra_value})"
            elif alert.condition_type == "pct_change_above":
                observed = str(extra_value)
                message = f"{alert.symbol}: +{extra_value:.2f}% change (threshold: {alert.parameter}%)"
            elif alert.condition_type in ("trend_crosses_above_70", "trend_crosses_below_70",
                                           "trend_direction_changes", "trend_strengthens", "trend_weakens"):
                if isinstance(extra_value, dict):
                    curr = extra_value.get("current", 0.0)
                    prev = extra_value.get("previous", 0.0)
                    curr_dir = extra_value.get("current_direction", "neutral")
                    prev_dir = extra_value.get("previous_direction", "neutral")
                    observed = f"score={curr:.1f} ({curr_dir}) prev={prev:.1f} ({prev_dir})"
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: {alert.condition_type} (param={alert.parameter})"
            elif alert.condition_type in ("full_timeframe_alignment", "timeframe_conflict"):
                if isinstance(extra_value, dict):
                    dirs = extra_value.get("directions", [])
                    observed = "timeframes=" + ",".join(dirs)
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: {alert.condition_type} ({len(dirs) if isinstance(dirs, list) else 0} timeframes)"
            elif alert.condition_type == "volume_expansion":
                if isinstance(extra_value, dict):
                    observed = (f"current={extra_value.get('current_volume', 0)}, "
                                f"avg={extra_value.get('avg_volume', 0):.0f}")
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: volume expansion (param={alert.parameter})"
            elif alert.condition_type in ("breakout", "breakdown"):
                if isinstance(extra_value, dict):
                    cp = extra_value.get("current_price", 0)
                    ref_key = "highest_high" if alert.condition_type == "breakout" else "lowest_low"
                    observed = f"price={cp}, ref={extra_value.get(ref_key, 0)}"
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: {alert.condition_type} (param={alert.parameter})"
            elif alert.condition_type == "divergence":
                if isinstance(extra_value, dict):
                    observed = f"price_chg={extra_value.get('price_change_pct', 0):.2f}%, rsi={extra_value.get('rsi_like', 0):.1f}"
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: {alert.parameter} divergence"
            elif alert.condition_type == "market_regime_change":
                if isinstance(extra_value, dict):
                    observed = (f"{extra_value.get('previous_regime', '?')} → "
                                f"{extra_value.get('current_regime', '?')}")
                else:
                    observed = str(extra_value)
                message = f"Market regime changed: {observed}"
            else:
                observed = str(extra_value) if extra_value is not None else None
                message = None

            trigger = AlertTrigger(
                alert_id=alert.id,
                symbol=alert.symbol.upper(),
                observed_value=observed,
                message=message,
            )
            db.add(trigger)
            db.commit()
            logger.info(f"Alert fired: id={alert.id} {alert.name} "
                        f"({alert.condition_type} {alert.parameter})")

            # Version 4, AI feature 3: enqueue AI commentary out-of-band.
            # This method runs inline on two latency-sensitive paths (an
            # async request handler, and a live-tick callback that gates
            # every other alert evaluation for that tick) — an AI call
            # has no safe "cheap" bound (up to AI_TIMEOUT, multi-provider
            # fallback chain), so commentary generation must never run
            # here directly. enqueue_alert_commentary_job() is itself a
            # fast, non-blocking Redis enqueue (or a no-op if Redis is
            # down) — but wrapped in its own try/except anyway so a
            # Redis hiccup can never turn a successful trigger-persist
            # (already committed above) into a logged failure.
            try:
                from backend.ai.background import enqueue_alert_commentary_job
                enqueue_alert_commentary_job(trigger.id)
            except Exception as e:
                logger.warning(f"Failed to enqueue alert commentary for trigger {trigger.id}: {e}")
        except Exception as e:
            logger.error(f"Failed to persist alert trigger for alert {alert.id}: {e}")
            db.rollback()
        finally:
            db.close()

    def _compute_pct_change(self, symbol: str, current_price: float) -> float | None:
        """Compute the 1-day percent change from the latest stored quote."""
        db = SessionLocal()
        try:
            from backend.models import QuoteModel
            now = datetime.now(UTC)
            cutoff = now - timedelta(hours=26)  # allow for market closed hours

            latest = (
                db.query(QuoteModel)
                .filter(
                    QuoteModel.symbol == symbol,
                    QuoteModel.timestamp <= now - timedelta(minutes=5),  # not <5min old
                    QuoteModel.timestamp >= cutoff,
                )
                .order_by(QuoteModel.timestamp.desc())
                .limit(2)
                .all()
            )
            if len(latest) < 2:
                return None
            old_price = float(latest[1].price or 0)
            if old_price <= 0:
                return None
            return (current_price - old_price) / old_price * 100.0
        except Exception as e:
            logger.debug(f"Could not compute pct_change for {symbol}: {e}")
            return None
        finally:
            db.close()


# Process-wide singleton. Imported by the router and main.py.
alerts_engine = AlertsEngine()
