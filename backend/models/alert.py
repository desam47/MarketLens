"""
Alert and AlertTrigger SQLAlchemy models.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from backend.database import Base


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
    parameter = Column(String(120), nullable=False)
    is_enabled = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    triggered_at = Column(DateTime, default=datetime.utcnow, index=True)
    observed_value = Column(String(120), nullable=True)
    message = Column(String(255), nullable=True)

    # Relationship back to the alert
    alert = relationship("Alert", back_populates="triggers")

    def __repr__(self):
        return f"<AlertTrigger(id={self.id}, alert_id={self.alert_id}, symbol={self.symbol})>"
