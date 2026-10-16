"""Getting the same episodes again from a different release (build 87).

The situation this is for: you took whichever group was fastest, watched a
season, and now you would rather have the group that encodes better - or the
same group at 1080p instead of 720p, or anything at all that is not 10-bit
because your television transcodes it.

Nothing about that is a new download in the ordinary sense. The anime is
already on the watchlist, the episodes already exist, and what has to change
is which release AniFlow considers correct for them. So this clears the
episodes back to "not owned", changes the release profile, and lets the normal
search-and-download path do exactly what it does for a new anime.

**What happens to the files you already have** is the user's choice, and both
answers are destructive in their own way:

``replace``
    The existing files are **deleted** and the episodes reset. Chosen
    deliberately over downloading first and deleting afterwards - the point is
    usually to free the space and to leave nothing for Shoko Server to have to
    reconcile - and it means a release that turns out to be unavailable leaves
    a gap. The interface says so before it is pressed.

``backup``
    Each file is renamed to ``<name>.backup`` and left where it is. Deleting
    it stays the user's job - but since build 119 AniFlow **writes down which
    files those are**, because "it is the user's job" and "nobody can find
    out which files they are" together make it nobody's job. Asked for
    exactly that way: *"it needs to have the renamed tracked in the database,
    maybe the user later wants to remove them"*. See services/superseded.py.

``keep``
    The file is left exactly where it is **and stays** (build 189): written
    down as ``kept_original``, never deleted by AniFlow. When the new release
    arrives the episode has two files, which raises a DUPLICATE_FILES
    attention asking which to delete - the one mode in which a media server
    such as Jellyfin will list the episode twice, which the interface says in
    red before it is chosen.

``when_replaced``
    The file is **left exactly where it is** and written down, and deleted
    when the replacement has actually been imported (build 116). Asked for
    because both answers above have the same hole: between pressing the
    button and the new group finishing there is no episode there at all, and
    if the new release turns out not to exist there never will be. The note
    lives in ``superseded_files``; see services/superseded.py.

Either way the episode rows are cleared, because a row pointing at a file that
has been deleted or renamed is worse than no row - it is what makes an import
think it has already run.

**And then the references go too** (build 119). Asked for as the end of the
same lifecycle: *"then when this is done, it needs to remove the references,
so if you redownload it, the other anime has not issues like this"*. So when
a swap leaves the anime with no files of its own, its emptied folder is
removed and the folder it was stored under is forgotten - the next download
learns where the files go from where they land, which in Shoko Server mode is
the only correct answer anyway.

**And so is everything the old release left behind** (build 90). Reported
against build 89: after changing SubsPlease to Erai-raws every episode still
named a SubsPlease file, nothing downloaded by itself although Automate was
on, and the Download button fetched SubsPlease again. Three things outlive the
files unless they are cleared here:

``episode_releases``
    Every release ever discovered for the episode. The table is append-only by
    design - keeping both groups' rows is what lets AniFlow choose - but the
    old group's rows are what the row *shows* and what the button hands over,
    and after this they are no longer candidates for anything.

``downloads``
    ``queue_automated_anime`` skips any episode that already has a download
    row, on the sound grounds that it has been handed over once already. The
    rows here were handed over for a release that no longer applies, so
    without clearing them automation can never touch these episodes again.
    Forgotten the way the Downloads page's own Clear does it: the row goes,
    and nothing outside AniFlow is touched.

``the search``
    Nothing used to ask the worker to look. A new anime requests a queue pass
    when it is added; a changed release requested nothing, so the first
    episode of the new group arrived whenever the next hourly feed scan
    happened to run. The endpoint asks for a search and a queue pass now.

**And a download still in flight has to be abandoned** (build 91). Reported
from the deployment: episode 11 was downloading from SubsPlease at the moment
the group changed. Build 90 cleared the row and left the torrent running, so
qBittorrent carried on fetching the rejected release - and because the feed's
MATCHED row now had no download against it, the queue pass adopted it and
started it *again*. So the torrent is removed with its files, its marker tag
is deleted, and the feed rows for those episodes are marked IGNORED.

That is deliberately more than ``downloads.clear_download`` does. Forgetting
a row and touching nothing outside AniFlow is right for tidying a list; here
the user has said "not this release", and a torrent left running is that
release still arriving.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, Episode, EpisodeStatus

logger = for_component("Library")

#: What to do with the files that are already there.
MODES = ("replace", "backup", "when_replaced", "keep")

#: What a backed-up file is called. Deliberately not a media extension, so
#: neither AniFlow, Shoko nor Jellyfin will look at it again.
BACKUP_SUFFIX = ".backup"


@dataclass
class Plan:
    """What a re-acquire would do, before it does it."""

    anime_id: str
    mode: str
    episodes: int = 0
    files: list[str] = field(default_factory=list)
    #: Files that are recorded but not on disk. Counted so "nothing to delete"
    #: reads as a fact rather than as a failure.
    missing: int = 0


@dataclass
class Result:
    """What it actually did."""

    cleared: int = 0
    deleted: int = 0
    backed_up: int = 0
    missing: int = 0
    failed: list[str] = field(default_factory=list)
    #: Files left in place for good, written down as kept originals (189).
    kept: int = 0
    #: Releases discovered for the old profile, forgotten (build 90).
    releases_forgotten: int = 0
    #: Download rows for the old profile, forgotten.
    downloads_forgotten: int = 0
    #: Torrents that were still running for the old release, removed from the
    #: client along with their part-files (build 91).
    torrents_abandoned: int = 0
    #: Feed matches for those episodes, marked so the queue pass cannot pick
    #: them up again. Build 90 left these, and they were re-downloaded.
    feed_matches_ignored: int = 0
    #: Files left on disk with a note against them, to be deleted when their
    #: replacement has been imported (build 116).
    remembered: int = 0
    #: Folders removed because deleting their files left them empty (119).
    folders_removed: int = 0
    #: Whether the stored library folder was forgotten, so the next download
    #: learns it from where the files actually land.
    references_cleared: bool = False


def _episodes_with_files(session: Session, anime_id: str) -> list[Episode]:
    return list(
        session.scalars(
            select(Episode)
            .where(Episode.anime_id == anime_id)
            .order_by(Episode.number)
        )
    )


def _library_path(anime: Anime, episode: Episode) -> Path | None:
    from backend.app.services.storage import roots_for, storage_roots

    if not episode.media_filename:
        return None
    roots = roots_for(anime, storage_roots())
    return Path(roots.media_root) / (anime.media_folder or "") / episode.media_filename


def plan(session: Session, anime_id: str, mode: str) -> Plan:
    """What would happen. Reads only - nothing is changed.

    The interface shows this before the button does anything, because
    "delete eleven files" is not a thing to find out afterwards.
    """
    found = Plan(anime_id=anime_id, mode=mode)
    anime = session.get(Anime, anime_id)
    if anime is None:
        return found
    for episode in _episodes_with_files(session, anime_id):
        if episode.status not in (EpisodeStatus.COMPLETE, EpisodeStatus.IMPORTING):
            continue
        found.episodes += 1
        target = _library_path(anime, episode)
        if target is None:
            continue
        if target.exists():
            found.files.append(target.name)
        else:
            found.missing += 1
    return found


def run(session: Session, anime_id: str, mode: str, *, client=None) -> Result:
    """Clear the episodes and deal with the files. Does not download anything.

    The caller starts the search afterwards - deliberately separate, so that
    "the old files are gone" and "the new ones are being looked for" are two
    steps that can be reported and reasoned about on their own.

    ``client`` is the torrent client, used only to abandon downloads that are
    still running for the release being left behind. Without one - not
    configured, or unreachable - everything else still happens and the
    torrents are reported as not dealt with rather than silently ignored.
    """
    if mode not in MODES:
        raise ValueError(f"{mode!r} is not a mode")
    result = Result()
    anime = session.get(Anime, anime_id)
    if anime is None:
        return result

    # A new release means starting over, so what the user's AniDB list had is wanted again (item 36b).
    from backend.app.services.episode_skip import clear_skips

    clear_skips(anime)

    for episode in _episodes_with_files(session, anime_id):
        target = _library_path(anime, episode)
        if target is not None:
            outcome = _handle(target, mode)
            if outcome == "deleted":
                result.deleted += 1
            elif outcome == "backed_up":
                result.backed_up += 1
                # Written down rather than abandoned (build 119). The file is
                # the user's to delete; which file it is has to be findable.
                from backend.app.services.superseded import remember

                renamed = _backup_name(target)
                if renamed is not None:
                    profile = getattr(anime, "profile", None)
                    remember(
                        session,
                        anime,
                        episode,
                        renamed,
                        subgroup=(getattr(profile, "subgroups", None) or [None])[0],
                        resolution=getattr(profile, "resolution", None),
                        kind="renamed",
                    )
            elif outcome == "kept":
                from backend.app.services.superseded import remember

                profile = getattr(anime, "profile", None)
                remember(
                    session,
                    anime,
                    episode,
                    target,
                    subgroup=(getattr(profile, "subgroups", None) or [None])[0],
                    resolution=getattr(profile, "resolution", None),
                    kind="kept_original",
                )
                result.kept += 1
            elif outcome == "remembered":
                # The file stays. What it is has to be written down now,
                # while the episode row still says - in a moment it will not.
                from backend.app.services.superseded import remember

                profile = getattr(anime, "profile", None)
                remember(
                    session,
                    anime,
                    episode,
                    target,
                    subgroup=(getattr(profile, "subgroups", None) or [None])[0],
                    resolution=getattr(profile, "resolution", None),
                )
                result.remembered += 1
            elif outcome == "missing":
                result.missing += 1
            else:
                result.failed.append(target.name)
                # The row is deliberately *not* cleared when the file could
                # not be dealt with: clearing it would leave a file in the
                # library that AniFlow has forgotten, which is the one outcome
                # worse than either of the two it was asked for.
                continue
        # Before the rows go: the torrent fetching this release is fetching
        # something the user has just rejected.
        result.torrents_abandoned += _abandon_torrents(session, episode, client)
        result.feed_matches_ignored += _ignore_feed_matches(session, episode)
        _clear(episode)
        result.cleared += 1
        # What the old release left behind, and what the reported bug was
        # made of. Done per episode and only for episodes that were actually
        # cleared, so an episode whose file could not be dealt with keeps
        # everything about it - including the release it came from.
        forgotten = _forget_releases(session, episode)
        result.releases_forgotten += forgotten[0]
        result.downloads_forgotten += forgotten[1]

    _tidy_up(session, anime, result)

    logger.warning(
        "%s: %s episodes cleared for re-acquisition"
        " (%s deleted, %s backed up, %s kept until replaced, %s missing;"
        " %s releases and %s download rows forgotten;"
        " %s torrents abandoned, %s feed matches ignored)",
        anime.display_name,
        result.cleared,
        result.deleted,
        result.backed_up,
        result.remembered,
        result.missing,
        result.releases_forgotten,
        result.downloads_forgotten,
        result.torrents_abandoned,
        result.feed_matches_ignored,
    )
    return result


def _backup_name(target: Path) -> Path | None:
    """The name ``_handle`` gave the renamed file, found again.

    Read off the disk rather than recomputed: the rename picks the first free
    ``.backup``, ``.backup.2``, ``.backup.3`` - and guessing which one it
    landed on would write down a path that does not exist.
    """
    candidates = [target.with_name(target.name + BACKUP_SUFFIX)]
    candidates += [
        target.with_name(f"{target.name}{BACKUP_SUFFIX}.{index}") for index in range(2, 12)
    ]
    newest = [path for path in candidates if path.exists()]
    return newest[-1] if newest else None


def _tidy_up(session: Session, anime: Anime, result: "Result") -> None:
    """Remove what the files left behind, then forget where they were.

    The end of the lifecycle the request described. Only when the anime has
    no files of its own left: a swap that could not deal with one file has
    left something in that folder, and neither the folder nor the reference
    to it may go while it is there.
    """
    from backend.app.services.storage import roots_for, storage_roots
    from backend.app.services.superseded import prune_empty_folders

    if result.failed:
        return
    still_here = [
        episode
        for episode in session.scalars(
            select(Episode).where(Episode.anime_id == anime.id)
        )
        if episode.media_filename
    ]
    if still_here:
        return

    roots = roots_for(anime, storage_roots())
    result.folders_removed = prune_empty_folders(roots.media_root, anime.media_folder)
    if anime.media_folder and result.folders_removed:
        # Forgotten, not renamed: where the next download lands is the
        # answer, and in Shoko Server mode Shoko decides that.
        logger.info(
            "%s: forgetting the library folder %s - nothing of its own is left",
            anime.display_name, anime.media_folder,
        )
        anime.media_folder = None
        result.references_cleared = True
    # The hand-over folder goes with it: it names a place in the import root
    # that this anime no longer has anything in.
    if anime.import_folder and result.folders_removed:
        if roots.import_root is not None:
            prune_empty_folders(roots.import_root, anime.import_folder)
        anime.import_folder = None
        result.references_cleared = True


def _abandon_torrents(session: Session, episode: Episode, client) -> int:
    """Stop fetching the release being left behind, and drop what arrived.

    The files go with the torrent. A part-file of a release the user has just
    rejected is not something to keep: it cannot be imported (the episode is
    about to be cleared), nothing will ever ask for it again, and on a
    seedbox-sized library it is the difference between a tidy download folder
    and a permanent one.

    The marker tag goes too. It names a download row that is about to be
    deleted, so leaving it turns qBittorrent's tag list into a graveyard -
    and ``prune_download_tags`` only removes tags whose row it can still see.

    A client that cannot be reached is reported by returning 0 rather than
    raising: the rest of the change is worth doing, and the torrent is
    visible in qBittorrent for somebody to deal with by hand.
    """
    if client is None:
        return 0
    from backend.app.integrations.torrent_client import TorrentClientError
    from backend.app.services.downloads import _download_tag

    abandoned = 0
    for download in list(episode.downloads):
        tag = _download_tag(download.id)
        if download.info_hash:
            try:
                client.delete_torrent(download.info_hash, delete_files=True)
                abandoned += 1
                logger.info(
                    "removed torrent %s and its files: %s is being re-acquired",
                    download.info_hash,
                    episode.number,
                )
            except TorrentClientError as exc:
                # Said out loud, because the user will find the torrent still
                # there and should know AniFlow tried.
                logger.warning(
                    "could not remove torrent %s for episode %s: %s",
                    download.info_hash,
                    episode.number,
                    exc,
                )
        try:
            client.delete_tags([tag])
        except TorrentClientError as exc:
            logger.info("could not delete tag %s: %s", tag, exc)
    return abandoned


def _ignore_feed_matches(session: Session, episode: Episode) -> int:
    """Stop the feed's own matches for this episode being queued again.

    ``queue_pending_downloads`` looks for MATCHED releases with no download
    row against them, which is precisely what clearing the download rows
    creates. Build 90 cleared them and the queue pass promptly re-downloaded
    the release that had just been abandoned - twice, in the deployment's own
    log.

    Marked IGNORED rather than deleted: the feed history is an audit log, and
    "this was matched and then set aside" is a truer record than a gap.
    """
    from backend.app.models.rss import Release, ReleaseDecision
    from backend.app.services.episode_state import episode_number_from_label

    if episode.anime_id is None:
        return 0
    ignored = 0
    rows = session.scalars(
        select(Release).where(
            Release.anime_id == episode.anime_id,
            Release.decision == ReleaseDecision.MATCHED,
        )
    ).all()
    for row in rows:
        if episode_number_from_label(row.episode) != episode.number:
            continue
        row.decision = ReleaseDecision.IGNORED
        row.failure_reasons = list(row.failure_reasons or []) + ["release_changed"]
        ignored += 1
    return ignored


def _forget_releases(session: Session, episode: Episode) -> tuple[int, int]:
    """Drop the releases and download rows of the release being left behind.

    Returns ``(releases, downloads)``.

    Nothing outside AniFlow is touched - not the torrent client, not a file in
    the download folder. This is the same contract as the Downloads page's
    Clear button and for the same reason: AniFlow forgetting a row is a
    statement about AniFlow, and deleting somebody's torrent is not something
    to do as a side effect of changing a subtitle group.
    """
    from backend.app.models.download import TorrentDownload
    from backend.app.models.episode_release import EpisodeRelease

    downloads = session.scalars(
        select(TorrentDownload).where(TorrentDownload.episode_id == episode.id)
    ).all()
    for row in downloads:
        session.delete(row)
    releases = session.scalars(
        select(EpisodeRelease).where(EpisodeRelease.episode_id == episode.id)
    ).all()
    for row in releases:
        session.delete(row)
    # The lists on the episode are stale the moment those go, and the caller
    # reads episode.releases when it decides what to show.
    session.flush()
    session.refresh(episode)
    return len(releases), len(downloads)


def _handle(target: Path, mode: str) -> str:
    """Delete, rename or keep one file. Returns what happened."""
    if not target.exists():
        return "missing"
    try:
        if mode == "keep":
            # Untouched and not to be deleted either (build 189).
            return "kept"
        if mode == "when_replaced":
            # Untouched, deliberately: the whole point of this mode is that
            # the episode keeps working until the replacement is there. The
            # caller writes down which file this was (build 116).
            return "remembered"
        if mode == "replace":
            target.unlink()
            return "deleted"
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        # Not overwritten: a second re-acquisition must not destroy the first
        # backup, which may be the only copy of a release nobody seeds now.
        index = 2
        while backup.exists():
            backup = target.with_name(f"{target.name}{BACKUP_SUFFIX}.{index}")
            index += 1
        target.rename(backup)
        return "backed_up"
    except OSError as exc:
        logger.warning("could not %s %s: %s", mode, target.name, exc)
        return "failed"


def _clear(episode: Episode) -> None:
    """Forget the file behind this episode, and everything derived from it."""
    episode.status = EpisodeStatus.MISSING
    episode.media_filename = None
    episode.media_size_bytes = None
    episode.media_imported_at = None
    episode.media_version = None
    # The compatibility verdict belonged to a file that is no longer there.
    episode.media_playback = None
    episode.media_summary = None
    episode.media_findings = []
    episode.media_scanned_at = None
    # And the download stages, so the import pipeline starts from the top
    # rather than believing it has already placed something.
    episode.download_folder = None
    episode.download_filename = None
    episode.import_filename = None
    episode.import_inode = None
    episode.import_size_bytes = None
    episode.import_started_at = None
    episode.error = None


__all__ = ["BACKUP_SUFFIX", "MODES", "Plan", "Result", "plan", "run"]
