"""Per-anime feed search: evaluate one feed against one anime's rules.

This powers the debug panel on the anime detail page. Nothing is persisted:
the caller sees exactly what the parser/matcher decided for every item and
can then explicitly queue a chosen item.
"""
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.integrations.torrent_client import TorrentClient, client_for

from backend.app.integrations.rss.base import FeedError, FeedFetcher, RssItem, fetch_feed
from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.services.app_settings import load_app_settings
from backend.app.models.anime import Anime
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.models.release_profile import ReleaseProfile
from backend.app.models.rss import FeedSource, ReleaseDecision
from backend.app.services.attention import raise_attention
from backend.app.models.activity import AttentionKind
from backend.app.services.matching import VersionAction, decide_version, evaluate_release
from backend.app.services.release_parser import parse_release_name

logger = for_component("FeedSearch")


@dataclass
class FeedSearchItem:
    raw_name: str
    torrent_url: str | None
    decision: str
    subgroup: str | None
    episode: str | None
    version: int
    resolution: str | None
    media_source: str | None
    failures: list[str] = field(default_factory=list)
    version_action: str | None = None
    #: Which feed offered this item - the check reads all of them.
    source_name: str | None = None


def _candidate_titles(anime: Anime) -> list[str]:
    return [
        anime.display_name,
        anime.local_name,
        anime.title_english or "",
        anime.title_romaji or "",
        anime.title_native or "",
        *(anime.aliases or []),
    ]


def _local_version(session: Session, anime_id: str | None, episode: str | None) -> int | None:
    """The version this anime already has for one episode label, if any.

    Resolved through the episode row rather than by comparing episode number
    strings, so "01" and "1" can no longer disagree.
    """
    from backend.app.services.downloads import (
        _episode_version_on_disk,
        resolve_episode,
    )
    from backend.app.services.episode_state import episode_number_for

    if anime_id is None:
        return None
    number = episode_number_for(session.get(Anime, anime_id), episode)
    if number is None:
        return None
    row = resolve_episode(session, anime_id, number, create=False)
    if row is None:
        return None
    # Asked of the one function that answers it (build 136). This used to be a
    # second copy of the same three lines, and it carried the same bug the
    # queue pass had: with media_version NULL - which is every episode Shoko
    # Server filed before build 135 - it answered "nothing on disk", and a
    # search then offered an episode that is sitting in the library. Reported
    # as *"It keeps download this 3 files ... All of this files do exist"*.
    return _episode_version_on_disk(row)


def _evaluate_parsed(
    session: Session,
    anime: Anime,
    profile: ReleaseProfile,
    parsed,
) -> FeedSearchItem:
    """Evaluate one parsed release against this anime (shared by search + connect)."""
    titles = _candidate_titles(anime)
    result = evaluate_release(
        parsed,
        subgroups=list(profile.subgroups),
        resolution=profile.resolution,
        media_source=profile.media_source,
        edition=profile.edition or [],
        edition_not=profile.edition_not or [],
        censor_preference=profile.censor_preference.value,
        candidate_titles=titles,
        require_title_match=profile.require_title_match,
        require_resolution_known=profile.require_resolution_known,
        require_source_known=profile.require_source_known,
        require_censor_known=profile.require_censor_known,
    )
    local = _local_version(session, anime.id, parsed.episode)
    version_action = decide_version(parsed.version, local)
    failures = [*result.failures]
    if version_action is VersionAction.SKIP_DUPLICATE:
        failures.append("duplicate_episode")
    elif version_action is VersionAction.SKIP_SUPERSEDED:
        failures.append("superseded_version")

    if not result.matched:
        decision = ReleaseDecision.REJECTED.value
    elif version_action is VersionAction.SKIP_DUPLICATE or version_action is VersionAction.SKIP_SUPERSEDED:
        decision = "SKIPPED"
    elif version_action is VersionAction.ATTENTION_NEW_VERSION:
        decision = "ATTENTION"
        failures.append("new_version_needs_review")
    else:
        decision = ReleaseDecision.MATCHED.value

    return FeedSearchItem(
        raw_name=parsed.raw_name,
        torrent_url=None,
        decision=decision,
        subgroup=parsed.subgroup,
        episode=parsed.episode,
        version=parsed.version,
        resolution=parsed.resolution,
        media_source=parsed.media_source,
        failures=failures,
        version_action=version_action.value,
    )


def evaluate_item_for_anime(session: Session, anime_id: str, raw_name: str) -> dict:
    """Re-evaluate a single release name after e.g. an alias was connected."""
    anime = session.get(Anime, anime_id)
    if anime is None or anime.profile is None:
        return {"decision": "REJECTED", "failures": ["no_profile"]}
    item = _evaluate_parsed(session, anime, anime.profile, parse_release_name(raw_name))
    return asdict(item)


def search_feed_for_anime(
    session: Session,
    anime_id: str,
    feed_source_id: str | None = None,
    *,
    feed_url_override: str | None = None,
    fetcher: FeedFetcher | None = None,
) -> dict[str, object]:
    """Fetch feeds once and evaluate every item against this anime.

    Which feeds, in order of precedence: ``feed_url_override`` (a one-off URL),
    then ``feed_source_id`` (one saved source), then - the normal case from the
    Configuration window - *every enabled source*, which is exactly what a
    scheduled scan reads. Checking a single feed would answer a narrower
    question than the one the user is asking ("is this show findable?").

    A feed that fails is reported rather than aborting the check: with three
    sources configured, one being down should not hide what the other two have.
    """
    settings = get_settings()
    effective_fetcher: FeedFetcher = fetcher or (
        lambda url: fetch_feed(url, timeout=settings.external_request_timeout_seconds)
    )

    anime = session.get(Anime, anime_id)
    if anime is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Anime not found")
    if feed_url_override:
        feeds = [("custom", feed_url_override)]
    elif feed_source_id:
        source = session.get(FeedSource, feed_source_id)
        if source is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Feed source not found")
        feeds = [(source.name, source.url)]
    else:
        feeds = [
            (source.name, source.url)
            for source in session.scalars(select(FeedSource).where(FeedSource.enabled.is_(True)))
        ]
        if not feeds:
            return {"items": [], "total": 0, "note": "no_feeds"}

    profile: ReleaseProfile | None = anime.profile
    if profile is None:
        return {"items": [], "total": 0, "note": "no_profile"}

    results: list[FeedSearchItem] = []
    failures: list[str] = []
    seen: set[str] = set()
    for name, feed_url in feeds:
        try:
            items: list[RssItem] = effective_fetcher(feed_url)
        except FeedError as exc:
            failures.append(f"{name}: {exc}")
            continue
        for item in items:
            # The same release often appears in more than one feed; showing it
            # twice would make the check look like it found twice as much.
            if item.link and item.link in seen:
                continue
            try:
                parsed = parse_release_name(item.title)
            except ValueError:
                continue
            entry = _evaluate_parsed(session, anime, profile, parsed)
            entry.torrent_url = item.link
            entry.source_name = name
            results.append(entry)
            if item.link:
                seen.add(item.link)

    payload: dict[str, object] = {
        "items": [asdict(item) for item in results],
        "total": len(results),
        "feeds_read": len(feeds) - len(failures),
    }
    if failures and not results:
        payload["note"] = f"feed_failed: {'; '.join(failures)}"
    elif failures:
        payload["note"] = f"partial: {'; '.join(failures)}"
    return payload


def queue_manual_release(
    session: Session,
    *,
    anime_id: str,
    raw_name: str,
    torrent_url: str,
    episode: str | None,
    version: int = 1,
    client=None,
) -> TorrentDownload:
    """Queue one explicitly chosen release from the debug panel.

    Works even without a configured qBittorrent: the row is created with
    ATTENTION/qb_not_configured so the linkage stays visible and auditable.
    """
    owns_client = client is None
    env = get_settings()
    settings = load_app_settings(session)
    effective_client = client or (
        client_for(settings.torrent_client, settings.qb_url, settings.qb_username, settings.qb_password,
                   timeout=env.external_request_timeout_seconds)
        if settings.qb_url else None
    )
    from backend.app.services.downloads import (
        _new_download,
        episode_number_from_label,
        resolve_episode,
    )
    from backend.app.services.episode_state import record_download_filename, refresh_episode_status
    from backend.app.services.release_parser import parse_release_name

    anime = session.get(Anime, anime_id)
    if anime is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Anime not found")

    # A download must belong to an episode, so fall back to the release name
    # when the caller did not name one.
    number = episode_number_from_label(episode)
    if number is None:
        try:
            number = episode_number_from_label(parse_release_name(raw_name).episode)
        except ValueError:
            number = None
    if number is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail="The episode number could not be determined from the release name",
        )
    episode_row = resolve_episode(session, anime.id, number)

    row = _new_download(
        episode_id=episode_row.id,
        anime_id=anime.id,
        version=version,
        label=raw_name[:500],
        status=DownloadStatus.QUEUED,
    )
    record_download_filename(episode_row, raw_name)
    session.add(row)
    session.flush()  # the attention FK below needs the download row to exist
    if effective_client is None:
        row.status = DownloadStatus.ATTENTION
        row.attention_reason = "qb_not_configured"
        raise_attention(
            session,
            kind=AttentionKind.IMPORT_BLOCKED,
            anime_id=anime.id,
            download_id=row.id,
            detail="qb_not_configured",
        )
    else:
        tag = f"aniflow-manual-{row.id}"
        try:
            accepted = effective_client.add_torrent(torrent_url, category=settings.qb_category, tag=tag)
        except Exception as exc:  # noqa: BLE001 - only a failed ADD is a failed queue
            row.status = DownloadStatus.FAILED
            row.error = str(exc)[:255]
        else:
            if not accepted:
                row.status = DownloadStatus.FAILED
                row.error = "client rejected the torrent"
            else:
                # The add succeeded. Hash lookup is best-effort; the periodic
                # sync resolves it via the marker tag if it lags behind.
                try:
                    found = effective_client.find_torrents(category=settings.qb_category, tag=tag)
                    if found:
                        row.info_hash = found[0].info_hash
                        row.qb_state = found[0].state
                except Exception:  # noqa: BLE001
                    logger.info("hash resolution deferred for %s", raw_name)
    session.flush()
    session.expire(episode_row, ["downloads"])  # see the row just added
    refresh_episode_status(episode_row)
    session.commit()
    session.refresh(row)
    if owns_client and effective_client is not None:
        effective_client.close()
    return row


__all__ = ["search_feed_for_anime", "queue_manual_release"]
