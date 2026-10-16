"""Basic or Advanced: what AniFlow is, given what it has been given.

**Advanced** is AniFlow as it has always been: indexers, subgroup profiles,
release names, torrents, imports.

**Basic** is what is left when no indexer is configured - a watchlist and a
calendar. Nothing can be downloaded without an indexer, so a screen full of
subgroups, qualities and release names describes machinery that is not there,
and offering it is worse than not having it.

The mode is **derived, not chosen**: an indexer file in the configuration
folder means Advanced, no indexer means Basic. That way there is one thing to
change rather than two that can disagree, and a fresh install is Basic without
anyone having to know the word.

The override exists for testing and says so. It is a setting rather than a
switch on the page: it will be removed once the automatic rule has been lived
with, and a setting is easier to take away than a button people have learned.

Two rules hold in both directions, and they are the whole reason this is a
label on an anime rather than a global flag:

* **Nothing is deleted or disabled by a mode change.** An advanced anime in
  Basic mode keeps its profile, its release name, its folder and its
  downloads - all of it hidden, none of it lost. Switch back and it works.
* **A basic anime cannot silently become an advanced one.** It has no release
  name, so nothing can match releases for it. When an indexer appears it is
  flagged, listed in Activity, and converted by hand through the same setup
  dialog every other anime went through - because choosing the release name is
  the one step nothing else can do for you.
"""
from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component

logger = for_component("Mode")


class AppMode(StrEnum):
    """What AniFlow is offering right now."""

    BASIC = "basic"
    ADVANCED = "advanced"


class EntryMode(StrEnum):
    """What an anime was added as. Recorded on the row; never guessed."""

    BASIC = "basic"
    ADVANCED = "advanced"
    #: Added before its first episode exists (build 121).
    #:
    #: Asked for after adding a show from a future season and being taken
    #: through release matching for releases that cannot exist: *"Maybe we
    #: need a new feature to add an anime without the download parts, also
    #: marked as future release ... This one can have all of the necessary
    #: infos like meta etc, but it will lack downloads related stuff."*
    #:
    #: A kind of its own rather than a flag on an advanced entry, and
    #: deliberately not the same thing as BASIC: basic is about what *this
    #: installation* can do - no indexer, and that will not change when a
    #: season starts - while future is about *this show*, and stops being
    #: true on a known date. So it has an end: once the premiere has passed
    #: it is offered for conversion, which is the same step a basic entry
    #: goes through and the same dialog.
    FUTURE = "future"


def has_indexer(db: Session) -> bool:
    """Whether anything is configured that could actually find a release."""
    from backend.app.models.indexer import Indexer

    return db.scalar(select(Indexer.id).limit(1)) is not None


def current_mode(db: Session) -> AppMode:
    """Advanced when an indexer exists, else Basic - unless overridden.

    The override is a testing aid and is checked first precisely so that
    testing the *other* mode does not require deleting a working indexer.
    """
    from backend.app.services.app_settings import load_app_settings

    forced = (getattr(load_app_settings(), "app_mode_override", "") or "").strip().lower()
    if forced in (AppMode.BASIC, AppMode.ADVANCED):
        return AppMode(forced)
    return AppMode.ADVANCED if has_indexer(db) else AppMode.BASIC


def entry_mode_for(db: Session, *, unaired: bool = False) -> EntryMode:
    """What a newly added anime should be recorded as.

    Basic wins over future when there is no indexer, and the order matters:
    both mean "nothing is being downloaded", but only one of them ends by
    itself. A show with no indexer behind it will still have no indexer when
    its season starts, so calling it FUTURE would promise a conversion that
    has nothing to convert to.
    """
    if current_mode(db) is not AppMode.ADVANCED:
        return EntryMode.BASIC
    return EntryMode.FUTURE if unaired else EntryMode.ADVANCED


def future_entries_now_airing(db: Session) -> list:
    """Future entries whose premiere has passed (build 121).

    The end of the FUTURE kind. The watchlist already moves them out of
    Upcoming by itself - that grouping reads the date - but moving on a
    screen is not the same as being ready: the entry still has no release
    name, no profile and no feed source, and only the user can say which
    releases are this show.

    So they are offered for conversion, through the same attention record and
    the same dialog a basic entry uses.
    """
    from datetime import date

    from backend.app.models.anime import Anime

    if current_mode(db) is not AppMode.ADVANCED:
        return []
    today = date.today()
    waiting = []
    for anime in db.scalars(select(Anime).where(Anime.entry_mode == EntryMode.FUTURE)):
        started = anime.air_start_date
        if started is not None and started <= today:
            waiting.append(anime)
    return waiting


def needs_conversion(db: Session) -> list:
    """Basic anime in an Advanced AniFlow.

    These are the only anime the mode change leaves in an inconsistent state:
    they are tracked but cannot be matched, and only the user can say which
    releases belong to them.
    """
    from backend.app.models.anime import Anime

    if current_mode(db) is not AppMode.ADVANCED:
        return []
    return list(db.scalars(select(Anime).where(Anime.entry_mode == EntryMode.BASIC)))


def review_conversions(db: Session) -> int:
    """Raise an Activity item per anime that is waiting to be converted.

    Run from the worker rather than at the moment the mode changes: the mode is
    derived from the indexer files, which can appear by someone dropping a
    YAML in a folder, and nothing tells AniFlow the moment that happens.

    Resolved automatically when the anime is converted or when the mode goes
    back to Basic, because an attention that outlives its cause trains people
    to ignore the page.
    """
    from datetime import UTC, datetime

    from backend.app.models.activity import Attention, AttentionKind, AttentionStatus
    from backend.app.services.attention import raise_attention

    waiting = needs_conversion(db)
    started = future_entries_now_airing(db)
    # A season that started with its first file already on disk needs no question (item 47).
    from backend.app.services.future_adopt import adopt_local_files

    adopted = [anime for anime in started if adopt_local_files(db, anime)]
    started = [anime for anime in started if anime not in adopted]
    waiting_ids = {anime.id for anime in waiting} | {anime.id for anime in started}

    for anime in waiting:
        raise_attention(
            db,
            kind=AttentionKind.NEEDS_CONVERSION,
            anime_id=anime.id,
            detail=f"{anime.display_name} was added without an indexer and has no release name",
        )
    for anime in started:
        # A different sentence for a different situation: nothing is wrong
        # with this entry, its season has simply arrived (build 121).
        raise_attention(
            db,
            kind=AttentionKind.NEEDS_CONVERSION,
            anime_id=anime.id,
            detail=f"{anime.display_name} has started airing and needs its releases set up",
        )

    # Anything no longer waiting - converted, deleted, or back in Basic mode.
    open_items = db.scalars(
        select(Attention).where(
            Attention.kind == AttentionKind.NEEDS_CONVERSION,
            Attention.status == AttentionStatus.OPEN,
        )
    )
    for item in list(open_items):
        if item.anime_id not in waiting_ids:
            item.status = AttentionStatus.RESOLVED
            item.resolved_at = datetime.now(UTC)

    return len(waiting)


__all__ = [
    "AppMode",
    "EntryMode",
    "current_mode",
    "entry_mode_for",
    "has_indexer",
    "needs_conversion",
    "review_conversions",
]
