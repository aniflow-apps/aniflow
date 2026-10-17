"""Presenting stored instants in the configured timezone.

Airing times are stored as the instant they happen (UTC) and shown in the
timezone the deployment is configured with, because "Tuesday 17:00" is only
meaningful locally - the same instant is Wednesday in Tokyo.

The resolution itself lives in `core.clock`, so the log formatter can use the
same answer without importing a service.
"""
from datetime import UTC, datetime, tzinfo

from backend.app.core.clock import (
    display_timezone as _display_timezone,
    resolved_timezone_name,
    timezone_mismatch,
)


def display_timezone() -> tzinfo:
    """The configured timezone, or UTC when it cannot be resolved."""
    return _display_timezone()


def timezone_name() -> str:
    """The name of the zone times are actually rendered in."""
    return resolved_timezone_name()


def unresolved_timezone() -> str | None:
    """The configured name when the host could not resolve it, else None."""
    return timezone_mismatch()


def to_display(moment: datetime | None) -> datetime | None:
    """One stored instant, in the configured timezone."""
    if moment is None:
        return None
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(display_timezone())


def now_local() -> datetime:
    """The current time where the deployment thinks it is."""
    return datetime.now(display_timezone())


__all__ = [
    "display_timezone",
    "now_local",
    "timezone_name",
    "to_display",
    "unresolved_timezone",
]
