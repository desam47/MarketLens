"""
Alert repository for data access operations.
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, desc, func, inspect

from backend.database import SessionLocal
from backend.models import Alert, AlertDelivery, AlertTrigger
from backend.utils.timezone import now_ny


class AlertRepository:
    """Repository for alert CRUD operations."""

    def __init__(self, db=None):
        self._owns_session = db is None
        self.db = db or SessionLocal()

    def close(self) -> None:
        if self._owns_session:
            self.db.close()

    # --- Read -----------------------------------------------------------

    def get_all(self) -> list[Alert]:
        """All alerts, newest first."""
        return (
            self.db.query(Alert)
            .order_by(desc(Alert.created_at))
            .all()
        )

    def get_all_enabled(self) -> list[Alert]:
        """All enabled alerts."""
        return (
            self.db.query(Alert)
            .filter(Alert.is_enabled)
            .order_by(desc(Alert.created_at))
            .all()
        )

    def get_by_id(self, alert_id: int) -> Alert | None:
        return self.db.query(Alert).filter(Alert.id == alert_id).first()

    def get_for_symbol(self, symbol: str) -> list[Alert]:
        """All alerts (enabled or not) for a given symbol."""
        return (
            self.db.query(Alert)
            .filter(Alert.symbol == symbol.upper())
            .order_by(desc(Alert.created_at))
            .all()
        )

    def get_enabled_for_symbol(self, symbol: str) -> list[Alert]:
        """All enabled alerts for a given symbol."""
        return (
            self.db.query(Alert)
            .filter(
                and_(
                    Alert.symbol == symbol.upper(),
                    Alert.is_enabled,
                )
            )
            .all()
        )

    # --- Write ---------------------------------------------------------

    def create(
        self,
        name: str,
        symbol: str,
        condition_type: str,
        parameter: str,
    ) -> Alert:
        alert = Alert(
            name=name,
            symbol=symbol.upper(),
            condition_type=condition_type,
            parameter=parameter,
        )
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return alert

    def update(
        self,
        alert_id: int,
        name: str | None = None,
        condition_type: str | None = None,
        parameter: str | None = None,
        is_enabled: bool | None = None,
    ) -> Alert | None:
        alert = self.get_by_id(alert_id)
        if alert is None:
            return None
        if name is not None:
            alert.name = name
        if condition_type is not None:
            alert.condition_type = condition_type
        if parameter is not None:
            alert.parameter = parameter
        if is_enabled is not None:
            alert.is_enabled = is_enabled
        alert.updated_at = now_ny()
        self.db.commit()
        self.db.refresh(alert)
        return alert

    def delete(self, alert_id: int) -> bool:
        alert = self.get_by_id(alert_id)
        if alert is None:
            return False
        self.db.delete(alert)
        self.db.commit()
        return True

    # --- Triggers ------------------------------------------------------

    def get_triggers(self, alert_id: int, limit: int = 100) -> list[AlertTrigger]:
        return (
            self.db.query(AlertTrigger)
            .filter(AlertTrigger.alert_id == alert_id)
            .order_by(desc(AlertTrigger.triggered_at))
            .limit(limit)
            .all()
        )

    def get_deliveries(self, alert_id: int, limit: int = 100) -> list[AlertDelivery]:
        return (
            self.db.query(AlertDelivery)
            .join(AlertTrigger, AlertDelivery.trigger_id == AlertTrigger.id)
            .filter(AlertTrigger.alert_id == alert_id)
            .order_by(desc(AlertDelivery.created_at))
            .limit(limit)
            .all()
        )

    def get_delivery(self, delivery_id: int) -> AlertDelivery | None:
        return self.db.query(AlertDelivery).filter(AlertDelivery.id == delivery_id).first()

    def get_delivery_summary(self) -> dict:
        """Return persisted notification counts for the alert operations view."""
        if not inspect(self.db.get_bind()).has_table("alert_deliveries"):
            return {"total": 0, "by_status": {}, "by_channel": {}}
        status_rows = (
            self.db.query(AlertDelivery.status, func.count(AlertDelivery.id))
            .group_by(AlertDelivery.status)
            .all()
        )
        channel_rows = (
            self.db.query(AlertDelivery.channel, func.count(AlertDelivery.id))
            .group_by(AlertDelivery.channel)
            .all()
        )
        return {
            "total": sum(count for _, count in status_rows),
            "by_status": {status: count for status, count in status_rows},
            "by_channel": {channel: count for channel, count in channel_rows},
        }

    def delete_all_triggers(self) -> int:
        """Clear every trigger row — the fired-alert history log, not
        the alerts themselves. Also sweeps up any trigger orphaned by
        an alert that's since been deleted (delete_alert() cascades
        correctly today via the ORM relationship, but rows created
        before that could still be dangling). Returns the count
        removed."""
        if inspect(self.db.get_bind()).has_table("alert_deliveries"):
            self.db.query(AlertDelivery).delete(synchronize_session=False)
        n = self.db.query(AlertTrigger).delete(synchronize_session=False)
        self.db.commit()
        return n

    def get_recent_triggers(self, since: datetime | None = None) -> list[AlertTrigger]:
        """Triggers fired within the last 24 hours (or since ``since``)."""
        if since is None:
            # AlertTrigger.triggered_at is naive NY-local (project convention —
            # see now_ny()/models/alert.py), so the cutoff must be too. This
            # used to compare against datetime.now(UTC), which — being ~4-5h
            # ahead of NY — silently excluded up to that many hours of
            # genuinely recent triggers from the "last 24 hours" query.
            since = now_ny() - timedelta(hours=24)
        return (
            self.db.query(AlertTrigger)
            .filter(AlertTrigger.triggered_at >= since)
            .order_by(desc(AlertTrigger.triggered_at))
            .all()
        )
