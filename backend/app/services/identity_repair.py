"""Undoing what series-wide ids linked by mistake (build 156).

Build 155 stopped TVDB and TMDB ids being treated as identities - they name a
whole series, so one of them is shared by every season of a show. What it
could not do was undo what they had already linked, which the user saw as
upcoming shows still marked "In your library" after the fix: *"I guess i have
to kill the database to fix this issue?"*

No - calendar.db is never deleted, and it does not need to be.
The damage has a shape that can be recognised:

* **identity rows for series ids.** They are no longer identities, so they
  are removed. Nothing is lost: they are re-derivable, and they were never
  anything but a way to be wrong.
* **a Work holding two ids from one provider** - two Kitsu ids, two AniList
  ids. One show has one id per database, so the extra one arrived with a
  listing that was routed onto the wrong Work. The one the offline mapping
  list names for the Work's own id is kept (or, if the list does not know,
  the one that has been there longest); the others are detached along with
  their season listings and whatever those providers wrote onto the Work.
  The next calendar Update finds those listings again and gives them Works
  of their own.
* **watchlist ids that came from such a Work** (build 153 copied a calendar
  entry's ids onto the watchlist). Removed where the mapping list names a
  different id, or where the id was one just detached.

Runs once - a calendar note records that it has - and then only after a new
mapping list, which is the one thing that can make it find more. The Work ids
themselves are never changed: that rule does not bend for a repair.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.models.calendar import (
    CalendarIdentity,
    CalendarMembership,
    CalendarObservation,
    CalendarPopularity,
    CalendarTitle,
    CalendarWork,
    CalendarWorkGenre,
    CalendarWorkStudio,
)

logger = for_component("Calendar")

NOTE_KEY = "identity_repair_155"
#: The canonical names an identity may carry (``canonical_provider`` folds
#: jikan and myanimelist into mal).
_SHOW_PROVIDERS = ("anidb", "anilist", "mal", "kitsu", "shikimori")
#: Which fetched providers write under a canonical identity name.
_FETCHED_AS = {"mal": ("mal", "jikan", "myanimelist"), "anilist": ("anilist",), "kitsu": ("kitsu",),
               "anidb": ("anidb",), "shikimori": ("shikimori",)}


def repair_calendar(session: Session) -> set[tuple[str, str]]:
    """Fix the calendar. Returns the (provider, id) pairs it detached."""
    from backend.app.services import calendar_metadata, id_mapping

    removed_series = session.execute(
        delete(CalendarIdentity).where(CalendarIdentity.provider.not_in(_SHOW_PROVIDERS))
    ).rowcount or 0

    detached: set[tuple[str, str]] = set()
    by_work: dict[str, dict[str, list[CalendarIdentity]]] = {}
    for identity in session.scalars(select(CalendarIdentity)):
        by_work.setdefault(identity.work_id, {}).setdefault(identity.provider, []).append(identity)

    for work_id, providers in by_work.items():
        doubled = {provider: rows for provider, rows in providers.items() if len(rows) > 1}
        if not doubled:
            continue
        work = session.get(CalendarWork, work_id)
        if work is None:
            continue
        expected = id_mapping.ids_for(session, work.id_provider, work.id_provider_id)
        for provider, rows in doubled.items():
            keep = next((row for row in rows if expected.get(provider) == row.provider_id), None)
            if keep is None and work.id_provider == provider:
                keep = next((row for row in rows if row.provider_id == work.id_provider_id), None)
            if keep is None:
                keep = min(rows, key=lambda row: (row.first_seen is None, row.first_seen, row.id))
            for row in rows:
                if row is keep:
                    continue
                detached.add((provider, row.provider_id))
                fetched = _FETCHED_AS.get(provider, (provider,))
                session.execute(delete(CalendarMembership).where(
                    CalendarMembership.work_id == work_id,
                    CalendarMembership.provider.in_(fetched),
                    CalendarMembership.provider_id == row.provider_id,
                ))
                # What those providers wrote here is a mix of two shows and
                # cannot be told apart; it is re-derivable, so it goes, and
                # the next Update writes the right half back.
                for table in (CalendarObservation, CalendarTitle, CalendarPopularity):
                    session.execute(delete(table).where(table.work_id == work_id, table.provider.in_(fetched)))
                session.delete(row)
            # Genres and studios are not kept per provider, so a repaired
            # Work loses them until the next Update rather than keeping the
            # other show's.
            session.execute(delete(CalendarWorkGenre).where(CalendarWorkGenre.work_id == work_id))
            session.execute(delete(CalendarWorkStudio).where(CalendarWorkStudio.work_id == work_id))
        session.flush()
        calendar_metadata.assemble(session, work)

    if removed_series or detached:
        logger.info(
            "calendar repair: %s series-id links removed, %s wrongly attached ids detached",
            removed_series, len(detached),
        )
    return detached


def repair_watchlist(db: Session, detached: set[tuple[str, str]]) -> int:
    """Drop watchlist ids that contradict the mapping list or were detached."""
    from backend.app.models.anime import Anime
    from backend.app.services import id_mapping
    from backend.app.services.calendar_identity import canonical_provider

    fixed = 0
    with calendar_store.sessions()() as calendar:
        for anime in db.scalars(select(Anime)):
            ids = dict(anime.provider_ids or {})
            anchor: dict[str, str] = {}
            if anime.anidb_id:
                anchor["anidb"] = str(anime.anidb_id)
            if anime.primary_provider and anime.primary_provider_id:
                anchor[canonical_provider(anime.primary_provider)] = str(anime.primary_provider_id)
            expected: dict[str, str] = {}
            for provider, provider_id in anchor.items():
                expected.update(id_mapping.ids_for(calendar, provider, provider_id))
            kept = {}
            for name, value in ids.items():
                canonical = canonical_provider(name)
                if name == anime.primary_provider or (canonical == "anidb" and anime.anidb_id):
                    kept[name] = value
                    continue
                if (canonical, str(value)) in detached:
                    continue
                if canonical in expected and expected[canonical] != str(value):
                    continue
                kept[name] = value
            if kept != ids:
                anime.provider_ids = kept
                fixed += 1
    if fixed:
        logger.info("watchlist repair: wrong ids removed from %s anime", fixed)
    return fixed


def run_once(db: Session) -> bool:
    """Repair once per installation. True when it ran now."""
    from backend.app.services import calendar_notes

    if not calendar_store.exists():
        return False
    try:
        if calendar_notes.read(NOTE_KEY):
            return False
    except Exception:  # noqa: BLE001 - an unreadable note is "not yet"
        pass
    with calendar_store.sessions()() as session:
        detached = repair_calendar(session)
        session.commit()
    repair_watchlist(db, detached)
    db.commit()
    calendar_notes.write(NOTE_KEY, "done")
    return True


__all__ = ["repair_calendar", "repair_watchlist", "run_once"]
