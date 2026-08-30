"""
Alert API endpoints.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.alerts.conditions import VALID_CONDITION_TYPES
from backend.alerts.engine import alerts_engine
from backend.repositories.alert_repository import AlertRepository

from backend.api.dependencies import get_db

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# --- Request / Response models -----------------------------------------


class AlertCreate(BaseModel):
    name: str
    symbol: str
    condition_type: str
    parameter: str

    def model_post_init(self, _):
        if self.condition_type not in VALID_CONDITION_TYPES:
            raise ValueError(
                f"condition_type must be one of {list(VALID_CONDITION_TYPES)}"
            )


class AlertUpdate(BaseModel):
    name: str | None = None
    condition_type: str | None = None
    parameter: str | None = None
    is_enabled: bool | None = None

    def model_post_init(self, _):
        if self.condition_type is not None and self.condition_type not in VALID_CONDITION_TYPES:
            raise ValueError(
                f"condition_type must be one of {list(VALID_CONDITION_TYPES)}"
            )


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    symbol: str
    condition_type: str
    parameter: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertTriggerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alert_id: int
    symbol: str
    observed_value: str | None
    message: str | None
    triggered_at: datetime


# --- Endpoints ----------------------------------------------------------


@router.get("/", response_model=list[AlertResponse])
def list_alerts(db: Session = Depends(get_db)):
    """List all alerts."""
    repo = AlertRepository(db)
    return repo.get_all()


@router.post("/", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def create_alert(payload: AlertCreate, db: Session = Depends(get_db)):
    """Create a new alert."""
    repo = AlertRepository(db)
    alert = repo.create(
        name=payload.name,
        symbol=payload.symbol,
        condition_type=payload.condition_type,
        parameter=payload.parameter,
    )
    # Immediately register the price callback so the engine evaluates it.
    alerts_engine.register_for_alert(alert)
    return alert


@router.get("/active", response_model=list[AlertTriggerResponse])
def list_active_triggers(db: Session = Depends(get_db)):
    """Triggers fired in the last 24 hours."""
    repo = AlertRepository(db)
    return repo.get_recent_triggers()


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    """Get a single alert by ID."""
    repo = AlertRepository(db)
    alert = repo.get_by_id(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.put("/{alert_id}", response_model=AlertResponse)
def update_alert(alert_id: int, payload: AlertUpdate, db: Session = Depends(get_db)):
    """Update an existing alert."""
    repo = AlertRepository(db)
    existing = repo.get_by_id(alert_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Capture old state for engine registration management.
    was_price_alert = existing.condition_type in (
        "price_above", "price_below", "pct_change_above"
    )

    updated = repo.update(
        alert_id,
        name=payload.name,
        condition_type=payload.condition_type,
        parameter=payload.parameter,
        is_enabled=payload.is_enabled,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    is_price_alert = updated.condition_type in (
        "price_above", "price_below", "pct_change_above"
    )

    if was_price_alert and (not is_price_alert or not updated.is_enabled):
        alerts_engine.unregister_for_alert(updated)
    elif is_price_alert and updated.is_enabled:
        alerts_engine.register_for_alert(updated)

    return updated


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(alert_id: int, db: Session = Depends(get_db)):
    """Delete an alert and all its triggers."""
    repo = AlertRepository(db)
    existing = repo.get_by_id(alert_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    alerts_engine.unregister_for_alert(existing)
    repo.delete(alert_id)


@router.get("/{alert_id}/triggers", response_model=list[AlertTriggerResponse])
def get_alert_triggers(alert_id: int, limit: int = 100, db: Session = Depends(get_db)):
    """Trigger history for a specific alert."""
    repo = AlertRepository(db)
    if repo.get_by_id(alert_id) is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return repo.get_triggers(alert_id, limit=limit)
