"""Which timezone the application presents times in.

Lives in `core` rather than `services` because the log formatter needs it, and
`services` may not be imported from there. `services.clock` is the friendlier
front for application code.

Precedence: the `timezone` setting (editable in aniflow.yaml, no redeploy),
then the `TZ` environment variable the container sets, then UTC. Resolution
never raises: a typo leaves times in UTC and says so in the log, which is
better than a container that will not start.
"""
import logging
import os
from datetime import UTC, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("aniflow.clock")


def resolve_timezone(name: str | None) -> tzinfo:
    """The timezone for a name, or UTC when it cannot be resolved."""
    candidate = (name or "").strip()
    if not candidate or candidate.upper() == "UTC":
        # ZoneInfo("UTC") needs a tz database, which a bare development
        # machine may not have; the fixed offset always works.
        return UTC
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning("timezone %r is not a known IANA name; using UTC", candidate)
        return UTC


def configured_timezone_name() -> str:
    """The name in force, from the settings file or the environment."""
    try:
        from backend.app.services.app_settings import load_app_settings

        configured = (load_app_settings().timezone or "").strip()
    except Exception:  # noqa: BLE001 - before settings exist, fall back quietly
        configured = ""
    return configured or (os.environ.get("TZ") or "").strip() or "UTC"


def display_timezone() -> tzinfo:
    """The timezone to render times in."""
    return resolve_timezone(configured_timezone_name())


def resolved_timezone_name() -> str:
    """The name of the zone actually in force.

    Not the same as the configured name: a zone the host cannot resolve (no tz
    database, or a typo) falls back to UTC, and reporting the requested name
    then would print "Europe/Berlin" beside times that are UTC.
    """
    zone = display_timezone()
    return getattr(zone, "key", None) or str(zone)


def timezone_mismatch() -> str | None:
    """The configured name when it could not be resolved, else None."""
    requested = configured_timezone_name()
    return requested if requested != resolved_timezone_name() else None


__all__ = [
    "configured_timezone_name",
    "display_timezone",
    "resolve_timezone",
    "resolved_timezone_name",
    "timezone_mismatch",
]
