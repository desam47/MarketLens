"""
Alert API endpoints.
"""
import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.orm import Session

from backend.alerts.conditions import VALID_CONDITION_TYPES
from backend.alerts.engine import alerts_engine
from backend.api.dependencies import get_db
from backend.api.rate_limit import _alerts_limiter, check_rate_limit
from backend.repositories.alert_repository import AlertRepository
from backend.utils.timezone import format_edt_iso

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

    # created_at/updated_at are naive NY-local (project convention — see
    # now_ny() in models/alert.py). Without an explicit serializer, Pydantic
    # emits a naive ISO string with no offset (e.g. "2026-09-08T19:59:00"),
    # which the frontend's parseET()/`new Date()` then misinterprets as the
    # VIEWER'S OWN BROWSER-LOCAL time instead of ET — every other timestamp
    # in the API goes through format_edt_iso for exactly this reason.
    @field_serializer("created_at", "updated_at")
    def _serialize_et(self, value: datetime) -> str | None:
        return format_edt_iso(value)


class ClearTriggersResponse(BaseModel):
    deleted: int


class AlertTriggerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alert_id: int
    symbol: str
    observed_value: str | None
    message: str | None
    triggered_at: datetime
    # Version 4, AI feature 3 — populated asynchronously after the
    # trigger fires; NULL/None means no commentary yet (pending, AI
    # off, or generation failed — all three collapse to the same
    # "nothing extra to show" UI treatment).
    ai_commentary: str | None = None

    @field_serializer("triggered_at")
    def _serialize_et(self, value: datetime) -> str | None:
        return format_edt_iso(value)


class AlertDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    trigger_id: int
    channel: str
    status: str
    attempts: int
    response: str | None
    delivered_at: datetime | None
    created_at: datetime

    @field_serializer("delivered_at", "created_at")
    def _serialize_delivery_et(self, value: datetime | None) -> str | None:
        return format_edt_iso(value)


class AlertTestDeliveryRequest(BaseModel):
    channel: str


class AlertTestDeliveryResponse(BaseModel):
    channel: str
    status: str
    response: str


# --- Endpoints ----------------------------------------------------------


@router.get("/", response_model=list[AlertResponse])
async def list_alerts(db: Session = Depends(get_db)):
    """List all alerts."""
    repo = AlertRepository(db)
    return await asyncio.to_thread(repo.get_all)


@router.post("/", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
async def create_alert(
    payload: AlertCreate,
    db: Session = Depends(get_db),
    _rl: None = Depends(check_rate_limit(_alerts_limiter)),
):
    """Create a new alert."""
    repo = AlertRepository(db)

    def _do_create():
        return repo.create(
            name=payload.name,
            symbol=payload.symbol,
            condition_type=payload.condition_type,
            parameter=payload.parameter,
        )

    alert = await asyncio.to_thread(_do_create)
    # Immediately register the price callback so the engine evaluates it.
    await asyncio.to_thread(alerts_engine.register_for_alert, alert)
    return alert


@router.get("/active", response_model=list[AlertTriggerResponse])
async def list_active_triggers(db: Session = Depends(get_db)):
    """Triggers fired in the last 24 hours."""
    repo = AlertRepository(db)
    return await asyncio.to_thread(repo.get_recent_triggers)


@router.delete("/triggers", response_model=ClearTriggersResponse)
async def clear_triggers(db: Session = Depends(get_db)):
    """Permanently clear the fired-alert trigger history (not the
    alerts themselves) — including any row orphaned by an alert
    deleted before this cascade-delete relationship existed."""
    repo = AlertRepository(db)
    deleted = await asyncio.to_thread(repo.delete_all_triggers)
    return ClearTriggersResponse(deleted=deleted)


@router.get("/{alert_id}/deliveries", response_model=list[AlertDeliveryResponse])
async def list_alert_deliveries(alert_id: int, limit: int = 100, db: Session = Depends(get_db)):
    """Delivery attempts for one alert, newest first."""
    repo = AlertRepository(db)
    if await asyncio.to_thread(repo.get_by_id, alert_id) is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return await asyncio.to_thread(repo.get_deliveries, alert_id, limit=limit)


@router.post("/deliveries/{delivery_id}/retry", response_model=AlertDeliveryResponse)
async def retry_alert_delivery(delivery_id: int, db: Session = Depends(get_db)):
    """Retry a failed/skipped external delivery without re-firing the alert."""
    repo = AlertRepository(db)
    delivery = await asyncio.to_thread(repo.get_delivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    from backend.notifications import retry_delivery
    await asyncio.to_thread(retry_delivery, delivery_id)
    db.expire_all()
    refreshed = await asyncio.to_thread(repo.get_delivery, delivery_id)
    return refreshed


@router.post("/{alert_id}/test-delivery", response_model=AlertTestDeliveryResponse)
async def test_alert_delivery(
    alert_id: int,
    payload: AlertTestDeliveryRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(check_rate_limit(_alerts_limiter)),
):
    """Send a one-off notification test without firing or storing a trigger."""
    repo = AlertRepository(db)
    alert = await asyncio.to_thread(repo.get_by_id, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    from backend.notifications.delivery import send_test_delivery
    try:
        result = await asyncio.to_thread(send_test_delivery, alert, payload.channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AlertTestDeliveryResponse(channel=payload.channel, **result)


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: int, db: Session = Depends(get_db)):
    """Get a single alert by ID."""
    repo = AlertRepository(db)
    alert = await asyncio.to_thread(repo.get_by_id, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.put("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: int,
    payload: AlertUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing alert."""
    repo = AlertRepository(db)
    existing = await asyncio.to_thread(repo.get_by_id, alert_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Capture old state for engine registration management.
    was_price_alert = existing.condition_type in (
        "price_above", "price_below", "pct_change_above"
    )

    def _do_update():
        return repo.update(
            alert_id,
            name=payload.name,
            condition_type=payload.condition_type,
            parameter=payload.parameter,
            is_enabled=payload.is_enabled,
        )

    updated = await asyncio.to_thread(_do_update)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    is_price_alert = updated.condition_type in (
        "price_above", "price_below", "pct_change_above"
    )

    if was_price_alert and (not is_price_alert or not updated.is_enabled):
        await asyncio.to_thread(alerts_engine.unregister_for_alert, existing)
    elif is_price_alert and updated.is_enabled:
        await asyncio.to_thread(alerts_engine.register_for_alert, updated)
    elif not was_price_alert:
        # Bar/signal alerts are cached in-memory too. Refresh the cache when
        # their profile parameter changes (for example, a snooze), and keep
        # enable/disable behavior consistent with price alerts.
        profile_changed = payload.condition_type is not None or payload.parameter is not None
        if existing.is_enabled and not updated.is_enabled:
            await asyncio.to_thread(alerts_engine.unregister_for_alert, existing)
        elif not existing.is_enabled and updated.is_enabled:
            await asyncio.to_thread(alerts_engine.register_for_alert, updated)
        elif updated.is_enabled and profile_changed:
            await asyncio.to_thread(alerts_engine.unregister_for_alert, existing)
            await asyncio.to_thread(alerts_engine.register_for_alert, updated)

    return updated


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(alert_id: int, db: Session = Depends(get_db)):
    """Delete an alert and all its triggers."""
    repo = AlertRepository(db)
    existing = await asyncio.to_thread(repo.get_by_id, alert_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    await asyncio.to_thread(alerts_engine.unregister_for_alert, existing)
    await asyncio.to_thread(repo.delete, alert_id)


@router.get("/{alert_id}/triggers", response_model=list[AlertTriggerResponse])
async def get_alert_triggers(alert_id: int, limit: int = 100, db: Session = Depends(get_db)):
    """Trigger history for a specific alert."""
    repo = AlertRepository(db)
    if await asyncio.to_thread(repo.get_by_id, alert_id) is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return await asyncio.to_thread(repo.get_triggers, alert_id, limit=limit)
