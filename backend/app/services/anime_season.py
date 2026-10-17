"""Which season a watchlist anime belongs to, as the providers say (build 190).

The watchlist used to work the season out from the premiere date alone, which
is wrong for a show that starts in the last days of a season: a premiere on
26 September is "summer" by the calendar months and a fall anime by every
catalogue. And a show added months ahead has a date that is still in the
future, so it could not be filed under the season it belongs to until its
first episode aired.

The calendar already knows - it is how the show appears under Fall 2026 at all.
Each provider says which season it lists a show in (``calendar_membership``),
so this reads that and writes it on the anime: ``season`` and ``season_year``.
Local reads only, never a request.

Providers disagree about shows that have not aired, and some list a running
show in two seasons. So the answer is chosen, not just taken: the membership
closest to where the premiere date puts the show, AniList first among equals,
and nothing at all when the closest one is more than two seasons away - a
carried-over listing is not the season the show started in. No answer leaves
the columns empty and the watchlist falls back to the date.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime

logger = for_component("Library")

SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")

#: Shows that premiere in the last days of a season belong to the next one -
#: the broadcast season starts a week before the calendar month does.
EDGE_DAYS = 7


def index_of(year: int, season: str) -> int:
    return year * 4 + SEASONS.index(season.upper())


def season_by_date(start: date) -> tuple[str, int]:
    """The season a premiere date means, with the end-of-season edge allowed for."""
    shifted = start + timedelta(days=EDGE_DAYS)
    return SEASONS[(shifted.month - 1) // 3], shifted.year


def pick_season(
    memberships: list[tuple[str, int, str]], start: date | None
) -> tuple[str, int] | None:
    """Choose one season from ``(provider, year, season)`` listings, or None.

    With a premiere date: the listing closest to where the date puts the show,
    AniList winning a tie and the later season winning after that; nothing if
    even the closest is more than two seasons off. Without one: AniList's, else
    the latest.
    """
    usable = [(provider, year, season) for provider, year, season in memberships if season.upper() in SEASONS]
    if not usable:
        return None
    if start is not None:
        expected = index_of(*reversed(season_by_date(start)))
        best = min(
            usable,
            key=lambda entry: (
                abs(index_of(entry[1], entry[2]) - expected),
                entry[0] != "anilist",
                -index_of(entry[1], entry[2]),
            ),
        )
        if abs(index_of(best[1], best[2]) - expected) > 2:
            return None
        return best[2].upper(), best[1]
    best = max(usable, key=lambda entry: (entry[0] == "anilist", index_of(entry[1], entry[2])))
    return best[2].upper(), best[1]


def calendar_memberships(anime: Anime) -> list[tuple[str, int, str]]:
    """Every season the calendar lists this show in, as (provider, year, season)."""
    from backend.app.db import calendar_store
    from backend.app.models.calendar import CalendarIdentity, CalendarMembership, CalendarSeason

    if not calendar_store.exists():
        return []
    wanted: list[tuple[str, str]] = []
    if anime.anidb_id:
        wanted.append(("anidb", str(anime.anidb_id)))
    if anime.primary_provider and anime.primary_provider_id:
        wanted.append((str(anime.primary_provider).lower(), str(anime.primary_provider_id)))
    if not wanted:
        return []
    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        return []
    with sessions() as calendar:
        work_ids: set[str] = set()
        for provider, provider_id in wanted:
            work_ids.update(
                calendar.scalars(
                    select(CalendarIdentity.work_id).where(
                        CalendarIdentity.provider == provider,
                        CalendarIdentity.provider_id == provider_id,
                    )
                )
            )
        if not work_ids:
            return []
        rows = calendar.execute(
            select(CalendarMembership.provider, CalendarSeason.year, CalendarSeason.season)
            .join(CalendarSeason, CalendarSeason.id == CalendarMembership.season_id)
            .where(CalendarMembership.work_id.in_(sorted(work_ids)), CalendarMembership.active.is_(True))
        ).all()
    return [(str(provider), int(year), str(season)) for provider, year, season in rows]


def fill_missing(session: Session, *, limit: int = 300) -> int:
    """Write the season on the anime that have none and that the calendar knows.

    Returns how many were filled. An anime the calendar does not know stays
    empty, and is asked about again on a later pass.
    """
    filled = 0
    pending = session.scalars(select(Anime).where(Anime.season.is_(None)).limit(limit))
    for anime in pending:
        try:
            picked = pick_season(calendar_memberships(anime), anime.air_start_date)
        except Exception as exc:  # noqa: BLE001 - never let a tag stop the pass
            logger.info("no season for %s yet: %s", anime.display_name, exc)
            continue
        if picked is None:
            continue
        anime.season, anime.season_year = picked
        filled += 1
    if filled:
        session.commit()
    return filled


def current_season(today: date | None = None) -> tuple[str, int]:
    """The season it is now, with the end-of-season edge allowed for (build 205)."""
    from datetime import UTC, datetime

    return season_by_date(today or datetime.now(UTC).date())


def refresh_unaired(session: Session, *, limit: int = 300) -> int:
    """Ask the calendar again about shows that have not started.

    ``fill_missing`` only writes a season that is empty, so one that providers
    later move (a show pushed from fall to winter) would stay wrong until it
    aired. Until then it is cheap to re-read. Returns how many changed.
    """
    from backend.app.models.anime import AnimeLifecycle

    changed = 0
    today = date.today()
    pending = session.scalars(
        select(Anime)
        .where(Anime.lifecycle == AnimeLifecycle.UPCOMING, Anime.season.is_not(None))
        .limit(limit)
    )
    from backend.app.services.metadata_fill import manual_fields

    for anime in pending:
        if "season" in manual_fields(anime):
            continue  # the user's own choice (item 17)
        if anime.air_start_date is not None and anime.air_start_date <= today:
            continue
        try:
            picked = pick_season(calendar_memberships(anime), anime.air_start_date)
        except Exception as exc:  # noqa: BLE001 - never let a tag stop the pass
            logger.info("could not re-read the season of %s: %s", anime.display_name, exc)
            continue
        if picked is not None and picked != (anime.season, anime.season_year):
            anime.season, anime.season_year = picked
            changed += 1
    if changed:
        session.commit()
    return changed


_last_refresh = 0.0

#: Upcoming shows are re-read about once a day.
REFRESH_SECONDS = 86400


def refresh_unaired_in(session_factory, *, now: float | None = None) -> int:
    import time

    global _last_refresh
    moment = now if now is not None else time.monotonic()
    if _last_refresh and moment - _last_refresh < REFRESH_SECONDS:
        return 0
    _last_refresh = moment
    session = session_factory()
    try:
        return refresh_unaired(session)
    finally:
        session.close()


_last_pass = 0.0

#: How long between passes: the calendar changes by the hour, not by the second.
PASS_SECONDS = 600


def fill_missing_in(session_factory, *, now: float | None = None) -> int:
    """``fill_missing`` at most every ten minutes, for the worker's busy job."""
    import time

    global _last_pass
    moment = now if now is not None else time.monotonic()
    if _last_pass and moment - _last_pass < PASS_SECONDS:
        return 0
    _last_pass = moment
    session = session_factory()
    try:
        return fill_missing(session)
    finally:
        session.close()


def reset() -> None:
    """For tests."""
    global _last_pass, _last_refresh
    _last_pass = 0.0
    _last_refresh = 0.0


__all__ = [
    "current_season",
    "fill_missing",
    "fill_missing_in",
    "pick_season",
    "refresh_unaired",
    "refresh_unaired_in",
    "season_by_date",
]
