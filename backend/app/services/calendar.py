"""Season arithmetic.

This module used to hold a second thing: an in-process cache of season
line-ups, persisted to ``data/season-cache.json``, which the calendar read
when its own database had nothing and which a background prefetch kept warm.

Build 143 removed it. It was the second of three places a season could come
from, and by the end it was the one nobody read - the calendar preferred
downloaded data, so on any installation that had used the sources page the
prefetch was warming a cache nothing looked at, and the Refresh button was
making a request whose result never reached the screen.

Calendar v2 has one persistent source of truth, ``data/calendar.db``, and a
season nobody has loaded loads itself on demand. What is left here is the
arithmetic, which was always the useful half.
"""
from datetime import date

from backend.app.core.logging import for_component

logger = for_component("Calendar")

SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")


def resolve_season(year: int | None, season: str | None) -> tuple[int, str]:
    if year is not None and season is not None:
        normalized = season.upper()
        if normalized not in SEASONS:
            raise ValueError("Season must be WINTER, SPRING, SUMMER, or FALL")
        return year, normalized

    today = date.today()
    current_season = ((today.month - 1) // 3)
    return today.year, SEASONS[current_season]


def next_season(year: int, season: str) -> tuple[int, str]:
    index = SEASONS.index(season)
    return (year + 1, SEASONS[0]) if index == len(SEASONS) - 1 else (year, SEASONS[index + 1])


def previous_season(year: int, season: str) -> tuple[int, str]:
    index = SEASONS.index(season)
    return (year - 1, SEASONS[-1]) if index == 0 else (year, SEASONS[index - 1])


#: How far either side of the current season the window reaches. Two is the
#: distance the arrows actually travel: the season that just finished and the
#: one before it are what people check for something they missed, and the next
#: two are what the announcements are about.
SEASON_SPAN = 2

#: The old name for the same number, kept so that nothing breaks over a
#: rename. There is no prefetch any more.
PREFETCH_SPAN = SEASON_SPAN


#: What ``POST /api/calendar/update`` can be asked to do (build 148).
#:
#: One button was not enough, and five would have been too many. These are the
#: three questions people actually have:
#:
#: * ``season`` - the season on screen, and nothing else. The predictable one,
#:   and what the button has meant since build 144.
#: * ``upcoming`` - the season airing now and the two after it. What is worth
#:   knowing about: the current line-up, plus the announcements.
#: * ``initial`` - the three before, the current one and the two after. Six
#:   seasons, offered once on a fresh calendar and never again, because it is
#:   thirty provider requests and the only time they are all worth making.
#:
#: ``upcoming`` and ``initial`` are both measured from the season airing *now*
#: rather than from the one on screen. That is deliberate: they answer "fill
#: the calendar in", which is not a question about where you happen to have
#: navigated, and a person on WINTER 2019 pressing "current and next" and
#: getting 2019 through 2020 would be a surprise.
UPDATE_SCOPES = ("season", "upcoming", "initial")

#: How many seasons ahead ``upcoming`` and ``initial`` reach.
SEASONS_AHEAD = 2

#: How many seasons back ``upcoming`` reaches (build 191). One: the season that
#: has just ended. A season is fetched while it airs, and then never again
#: unless somebody goes and presses Update on it - so a show that finished
#: airing last month still read AIRING in the calendar, which is what AniFlow
#: asks before it marks a finished show Complete.
SEASONS_BACK_ON_UPDATE = 1

#: How far back ``initial`` reaches. Three: far enough to cover the shows
#: somebody is catching up on, short of the point where it is an archive.
SEASONS_BACK_ON_FIRST_FILL = 3


def seasons_after(year: int, season: str, count: int) -> list[tuple[int, str]]:
    """The ``count`` seasons following this one, nearest first."""
    found: list[tuple[int, str]] = []
    cursor = (year, season)
    for _ in range(max(0, count)):
        cursor = next_season(*cursor)
        found.append(cursor)
    return found


def seasons_before(year: int, season: str, count: int) -> list[tuple[int, str]]:
    """The ``count`` seasons preceding this one, nearest first."""
    found: list[tuple[int, str]] = []
    cursor = (year, season)
    for _ in range(max(0, count)):
        cursor = previous_season(*cursor)
        found.append(cursor)
    return found


def update_window(scope: str, year: int, season: str) -> list[tuple[int, str]]:
    """Which seasons one press of Update should fetch, in the order to do it.

    Order is a priority, not a preference: a run can be watched, and the first
    season to finish should be the one somebody is most likely looking at. So
    ``season`` does the one it was given, and the two wide scopes do the
    current season, then forward, then back - the old seasons last, because
    nobody is waiting on them.
    """
    if scope == "season":
        return [(year, season)]

    now_year, now_season = resolve_season(None, None)
    ahead = seasons_after(now_year, now_season, SEASONS_AHEAD)
    if scope == "upcoming":
        # Last, like every old season: nobody is waiting on it, and it is one
        # more request per provider on a run that is already paced (build 191).
        back = seasons_before(now_year, now_season, SEASONS_BACK_ON_UPDATE)
        return [(now_year, now_season), *ahead, *back]
    if scope == "initial":
        back = seasons_before(now_year, now_season, SEASONS_BACK_ON_FIRST_FILL)
        # Current first, then forward, then back: the current season is what
        # somebody is looking at while this runs, and the three old ones are
        # the part nobody is waiting on.
        return [(now_year, now_season), *ahead, *back]
    raise ValueError(f"Unknown update scope {scope!r}")


def season_window(year: int, season: str, span: int = SEASON_SPAN) -> list[tuple[int, str]]:
    """The seasons around one, oldest first, including it.

    Ordered rather than a set, because callers walk it in order and the order
    is a priority: the season on screen first, then outwards.
    """
    back: list[tuple[int, str]] = []
    cursor = (year, season)
    for _ in range(span):
        cursor = previous_season(*cursor)
        back.append(cursor)
    forward: list[tuple[int, str]] = []
    cursor = (year, season)
    for _ in range(span):
        cursor = next_season(*cursor)
        forward.append(cursor)
    return list(reversed(back)) + [(year, season)] + forward
