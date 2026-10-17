"""Reading the Calendar out of its own tables (build 143).

The layer that makes the Calendar provider-agnostic. Everything above this
point - the web page, and the television client that will be written next -
sees one shape: a list of shows with a stable ``calendar_id``, and no sign of
which provider supplied the title, the poster or the airing time.

Two things are deliberately **not** stored and are computed per request:

* ``in_library`` and the sequel mark, because they change the moment somebody
  adds or deletes something, and storing them beside a line-up is what once
  left a deleted anime hidden behind the calendar's own filter;
* ``addable``, because it depends on which providers *this* AniFlow can
  actually query.

That last one is worth stating plainly. The calendar knows about shows from
five sources; the add flow can only ask AniList and Kitsu for details. So a
show only MyAnimeList and Shikimori describe is real, is worth showing, and
cannot be added - and the honest thing is to say so on the card rather than
to fail when somebody clicks it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db.types import now_utc
from backend.app.models.calendar import (
    STATUS_UNKNOWN,
    TITLE_ENGLISH,
    TITLE_MAIN,
    TITLE_NATIVE,
    TITLE_ROMAJI,
    TITLE_SYNONYM,
    CalendarArtwork,
    CalendarGenre,
    CalendarIdentity,
    CalendarMembership,
    CalendarSeason,
    CalendarStudio,
    CalendarTitle,
    CalendarWork,
    CalendarWorkGenre,
    CalendarWorkStudio,
)
from backend.app.schemas.anime import AnimeSearchResult
from backend.app.services import calendar_metadata
from backend.app.services.calendar_metadata import ORDER

logger = for_component("Calendar")

#: After this long without a successful load, a season is worth refreshing.
#:
#: Not a cache expiry - the data stays and stays visible. It only decides
#: whether the page offers to update, and whether an automatic update includes
#: this season.
STALE_AFTER = timedelta(hours=24)


def is_past(year: int, season: str) -> bool:
    """Whether this season finished before the one airing now.

    Build 148. A season that has already aired does not go out of date: its
    line-up is a matter of record, and the page saying *"this season has not
    been updated for a while"* about WINTER 2019 was asking for a download
    that could only confirm what is already stored.

    What can still be said about a past season is that it has never been
    loaded at all, which is a different sentence and a real thing to act on.
    """
    from backend.app.services.calendar import SEASONS, resolve_season

    if season not in SEASONS:
        return False
    now_year, now_season = resolve_season(None, None)
    return (year, SEASONS.index(season)) < (now_year, SEASONS.index(now_season))


def addable_providers() -> tuple[str, ...]:
    """The providers the add flow can actually ask for details.

    Read from ``metadata.PROVIDERS`` rather than listed here, so a provider
    added there becomes addable without anybody remembering this file.
    """
    from backend.app.services.metadata import PROVIDERS

    return tuple(PROVIDERS.keys())


@dataclass(frozen=True)
class SeasonState:
    """What is known about a season, apart from the shows in it."""

    loaded_at: object | None
    known: bool
    stale: bool


def season_state(session: Session, year: int, season: str) -> SeasonState:
    row = session.scalar(
        select(CalendarSeason).where(
            CalendarSeason.year == year, CalendarSeason.season == season
        )
    )
    if row is None or row.last_success_at is None:
        return SeasonState(loaded_at=None, known=row is not None, stale=True)
    age = now_utc() - row.last_success_at
    return SeasonState(
        loaded_at=row.last_success_at,
        known=True,
        # A season that has already aired is never stale, however old the
        # load is. See `is_past`.
        stale=age > STALE_AFTER and not is_past(year, season),
    )


def _titles_for(session: Session, work_ids: set[str]) -> dict[str, list[CalendarTitle]]:
    found: dict[str, list[CalendarTitle]] = {}
    if not work_ids:
        return found
    for row in session.scalars(
        select(CalendarTitle).where(CalendarTitle.work_id.in_(work_ids))
    ):
        found.setdefault(row.work_id, []).append(row)
    return found


def _pick(titles: list[CalendarTitle], kind: str) -> str | None:
    """The best-placed provider's version of one kind of title."""
    candidates = [row for row in titles if row.kind == kind]
    if not candidates:
        return None
    candidates.sort(key=lambda row: ORDER.index(row.provider) if row.provider in ORDER else 99)
    return candidates[0].title


def _identities(session: Session, work_ids: set[str]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    if not work_ids:
        return found
    for row in session.scalars(
        select(CalendarIdentity).where(CalendarIdentity.work_id.in_(work_ids))
    ):
        found.setdefault(row.work_id, {})[row.provider] = row.provider_id
    return found


def _genres_for(session: Session, work_ids: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if not work_ids:
        return found
    rows = session.execute(
        select(CalendarWorkGenre.work_id, CalendarGenre.name)
        .join(CalendarGenre, CalendarGenre.id == CalendarWorkGenre.genre_id)
        .where(CalendarWorkGenre.work_id.in_(work_ids))
    )
    for work_id, name in rows:
        found.setdefault(work_id, []).append(name)
    return found


def _studios_for(session: Session, work_ids: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if not work_ids:
        return found
    rows = session.execute(
        select(CalendarWorkStudio.work_id, CalendarStudio.name)
        .join(CalendarStudio, CalendarStudio.id == CalendarWorkStudio.studio_id)
        .where(CalendarWorkStudio.work_id.in_(work_ids))
    )
    for work_id, name in rows:
        found.setdefault(work_id, []).append(name)
    return found


def _art_for(session: Session, work_ids: set[str]) -> dict[str, CalendarArtwork]:
    if not work_ids:
        return {}
    return {
        row.work_id: row
        for row in session.scalars(
            select(CalendarArtwork).where(CalendarArtwork.work_id.in_(work_ids))
        )
    }


def _cover(art: CalendarArtwork | None) -> str | None:
    """Where the page should fetch the poster from.

    Served by AniFlow once the bytes are on disk, and straight from the
    provider until then - so a season is illustrated the moment it is listed
    rather than after every download has finished.
    """
    if art is None:
        return None
    if art.file:
        return f"/api/calendar/art/{art.work_id}"
    return art.source_url


def entries_for(session: Session, work_ids: set[str]) -> list[AnimeSearchResult]:
    """These Works, as everything above this layer sees them (build 149).

    Split out of ``line_up`` so that a Work can be read without being asked
    for by season. Search and the two id lookups need exactly this and have
    no season to name; before build 149 they read the v1 tables instead,
    which the Update button had not written to since build 143.

    Batched on purpose - one query per related table rather than one per Work
    - which is what makes it usable for a season of two hundred and for a
    search result of five.

    Ordered most-watched first, the same as a season. ``in_library`` and the
    sequel mark are filled in afterwards by the caller, from AniFlow's own
    database.
    """
    if not work_ids:
        return []

    titles = _titles_for(session, work_ids)
    identities = _identities(session, work_ids)
    genres = _genres_for(session, work_ids)
    studios = _studios_for(session, work_ids)
    art = _art_for(session, work_ids)
    askable = addable_providers()

    results: list[AnimeSearchResult] = []
    for work in session.scalars(select(CalendarWork).where(CalendarWork.id.in_(work_ids))):
        mine = titles.get(work.id, [])
        ids = identities.get(work.id, {})

        # Which provider's id to hand the add flow. The calendar id is the
        # stable handle; this is the one that can answer a question about it.
        usable = [name for name in ORDER if name in ids and name in askable]
        provider = usable[0] if usable else (next(iter(ids), "") or "")

        display = _pick(mine, TITLE_MAIN) or _pick(mine, TITLE_ROMAJI) or work.label or work.id
        results.append(
            AnimeSearchResult(
                provider=provider,
                provider_id=ids.get(provider, ""),
                calendar_id=work.id,
                addable=bool(usable),
                display_name=display,
                title_english=_pick(mine, TITLE_ENGLISH),
                title_romaji=_pick(mine, TITLE_ROMAJI),
                title_native=_pick(mine, TITLE_NATIVE),
                aliases=[row.title for row in mine if row.kind == TITLE_SYNONYM][:12],
                cover_url=_cover(art.get(work.id)),
                genres=sorted(genres.get(work.id, [])),
                studios=sorted(studios.get(work.id, [])),
                synopsis=work.synopsis,
                # Normalised here as well as when it was observed, so a
                # calendar filled before build 148 reads "TV" rather than
                # "tv" without being downloaded again.
                format=calendar_metadata.normalize_kind(work.kind),
                episode_count=work.episode_count,
                airing_status=work.airing_status or STATUS_UNKNOWN,
                is_adult=work.is_adult,
                air_start_date=work.air_start_date,
                air_slot_at=work.air_slot_at,
                air_slot_episode=work.air_slot_episode,
                # The page turns this into stars relative to the season on
                # screen; the number itself is only ever an ordering.
                popularity=int((work.popularity_score or 0.0) * 1_000_000),
                provider_ids=dict(ids),
            )
        )

    # What to call each show, per the Preferred titles setting. Read once for
    # the whole line-up rather than once per card - the same thing v1 did, and
    # the reason a Japanese-first installation still reads Japanese here.
    from backend.app.services import title_preference

    naming = title_preference.display_order()
    for item in results:
        item.display_name = title_preference.display_name(item, naming) or item.display_name

    results.sort(key=lambda item: (-(item.popularity or 0), item.display_name.casefold()))
    return results


def line_up(session: Session, year: int, season: str) -> list[AnimeSearchResult]:
    """Every show in this season, as the page and the TV client see it.

    Reads only the calendar database. What a Work looks like is
    ``entries_for``; this is only the question of which Works are in a season.
    """
    row = session.scalar(
        select(CalendarSeason).where(
            CalendarSeason.year == year, CalendarSeason.season == season
        )
    )
    if row is None:
        return []

    return entries_for(
        session,
        {
            membership.work_id
            for membership in session.scalars(
                select(CalendarMembership).where(
                    CalendarMembership.season_id == row.id,
                    CalendarMembership.active.is_(True),
                )
            )
        },
    )


def one(session: Session, calendar_id: str) -> AnimeSearchResult | None:
    """A single show by its calendar id, following a merge if one happened.

    Build 149: one Work rather than its whole season filtered down to one.
    It also no longer needs the Work to be *in* a season - a show whose
    memberships were all deactivated still answers, which is the promise the
    calendar id makes.
    """
    from backend.app.services.calendar_identity import resolve

    work = resolve(session, calendar_id)
    if work is None:
        return None
    found = entries_for(session, {work.id})
    return found[0] if found else None
