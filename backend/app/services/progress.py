"""What is currently moving through AniFlow, in one list (build 164-165).

The In Progress page's whole reason to exist: today an episode's journey is
told across three places that do not agree with each other - the watchlist's
own per-episode state, the Downloads page's torrent rows, and the Attention
page's to-do list. This module answers the same question all three actually
want answered - "where is this episode, and does it need anything?" - from
the one place that already knows: ``services/episode_view.py``.

Nothing here recomputes what that module already derives. This is a listing
and a bit of bookkeeping (archiving, "how long has it been like this") layered
on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.activity import Attention, AttentionStatus
from backend.app.models.anime import Anime, AnimeLifecycle, Episode, EpisodeStatus
from backend.app.models.progress_event import EpisodeProgressEvent
from backend.app.services.episode_view import EpisodeView, view_for

#: States nothing is currently asking anything of - the bulk of a library that
#: has nothing to do with what is "in progress" right now. Never reachable for
#: this page's SQL prefilter alone (see the query below); kept as a second,
#: authoritative check because `releases_ignored` can put an otherwise
#: non-UNAVAILABLE episode into one of these too.
_HIDDEN_STATES = frozenset({"not_tracked", "not_aired", "no_release_yet", "skipped"})

#: An anime in one of these is not being worked on - see attention_review.py,
#: which excludes the same lifecycles for the same reason.
_FORGOTTEN = (AnimeLifecycle.DROPPED, AnimeLifecycle.ARCHIVED)

#: How many past steps a card's history shows. Every real change in the
#: episode's own state (episode_state._record_progress_event), so a season's
#: worth of episodes never approaches this on its own.
_HISTORY_LIMIT = 50

#: How many episodes the Archive shows. Asked for directly (166): past this,
#: the oldest simply drops off the list - nothing is deleted, and nothing
#: about the anime or episode itself changes, so an episode dropped this way
#: is not reachable any other way either (it is not "in progress" - archiving
#: it was correct and stays correct) until something about it genuinely
#: changes again, the same as any other archived episode.
ARCHIVE_LIMIT = 150


@dataclass
class ProgressItem:
    episode: Episode
    anime: Anime
    view: EpisodeView
    #: When the episode entered its current state, from whichever timestamp
    #: actually means that for this state - None when nothing on the episode
    #: or its download says (e.g. a plain "ready").
    since: datetime | None
    #: The OPEN attention record about this episode, if any - reused rather
    #: than duplicated, so a working fix button (Move now, Approve, Retry...)
    #: appears here exactly where it already does on the Attention page.
    attention: Attention | None
    #: The release AniFlow queued, for the same reason the Attention page
    #: names it: three rows reading only the anime's name are indistinguishable
    #: when there is more than one thing happening to it.
    release_label: str | None = None
    history: list[EpisodeProgressEvent] = field(default_factory=list)


def _duplicate_episode_ids():
    """Episodes with an open duplicate-files case (build 189), archived or not."""
    from backend.app.models.activity import AttentionKind

    return select(Attention.episode_id).where(
        Attention.kind == AttentionKind.DUPLICATE_FILES,
        Attention.status == AttentionStatus.OPEN,
        Attention.episode_id.is_not(None),
    )


def _visible_episodes(session: Session, *, archived: bool):
    # A finished episode is archived the moment it completes, but one with two
    # files is not finished: it stays on the page until somebody chooses.
    not_archived = Episode.progress_archived_at.is_(None) | Episode.id.in_(_duplicate_episode_ids())
    archived_only = Episode.progress_archived_at.is_not(None) & Episode.id.not_in(_duplicate_episode_ids())
    statement = (
        select(Episode)
        .join(Anime, Episode.anime_id == Anime.id)
        .where(Anime.lifecycle.not_in(_FORGOTTEN))
        .where(Episode.status != EpisodeStatus.UNAVAILABLE)
        .where(archived_only if archived else not_archived)
        .options(selectinload(Episode.anime), selectinload(Episode.downloads))
    )
    if archived:
        # Most recently archived first, and no more than the Archive shows at
        # all - fetching everything just to keep the newest ARCHIVE_LIMIT
        # would grow with the library forever for no reason.
        statement = statement.order_by(Episode.progress_archived_at.desc()).limit(ARCHIVE_LIMIT)
    return session.scalars(statement).all()


def _build_item(session: Session, episode: Episode, anime: Anime, shoko_mode: bool) -> ProgressItem | None:
    view = view_for(episode, anime=anime, shoko_mode=shoko_mode)
    if view.state in _HIDDEN_STATES:
        return None
    download = episode.download
    attention = _open_attention_for(session, episode)
    from backend.app.models.activity import AttentionKind

    if attention is not None and attention.kind is AttentionKind.DUPLICATE_FILES and view.state == "complete":
        view = EpisodeView("duplicate_files", detail=attention.detail)
    return ProgressItem(
        episode=episode,
        anime=anime,
        view=view,
        since=_since_for(episode, view),
        attention=attention,
        release_label=download.label if download is not None else None,
        history=_history_for(session, episode),
    )


def list_in_progress(session: Session, *, shoko_mode: bool) -> list[ProgressItem]:
    """Every episode with something currently happening to it, newest first."""
    items = [
        item
        for episode in _visible_episodes(session, archived=False)
        if episode.anime is not None
        and (item := _build_item(session, episode, episode.anime, shoko_mode)) is not None
    ]
    items.sort(key=lambda item: item.since or item.episode.updated_at, reverse=True)
    return items


def in_progress_episode_ids(session: Session, *, shoko_mode: bool) -> set[str]:
    """Which episodes are on the In Progress page right now (build 167).

    Read by Attention and Activity to leave out anything about an episode
    that already has a card here - an episode moving through the pipeline
    used to tell the same story three times: once as a card, once as a
    permanent Attention row and once as a permanent Activity row. Reuses this
    module's own listing rather than a second opinion about what "in
    progress" means, the same reasoning as everything else in this file.
    """
    return {item.episode.id for item in list_in_progress(session, shoko_mode=shoko_mode)}


def list_archived(session: Session, *, shoko_mode: bool) -> list[ProgressItem]:
    """Every episode that has left the active list, most recently archived first."""
    items = [
        item
        for episode in _visible_episodes(session, archived=True)
        if episode.anime is not None
        and (item := _build_item(session, episode, episode.anime, shoko_mode)) is not None
    ]
    items.sort(key=lambda item: item.episode.progress_archived_at or item.episode.updated_at, reverse=True)
    return items


def _since_for(episode: Episode, view: EpisodeView) -> datetime | None:
    """When the episode entered the state it is currently reporting.

    Read from whichever fact already means that - nothing new is recorded to
    answer this. A state with no clean answer (``ready``, mainly - a release
    just sitting there matched) reports None rather than a guess.
    """
    download = episode.download
    if view.state == "download_missing":
        return download.missing_since if download is not None else None
    if view.state == "download_stalled":
        return download.stalled_since if download is not None else None
    if view.state == "downloading":
        return download.created_at if download is not None else None
    if view.state in ("moving_to_library", "moving_to_shoko"):
        return download.updated_at if download is not None else None
    if view.state == "with_shoko":
        return episode.import_started_at
    if view.state == "complete":
        return episode.media_imported_at
    if view.state == "failed":
        return download.updated_at if download is not None else episode.updated_at
    return None


def _open_attention_for(session: Session, episode: Episode) -> Attention | None:
    """The OPEN attention this episode is reachable through, if any.

    An attention names its subject one of two ways (build 90): directly by
    episode, or through the download it is about - the same two routes
    services/attention.py's own action buttons already rely on.
    """
    download = episode.download
    conditions = [Attention.episode_id == episode.id]
    if download is not None:
        conditions.append(Attention.download_id == download.id)
    return session.scalar(
        select(Attention)
        .where(Attention.status == AttentionStatus.OPEN, or_(*conditions))
        .order_by(Attention.created_at.desc())
    )


def _history_for(
    session: Session, episode: Episode, limit: int = _HISTORY_LIMIT
) -> list[EpisodeProgressEvent]:
    return list(
        session.scalars(
            select(EpisodeProgressEvent)
            .where(EpisodeProgressEvent.episode_id == episode.id)
            .order_by(EpisodeProgressEvent.created_at.desc())
            .limit(limit)
        )
    )


def dismiss_progress(session: Session, episode_id: str) -> Episode:
    """Send a stuck episode to the Archive by hand.

    Not a resolution of anything - the episode's own facts are untouched.
    `refresh_episode_status` clears this the moment something about the
    episode actually changes again, so archiving a stuck episode is only
    until the next real development, while a COMPLETE one (archived
    automatically the moment it gets there - see episode_state.py) stays
    archived for good, since nothing changes a finished episode on its own.
    """
    from fastapi import HTTPException

    episode = session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    episode.progress_archived_at = datetime.now(UTC)
    return episode


__all__ = [
    "ProgressItem",
    "dismiss_progress",
    "in_progress_episode_ids",
    "list_archived",
    "list_in_progress",
]
