"""
Timezone helpers — central source of truth for the project's display zone.

Policy (2026-09-02): every timestamp that leaves the backend carries
``America/New_York`` (EDT/EST, auto-DST). All providers return UTC;
this module is the one place that converts to NY local time so the
frontend doesn't have to guess.

**Convention:** naive ``datetime`` objects are always NY local time
(EDT/EST). UTC datetimes are always timezone-aware. The boundary is
at the provider layer — providers convert UTC → NY naive before returning.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Single source of truth. America/New_York switches between EDT (UTC-4) and
# EST (UTC-5) automatically based on the date.
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def to_ny(dt: datetime | None) -> datetime | None:
    """Convert any datetime to a naive America/New_York datetime.

    - Aware UTC input  → converted to NY wall time, tzinfo stripped
    - Naive input     → returned as-is (assumed to already be NY)
    - None           → None

    Callers that receive UTC datetimes from providers (alpaca, yfinance, etc.)
    MUST call this before building Bar / Quote objects.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Already naive — treat as NY local time (our storage convention).
        return dt
    return dt.astimezone(NY).replace(tzinfo=None)


def ny_to_utc(dt: datetime | None) -> datetime | None:
    """Convert a naive NY-local datetime back to a UTC-aware datetime.

    Naive inputs are interpreted as NY local time. Aware inputs are converted.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(UTC)


def ensure_aware_ny(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to timezone-aware America/New_York.

    - Naive input  → stamped NY (our storage convention; NOT UTC)
    - Aware input  → converted to NY
    - None         → None

    Use this when an engine or serializer needs an aware datetime and the
    natural expression is NY. When the consumer wants UTC instead, use
    ``ny_to_utc`` — both interpret naive input as NY, which is the whole
    point: interpreting a naive NY timestamp as UTC shifts it by 4-5h.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


def now_ny() -> datetime:
    """Current wall-clock time in America/New_York, as a naive datetime."""
    return datetime.now(UTC).astimezone(NY).replace(tzinfo=None)


def format_edt_iso(dt: datetime | None) -> str | None:
    """Format a datetime as ISO-8601 with explicit EDT/EST offset suffix.

    Returns e.g. ``2026-09-02T12:19:00-04:00`` (EDT) or
    ``2026-01-15T09:30:00-05:00`` (EST). Use this when the caller needs
    an explicit offset in the string (e.g. for JavaScript parsing).
    Returns the input unchanged if None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    else:
        dt = dt.astimezone(NY)
    return dt.isoformat()
