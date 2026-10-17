"""A show with every episode on disk is complete (build 188).

Asked for after the watchlist showed last season's twelve-episode shows at
12 / 12 and still "Active": nothing ever moved them, because the lifecycle was
only ever changed by hand. This does it, and does it narrowly.

**The user always wins.** Setting a lifecycle by hand - "Set active" on a show
that is 12 / 12, say - writes ``_lifecycle_manual`` into the anime's metadata,
and nothing here touches that anime again. Without that, "edit it back to
Active" would last until the next pass.

**Complete is not free.** A COMPLETE anime is no longer scanned for releases or
searched for, which is exactly right for a finished show and exactly wrong for
one whose provider understated the episode count: it would read 12 / 12 while
episode 13 aired, and nothing would fetch it. So a show whose premiere is known
is only completed once its nominal run is over - one episode a week from the
premiere, plus a week's margin. A show with no date is judged on the count
alone; a library series adopted from Shoko typically has none, and is typically
finished.

Only ACTIVE shows are completed. UPCOMING has not aired, and DROPPED and
ARCHIVED are the user's own decisions.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle, EpisodeStatus

logger = for_component("Lifecycle")

#: Metadata key: the user chose this anime's lifecycle, so it is not automatic.
MANUAL_KEY = "_lifecycle_manual"

#: Extra time after the nominal last episode before a show counts as over.
MARGIN = timedelta(days=7)


def mark_manual(anime: Anime, manual: bool = True) -> None:
    """Remember that a person set this anime's lifecycle."""
    raw = dict(anime.metadata_json) if isinstance(anime.metadata_json, dict) else {}
    if manual:
        raw[MANUAL_KEY] = True
    else:
        raw.pop(MANUAL_KEY, None)
    # Reassigned, not mutated: SQLAlchemy does not see a change inside JSON.
    anime.metadata_json = raw


def is_manual(anime: Anime) -> bool:
    raw = anime.metadata_json if isinstance(anime.metadata_json, dict) else {}
    return bool(raw.get(MANUAL_KEY))


def mark_upcoming(anime: Anime) -> bool:
    """A future entry has not started, so it is Upcoming, not Active (build 205).

    Added as Active it read "Active" on the page of a show with no episode aired.
    Every tracking path treats the two alike, so nothing stops being tracked. A
    lifecycle the user set by hand is left alone. Returns whether it changed.
    """
    if anime.entry_mode != "future" or anime.lifecycle is not AnimeLifecycle.ACTIVE or is_manual(anime):
        return False
    anime.lifecycle = AnimeLifecycle.UPCOMING
    return True


def activate_when_it_starts(anime: Anime) -> bool:
    """An Upcoming anime that leaves Upcoming (it starts, or is moved to a real library) is Active."""
    if anime.lifecycle is not AnimeLifecycle.UPCOMING or is_manual(anime):
        return False
    anime.lifecycle = AnimeLifecycle.ACTIVE
    return True


def correct_upcoming(session: Session) -> int:
    """Future entries filed as Active before build 205 become Upcoming. Idempotent."""
    changed = 0
    for anime in session.scalars(
        select(Anime).where(Anime.entry_mode == "future", Anime.lifecycle == AnimeLifecycle.ACTIVE)
    ):
        changed += int(mark_upcoming(anime))
    if changed:
        session.commit()
    return changed


def correct_upcoming_in(session_factory: sessionmaker[Session]) -> int:
    session = session_factory()
    try:
        return correct_upcoming(session)
    finally:
        session.close()


def run_is_over(anime: Anime, today: date) -> bool:
    """Whether the show's nominal run has ended, by its premiere date."""
    count = anime.effective_episode_count or 0
    if anime.air_start_date is None or count <= 0:
        return True
    last_aired = anime.air_start_date + timedelta(weeks=max(count - 1, 0))
    return today >= last_aired + MARGIN


def owns_every_episode(anime: Anime) -> bool:
    """Every episode the show has is on disk - the "12 / 12" on its card."""
    count = anime.effective_episode_count or 0
    if count <= 0:
        return False
    owned = sum(1 for episode in anime.episodes if episode.status is EpisodeStatus.COMPLETE)
    return owned >= count


def provider_says_finished(anime: Anime) -> bool:
    """Whether the calendar's own record of this show says it has finished airing.

    A local lookup, never a request (build 190). Reported on a 13 / 13 show
    left Active: the week's margin below is there for a count that is too low,
    and it also made a show that really had finished wait. A provider that
    says FINISHED settles it - the count and the run are then the provider's
    own statement, not a guess from a premiere date. A calendar that has not
    been refreshed still says AIRING, which leaves things as they were.
    """
    try:
        from backend.app.services import calendar_lookup

        found = None
        if anime.anidb_id:
            found = calendar_lookup.by_anidb_id(int(anime.anidb_id))
        if found is None and anime.primary_provider and anime.primary_provider_id:
            found = calendar_lookup.by_provider_id(anime.primary_provider, str(anime.primary_provider_id))
        return found is not None and found.airing_status == "FINISHED"
    except Exception:  # noqa: BLE001 - a calendar that cannot answer is "not known"
        return False


def should_complete(anime: Anime, today: date) -> bool:
    if anime.lifecycle is not AnimeLifecycle.ACTIVE or is_manual(anime) or not owns_every_episode(anime):
        return False
    return run_is_over(anime, today) or provider_says_finished(anime)


def complete_finished(session: Session, *, today: date | None = None) -> int:
    """Complete every show that is done. Returns how many changed."""
    moment = today or datetime.now(UTC).date()
    changed = 0
    for anime in session.scalars(select(Anime).where(Anime.lifecycle == AnimeLifecycle.ACTIVE)):
        if should_complete(anime, moment):
            anime.lifecycle = AnimeLifecycle.COMPLETE
            anime.drop_mode = None
            changed += 1
            logger.info("%r has every episode - marked complete", anime.display_name)
    if changed:
        session.commit()
    return changed


def complete_finished_in(session_factory: sessionmaker[Session]) -> int:
    session = session_factory()
    try:
        return complete_finished(session)
    finally:
        session.close()


__all__ = [
    "MANUAL_KEY",
    "activate_when_it_starts",
    "complete_finished",
    "complete_finished_in",
    "correct_upcoming",
    "correct_upcoming_in",
    "is_manual",
    "mark_manual",
    "mark_upcoming",
]
