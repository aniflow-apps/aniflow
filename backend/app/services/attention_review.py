"""Warnings that stop being true close themselves (build 94).

Asked for: "it would be very helpful if 'Needs your attention' is a bit
dynamic and checks after an hour whether the issue is still present - if yes
it stays, if not it disappears itself."

Most kinds already do close themselves, at the moment the thing that fixes
them happens: an import resolves its own blocker, a feed that answers clears
its failure, a scan that finds a playable file closes the transcode warning.
What none of them survives is the state changing *without* AniFlow being the
one to change it - and that is most of them:

* the user deletes the duplicate file by hand;
* the episode is re-downloaded from another group and imported;
* the anime is dropped, or deleted outright;
* Shoko renames the file that was in the way.

In each case the warning is about a world that has moved on, and the only
thing left to do with it is close it. So this pass asks, of every open
record, whether the condition it was raised for still holds. A record whose
condition cannot be checked is left alone: not knowing is not the same as
knowing it is resolved.

Run beside reconcile, on the half hour. Everything it closes is marked
RESOLVED rather than deleted, so the history still says what happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.models.activity import Attention, AttentionKind, AttentionStatus
from backend.app.models.anime import Anime, AnimeLifecycle

logger = for_component("Activity")

#: An anime in one of these is not being worked on, so nothing about it needs
#: attention. Dropping a show is a way of saying "stop telling me about it".
_FORGOTTEN = (AnimeLifecycle.DROPPED, AnimeLifecycle.ARCHIVED)


@dataclass
class ReviewSummary:
    checked: int = 0
    #: Closed because the condition behind them no longer holds.
    resolved: int = 0
    #: Left open: still true, or not answerable.
    kept: int = 0
    changes: list[str] = field(default_factory=list)


def review_open_attentions(session_factory: sessionmaker[Session]) -> ReviewSummary:
    """Close every open record whose reason has gone away."""
    summary = ReviewSummary()
    session = session_factory()
    try:
        records = list(
            session.scalars(
                select(Attention).where(Attention.status == AttentionStatus.OPEN)
            )
        )
        summary.checked = len(records)
        for record in records:
            reason = _no_longer_true(session, record)
            if reason is None:
                summary.kept += 1
                continue
            record.status = AttentionStatus.RESOLVED
            record.resolved_at = datetime.now(UTC)
            record.note = (record.note or "") + f" [closed: {reason}]"
            summary.resolved += 1
            summary.changes.append(f"{record.kind.value}: {reason}")
        if summary.resolved:
            session.commit()
            logger.info(
                "closed %s of %s open attention record(s) whose reason had gone",
                summary.resolved,
                summary.checked,
            )
        return summary
    finally:
        session.close()


def _no_longer_true(session: Session, record: Attention) -> str | None:
    """Why this record can be closed, or None to leave it open.

    Every branch has to be able to say *why*, because a warning that closes
    itself for a reason nobody can read is worse than one that lingers.
    """
    anime = session.get(Anime, record.anime_id) if record.anime_id else None
    if record.anime_id and anime is None:
        return "the anime was deleted"
    if anime is not None and anime.lifecycle in _FORGOTTEN:
        return f"the anime is {anime.lifecycle.value.lower()}"

    # Deleting an anime sets these references to NULL rather than removing the
    # record, so a record about an episode of a deleted anime is left pointing
    # at nothing at all. A feed failure legitimately points at nothing but its
    # feed, which is why that kind is excluded rather than the test being
    # "has no anime".
    if (
        record.kind is not AttentionKind.FEED_FAILED
        and not record.anime_id
        and not record.download_id
        and not record.episode_id
        and not record.feed_source_id
    ):
        return "whatever it was about has been deleted"

    if record.kind is AttentionKind.IMPORT_BLOCKED:
        return _import_blocker_gone(session, record)
    if record.kind is AttentionKind.DOWNLOAD_MISSING:
        return _download_back(session, record)
    if record.kind is AttentionKind.NO_RELEASES:
        return _releases_found(session, record)
    if record.kind is AttentionKind.METADATA_CHANGED:
        return _values_agree_again(session, record, anime)
    # NEW_VERSION and AMBIGUOUS_RELEASE are decisions, not conditions: they
    # stay until somebody makes them. FEED_FAILED is cleared by the scan that
    # succeeds, which knows more about it than this does. EPISODE_NOT_IDENTIFIABLE
    # is the same as the first two in the way that matters here - the release's
    # name is not going to reparse itself - so it stays open the same way.
    return None


def _values_agree_again(session: Session, record: Attention, anime) -> str | None:
    """A provider/user disagreement that has stopped being one (build 123).

    Three ways out, and this pass only has to notice two of them: the user
    pressed the Fix button, which drops the override; or the user edited the
    override to something else. The third - Dismiss, meaning keep mine - is
    the user closing it themselves.

    Which value the record is about is read from the anime rather than from
    the record, because there are two of them and each has its own override
    column. A record whose field cannot be read is left open: not knowing is
    not knowing.
    """
    if anime is None:
        return "the anime was deleted"
    field = (record.detail or "").split(":", 1)[0].strip()
    if field == "episode_count":
        if not anime.episode_count_is_manual:
            return "the episode count is no longer overridden"
        if anime.episode_count_override == anime.episode_count:
            return "the episode count you set is what the provider says now"
        return None
    if field == "air_slot":
        if not anime.schedule_is_manual:
            return "the airing time is no longer overridden"
        return None
    return None


def _episode_of(session: Session, record: Attention):
    """The episode a record is about, by either route."""
    from backend.app.models.anime import Episode
    from backend.app.models.download import TorrentDownload

    if record.episode_id:
        return session.get(Episode, record.episode_id)
    if record.download_id:
        download = session.get(TorrentDownload, record.download_id)
        return download.episode if download is not None else None
    return None


def _import_blocker_gone(session: Session, record: Attention) -> str | None:
    """A blocked import that is no longer blocked.

    The download row going is the clearest signal - the user cleared it, or a
    release change abandoned it - and so is the episode having been imported
    since, which is what happens when the duplicate is dealt with by hand.
    """
    from backend.app.models.download import TorrentDownload

    if record.download_id and session.get(TorrentDownload, record.download_id) is None:
        return "the download it was about is gone"
    episode = _episode_of(session, record)
    if episode is None:
        return "the episode it was about is gone"
    if record.detail == "target_exists" and not episode.media_filename:
        # It was a duplicate of a file in the library, and that file has since
        # been removed - so there is nothing left to be a duplicate of.
        return "the file it clashed with is no longer in the library"
    return None


def _download_back(session: Session, record: Attention) -> str | None:
    """A torrent that had vanished from the client."""
    from backend.app.models.download import DownloadStatus, TorrentDownload

    if not record.download_id:
        return None
    download = session.get(TorrentDownload, record.download_id)
    if download is None:
        return "the download it was about is gone"
    if download.status in (DownloadStatus.COMPLETED, DownloadStatus.REMOVED):
        return "the download finished"
    if download.missing_since is None and download.attention_reason is None:
        return "the torrent is back in the client"
    return None


def _releases_found(session: Session, record: Attention) -> str | None:
    """"Nothing found to download", for an anime that now has something."""
    from backend.app.models.anime import Episode
    from backend.app.models.episode_release import EpisodeRelease

    if not record.anime_id:
        return None
    found = session.scalar(
        select(EpisodeRelease.id)
        .join(Episode, Episode.id == EpisodeRelease.episode_id)
        .where(Episode.anime_id == record.anime_id)
        .limit(1)
    )
    return "releases have been found since" if found else None


__all__ = ["ReviewSummary", "review_open_attentions"]
