"""Rebuild and Reset: the two things allowed to destroy calendar data (141).

Neither happens by itself. That is the change build 141 makes - up to version
5 a schema mismatch deleted the file, and the calendar that came back was
empty until somebody noticed and downloaded it all again.

The distinction that makes the rest obvious is that ``calendar.db`` holds two
very different kinds of thing:

**Irreplaceable.** Canonical ids, the identities behind them, merge aliases,
the evidence each link was proven on, and the memory that a provider once
placed a show in a season. No provider will hand any of that back, and one of
them - the ids - is a contract with AniFlow TV.

**Re-derivable.** Canonical metadata, observations, artwork, popularity,
genres, studios, relations. Downloading it again costs API calls and nothing
else.

So there are two operations rather than one:

* **Rebuild** throws away the second list and keeps the first. It is the
  honest answer to "the metadata looks wrong" and costs a re-download.
* **Reset** deletes the file. It is the answer to "the file is broken", it
  breaks every id a client holds, and it says so before it runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.models.calendar import (
    STATUS_UNKNOWN,
    CalendarAlias,
    CalendarArtwork,
    CalendarIdentity,
    CalendarMembership,
    CalendarObservation,
    CalendarPopularity,
    CalendarRelation,
    CalendarSeason,
    CalendarSourceState,
    CalendarWork,
    CalendarWorkGenre,
    CalendarWorkStudio,
)

logger = for_component("Calendar")

#: Cleared by a Rebuild. Every one of these comes back from a download.
#:
#: Memberships are *not* here, and deliberately. They are managed by the
#: snapshot engine, which adds, keeps, moves and deactivates them with rules
#: of its own; a bulk delete would throw away the inactive rows, which are the
#: one part of a membership nothing can reconstruct.
#:
#: Titles are not here either. They are the matching material for the whole
#: application, the AniDB dump behind most of them is fetched at most weekly,
#: and nothing is gained by making somebody wait for it again.
REDERIVABLE = (
    CalendarObservation,
    CalendarPopularity,
    CalendarArtwork,
    CalendarWorkGenre,
    CalendarWorkStudio,
    CalendarRelation,
)


@dataclass(frozen=True)
class WhatResetDestroys:
    """The sentence a person should read before pressing Reset."""

    works: int
    identities: int
    aliases: int
    remembered_memberships: int

    def as_text(self) -> str:
        parts = [
            f"{self.works} calendar ids",
            f"{self.identities} provider links",
        ]
        if self.aliases:
            parts.append(f"{self.aliases} merged ids that clients may still be using")
        if self.remembered_memberships:
            parts.append(
                f"{self.remembered_memberships} records of seasons providers no longer report"
            )
        return ", ".join(parts)


def what_reset_destroys(session: Session) -> WhatResetDestroys:
    """Count what would not come back, for the confirmation to name.

    Counted rather than estimated: "this deletes some things" is the kind of
    warning people learn to click through.
    """
    return WhatResetDestroys(
        works=session.scalar(select(func.count()).select_from(CalendarWork)) or 0,
        identities=session.scalar(select(func.count()).select_from(CalendarIdentity)) or 0,
        aliases=session.scalar(select(func.count()).select_from(CalendarAlias)) or 0,
        remembered_memberships=session.scalar(
            select(func.count()).select_from(CalendarMembership).where(
                CalendarMembership.active.is_(False)
            )
        )
        or 0,
    )


def rebuild(session: Session) -> dict[str, int]:
    """Clear everything a provider can hand back. Keep the identity spine.

    Returns what was removed, per table, so the log and the page can say what
    happened rather than "done".

    The canonical metadata on each Work is emptied too - it is assembled from
    the observations that have just gone, and leaving it would present values
    with nothing behind them.
    """
    removed: dict[str, int] = {}
    for model in REDERIVABLE:
        result = session.execute(delete(model))
        removed[model.__tablename__] = result.rowcount or 0

    session.execute(
        update(CalendarWork).values(
            kind=None,
            episode_count=None,
            air_start_date=None,
            air_slot_at=None,
            air_weekday=None,
            airing_status=STATUS_UNKNOWN,
            is_adult=False,
            duration_minutes=None,
            source_material=None,
            synopsis=None,
            popularity_score=None,
        )
    )
    # Every season becomes due again, which is what makes the next update
    # actually fetch rather than decide it is fresh.
    session.execute(update(CalendarSeason).values(last_success_at=None))
    session.execute(
        update(CalendarSourceState).values(last_success_at=None, failures=0, last_error=None)
    )
    session.commit()

    kept = session.scalar(select(func.count()).select_from(CalendarWork)) or 0
    logger.warning(
        "calendar rebuilt on request: %s rows cleared, %s calendar ids kept",
        sum(removed.values()),
        kept,
    )
    return removed


def reset() -> None:
    """Delete the file. The last resort, and never automatic.

    Whoever calls this has already been told what it destroys - see
    ``what_reset_destroys``. Afterwards the next read builds an empty
    database at the current schema, exactly as a fresh install does.
    """
    calendar_store.delete_database()
