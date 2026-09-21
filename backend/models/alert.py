"""
Alert and AlertTrigger SQLAlchemy models.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.database import Base
from backend.utils.timezone import now_ny


class Alert(Base):
    """User-defined alert rule.

    Evaluated by AlertsEngine when fresh scanner results or live quotes arrive.
    The ``condition_type`` field drives which evaluator branch fires.
    """

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    condition_type = Column(String(40), nullable=False)
    # Signal-profile alerts store a compact JSON rule (filters, cooldown,
    # and optional snooze deadline); keep enough room for that payload while
    # preserving the same column for the simpler legacy conditions.
    parameter = Column(String(500), nullable=False)
    is_enabled = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=now_ny)
    updated_at = Column(DateTime, default=now_ny, onupdate=now_ny)

    # Relationship to triggers
    triggers = relationship("AlertTrigger", back_populates="alert", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Alert(id={self.id}, name={self.name!r}, symbol={self.symbol}, condition={self.condition_type})>"


class AlertTrigger(Base):
    """Historical record of every time an alert fired.

    ``AlertEngine`` writes a row here on every trigger. The
    ``/api/alerts/active`` endpoint reads rows younger than 24 hours
    so dashboards can surface recent events.
    """

    __tablename__ = "alert_triggers"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    triggered_at = Column(DateTime, default=now_ny, index=True)
    observed_value = Column(String(120), nullable=True)
    message = Column(String(255), nullable=True)
    # Version 4, AI feature 3: a short AI-generated note explaining why
    # this condition fired, populated asynchronously (RQ job enqueued
    # from AlertsEngine._persist_trigger() after this row commits — see
    # backend/ai/alert_commentary.py) — never blocks the trigger-persist
    # path, which runs inline in a live-tick callback and must stay fast.
    # NULL means "no commentary yet" (pending, AI off, or failed) — all
    # three collapse to the same UI treatment (nothing shown extra).
    ai_commentary = Column(Text, nullable=True)

    # Relationship back to the alert
    alert = relationship("Alert", back_populates="triggers")
    deliveries = relationship(
        "AlertDelivery",
        back_populates="trigger",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<AlertTrigger(id={self.id}, alert_id={self.alert_id}, symbol={self.symbol})>"


class AlertDelivery(Base):
    """Delivery attempt for an alert trigger and one notification channel."""

    __tablename__ = "alert_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    trigger_id = Column(
        Integer, ForeignKey("alert_triggers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key = Column(String(120), nullable=True, unique=True, index=True)
    channel = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    response = Column(Text, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_ny)

    trigger = relationship("AlertTrigger", back_populates="deliveries")

    def __repr__(self):
        return f"<AlertDelivery(id={self.id}, trigger_id={self.trigger_id}, channel={self.channel}, status={self.status})>"
