"""The one place an episode's status is decided.

Status used to be assigned ad hoc from four different services, each with its
own idea of when to write which value - and two of them matched episodes by
number string, so they silently wrote nothing at all. Here the status is
*derived* from the facts instead: what is on disk, what the torrent client is
doing, and which releases are known.

Call :func:`refresh_episode_status` after changing any of those facts. It is
idempotent, so calling it more often than necessary is always safe.
"""
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Episode, EpisodeStatus, EpisodeType
from backend.app.models.download import DownloadStatus
from backend.app.services.storage import StorageRoots, media_path, storage_roots, roots_for

logger = for_component("Episode")

#: Download states that mean "the client is still working on this".
_ACTIVE_DOWNLOAD = (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)

#: ATTENTION reasons that mean the client still owns this episode, just stuck
#: on it - the queue pass keeps re-handing a missing torrent on its own, so
#: this reads as DOWNLOADING rather than falling through to "nothing tried
#: yet". See ``QUEUE_REASONS``/``SYNC_REASONS`` in services/reasons.py.
_ATTENTION_STILL_DOWNLOADING = frozenset({"missing_in_client"})

#: ATTENTION reasons raised once a download has actually finished, before it
#: could be placed - the same situation DOWNLOADED already describes, just
#: reported through the ATTENTION status instead of COMPLETED because a
#: person has to decide something first. See ``IMPORT_REASONS``.
_ATTENTION_AFTER_DOWNLOAD = frozenset({"target_exists", "ready_to_move"})

#: States nothing is currently asking anything of - not worth a history row,
#: since an episode sitting in one of these never appears on the page that
#: would read it. Mirrors services/progress.py's own filter.
_NOT_WORTH_LOGGING = frozenset({"not_tracked", "not_aired", "no_release_yet", "skipped"})


def episode_number_from_label(label: str | None) -> int | None:
    """The single episode number a release label refers to, if any."""
    if not label:
        return None
    stripped = label.strip()
    return int(stripped) if stripped.isdigit() else None


def episode_number_for(anime, label: str | None) -> int | None:
    """Which of *this anime's* episodes a release label refers to (build 112).

    The same file is "01" to a group that restarts each season and "67" to one
    that counts from the start of the franchise. `anime.episode_offset` says
    which this anime follows, and this is the only place that arithmetic
    happens - so a path that forgets it is a path that does not compile.

    A number below the offset belongs to an **earlier season**: SubsPlease's
    39 to 66 are seasons two and three, sitting in the same search results
    under the same name. None, not a negative episode.
    """
    number = episode_number_from_label(label)
    if number is None:
        return None
    offset = int(getattr(anime, "episode_offset", 0) or 0)
    if offset <= 0:
        return number
    shifted = number - offset
    return shifted if shifted >= 1 else None


def resolve_episode(
    session: Session,
    anime_id: str,
    number: int,
    *,
    create: bool = True,
) -> Episode | None:
    """The episode row for ``number``, created on demand.

    This replaces the old lookup that compared episode *number strings* and
    silently matched nothing whenever the two sides disagreed on zero padding.
    """
    episode = session.scalar(
        select(Episode).where(
            Episode.anime_id == anime_id,
            Episode.number == number,
            Episode.episode_type == EpisodeType.REGULAR,
        )
    )
    if episode is not None or not create:
        return episode
    episode = Episode(
        anime_id=anime_id,
        number=number,
        episode_type=EpisodeType.REGULAR,
        status=EpisodeStatus.UNAVAILABLE,
    )
    session.add(episode)
    session.flush()
    return episode


def derive_status(episode: Episode, *, media_exists: bool | None) -> EpisodeStatus:
    """The status implied by this episode's current facts.

    ``media_exists`` is the answer to "is the media file on disk?" - pass
    ``None`` when the filesystem was not consulted, in which case a recorded
    media file is trusted (no episode is ever demoted to MISSING on a guess).

    Precedence, highest first:

    1. ``ERROR``     - an explicit error was recorded.
    2. ``DOWNLOADING`` - the client owns this episode right now (including a
       torrent that has gone missing - the queue pass is still trying).
    3. ``IMPORTING`` - handed to Shoko Server, not in the library yet.
    4. ``DOWNLOADED`` - finished in the client, not placed yet (including one
       stuck on a decision - a duplicate, or automation left it for a person).
    5. ``COMPLETE``/``MISSING``/``UPDATE`` - a media file was placed.
    6. ``AVAILABLE`` - releases are known, nothing downloaded yet.
    7. ``UNAVAILABLE`` - nothing is known about this episode.
    """
    if episode.error:
        return EpisodeStatus.ERROR

    download = episode.download
    if download is not None:
        if download.status in _ACTIVE_DOWNLOAD:
            return EpisodeStatus.DOWNLOADING
        if download.status is DownloadStatus.FAILED:
            return EpisodeStatus.ERROR
        if download.status is DownloadStatus.ATTENTION:
            # Left unhandled, none of the checks below know what to do with
            # an ATTENTION row either - there is no active download, no file
            # yet - and they reported AVAILABLE: "ready to download", for an
            # episode that has a download sitting right there, just a stuck
            # one. A reason nothing here recognises is genuinely unmapped and
            # falls through on purpose, rather than guessing.
            if download.attention_reason in _ATTENTION_STILL_DOWNLOADING:
                return EpisodeStatus.DOWNLOADING
            if download.attention_reason in _ATTENTION_AFTER_DOWNLOAD and not episode.media_filename:
                return EpisodeStatus.DOWNLOADED

    # Checked before DOWNLOADED, and outside the download block, because the
    # hand-over survives the torrent being removed from the client.
    if episode.import_filename and not episode.media_filename:
        return EpisodeStatus.IMPORTING

    if download is not None:
        if download.status is DownloadStatus.COMPLETED and not episode.media_filename:
            # Finished in the client but not placed in the library yet.
            return EpisodeStatus.DOWNLOADED

    if episode.media_filename:
        if media_exists is False:
            # It was COMPLETE and the file is gone. Internally this is the same
            # situation as AVAILABLE; the distinct label just tells the user
            # that something disappeared rather than never arriving.
            return EpisodeStatus.MISSING
        if _newer_release_exists(episode):
            return EpisodeStatus.UPDATE
        return EpisodeStatus.COMPLETE

    if episode.releases:
        return EpisodeStatus.AVAILABLE
    return EpisodeStatus.UNAVAILABLE


def _newer_release_exists(episode: Episode) -> bool:
    """Whether a known release supersedes the version already on disk.

    Deliberately minimal: it only compares version numbers. Deciding what to
    *do* about a newer version is the future Activity workflow's job - AniFlow
    never replaces media on its own.
    """
    current = episode.media_version
    if current is None:
        return False
    return any((release.version or 1) > current for release in episode.releases)


def media_file_exists(episode: Episode, *, roots: StorageRoots | None = None) -> bool | None:
    """Is this episode's media file on disk? ``None`` when there is nothing to check."""
    if not episode.media_filename:
        return None
    resolved = roots_for(episode.anime, roots)
    target = media_path(resolved, episode.anime.media_folder, episode.media_filename)
    if target is None:
        return None
    try:
        return target.is_file()
    except OSError:  # pragma: no cover - unreadable mount
        return None


def refresh_episode_status(
    episode: Episode,
    *,
    roots: StorageRoots | None = None,
    check_disk: bool = False,
) -> EpisodeStatus:
    """Recompute and assign this episode's status. Returns the new status.

    ``check_disk`` consults the filesystem so a deleted file can demote
    ``COMPLETE`` to ``MISSING``. It is off by default because most callers just
    changed a database fact and should not pay for a stat() per episode.
    """
    exists = media_file_exists(episode, roots=roots) if check_disk else None
    if not check_disk and episode.media_filename:
        exists = True  # trust the record; never demote without looking
    new_status = derive_status(episode, media_exists=exists)
    if episode.status is not new_status:
        logger.debug(
            "episode %s (#%s) %s -> %s", episode.id, episode.number, episode.status.value, new_status.value
        )
        episode.status = new_status
        # A real transition is news again, even for an episode archived from
        # the In Progress page in its previous state - an archived COMPLETE
        # that goes MISSING, or gets a new version, is not the same situation
        # the user already saw.
        episode.progress_archived_at = None
    if new_status is EpisodeStatus.COMPLETE and episode.progress_archived_at is None:
        # Checked independently of the transition above, not nested inside it
        # (166): an episode that reached COMPLETE before this feature existed,
        # or by any path that set the status directly, is COMPLETE on every
        # later call too - "is not new_status" never fires again for it, so
        # nesting this there left it stuck in In Progress for good, with
        # nothing but the manual Archive button able to move it. Checking the
        # status itself instead means the very next thing that touches this
        # episode archives it, transition or not.
        episode.progress_archived_at = datetime.now(UTC)
    _record_progress_event(episode)
    return new_status


def _record_progress_event(episode: Episode) -> None:
    """Log a step in the In Progress page's history, if this one is new.

    Best-effort: like this function's own caller, calling it is always safe,
    including on an episode nobody has added to a session yet (a few tests
    build one in memory) - so a step that cannot be filed is silently
    skipped rather than raising.

    Compares against ``episode_view.view_for()``, not the coarser
    ``EpisodeStatus`` this function just set two lines up - a torrent going
    missing or a duplicate blocking an import change neither `EpisodeStatus`
    nor the database row this function is called for, and those are exactly
    the steps the page most needs a history for.
    """
    from sqlalchemy.orm import object_session

    session = object_session(episode)
    if session is None:
        return

    from backend.app.core.mode import is_shoko_mode
    from backend.app.models.progress_event import EpisodeProgressEvent
    from backend.app.services.episode_view import view_for

    current = view_for(episode, shoko_mode=is_shoko_mode()).state
    if current in _NOT_WORTH_LOGGING:
        return
    # The session is opened with autoflush=False (db/session.py), so a step
    # logged earlier in this same transaction - by an earlier episode in a
    # pass's loop, or by this function's own caller running twice before a
    # commit - is not yet visible to a plain query. Reported live: an episode
    # Shoko took a day to import logged "Complete" twice, same minute, same
    # from_state. Flushing first is the fix used everywhere else in this
    # codebase for the same reason (see resolve_episode, a few lines up).
    session.flush()
    last = session.scalar(
        select(EpisodeProgressEvent.to_state)
        .where(EpisodeProgressEvent.episode_id == episode.id)
        .order_by(EpisodeProgressEvent.created_at.desc())
        .limit(1)
    )
    if last == current:
        return
    session.add(EpisodeProgressEvent(episode_id=episode.id, from_state=last, to_state=current))


def record_import_file(
    episode: Episode,
    *,
    filename: str,
    inode: int | None,
    size_bytes: int | None,
) -> None:
    """Mark this episode as handed over to Shoko Server.

    The inode is what makes the file findable again: Shoko renames it and
    moves it into the media root, and a move preserves the inode, so AniFlow
    can identify its own file without comparing titles. Size is the fallback
    for a Shoko that copied across a filesystem boundary instead.
    """
    episode.import_filename = filename
    episode.import_inode = inode
    episode.import_size_bytes = size_bytes
    episode.import_started_at = datetime.now(UTC)
    episode.error = None
    refresh_episode_status(episode)


def clear_import_file(episode: Episode) -> None:
    """Forget the hand-over, once the file has arrived in the library."""
    episode.import_filename = None
    episode.import_inode = None
    episode.import_size_bytes = None
    episode.import_started_at = None


def record_media_file(
    episode: Episode,
    *,
    filename: str,
    size_bytes: int | None,
    version: int | None = None,
) -> None:
    """Mark this episode as placed in the media library."""
    episode.media_filename = filename
    episode.media_size_bytes = size_bytes
    episode.media_imported_at = datetime.now(UTC)
    if version is not None:
        episode.media_version = version
    episode.error = None
    # The hand-over is over the moment the file is in the library, and leaving
    # it recorded would keep the episode reading as IMPORTING for ever.
    clear_import_file(episode)
    refresh_episode_status(episode)
    _learn_what_it_was_released_as(episode)


def _learn_what_it_was_released_as(episode: Episode) -> None:
    """Let the file that just arrived fill in the anime's group and quality (build 222).

    Best effort and never in the way: a file landing is the event, and a profile
    that could not be filled is not a reason for it to fail.
    """
    from sqlalchemy.orm import object_session

    session = object_session(episode)
    anime = episode.anime
    if session is None or anime is None:
        return
    try:
        from backend.app.services.shoko_adopt import learn_from_files

        learn_from_files(session, anime)
    except Exception:  # noqa: BLE001 - see above
        logger.debug("could not learn the release from %s", episode.media_filename, exc_info=True)


def record_download_filename(
    episode: Episode,
    filename: str | None,
    *,
    folder: str | None = None,
) -> None:
    """Store where the torrent client wrote this episode.

    The name is only known for certain once the client reports its content
    path; before that a best guess from the release name is used. Both are
    written here, later calls simply overwrite the earlier guess.

    ``folder`` is the client's category subfolder, stored so the source path
    stays resolvable even if the category setting changes later. It is only
    overwritten when a value is supplied.
    """
    if filename:
        episode.download_filename = filename
    if folder is not None:
        episode.download_folder = folder or None


def clear_error(episode: Episode) -> None:
    episode.error = None
    refresh_episode_status(episode)


def set_error(episode: Episode, reason: str) -> None:
    episode.error = reason[:255]
    episode.status = EpisodeStatus.ERROR


def refresh_anime_statuses(
    session: Session,
    anime_id: str,
    *,
    roots: StorageRoots | None = None,
    check_disk: bool = False,
) -> dict[str, int]:
    """Recompute every episode status of one anime. Returns a per-status count."""
    from backend.app.models.anime import Anime

    anime = session.get(Anime, anime_id)
    if anime is None:
        return {}
    counts: dict[str, int] = {}
    for episode in anime.episodes:
        status = refresh_episode_status(episode, roots=roots, check_disk=check_disk)
        counts[status.value] = counts.get(status.value, 0) + 1
    return counts


def expected_media_filename(download_filename: str | None) -> str | None:
    """The media filename AniFlow would use for a given download name.

    Currently the download name is kept verbatim, which makes the move
    deterministic and reversible. Shoko Server's own naming pass can
    change this without touching the stored download name - which is exactly
    why the two are separate columns.
    """
    if not download_filename:
        return None
    return Path(download_filename).name


__all__ = [
    "clear_error",
    "clear_import_file",
    "episode_number_for",
    "episode_number_from_label",
    "resolve_episode",
    "derive_status",
    "expected_media_filename",
    "media_file_exists",
    "record_download_filename",
    "record_import_file",
    "record_media_file",
    "refresh_anime_statuses",
    "refresh_episode_status",
    "set_error",
]
