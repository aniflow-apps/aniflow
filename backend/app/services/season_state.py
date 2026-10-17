"""What an anime is doing, as the season rollover reads it (build 205).

See docs/season-rollover-plan.md. The rollover files an anime by what it is
*doing*, not by its season label: a show that started in summer and is still
airing in fall is airing. Pure rules on a few facts, so the watchlist's
"Waiting" mark and the worker's decisions read the same answer.

States, in the order they are tested:

``SKIP``      dropped, archived or pinned by the user: never touched.
``UPCOMING``  not started (lifecycle Upcoming, or a premiere date still ahead).
``FINISHED``  lifecycle Complete (``lifecycle_auto`` decides that).
``RUNNING``   an episode arrived in the last three weeks.
``WAITING``   started, not finished, and nothing new for three weeks - between
              parts. A newly arrived episode makes it RUNNING again.
``UNKNOWN``   not enough is known (no file yet, or about to complete): left alone.

"Arrived" is the newest file AniFlow (or an adoption) put in the library, or an
episode's own air date when one is stored. The episode rows of an unaired show
carry no air date, so the nominal weekly schedule is deliberately not used: it
cannot tell a break from a run.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.models.anime import Anime, AnimeLifecycle, Episode, EpisodeStatus
from backend.app.services.anime_season import SEASONS, index_of, season_by_date

#: No new episode for this long and a started, unfinished show is waiting.
#: One constant on purpose: the user chose three weeks, and may change it.
WAIT_AFTER = timedelta(days=21)

#: Metadata key: the user said "keep this one where it is" (see ``pin``).
PINNED_KEY = "_library_pinned"


class State(str, enum.Enum):
    SKIP = "skip"
    UPCOMING = "upcoming"
    FINISHED = "finished"
    RUNNING = "running"
    WAITING = "waiting"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Facts:
    lifecycle: AnimeLifecycle
    air_start_date: date | None
    season: str | None
    season_year: int | None
    episode_count: int | None
    owned: int
    last_episode_at: datetime | None
    pinned: bool = False


def is_pinned(anime: Anime) -> bool:
    raw = anime.metadata_json if isinstance(anime.metadata_json, dict) else {}
    return bool(raw.get(PINNED_KEY))


def set_pinned(anime: Anime, pinned: bool) -> None:
    """Remember that the user put this anime where it is on purpose."""
    raw = dict(anime.metadata_json) if isinstance(anime.metadata_json, dict) else {}
    if pinned:
        raw[PINNED_KEY] = True
    else:
        raw.pop(PINNED_KEY, None)
    # Reassigned, not mutated: SQLAlchemy does not see a change inside JSON.
    anime.metadata_json = raw


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def classify(facts: Facts, now: datetime | None = None) -> State:
    moment = now or datetime.now(UTC)
    if facts.pinned or facts.lifecycle in (AnimeLifecycle.DROPPED, AnimeLifecycle.ARCHIVED):
        return State.SKIP
    if facts.lifecycle is AnimeLifecycle.UPCOMING:
        return State.UPCOMING
    if facts.lifecycle is AnimeLifecycle.COMPLETE:
        return State.FINISHED
    if facts.air_start_date is not None and facts.air_start_date > moment.date():
        return State.UPCOMING
    last = _aware(facts.last_episode_at)
    if facts.owned <= 0 or last is None:
        return State.UNKNOWN
    if moment - last <= WAIT_AFTER:
        return State.RUNNING
    if facts.episode_count and facts.owned >= facts.episode_count:
        return State.UNKNOWN  # every episode is here; lifecycle_auto completes it
    return State.WAITING


def season_of(facts_or_anime: Facts | Anime) -> tuple[str, int] | None:
    """The stored season, else the one the premiere date means; None when neither is known."""
    season = facts_or_anime.season
    year = facts_or_anime.season_year
    if season and year:
        return season.upper(), int(year)
    start = facts_or_anime.air_start_date
    return season_by_date(start) if start is not None else None


def season_index(pair: tuple[str, int] | None) -> int | None:
    return None if pair is None else index_of(pair[1], pair[0])


def current_season_index(today: date | None = None) -> int:
    season, year = season_by_date(today or datetime.now(UTC).date())
    return index_of(year, season)


def episode_facts(db: Session, anime_ids: list[str] | None = None) -> dict[str, tuple[int, datetime | None]]:
    """Per anime: how many episodes are on disk, and when the newest one arrived."""
    from backend.app.db.types import now_utc

    moment = now_utc()
    arrived = func.max(Episode.media_imported_at)
    aired = func.max(case((Episode.aired_at <= moment, Episode.aired_at)))
    statement = (
        select(Episode.anime_id, func.count(Episode.id), arrived, aired)
        .where(Episode.status == EpisodeStatus.COMPLETE)
        .group_by(Episode.anime_id)
    )
    if anime_ids is not None:
        statement = statement.where(Episode.anime_id.in_(anime_ids))
    found: dict[str, tuple[int, datetime | None]] = {}
    for anime_id, owned, imported, aired_at in db.execute(statement).all():
        stamps = [stamp for stamp in (_aware(imported), _aware(aired_at)) if stamp is not None]
        found[anime_id] = (int(owned), max(stamps) if stamps else None)
    return found


def facts_of(anime: Anime, owned: int, last_episode_at: datetime | None) -> Facts:
    return Facts(
        lifecycle=anime.lifecycle,
        air_start_date=anime.air_start_date,
        season=anime.season,
        season_year=anime.season_year,
        episode_count=anime.effective_episode_count,
        owned=owned,
        last_episode_at=last_episode_at,
        pinned=is_pinned(anime),
    )


__all__ = [
    "SEASONS",
    "WAIT_AFTER",
    "Facts",
    "State",
    "classify",
    "current_season_index",
    "episode_facts",
    "facts_of",
    "is_pinned",
    "season_index",
    "season_of",
    "set_pinned",
]
