"""Episodes AniFlow is told not to fetch because the user has them already (item 36b).

The AniDB My List export says, per anime, which episodes are in the user's list. An anime added from
that list used to start empty, so all of it was wanted - and with Automate on, all of it was fetched,
although eleven of twelve were owned. Those episodes carry a ``skip_reason`` instead: they are not
fetched by anything automatic, they read "In your AniDB list" on the row, and the user can still
download one by hand, fetch them all, or change the release (which starts over and clears the mark).
"""
from __future__ import annotations

from backend.app.models.anime import Anime, EpisodeStatus, EpisodeType

#: The one reason so far; the code is also the wording's key (``import.reason.<code>``).
ANIDB_LIST = "skipped_anidb_list"

#: Where an episode can be marked: nothing has been fetched for it.
_NOTHING_YET = (EpisodeStatus.UNAVAILABLE, EpisodeStatus.AVAILABLE, EpisodeStatus.MISSING)


def skip_episodes(anime: Anime, numbers, reason: str = ANIDB_LIST) -> int:
    """Mark these regular episodes, where they have no file and nothing is under way. Returns how many."""
    wanted = {int(number) for number in numbers}
    marked = 0
    for episode in anime.episodes:
        if episode.episode_type is not EpisodeType.REGULAR or episode.number not in wanted:
            continue
        if episode.skip_reason or episode.media_filename or episode.status not in _NOTHING_YET:
            continue
        episode.skip_reason = reason
        marked += 1
    return marked


def clear_skips(anime: Anime) -> int:
    """Wanted again, every one. Returns how many were marked."""
    cleared = 0
    for episode in anime.episodes:
        if episode.skip_reason:
            episode.skip_reason = None
            cleared += 1
    return cleared


def skipped_count(anime: Anime) -> int:
    return sum(1 for episode in anime.episodes if episode.skip_reason)
