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
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_

from backend.database import SessionLocal
from backend.market_data.services.engine_seeder import engine_registry
from backend.models import Alert, AlertTrigger

from .conditions import evaluate

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
            alerts = db.query(Alert).filter(Alert.is_enabled == True).all()
        finally:
            db.close()

        # Rebuild the alerts cache.
        self._alerts_cache = {a.id: a for a in alerts}

        # Rebuild the price symbol → alert_ids map, re-registering
        # callbacks for any symbols that are new.
        current_symbols = set(self._price_alert_ids.keys())
        new_symbols: set[str] = set()
        for alert in alerts:
            if alert.condition_type in ("price_above", "price_below", "pct_change_above"):
                sym = alert.symbol.upper()
                new_symbols.add(sym)
                if sym not in self._price_alert_ids or alert.id not in self._price_alert_ids[sym]:
                    self._price_alert_ids.setdefault(sym, []).append(alert.id)
                    if sym not in current_symbols:
                        engine_registry.register("quote", sym, self._on_quote)

        # Unregister callbacks for symbols that no longer have any price alerts.
        for sym in current_symbols - new_symbols:
            for alert_id in self._price_alert_ids.get(sym, []):
                # The callback is shared per symbol, so we only need to
                # unregister once. Track which we've unregistered.
                pass
            if sym in self._price_alert_ids:
                engine_registry.unregister("quote", sym, self._on_quote)
                del self._price_alert_ids[sym]

        logger.info(f"AlertsEngine loaded {len(alerts)} enabled alerts "
                    f"({len(new_symbols)} symbols with price alerts)")

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
        """Register a price-alert callback when a new alert is created.

        Called from the alerts router's POST handler so the engine starts
        evaluating the new alert immediately, without waiting for the
        next startup reload.
        """
        if alert.condition_type not in ("price_above", "price_below", "pct_change_above"):
            return
        sym = alert.symbol.upper()
        with self._lock:
            self._alerts_cache[alert.id] = alert
            already_registered = sym in self._price_alert_ids
            self._price_alert_ids.setdefault(sym, []).append(alert.id)
        if not already_registered:
            engine_registry.register("quote", sym, self._on_quote)

    def unregister_for_alert(self, alert: Alert) -> None:
        """Remove a price-alert callback when an alert is deleted or disabled.

        Called from the alerts router's DELETE / PUT (is_enabled=False) handlers.
        """
        if alert.condition_type not in ("price_above", "price_below", "pct_change_above"):
            return
        sym = alert.symbol.upper()
        with self._lock:
            self._alerts_cache.pop(alert.id, None)
            # ``.remove`` throws ValueError if the alert id isn't in the
            # list — that can happen if the engine was restarted between
            # the alert's last registration and this unregister call.
            # Treat that as a no-op so a delete always succeeds.
            ids = self._price_alert_ids.get(sym)
            if ids and alert.id in ids:
                ids.remove(alert.id)
            if not self._price_alert_ids.get(sym):
                self._price_alert_ids.pop(sym, None)
                engine_registry.unregister("quote", sym, self._on_quote)

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
            now = datetime.now(timezone.utc)
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
