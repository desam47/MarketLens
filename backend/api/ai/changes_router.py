"""Read-only What-changed Inbox for the local AI Hub.

The inbox compares durable application rows with a caller-supplied last-visit
checkpoint.  It deliberately does not call market-data or auxiliary providers:
all items come from rows already written by normal application workflows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.models.alert import Alert, AlertTrigger
from backend.models.market_data_sql import ProviderEventModel, ProviderStatusModel
from backend.models.signal import HistoricalSignal
from backend.models.watchlist import Watchlist, WatchlistSymbol
from backend.utils.timezone import format_edt_iso, now_ny, to_ny

router = APIRouter(prefix="/api/ai/changes", tags=["ai-changes"])

ChangeCategory = Literal["watchlist", "alert", "signal", "catalyst", "provider"]
ChangeSeverity = Literal["info", "positive", "warning", "error"]


class ChangeItem(BaseModel):
    id: str
    category: ChangeCategory
    severity: ChangeSeverity
    title: str
    detail: str | None = None
    symbol: str | None = None
    occurred_at: str
    href: str
    dedupe_key: str


class ChangeInboxResponse(BaseModel):
    since: str
    as_of: str
    items: list[ChangeItem]
    counts: dict[str, int]
    coverage: dict[str, bool]
    warnings: list[str] = Field(default_factory=list)


def _timestamp(value: datetime | None) -> str:
    return format_edt_iso(value) or ""


def _item(
    *,
    category: ChangeCategory,
    severity: ChangeSeverity,
    title: str,
    occurred_at: datetime,
    href: str,
    dedupe_key: str,
    detail: str | None = None,
    symbol: str | None = None,
) -> ChangeItem:
    return ChangeItem(
        id=f"{category}:{dedupe_key}",
        category=category,
        severity=severity,
        title=title,
        detail=detail,
        symbol=symbol,
        occurred_at=_timestamp(occurred_at),
        href=href,
        dedupe_key=dedupe_key,
    )


def _as_since(value: datetime | None) -> datetime:
    # A missing checkpoint means "since yesterday".  The local browser stores
    # the server's `as_of` value after a successful load, so subsequent visits
    # are exact and do not need an authenticated user table.
    if value is None:
        return now_ny() - timedelta(hours=24)
    return to_ny(value) or (now_ny() - timedelta(hours=24))


def _signal_detail(signal: HistoricalSignal) -> tuple[str, ChangeSeverity]:
    trend = (signal.trend_state or "unknown").replace("_", " ")
    score = f" · score {signal.trend_score:.1f}" if signal.trend_score is not None else ""
    severity: ChangeSeverity = "positive" if signal.trend_state == "bullish" else "warning" if signal.trend_state == "bearish" else "info"
    return f"{trend}{score} · {signal.timeframe}", severity


@router.get("", response_model=ChangeInboxResponse)
def get_change_inbox(
    since: datetime | None = Query(default=None, description="Last successful inbox checkpoint"),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ChangeInboxResponse:
    """Return bounded, deduplicated changes without triggering provider calls."""
    start = _as_since(since)
    as_of = now_ny()
    items: list[ChangeItem] = []
    warnings: list[str] = []

    # Watchlists and symbol membership changes.
    watchlists = db.query(Watchlist).filter(
        or_(Watchlist.created_at >= start, Watchlist.updated_at >= start)
    ).order_by(Watchlist.updated_at.desc()).limit(limit).all()
    for watchlist in watchlists:
        changed_at = watchlist.updated_at or watchlist.created_at or as_of
        items.append(_item(
            category="watchlist", severity="info",
            title=f"Watchlist updated: {watchlist.name}",
            detail="Watchlist settings or membership changed.", symbol=None,
            occurred_at=changed_at, href="#watchlist",
            dedupe_key=f"watchlist:{watchlist.id}:{changed_at.isoformat()}",
        ))
    symbol_rows = db.query(WatchlistSymbol, Watchlist).join(
        Watchlist, Watchlist.id == WatchlistSymbol.watchlist_id
    ).filter(WatchlistSymbol.added_at >= start).order_by(WatchlistSymbol.added_at.desc()).limit(limit).all()
    for row, watchlist in symbol_rows:
        changed_at = row.added_at or as_of
        items.append(_item(
            category="watchlist", severity="positive",
            title=f"{row.symbol} added to {watchlist.name}",
            detail="The symbol is now part of this watchlist.", symbol=row.symbol,
            occurred_at=changed_at, href="#watchlist",
            dedupe_key=f"watchlist-symbol:{row.id}:{changed_at.isoformat()}",
        ))

    # Alert rule edits and fired observations.
    alerts = db.query(Alert).filter(
        or_(Alert.created_at >= start, Alert.updated_at >= start)
    ).order_by(Alert.updated_at.desc()).limit(limit).all()
    for alert in alerts:
        changed_at = alert.updated_at or alert.created_at or as_of
        state = "enabled" if alert.is_enabled else "disabled"
        items.append(_item(
            category="alert", severity="info",
            title=f"Alert {state}: {alert.name}",
            detail=f"{alert.symbol} · {alert.condition_type}", symbol=alert.symbol,
            occurred_at=changed_at, href="#alerts",
            dedupe_key=f"alert:{alert.id}:{changed_at.isoformat()}",
        ))
    triggers = db.query(AlertTrigger, Alert).join(Alert, Alert.id == AlertTrigger.alert_id).filter(
        AlertTrigger.triggered_at >= start
    ).order_by(AlertTrigger.triggered_at.desc()).limit(limit).all()
    for trigger, alert in triggers:
        changed_at = trigger.triggered_at or as_of
        items.append(_item(
            category="alert", severity="warning",
            title=f"Alert fired: {alert.name}",
            detail=trigger.message or trigger.observed_value or "Trigger condition matched.",
            symbol=trigger.symbol, occurred_at=changed_at, href="#alerts",
            dedupe_key=f"alert-trigger:{trigger.id}",
        ))

    # Historical signals can be written several times for the same symbol and
    # timeframe. Keep only the newest row for each pair so the inbox describes
    # a transition rather than flooding the user with bar-close snapshots.
    signals = db.query(HistoricalSignal).filter(
        HistoricalSignal.created_at >= start
    ).order_by(HistoricalSignal.created_at.desc()).limit(limit * 4).all()
    seen_signals: set[tuple[str, str]] = set()
    for signal in signals:
        key = (signal.symbol.upper(), signal.timeframe)
        if key in seen_signals:
            continue
        seen_signals.add(key)
        changed_at = signal.created_at or signal.timestamp or as_of
        detail, severity = _signal_detail(signal)
        items.append(_item(
            category="signal", severity=severity,
            title=f"Signal updated: {signal.symbol} {signal.timeframe}",
            detail=detail, symbol=signal.symbol, occurred_at=changed_at, href="#signals",
            dedupe_key=f"signal:{signal.symbol.upper()}:{signal.timeframe}:{changed_at.isoformat()}",
        ))

    # Provider status rows are already persisted by the health loop. Keep the
    # latest state per provider and include failures/fallbacks from the durable
    # activity log as separate actionable changes.
    provider_statuses = db.query(ProviderStatusModel).filter(
        ProviderStatusModel.timestamp >= start
    ).order_by(ProviderStatusModel.timestamp.desc()).limit(limit * 2).all()
    seen_providers: set[str] = set()
    for status in provider_statuses:
        provider = status.provider_name
        if provider in seen_providers:
            continue
        seen_providers.add(provider)
        healthy = bool(status.is_healthy)
        changed_at = status.timestamp or as_of
        items.append(_item(
            category="provider", severity="positive" if healthy else "error",
            title=f"Provider health: {provider}",
            detail="Healthy" if healthy else (status.error_message or "Provider is unavailable."),
            occurred_at=changed_at, href="#system-health",
            dedupe_key=f"provider-status:{provider}:{changed_at.isoformat()}",
        ))
    provider_events = db.query(ProviderEventModel).filter(
        ProviderEventModel.timestamp >= start,
        ProviderEventModel.outcome.in_(["failure", "fallback"]),
    ).order_by(ProviderEventModel.timestamp.desc()).limit(limit * 2).all()
    seen_provider_events: set[tuple[str, str, str]] = set()
    for event in provider_events:
        key = (event.provider, event.method, event.outcome)
        if key in seen_provider_events:
            continue
        seen_provider_events.add(key)
        changed_at = event.timestamp or as_of
        items.append(_item(
            category="provider", severity="error" if event.outcome == "failure" else "warning",
            title=f"Provider {event.outcome}: {event.provider}",
            detail=f"{event.method}{f' · {event.error}' if event.error else ''}",
            occurred_at=changed_at, href="#system-health",
            dedupe_key=f"provider-event:{event.provider}:{event.method}:{event.outcome}",
        ))

    # Catalyst/news responses are intentionally not persisted by the current
    # auxiliary providers. We surface refresh/failure activity already present
    # in the provider log, but never poll those providers just to build an inbox.
    catalyst_events = db.query(ProviderEventModel).filter(
        ProviderEventModel.timestamp >= start,
        or_(ProviderEventModel.method.ilike("%news%"), ProviderEventModel.method.ilike("%calendar%")),
    ).order_by(ProviderEventModel.timestamp.desc()).limit(limit).all()
    seen_catalysts: set[tuple[str, str, str]] = set()
    for event in catalyst_events:
        key = (event.provider, event.method, event.outcome)
        if key in seen_catalysts:
            continue
        seen_catalysts.add(key)
        changed_at = event.timestamp or as_of
        items.append(_item(
            category="catalyst", severity="error" if event.outcome == "failure" else "info",
            title=f"Catalyst source {event.outcome}: {event.provider}",
            detail=f"{event.method} · source activity only; no provider refresh was triggered.",
            occurred_at=changed_at, href="#calendar",
            dedupe_key=f"catalyst:{event.provider}:{event.method}:{event.outcome}",
        ))

    warnings.append("Catalyst items reflect already-recorded provider activity; this inbox does not poll providers.")
    # Stable newest-first ordering, then deterministic category/id ordering.
    items.sort(
        key=lambda item: (datetime.fromisoformat(item.occurred_at), item.id),
        reverse=True,
    )
    items = items[:limit]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    return ChangeInboxResponse(
        since=_timestamp(start), as_of=_timestamp(as_of), items=items, counts=counts,
        coverage={"watchlists": True, "alerts": True, "signals": True, "catalysts": bool(catalyst_events), "provider_health": True},
        warnings=warnings,
    )


__all__ = ["router", "ChangeInboxResponse", "ChangeItem"]
