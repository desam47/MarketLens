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

import json
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

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

# Condition-type groupings (2026-09-11: hoisted to module level — these
# used to be redefined as identical local tuples in three separate
# methods below, which is exactly how "signal_equals" once fell through
# every one of them and silently stopped evaluating until the next
# restart; one copy can't drift out of sync with itself).
#
# Price-based: fire on every quote tick (_on_quote).
PRICE_CONDITIONS: tuple[str, ...] = ("price_above", "price_below", "pct_change_above")

# Bar-based: fire on every completed bar (_on_bar). Grouped by which
# payload builder they need — _on_bar uses these groups to build only
# the payload(s) actually required for the alerts registered on a
# symbol, instead of building all of them (11 DB queries, 7 of those
# just for alignment) on every single bar tick regardless of which
# condition types are actually in use.
TREND_CONDITIONS: tuple[str, ...] = (
    "trend_crosses_above_70",
    "trend_crosses_below_70",
    "trend_direction_changes",
    "trend_strengthens",
    "trend_weakens",
)
ALIGNMENT_CONDITIONS: tuple[str, ...] = ("full_timeframe_alignment", "timeframe_conflict")
VOLUME_CONDITIONS: tuple[str, ...] = ("volume_expansion",)
BREAKOUT_CONDITIONS: tuple[str, ...] = ("breakout",)
BREAKDOWN_CONDITIONS: tuple[str, ...] = ("breakdown",)
# divergence / market_regime_change are already computed lazily, one
# builder call per matching alert — not part of the eager-build groups.
DIVERGENCE_CONDITIONS: tuple[str, ...] = ("divergence",)
REGIME_CONDITIONS: tuple[str, ...] = ("market_regime_change",)
SIGNAL_PROFILE_CONDITIONS: tuple[str, ...] = ("signal_profile",)
AUX_CONDITIONS: tuple[str, ...] = (
    "news_arrival",
    "insider_sentiment_change",
    "options_activity_change",
)

BAR_CONDITIONS: tuple[str, ...] = (
    TREND_CONDITIONS
    + ALIGNMENT_CONDITIONS
    + VOLUME_CONDITIONS
    + BREAKOUT_CONDITIONS
    + BREAKDOWN_CONDITIONS
    + DIVERGENCE_CONDITIONS
    + REGIME_CONDITIONS
    + SIGNAL_PROFILE_CONDITIONS
)


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
        # Auxiliary data is checked from scanner refreshes rather than every
        # quote/bar tick. The first provider read establishes a baseline.
        self._aux_state: dict[tuple[str, str], dict] = {}
        self._aux_checked_at: dict[tuple[str, str], float] = {}
        self._aux_lock = threading.Lock()
        self._aux_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alerts-aux")
        self._aux_poll_interval_seconds = 60.0

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

        current_price_symbols = set(self._price_alert_ids.keys())
        current_bar_symbols = (
            set(self._bar_alert_ids.keys()) if hasattr(self, "_bar_alert_ids") else set()
        )
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

        logger.info(
            f"AlertsEngine loaded {len(alerts)} enabled alerts "
            f"({len(new_price_symbols)} price symbols, {len(new_bar_symbols)} bar symbols)"
        )

    def evaluate_scan_result(self, result) -> None:
        """Evaluate scanner-signal alerts and schedule auxiliary checks.

        Called from the scanner router after each full scan. ``result``
        is a ScanResult with a .signals list and a .symbol field.
        """
        if not self._started:
            return
        sym = result.symbol.upper()
        with self._lock:
            # Snapshot the current alert IDs so we can release the lock.
            candidates = [
                a
                for a in self._alerts_cache.values()
                if a.symbol.upper() == sym and a.condition_type == "signal_equals" and a.is_enabled
            ]

        for alert in candidates:
            price = result.quote.price if result.quote else None
            self._try_fire(alert, price, extra_value=result.signals)
        self._schedule_aux_evaluation(sym, result.quote.price if result.quote else None)

    def register_for_alert(self, alert: Alert) -> None:
        """Register a callback when a new alert is created.

        Called from the alerts router's POST handler so the engine starts
        evaluating the new alert immediately, without waiting for the
        next startup reload.
        """
        if alert.condition_type == "signal_equals":
            # No engine_registry callback needed — evaluate_scan_result()
            # iterates _alerts_cache directly against every fresh scan
            # result; there's no per-symbol subscription for this
            # condition type. Just needs to be visible in the cache.
            # Found live 2026-09-09: signal_equals fell straight through
            # the "not in PRICE_CONDITIONS + BAR_CONDITIONS" check below
            # (it's in neither tuple) and was silently never added to
            # _alerts_cache here — a freshly-created signal_equals alert
            # didn't actually evaluate until the next full _reload()
            # (i.e. a server restart), despite this method's own
            # docstring promising "the engine starts evaluating the new
            # alert immediately."
            with self._lock:
                self._alerts_cache[alert.id] = alert
            return
        if alert.condition_type not in PRICE_CONDITIONS + BAR_CONDITIONS + AUX_CONDITIONS:
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
            # Auxiliary conditions are evaluated on scanner refreshes; no
            # market-data callback is needed here.

    def unregister_for_alert(self, alert: Alert) -> None:
        """Remove a callback when an alert is deleted or disabled.

        Called from the alerts router's DELETE / PUT (is_enabled=False) handlers.
        """
        if alert.condition_type == "signal_equals":
            # Mirrors register_for_alert's signal_equals branch — no
            # engine_registry callback to unregister, just drop it from
            # the cache so a disabled/deleted alert stops being
            # evaluated immediately rather than surviving until the
            # next full reload.
            with self._lock:
                self._alerts_cache.pop(alert.id, None)
            return
        if alert.condition_type not in PRICE_CONDITIONS + BAR_CONDITIONS + AUX_CONDITIONS:
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

    def _schedule_aux_evaluation(self, symbol: str, price: float | None) -> None:
        """Refresh auxiliary providers at most once per minute per rule."""
        sym = symbol.upper()
        with self._lock:
            candidates = [
                alert
                for alert in self._alerts_cache.values()
                if alert.symbol.upper() == sym
                and alert.condition_type in AUX_CONDITIONS
                and alert.is_enabled
            ]
        if not candidates:
            return
        now = time.time()
        due: list[Alert] = []
        with self._aux_lock:
            for alert in candidates:
                key = (alert.condition_type, sym)
                if now - self._aux_checked_at.get(key, 0.0) >= self._aux_poll_interval_seconds:
                    self._aux_checked_at[key] = now
                    due.append(alert)
        if due:
            self._aux_executor.submit(self._evaluate_aux_alerts, sym, price, due)

    def _evaluate_aux_alerts(self, symbol: str, price: float | None, alerts: list[Alert]) -> None:
        by_condition: dict[str, list[Alert]] = defaultdict(list)
        for alert in alerts:
            by_condition[alert.condition_type].append(alert)
        for condition_type, condition_alerts in by_condition.items():
            try:
                payload = self._build_aux_payload(condition_type, symbol)
                if payload is not None:
                    for alert in condition_alerts:
                        self._try_fire(alert, price, extra_value=payload)
            except Exception:
                logger.warning(
                    "Auxiliary alert evaluation failed for %s/%s",
                    symbol,
                    condition_type,
                    exc_info=True,
                )

    def _build_aux_payload(self, condition_type: str, symbol: str) -> dict | None:
        """Build a provider-change payload, establishing a baseline first."""
        key = (condition_type, symbol)
        if condition_type == "news_arrival":
            from backend.aux_data.services.manager import aux_data_manager

            response = aux_data_manager.get_news(symbol, limit=20)
            items = list(response.items or [])
            identities = {
                f"{item.timestamp.isoformat()}|{item.headline}|{item.url or ''}" for item in items
            }
            with self._aux_lock:
                previous = self._aux_state.get(key, {}).get("news_ids")
                self._aux_state[key] = {"news_ids": identities}
            if previous is None:
                return None
            new_items = [
                item
                for item in items
                if f"{item.timestamp.isoformat()}|{item.headline}|{item.url or ''}" not in previous
            ]
            return {
                "new_count": len(new_items),
                "max_relevance": max((float(item.relevance) for item in new_items), default=0.0),
                "headline": new_items[0].headline if new_items else None,
            }

        if condition_type == "insider_sentiment_change":
            from backend.market_data.services.finnhub_service import finnhub_service

            rows = finnhub_service.get_insider_sentiment(
                symbol,
                date.today() - timedelta(days=365),
                date.today(),
            )
            rows = [row for row in rows if row.sentiment is not None]
            rows.sort(key=lambda row: (row.year, row.month))
            if len(rows) < 2:
                return None
            current, previous = rows[-1], rows[-2]
            current_value = float(current.sentiment)
            previous_value = float(previous.sentiment)
            with self._aux_lock:
                prior_period = self._aux_state.get(key, {}).get("period")
                self._aux_state[key] = {
                    "period": (current.year, current.month),
                    "sentiment": current_value,
                }
            if prior_period is None:
                return None
            if prior_period == (current.year, current.month):
                return {"current_sentiment": previous_value, "previous_sentiment": previous_value}
            return {
                "current_sentiment": current_value,
                "previous_sentiment": previous_value,
                "period": f"{current.year}-{current.month:02d}",
            }

        if condition_type == "options_activity_change":
            from backend.aux_data.services.manager import aux_data_manager

            response = aux_data_manager.get_options(symbol)
            call_volume = sum(int(chain.total_call_volume or 0) for chain in response.chains)
            put_volume = sum(int(chain.total_put_volume or 0) for chain in response.chains)
            call_oi = sum(
                sum(int(contract.open_interest or 0) for contract in chain.calls)
                for chain in response.chains
            )
            put_oi = sum(
                sum(int(contract.open_interest or 0) for contract in chain.puts)
                for chain in response.chains
            )
            current = {
                "volume": call_volume + put_volume,
                "open_interest": call_oi + put_oi,
                "call_volume": call_volume,
                "put_volume": put_volume,
            }
            with self._aux_lock:
                previous = self._aux_state.get(key)
                self._aux_state[key] = current
            if previous is None:
                return None

            def pct_change(now_value: int, old_value: int) -> float | None:
                return ((now_value - old_value) / old_value * 100.0) if old_value else None

            return {
                "volume_change_pct": pct_change(current["volume"], previous["volume"]),
                "open_interest_change_pct": pct_change(
                    current["open_interest"], previous["open_interest"]
                ),
                "volume": current["volume"],
                "open_interest": current["open_interest"],
                "previous_volume": previous["volume"],
                "previous_open_interest": previous["open_interest"],
            }
        return None

    # --- Price callback (called from engine_registry, any thread) -------

    def _on_quote(
        self, symbol: str, price: float, volume: float = 0, timestamp=None, **_: object
    ) -> None:
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

    def _on_bar(
        self,
        symbol: str,
        timeframe: str,
        price: float,
        volume: float = 0,
        timestamp=None,
        **_: object,
    ) -> None:
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

        # Build only the payload(s) actually needed for the condition
        # types registered on this symbol (2026-09-11) — each builder is
        # its own DB round trip (alignment alone is 7 queries, one per
        # timeframe), so unconditionally building all five on every bar
        # tick meant up to 11 queries even for a symbol with a single
        # breakout alert and nothing else.
        condition_types = {a.condition_type for a in alerts.values()}
        trend_payload = (
            build_trend_payload(sym, tf)
            if condition_types & (set(TREND_CONDITIONS) | set(SIGNAL_PROFILE_CONDITIONS))
            else None
        )
        signal_profile_payload = None
        if SIGNAL_PROFILE_CONDITIONS[0] in condition_types and trend_payload is not None:
            signal_profile_payload = dict(trend_payload)
            signal_profile_payload["timeframe"] = tf
            signal_profile_payload["symbol"] = sym
            signal_profile_payload["strength"] = min(
                abs(float(trend_payload.get("current", 0.0))) / 100.0, 1.0
            )
            from .conditions import build_regime_change_payload

            signal_profile_payload.update(build_regime_change_payload(sym))
        alignment_payload = (
            build_alignment_payload(sym) if condition_types & set(ALIGNMENT_CONDITIONS) else None
        )
        volume_payload = (
            build_volume_payload(sym, tf) if condition_types & set(VOLUME_CONDITIONS) else None
        )
        breakout_payload = (
            build_breakout_payload(sym, tf) if condition_types & set(BREAKOUT_CONDITIONS) else None
        )
        breakdown_payload = (
            build_breakdown_payload(sym, tf)
            if condition_types & set(BREAKDOWN_CONDITIONS)
            else None
        )

        for alert in alerts.values():
            ct = alert.condition_type
            if ct in TREND_CONDITIONS:
                self._try_fire(alert, price, extra_value=trend_payload)
            elif ct in SIGNAL_PROFILE_CONDITIONS:
                self._try_fire(alert, price, extra_value=signal_profile_payload)
            elif ct in ALIGNMENT_CONDITIONS:
                self._try_fire(alert, price, extra_value=alignment_payload)
            elif ct in VOLUME_CONDITIONS:
                self._try_fire(alert, price, extra_value=volume_payload)
            elif ct in BREAKOUT_CONDITIONS:
                self._try_fire(alert, price, extra_value=breakout_payload)
            elif ct in BREAKDOWN_CONDITIONS:
                self._try_fire(alert, price, extra_value=breakdown_payload)
            elif ct in DIVERGENCE_CONDITIONS:
                from .conditions import build_divergence_payload

                div_payload = build_divergence_payload(sym, tf)
                self._try_fire(alert, price, extra_value=div_payload)
            elif ct in REGIME_CONDITIONS:
                from .conditions import build_regime_change_payload

                regime_payload = build_regime_change_payload(sym)
                self._try_fire(alert, price, extra_value=regime_payload)

    # --- Core trigger logic --------------------------------------------

    def _try_fire(self, alert: Alert, price: float | None, *, extra_value: object) -> bool:
        """Evaluate an alert's condition; persist a trigger if it fires.

        Returns True if the alert fired, False otherwise.
        Dedup is applied per (alert_id, symbol) within DEDUP_WINDOW_SECONDS.
        """
        if alert.condition_type == "signal_profile":
            try:
                profile = json.loads(alert.parameter or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                profile = {}
            snoozed_until = profile.get("snoozed_until") if isinstance(profile, dict) else None
            if snoozed_until:
                try:
                    until = datetime.fromisoformat(str(snoozed_until).replace("Z", "+00:00"))
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=UTC)
                    if until > datetime.now(UTC):
                        return False
                except (TypeError, ValueError):
                    pass

        # Evaluate the condition.
        if not evaluate(alert.condition_type, alert.parameter, extra_value):
            return False

        # Check dedup window.
        key = (alert.id, alert.symbol.upper())
        with self._lock:
            last = self._fired_at.get(key, 0)
            window = DEDUP_WINDOW_SECONDS
            if alert.condition_type == "signal_profile":
                try:
                    profile = json.loads(alert.parameter or "{}")
                    window = max(60, int(profile.get("cooldown_minutes", 60)) * 60)
                except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                    pass
            if time.time() - last < window:
                return False
            self._fired_at[key] = time.time()

        # Persist the trigger.
        self._persist_trigger(alert, price, extra_value)
        return True

    def _persist_trigger(self, alert: Alert, price: float | None, extra_value: object) -> None:
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
                message = (
                    f"{alert.symbol}: +{extra_value:.2f}% change (threshold: {alert.parameter}%)"
                )
            elif alert.condition_type in (
                "trend_crosses_above_70",
                "trend_crosses_below_70",
                "trend_direction_changes",
                "trend_strengthens",
                "trend_weakens",
            ):
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
                    observed = (
                        f"current={extra_value.get('current_volume', 0)}, "
                        f"avg={extra_value.get('avg_volume', 0):.0f}"
                    )
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
                    observed = (
                        f"{extra_value.get('previous_regime', '?')} → "
                        f"{extra_value.get('current_regime', '?')}"
                    )
                else:
                    observed = str(extra_value)
                message = f"Market regime changed: {observed}"
            elif alert.condition_type == "news_arrival":
                if isinstance(extra_value, dict):
                    observed = f"{extra_value.get('new_count', 0)} new headline(s)"
                    headline = extra_value.get("headline")
                    if headline:
                        observed += f": {headline}"
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: new news arrived"
            elif alert.condition_type == "insider_sentiment_change":
                if isinstance(extra_value, dict):
                    observed = (
                        f"sentiment={extra_value.get('current_sentiment', 0):.3f}, "
                        f"previous={extra_value.get('previous_sentiment', 0):.3f}"
                    )
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: insider sentiment changed"
            elif alert.condition_type == "options_activity_change":
                if isinstance(extra_value, dict):
                    volume_change = extra_value.get("volume_change_pct")
                    oi_change = extra_value.get("open_interest_change_pct")
                    observed = (
                        f"volume Δ={volume_change if volume_change is not None else 0:.1f}%, "
                        f"open interest Δ={oi_change if oi_change is not None else 0:.1f}%"
                    )
                else:
                    observed = str(extra_value)
                message = f"{alert.symbol}: options activity changed"
            elif alert.condition_type == "signal_profile":
                observed = str(extra_value)
                try:
                    profile = json.loads(alert.parameter or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    profile = {}
                message = (
                    f"{alert.symbol}: signal profile matched ({profile.get('direction', 'any')})"
                )
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
            logger.info(
                f"Alert fired: id={alert.id} {alert.name} "
                f"({alert.condition_type} {alert.parameter})"
            )

            try:
                from backend.notifications import dispatch_trigger_async

                dispatch_trigger_async(trigger.id)
            except Exception as e:
                logger.warning(f"Failed to enqueue alert delivery for trigger {trigger.id}: {e}")

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
