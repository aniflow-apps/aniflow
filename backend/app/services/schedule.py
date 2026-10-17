"""The weekly slot an anime occupies, in the timezone the app displays.

One instant is stored (``air_slot_at``); weekday and time of day are derived
here. A show with no slot but a known premiere still has a weekday - that is
the day of the week it aired on - so the weekly view can place it.
"""
from datetime import UTC, date, datetime, timedelta

from backend.app.services.clock import display_timezone, now_local, to_display

#: Monday-first, matching the Mo-Su columns the watchlist draws.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def slot_weekday(air_slot_at: datetime | None, air_start_date: date | None) -> int | None:
    """0 = Monday .. 6 = Sunday, or None when nothing is known.

    The airing instant wins: it is the show's current slot, while the premiere
    date only says which day the first episode landed on (which for a delayed
    or rescheduled show is no longer the weekly slot).
    """
    local = to_display(air_slot_at)
    if local is not None:
        return local.weekday()
    if air_start_date is not None:
        return air_start_date.weekday()
    return None


def slot_time(air_slot_at: datetime | None) -> str | None:
    """The airing time as ``HH:MM`` in the display timezone, if known."""
    local = to_display(air_slot_at)
    return None if local is None else local.strftime("%H:%M")


def slot_minutes(air_slot_at: datetime | None) -> int | None:
    """Minutes since local midnight - what the weekly column sorts on."""
    local = to_display(air_slot_at)
    return None if local is None else local.hour * 60 + local.minute


def manual_slot(weekday: int, hour: int, minute: int = 0) -> datetime:
    """An instant whose *local* weekday and time are the ones asked for.

    The user picks "Sunday, 18:00"; what gets stored is one UTC instant, the
    same shape the provider's own slot has, so weekday, time of day and sort
    order all come out of the existing helpers rather than a second set for
    hand-entered values.

    Which week it lands in does not matter - only the weekday and the time are
    ever read back. The nearest upcoming date is used so the stored value reads
    sensibly if anyone ever looks at the raw column.

    One wart, shared with the provider's slots: a fixed instant keeps its UTC
    offset, so a slot entered in summer reads an hour out in winter for a zone
    that observes DST.
    """
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be 0 (Monday) to 6 (Sunday)")
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 0 to 23")
    local_now = now_local()
    ahead = (weekday - local_now.weekday()) % 7
    local = (local_now + timedelta(days=ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    # Rebuilt in the zone rather than shifted, so the hour asked for is the
    # hour stored even across a DST change in the week ahead.
    local = local.replace(tzinfo=None).replace(tzinfo=display_timezone())
    return local.astimezone(UTC)


__all__ = ["WEEKDAYS", "manual_slot", "slot_minutes", "slot_time", "slot_weekday"]
