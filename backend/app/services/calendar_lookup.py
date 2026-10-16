"""Finding a show in the calendar, by name or by id (build 149).

What ``calendar_local`` did, against the tables the calendar actually keeps.

The bug this closes is quiet and was three builds old. Anime search, adopt-by
-AniDB-id and Shoko adoption all looked in ``calendar_entry`` - a v1 table
that the Update button stopped writing to in build 143, when the calendar
moved to its own schema. Only the debug Sources page filled it after that. So
those three read a snapshot frozen at whenever somebody last used a page most
installations never open, while the calendar they were sitting next to was
current. Nothing broke loudly: search asks the providers as well and merges,
so the symptom was only that the offline half quietly stopped finding
anything.

Three entry points, and they are deliberately not the same shape:

* ``search`` is fuzzy, scored, and for a person typing. It is the one that
  needs the season aliases, because "Mushoku Tensei S3" and "Mushoku Tensei
  III" are the same show and neither string contains the other.
* ``by_anidb_id`` and ``by_provider_id`` are exact. An id is an id: no
  scoring, no aliases, no near misses. Build 89 learned that the hard way -
  the title search was answering season three for a season-one id.

All three return ``AnimeCandidate``, because that is what the add flow speaks,
and all three answer ``None``/``[]`` rather than raising when the calendar has
nothing - which the callers already read as "ask the network instead".
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.models.calendar import PROVEN, CalendarIdentity, CalendarTitle
from backend.app.schemas.anime import AnimeSearchResult
from backend.app.services import calendar_view

logger = for_component("Calendar")

#: How many rows a title query may pull back before scoring.
#:
#: Narrowed by SQLite, judged in Python, the same as v1 did it: the scoring
#: needs normalised titles and season aliases, neither of which is a thing
#: SQL can do. Six hundred is far more than any real query matches and still
#: cheap.
_SCAN_LIMIT = 600

#: Jikan and MyAnimeList are one catalogue under two names, and the calendar
#: stores both because provenance is about who answered.
_SAME_CATALOGUE = {
    "mal": ("mal", "jikan", "myanimelist"),
    "jikan": ("mal", "jikan", "myanimelist"),
    "myanimelist": ("mal", "jikan", "myanimelist"),
}


def _as_candidate(entry: AnimeSearchResult) -> AnimeCandidate:
    """A calendar entry in the shape the add flow already speaks.

    The two are nearly the same record seen from two sides - the calendar's
    and the provider's - so this is a rename rather than a conversion. The
    fields the calendar has and a provider search does not (``calendar_id``,
    ``addable``, ``in_library``) are simply not carried: the add flow decides
    those for itself.
    """
    return AnimeCandidate(
        provider=entry.provider,
        provider_id=entry.provider_id,
        display_name=entry.display_name,
        title_english=entry.title_english,
        title_romaji=entry.title_romaji,
        title_native=entry.title_native,
        aliases=tuple(entry.aliases or ()),
        cover_url=entry.cover_url,
        genres=tuple(entry.genres or ()),
        studios=tuple(entry.studios or ()),
        synopsis=entry.synopsis,
        format=entry.format,
        episode_count=entry.episode_count,
        airing_status=entry.airing_status,
        is_adult=entry.is_adult,
        popularity=entry.popularity,
        air_start_date=entry.air_start_date,
        air_slot_at=entry.air_slot_at,
        air_slot_episode=entry.air_slot_episode,
        provider_ids=dict(entry.provider_ids or {}),
    )


def _candidates(session: Session, work_ids: set[str]) -> list[AnimeCandidate]:
    return [_as_candidate(entry) for entry in calendar_view.entries_for(session, work_ids)]


def search(query: str, *, limit: int = 25) -> list[AnimeCandidate]:
    """Find a show among everything the calendar holds.

    Needs no network, covers every season that has been loaded, and is why
    adopting a Shoko series does not fail with "no metadata provider answered"
    while AniList is refusing.

    **First, not instead.** The caller still asks the providers and merges
    (see ``api/routes/anime.py``): build 120 reported that searching
    "ascendance of a" offered one season of a show that has five, because the
    calendar only holds the seasons somebody has loaded and one local hit was
    enough to stop anything else being asked.

    Matched the way the AniDB index is - normalised, with the season aliases -
    so "Mushoku Tensei S3" finds "Mushoku Tensei III". An exact match on any
    title comes first, then anything containing the query, and within each the
    most-watched.
    """
    from backend.app.services.anidb_titles import normalize, season_alias

    wanted = normalize(query)
    if len(wanted) < 2 or not calendar_store.exists():
        return []
    alias = season_alias(wanted)

    # Per *word* rather than by the whole query: "Mushoku Tensei: Isekai
    # Ittara Honki Dasu" and "Mushoku Tensei III: Isekai Ittara Honki Dasu"
    # are the same show and neither contains the other, which is the whole
    # reason the titles are normalised at all. The longest three words are
    # the most selective.
    words = sorted((word for word in wanted.split() if len(word) > 1), key=len, reverse=True)
    if not words:
        words = [wanted]

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        return []

    with sessions() as session:
        # Every title row, whatever its kind or language: a show is as
        # findable by its Japanese name as by its English one, and v2 stores
        # each of them as a row rather than as a column.
        statement = select(CalendarTitle.work_id)
        for word in words[:3]:
            statement = statement.where(CalendarTitle.title.ilike(f"%{word}%"))
        direct = set(session.scalars(statement.limit(_SCAN_LIMIT)))

        # A word spread across two titles - a show named in English and in
        # romaji - is not found by the query above, which asks one row to
        # contain all of them. So the words are also tried one at a time and
        # the Works that matched every word in *some* title are kept.
        if not direct and len(words) > 1:
            per_word: list[set[str]] = []
            for word in words[:3]:
                per_word.append(
                    set(
                        session.scalars(
                            select(CalendarTitle.work_id)
                            .where(CalendarTitle.title.ilike(f"%{word}%"))
                            .limit(_SCAN_LIMIT)
                        )
                    )
                )
            direct = set.intersection(*per_word) if per_word else set()

        if not direct:
            return []
        found = _candidates(session, direct)

    def score(candidate: AnimeCandidate) -> tuple[int, int]:
        names = {
            normalize(name)
            for name in (
                candidate.display_name,
                candidate.title_english,
                candidate.title_romaji,
                candidate.title_native,
                *(candidate.aliases or ()),
            )
            if name
        }
        names |= {found for found in map(season_alias, list(names)) if found}
        exact = wanted in names or (alias is not None and alias in names)
        # Negated, so sorting ascending puts an exact match and then the
        # most-watched first.
        return (0 if exact else 1, -(candidate.popularity or 0))

    found.sort(key=score)
    return found[:limit]


def by_anidb_id(anidb_id: int, *, proven_only: bool = False) -> AnimeCandidate | None:
    """The calendar's entry for one AniDB id, or None.

    An id, so no scoring and no season aliases - the two things that make the
    title search useful and made it answer season three for a season-one name.
    """
    return _by_identity("anidb", str(anidb_id), proven_only=proven_only)


def by_provider_id(provider: str, provider_id: str, *, proven_only: bool = False) -> AnimeCandidate | None:
    """The calendar's entry for one source's id, or None.

    The lookup adding an anime does before it asks the network. Every id the
    calendar knows is indexed together on the Work, so the record is found
    whichever of them the search happened to present it under.
    """
    return _by_identity(provider, provider_id, proven_only=proven_only)


def _by_identity(provider: str, provider_id: str, *, proven_only: bool = False) -> AnimeCandidate | None:
    # ``proven_only`` for the callers that write the answer down unseen
    # (build 153). An AniDB id the calendar took from a *title* match in the
    # dump is a guess; a person adding by id sees the result first and can
    # tell, the watchlist's fill-from-the-calendar cannot, and would hand one
    # show's genres and synopsis to another.
    proven_filter = (CalendarIdentity.evidence.in_(sorted(PROVEN)),) if proven_only else ()
    name = (provider or "").strip().lower()
    wanted = str(provider_id or "").strip()
    if not name or not wanted or not calendar_store.exists():
        return None
    names = _SAME_CATALOGUE.get(name, (name,))

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        return None

    with sessions() as session:
        work_ids = set(
            session.scalars(
                select(CalendarIdentity.work_id).where(
                    CalendarIdentity.provider.in_(sorted(names)),
                    CalendarIdentity.provider_id == wanted,
                    *proven_filter,
                )
            )
        )
        if not work_ids:
            return None
        found = _candidates(session, work_ids)
    # One id is one show, so this is normally a list of one. Two means the
    # identity spine has not merged them yet; the most-watched is the one
    # somebody looking for it means, and `entries_for` already sorts that way.
    return found[0] if found else None


def entry_by_anidb_id(anidb_id: int) -> AnimeSearchResult | None:
    """The calendar's own record for one AniDB id (build 254), exact like ``by_anidb_id``.

    For the TV's detail page of a watchlist show: it wants the calendar's view - airing status,
    slot, studios, synopsis - rather than the add flow's candidate.
    """
    if not calendar_store.exists():
        return None
    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        return None
    with sessions() as session:
        work_ids = set(
            session.scalars(
                select(CalendarIdentity.work_id).where(
                    CalendarIdentity.provider == "anidb",
                    CalendarIdentity.provider_id == str(anidb_id),
                )
            )
        )
        if not work_ids:
            return None
        found = calendar_view.entries_for(session, work_ids)
    return found[0] if found else None


__all__ = ["by_anidb_id", "by_provider_id", "entry_by_anidb_id", "search"]
