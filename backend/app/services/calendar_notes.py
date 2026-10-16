"""What the calendar remembers about itself (build 148).

One fact today: whether the first fill has been done. It is small enough that
a module for it looks like overkill until you ask where else it would go -
``calendar_view`` only reads, ``calendar_lifecycle`` is about destroying
things, and putting a write into either would be filing it by accident.

The store is ``calendar.db``, not the settings file, and that is the whole
design decision. *"It will only do once you have the clean setup ... and will
never do this again"* is a statement about a particular calendar database: a
Reset makes the first fill worth offering again, and a flag in
``aniflow.yaml`` would have survived the Reset and refused it.

Every function here tolerates a database that will not open. The calendar is
optional by design, and a note that cannot be read is reported as "not
written" - which makes the button appear rather than disappear, and pressing
it is the recoverable direction.
"""
from __future__ import annotations

from sqlalchemy import select

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.db.types import now_utc
from backend.app.models.calendar import NOTE_FIRST_FILL, CalendarNote

logger = for_component("Calendar")


def read_in(handle, key: str) -> str | None:
    """What is written under this key, using a session somebody already has.

    The season endpoint reads a note on every page load and already has the
    calendar database open. Opening a second connection to answer one boolean
    is the kind of thing that does not show up until a television client is
    polling this.
    """
    row = handle.scalar(select(CalendarNote).where(CalendarNote.key == key))
    return row.value if row is not None else None


def read(key: str) -> str | None:
    """What is written under this key, or None for nothing and for a
    database that could not be opened."""
    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        return None
    with sessions() as handle:
        return read_in(handle, key)


def write(key: str, value: str) -> None:
    """Record a fact about this calendar. Silent when it cannot be stored."""
    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked as exc:
        logger.info("the calendar note %r could not be written: %s", key, exc)
        return
    with sessions() as handle:
        row = handle.scalar(select(CalendarNote).where(CalendarNote.key == key))
        if row is None:
            handle.add(CalendarNote(key=key, value=value, written_at=now_utc()))
        else:
            row.value = value
            row.written_at = now_utc()
        handle.commit()


def first_fill_done(handle=None) -> bool:
    """Whether this calendar has had its one initial fill.

    Given a session, it uses it; given nothing, it opens one and reports False
    for a database that will not open - which makes the button appear rather
    than vanish, and pressing it is the recoverable direction.
    """
    if handle is not None:
        return read_in(handle, NOTE_FIRST_FILL) is not None
    return read(NOTE_FIRST_FILL) is not None


def mark_first_fill_done() -> None:
    """Written when an initial fill *finishes*, not when it starts.

    A run that died on its first source has not filled anything, and burning
    the one offer on it would leave the button gone and the calendar empty -
    the one combination a person cannot get out of without a Reset.
    """
    if first_fill_done():
        return
    write(NOTE_FIRST_FILL, now_utc().isoformat())
    logger.info("the calendar's initial fill is done and will not be offered again")
