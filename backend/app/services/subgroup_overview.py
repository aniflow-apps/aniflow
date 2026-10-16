"""Which subgroups you use, and whether any of your feeds carries them (build 193).

Two questions from the RSS Source Profiles page:

* *"A subgroup list ... how many animes (episodes) are linked to this
  subgroup ... and which RSS does cover it."*
* *"This anime or this subgroup is currently not reachable by any RSS you
  have."*

Everything here is read from what AniFlow already stores; nothing is fetched.
That sets what can honestly be said, and the page says it too:

* "Seen on feed X" comes from the scan's own history (``Release`` rows), so it
  covers the time since AniFlow started scanning, and a feed only ever offers
  its latest items. A group that has simply not released lately looks
  unreachable. "Not seen" is a hint, never proof.
* The subgroup of a file on disk is not stored, so it is read from the file's
  name. A library renamed by Shoko may drop the [Group] tag; those files count
  for nobody.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.anime import Anime, AnimeLifecycle, Episode, EpisodeStatus
from backend.app.models.release_profile import ReleaseProfile
from backend.app.models.rss import FeedSource, Release
from backend.app.services.release_parser import parse_release_name

#: How long an airing anime may go without any feed item before it is listed.
DEFAULT_SILENT_DAYS = 14


def group_key(name: str | None) -> str:
    """The same folding the setup dialog uses: case and punctuation do not matter."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


@dataclass
class FeedCoverage:
    feed_id: str | None
    feed_name: str
    items: int
    last_seen: datetime | None


@dataclass
class SubgroupRow:
    subgroup: str
    anime: int = 0
    episodes: int = 0
    wanted_by: int = 0
    feeds: list[FeedCoverage] = field(default_factory=list)

    @property
    def seen_on_a_feed(self) -> bool:
        return bool(self.feeds)


@dataclass
class SilentAnime:
    anime_id: str
    name: str
    last_seen: datetime | None


@dataclass
class Overview:
    subgroups: list[SubgroupRow]
    silent_anime: list[SilentAnime]
    days: int
    #: When the scan history starts: nothing earlier can be said about a feed.
    history_since: datetime | None
    feeds: int


def _subgroup_of_file(filename: str) -> str | None:
    try:
        return parse_release_name(filename).subgroup
    except ValueError:
        return None


def build(db: Session, *, days: int = DEFAULT_SILENT_DAYS, now: datetime | None = None) -> Overview:
    moment = now or datetime.now(UTC)
    names: dict[str, Counter] = defaultdict(Counter)  # key -> spellings seen
    held_anime: dict[str, set[str]] = defaultdict(set)
    held_episodes: Counter = Counter()
    wanted: Counter = Counter()

    # What you hold: read from the filename of every episode with a file.
    for anime_id, filename in db.execute(
        select(Episode.anime_id, Episode.media_filename).where(Episode.media_filename.is_not(None))
    ):
        subgroup = _subgroup_of_file(filename)
        key = group_key(subgroup)
        if not key:
            continue
        names[key][subgroup] += 1
        held_anime[key].add(anime_id)
        held_episodes[key] += 1

    # What you ask for: one count per anime profile that names the group.
    for (subgroups,) in db.execute(select(ReleaseProfile.subgroups)):
        once: dict[str, str] = {}
        for subgroup in subgroups or []:
            once.setdefault(group_key(subgroup), subgroup)  # the first spelling stays
        for key, subgroup in once.items():
            if key:
                names[key][subgroup] += 1
                wanted[key] += 1

    # What the feeds showed: every Release the scanner wrote, grouped by feed.
    feed_names = {fid: name for fid, name in db.execute(select(FeedSource.id, FeedSource.name))}
    seen: dict[str, dict[str | None, list]] = defaultdict(dict)
    for subgroup, feed_id, count, last in db.execute(
        select(Release.subgroup, Release.feed_source_id, func.count(), func.max(Release.created_at))
        .where(Release.subgroup.is_not(None))
        .group_by(Release.subgroup, Release.feed_source_id)
    ):
        key = group_key(subgroup)
        if not key:
            continue
        entry = seen[key].setdefault(feed_id, [0, None])
        entry[0] += count
        if last is not None and (entry[1] is None or last > entry[1]):
            entry[1] = last

    rows: list[SubgroupRow] = []
    for key in sorted(set(held_anime) | set(wanted), key=lambda k: names[k].most_common(1)[0][0].lower()):
        row = SubgroupRow(
            subgroup=names[key].most_common(1)[0][0],
            anime=len(held_anime[key]),
            episodes=held_episodes[key],
            wanted_by=wanted[key],
        )
        for feed_id, (count, last) in seen.get(key, {}).items():
            # A deleted feed leaves its items behind with no feed: still seen.
            name = feed_names.get(feed_id, "—") if feed_id else "—"
            row.feeds.append(FeedCoverage(feed_id, name, count, last))
        row.feeds.sort(key=lambda f: (-f.items, f.feed_name.lower()))
        rows.append(row)

    since = db.scalar(select(func.min(Release.created_at)))
    return Overview(
        subgroups=rows,
        silent_anime=_silent_anime(db, days, moment),
        days=days,
        history_since=since,
        feeds=db.scalar(select(func.count()).select_from(FeedSource).where(FeedSource.enabled.is_(True))) or 0,
    )


def _silent_anime(db: Session, days: int, moment: datetime) -> list[SilentAnime]:
    """Airing anime that are still waiting for episodes and that no feed has mentioned lately.

    Only those a feed *could* matter for: active, set up for releases, not told
    to ignore them, and with something still to come.
    """
    cutoff = moment - timedelta(days=days)
    last_seen = {
        anime_id: last
        for anime_id, last in db.execute(
            select(Release.anime_id, func.max(Release.created_at))
            .where(Release.anime_id.is_not(None))
            .group_by(Release.anime_id)
        )
    }
    waiting = {
        anime_id
        for (anime_id,) in db.execute(
            select(Episode.anime_id).where(Episode.status != EpisodeStatus.COMPLETE).distinct()
        )
    }
    out: list[SilentAnime] = []
    for anime in db.scalars(
        select(Anime).where(
            Anime.lifecycle == AnimeLifecycle.ACTIVE,
            Anime.entry_mode == "advanced",
            Anime.releases_ignored.is_(False),
        )
    ):
        if anime.id not in waiting:
            continue
        last = last_seen.get(anime.id)
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is None or last < cutoff:
            out.append(SilentAnime(anime.id, anime.display_name or anime.local_name, last))
    out.sort(key=lambda item: item.name.lower())
    return out
