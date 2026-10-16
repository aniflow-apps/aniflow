"""Recording a discovered release against its episode.

This is the single place a discovery becomes visible in the UI. Three things
find releases - the add-anime indexer search, the RSS scan and the per-anime
feed search - and every one of them must land here, because ``EpisodeRelease``
rows are what the episode list renders. Previously only the add-anime path
wrote them, so RSS could match an episode and produce nothing at all.

Nothing is guessed:

- a release whose episode number is not a single number (a batch, "01-12") has
  no owner and is skipped rather than attached somewhere plausible;
- the same torrent found through two feeds collapses onto one row, keyed on the
  info hash when it is known;
- an episode beyond the provider's episode count creates the row *and* raises
  the count, so the UI total does not go stale.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, Episode
from backend.app.models.episode_release import EpisodeRelease
from backend.app.services.episode_state import (
    episode_number_for,
    episode_number_from_label,
    refresh_episode_status,
    resolve_episode,
)
from backend.app.services.release_parser import ParsedRelease

logger = for_component("Release")


@dataclass
class AttachResult:
    """What happened to one release."""

    episode: Episode | None = None
    release: EpisodeRelease | None = None
    created: bool = False
    skipped_reason: str | None = None

    @property
    def is_new(self) -> bool:
        return self.created and self.episode is not None


def _dedupe_key(provider: str, provider_id: str | None, info_hash: str | None) -> tuple[str, str]:
    """Identity of a release within one episode.

    The info hash is the torrent's real identity, so the same release offered
    by two different feeds (different GUIDs) becomes one row. Without a hash we
    fall back to the provider's own id.
    """
    if info_hash:
        return "infohash", info_hash.lower()
    return provider, provider_id or ""


def attach_release_to_episode(
    session: Session,
    anime: Anime,
    parsed: ParsedRelease,
    *,
    provider: str,
    provider_id: str | None = None,
    raw_name: str | None = None,
    magnet_url: str | None = None,
    torrent_url: str | None = None,
    size: int | None = None,
    seeders: int | None = None,
    info_hash: str | None = None,
) -> AttachResult:
    """Record one discovered release against the episode it belongs to.

    The caller has already decided that the release belongs to ``anime`` (see
    :mod:`services.release_identity`) and satisfies its profile.
    """
    number = episode_number_for(anime, parsed.episode)
    if number is None:
        # Either the label is not one episode, or it is one this anime does
        # not have: an earlier season's file, under the same name, from a
        # group that never restarts its numbering.
        return AttachResult(skipped_reason="episode_not_identifiable")

    episode = resolve_episode(session, anime.id, number)
    if episode is None:  # pragma: no cover - create=True never returns None
        return AttachResult(skipped_reason="episode_missing")

    # A release past the provider's count is real; the count was just stale.
    if number > (anime.episode_count or 0):
        anime.episode_count = number

    kind, key = _dedupe_key(provider, provider_id, info_hash)
    existing = None
    for candidate in episode.releases:
        candidate_kind, candidate_key = _dedupe_key(
            candidate.provider, candidate.provider_id, _info_hash_of(candidate)
        )
        if (candidate_kind, candidate_key) == (kind, key):
            existing = candidate
            break

    target = existing or EpisodeRelease(
        episode_id=episode.id,
        provider=provider,
        provider_id=provider_id or key,
    )
    target.raw_name = (raw_name or parsed.raw_name)[:500]
    if magnet_url:
        target.magnet_url = magnet_url[:1000]
    if torrent_url:
        target.torrent_url = torrent_url[:500]
    if size is not None:
        target.size = size
    if seeders is not None:
        target.seeders = seeders
    target.subgroup = parsed.subgroup
    target.resolution = parsed.resolution
    target.version = parsed.version or 1

    created = existing is None
    if created:
        session.add(target)
        session.flush()
        session.refresh(episode)

    refresh_episode_status(episode)
    return AttachResult(episode=episode, release=target, created=created)


def _info_hash_of(release: EpisodeRelease) -> str | None:
    """The info hash a stored release carries, read back out of its magnet."""
    magnet = release.magnet_url or ""
    marker = "xt=urn:btih:"
    if marker not in magnet:
        return None
    tail = magnet.split(marker, 1)[1]
    return tail.split("&", 1)[0].lower() or None


def existing_release_count(session: Session, episode_id: str) -> int:
    """How many releases an episode already knows about."""
    return len(
        list(session.scalars(select(EpisodeRelease).where(EpisodeRelease.episode_id == episode_id)))
    )


__all__ = ["AttachResult", "attach_release_to_episode", "existing_release_count"]
