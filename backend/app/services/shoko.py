"""Shoko Server mode: hand a file over, then find out where it went.

AniFlow never talks to AniDB, and it hands a file to Shoko Server through two
folders rather than through Shoko's API. The placement is two folders and patience:

1. a finished download is placed in the **import root** (a Shoko *drop
   source*), and what was placed is recorded on the episode;
2. Shoko identifies the file against AniDB, renames it and moves it into the
   **media root** (a Shoko *drop destination*), under a folder of its own
   naming;
3. this module finds that file again, and only then does AniFlow know the
   anime's library folder and the episode's final filename.

Step 3 is the interesting one, because the obvious approach does not work.
Matching Shoko's folder name against the anime's title means comparing an
AniDB title with an AniList one, and those disagree exactly where it matters:
AniDB's "Mushoku Tensei: Isekai Ittara Honki Dasu 2nd Season" is AniList's
"Mushoku Tensei S2", and split cours are numbered differently on each side. A
title guess would occasionally attach the wrong folder to an anime, and then
every later episode of it would be filed into somebody else's library.

So the file is identified rather than matched, cheapest and most certain
first:

``inode``
    A move inside one filesystem is a rename: the file keeps its inode. The
    inode recorded at hand-over therefore identifies AniFlow's own file with
    certainty - no titles, no hashes, and it settles which *episode* the file
    is, which re-parsing Shoko's filename would not (AniDB numbering again).

``crc32``
    Release names carry one and Shoko's renamer keeps it, so
    ``[Group] Show - 01 (1080p) [F43EC3AE].mkv`` and
    ``Show - 01 [Group] [F43EC3AE].mkv`` are recognisably the same file. Used
    when the inode changed, which means Shoko copied across a filesystem
    boundary rather than moving.

``size``
    Byte-exact, and neither a move nor a rename changes it. Last resort, and
    only when it identifies exactly one candidate.

Nothing else is attempted. A file that none of the three finds stays waiting
and is eventually reported, because the alternative - guessing from a title -
is the failure this design exists to avoid.

Once one episode of an anime is found, the folder is known and stored, and
every later episode of that anime is expected there directly. The search only
ever runs for an anime whose folder AniFlow does not know yet.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.models.activity import AttentionKind
from backend.app.models.anime import Anime, Episode
from backend.app.services.attention import log_activity, raise_attention, resolve_open
from backend.app.services.episode_state import record_media_file, refresh_episode_status
from backend.app.services.release_parser import parse_release_name
from backend.app.services.storage import (
    StorageRoots,
    is_media_file,
    roots_for,
    safe_relative,
    storage_roots,
)

logger = for_component("Shoko")

#: How long a file may sit in the import folder before the user is told. Shoko
#: normally takes seconds to minutes; a file still there after this either was
#: not recognised (an AniDB gap, an unusual release) or was treated as a
#: duplicate and dropped. Neither resolves itself, so it stops being a wait
#: and becomes something to look at.
STUCK_AFTER = timedelta(hours=6)

#: How deep to look under the media root. Shoko usually writes one folder with
#: the files inside it, but a season subfolder is a common configuration, so a
#: little nesting is searched. Bounded on purpose: an unbounded walk of a
#: library with thousands of files runs every pass.
MAX_DEPTH = 3


@dataclass
class ShokoSummary:
    """What one resolve pass did."""

    waiting: int = 0
    resolved: int = 0
    linked: int = 0
    stuck: int = 0
    changes: list[str] = field(default_factory=list)


def _as_utc(moment: datetime) -> datetime:
    """A timestamp made comparable, wherever it came from.

    Stored instants are already aware - db/types.UtcDateTime sees to that -
    but an object built in memory and not yet round-tripped need not be, and
    comparing the two raises rather than being wrong quietly.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _pending(session: Session) -> list[Episode]:
    """Episodes handed over and not yet found in the library.

    Whether or not the episode already has a file (item 15): a v2 handed over for an episode that holds a v1 is
    waiting just the same, and was never resolved while this asked for an empty episode - so the new file was
    never recorded and the old one never remembered. ``record_media_file`` clears the hand-over, so a finished
    episode drops out of this list by itself.
    """
    return list(
        session.scalars(
            select(Episode)
            .where(Episode.import_filename.is_not(None))
            .order_by(Episode.import_started_at)
        )
    )


def _still_in_import(roots: StorageRoots, episode: Episode) -> bool:
    """Whether the handed-over file is still sitting in the import folder.

    Both layouts are checked: an anime gets a folder of its own now, and one
    handed over before that change is still where it was put.
    """
    if roots.import_root is None or not episode.import_filename:
        return False
    folder = getattr(episode.anime, "import_folder", None)
    if folder and (roots.import_root / folder / episode.import_filename).exists():
        return True
    return (roots.import_root / episode.import_filename).exists()


def _walk_media(root: Path, *, max_depth: int = MAX_DEPTH, skip: frozenset[Path] = frozenset()) -> list[Path]:
    """Every media file under ``root``, no deeper than ``max_depth``.

    ``skip`` holds folders not to enter: the folders of other libraries inside
    this one, which are walked as roots of their own.
    """
    found: list[Path] = []

    def descend(folder: Path, depth: int) -> None:
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if entry in skip:
                    continue
                if depth < max_depth:
                    descend(entry, depth + 1)
            elif entry.is_file() and is_media_file(entry):
                found.append(entry)

    descend(root, 1)
    return found


def _crc32(name: str) -> str | None:
    try:
        return parse_release_name(name).crc32
    except ValueError:
        return None


def _identify(episode: Episode, candidates: list[Path]) -> tuple[Path | None, str | None]:
    """AniFlow's own file among ``candidates``, and how it was recognised.

    Returns ``(None, None)`` when nothing identifies it. Ambiguity always
    loses: two files of the same size are not evidence about either.
    """
    if episode.import_inode:
        for candidate in candidates:
            try:
                if candidate.stat().st_ino == episode.import_inode:
                    return candidate, "inode"
            except OSError:
                continue

    wanted_crc = _crc32(episode.import_filename or "")
    if wanted_crc:
        matches = [c for c in candidates if _crc32(c.name) == wanted_crc]
        if len(matches) == 1:
            return matches[0], "crc32"

    if episode.import_size_bytes:
        matches = []
        for candidate in candidates:
            try:
                if candidate.stat().st_size == episode.import_size_bytes:
                    matches.append(candidate)
            except OSError:
                continue
        if len(matches) == 1:
            return matches[0], "size"

    return None, None


def _folder_of(roots: StorageRoots, path: Path) -> str | None:
    """The path's folder, relative to the media root. None if it escapes."""
    relative = safe_relative(roots.media_root, path)
    if relative is None:
        return None
    parent = relative.parent
    return None if str(parent) in (".", "") else parent.as_posix()


def _media_roots(session: Session, roots: StorageRoots) -> list[Path]:
    """Every folder Shoko may have filed a file into (build 192).

    With libraries there is no single media root: Shoko renames and moves
    things on its own, so "where is it" is answered by looking in all of them.
    A media root a caller pinned is the only one - that is what it asked for.
    """
    if roots.media_root_pinned:
        return [roots.media_root]
    from backend.app.services import libraries

    found = libraries.folders_of_all_libraries(session)
    if roots.media_root not in found:
        found.append(roots.media_root)
    return found


def _others(media_roots: list[Path], root: Path) -> frozenset[Path]:
    return frozenset(other for other in media_roots if other != root)


def _where_is(media_roots: list[Path], path: Path) -> tuple[Path, str] | None:
    """Which root a file is in, and its folder there. None if it is in none.

    Directly in a root is not a folder: Shoko has not filed it yet. When roots
    nest (``/media`` and ``/media/adult_shows``) the innermost one wins, so a
    file in ``/media/adult_shows/Show`` is Show in that library, not
    ``adult_shows/Show`` in the outer one.
    """
    for root in sorted(media_roots, key=lambda r: len(r.parts), reverse=True):
        relative = safe_relative(root, path)
        if relative is None:
            continue
        parent = relative.parent
        if str(parent) in (".", ""):
            # Directly in the innermost root: not filed. An outer root must not claim it.
            return None
        return root, parent.as_posix()
    return None


def _follow_to_library(session: Session, anime: Anime, root: Path) -> None:
    """The file is in another library's folder: the anime lives there now.

    Only when exactly one library claims that folder (or the anime's own does):
    two libraries over one folder cannot be told apart by where a file is, so
    the anime keeps the one it has.
    """
    from backend.app.models.library import Library

    current = getattr(anime, "library", None)
    if current is not None and Path(current.folder) == root:
        return
    owners = [
        library
        for library in session.scalars(select(Library))
        if Path(library.folder) == root
    ]
    if len(owners) == 1:
        logger.info(
            "%s is in %s now: moving it to the library %r",
            anime.display_name, root, owners[0].name,
        )
        before = current.name if current is not None else None
        anime.library_id = owners[0].id
        anime.library = owners[0]
        # Said, not just done: the usual cause is Shoko's Move on Import, and
        # the user can switch that off.
        log_activity(
            session,
            kind="LIBRARY_MOVED_BY_SHOKO",
            message=(
                f"Shoko filed it in {owners[0].name}"
                + (f" instead of {before}" if before else "")
                + "; AniFlow now follows it there"
            ),
            details={"from": before, "to": owners[0].name, "folder": str(root)},
            anime_id=anime.id,
        )


def _search_scope(roots: StorageRoots, anime: Anime) -> list[Path]:
    """The first place to look for one anime's file, if there is one.

    Shoko keeps an anime's episodes together, so its known folder is worth
    trying before the whole library. Returns an empty list when there is no
    folder to try - the caller then searches everything.
    """
    if anime.media_folder:
        folder = roots_for(anime, roots).media_root / anime.media_folder
        if folder.is_dir():
            return _walk_media(folder)
    return []


def _note_previous_version(session: Session, anime, episode: Episode, found: Path) -> None:
    """Remember the file this episode pointed at, when Shoko has now filed a different one for it.

    Looked for beside the new file first - Shoko files a series into one folder - then in the anime's own
    folder. Nothing is deleted, and a failure to write the note never fails the pass.
    """
    old_name = episode.media_filename
    if not old_name or old_name == found.name:
        return
    try:
        old = found.parent / old_name
        if not old.exists():
            from backend.app.services.storage import media_path, roots_for, storage_roots

            old = media_path(roots_for(anime, storage_roots()), anime.media_folder, old_name)
        if old is None or not old.exists() or old == found:
            return
        from backend.app.services.superseded import remember

        remember(session, anime, episode, old, kind="previous_version")
    except Exception as exc:  # noqa: BLE001 - never fail the pass over a note
        logger.warning("could not write down the previous version %s: %s", old_name, exc)


def resolve_pending(
    session_factory: sessionmaker[Session],
    *,
    media_root=None,
    import_root=None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Find handed-over files in the library and record where they landed.

    Also the other half of the story: a file that has been waiting too long is
    raised as attention, because in Shoko Server mode a file AniFlow cannot
    find is not an episode that will arrive later - it is one nobody is
    working on.
    """
    roots = storage_roots(media_root=media_root, import_root=import_root)
    summary = ShokoSummary()
    if roots.import_root is None:
        return {"error": "no import root is configured"}

    moment = now or datetime.now(UTC)
    session = session_factory()
    #: Settled here since build 131 - see the call below for why this mode
    #: needs its own. Opened once per pass and closed in the finally.
    settle_client = None
    settings = None
    try:
        from backend.app.services.app_settings import load_app_settings
        from backend.app.services.downloads import _client_from

        settings = load_app_settings(session)
        if settings.after_import_action != "keep":
            try:
                settle_client = _client_from(settings, None)
            except Exception as exc:  # noqa: BLE001 - no client is normal
                logger.info("no torrent client for the hand-over pass: %s", exc)
        # Walking the media root is the expensive part, so it is done once per
        # pass and shared by every anime whose folder is still unknown.
        media_roots = _media_roots(session, roots)
        library: list[Path] | None = None
        for episode in _pending(session):
            anime = episode.anime
            if anime is None:
                continue

            # The anime's own folder first, then everything. Narrowing is only
            # an optimisation, and it must never be the reason a file is not
            # found: a stored folder can be wrong, and one that is wrong *and
            # exists* would otherwise trap the search inside it for ever.
            found, how = _identify(episode, _search_scope(roots, anime))
            if found is None:
                if library is None:
                    library = [path for root in media_roots for path in _walk_media(root, skip=_others(media_roots, root))]
                found, how = _identify(episode, library)
            if found is None:
                waited = moment - _as_utc(episode.import_started_at) if episode.import_started_at else timedelta(0)
                if waited >= STUCK_AFTER:
                    summary.stuck += 1
                    _raise_stuck(session, episode, roots)
                else:
                    summary.waiting += 1
                    refresh_episode_status(episode)
                continue

            located = _where_is(media_roots, found)
            folder = located[1] if located else None
            if folder is None:
                # Directly in the media root: Shoko has not filed it yet, or
                # it is configured not to. Not an error, just not done.
                summary.waiting += 1
                continue

            if located is not None:
                _follow_to_library(session, anime, located[0])
            if anime.media_folder != folder:
                # Not only when empty: an anime carried over from a version
                # that invented a folder name, or one Shoko has since
                # reorganised, has to follow the file rather than the other way
                # round. The file was identified, so where it is *is* the
                # answer.
                previous = anime.media_folder
                anime.media_folder = folder
                summary.linked += 1
                summary.changes.append(f"{anime.display_name}: library folder is {folder}")
                logger.info(
                    "linked %s to %s (recognised by %s)%s",
                    anime.display_name, folder, how,
                    f", replacing {previous!r}" if previous else "",
                )

            try:
                size = found.stat().st_size
            except OSError:
                size = episode.import_size_bytes
            # With the version (build 135). Recording the file without one
            # left episode.media_version NULL, and a null version is not "the
            # same version" - `decide_version` reads it as "nothing to compare
            # with" and returns DOWNLOAD_NEW. So every episode filed by Shoko
            # Server was a candidate for being downloaded again as soon as its
            # download row went away, which is what Clear all does.
            #
            # services/importer.py has always passed `row.version` here; this
            # path simply never did. A file with no version behind it is v1,
            # which is the same convention `_new_download(version=... or 1)`
            # uses - and a wrong guess of 1 surfaces as a NEW_VERSION record
            # for the user to decide, rather than a silent re-download.
            # A v2 Shoko filed next to the v1 already in the library: write the v1 down before the episode
            # points at the new file, so both copies show and the user chooses (item 15, as the standalone
            # importer does).
            _note_previous_version(session, anime, episode, found)
            record_media_file(
                episode,
                filename=found.name,
                size_bytes=size,
                version=episode.download.version if episode.download is not None else 1,
            )
            if episode.download is not None:
                episode.download.attention_reason = None
                resolve_open(
                    session,
                    kind=AttentionKind.IMPORT_BLOCKED,
                    download_id=episode.download.id,
                )
                # And the torrent (build 131). This is the moment the episode
                # is actually in the library in Shoko Server mode, so it is
                # the moment the after-import action applies - reported as
                # *"again torrents are not stopped and removed after the file
                # is done"*.
                #
                # The importer settles the torrent where it *places* the file,
                # and in this mode it never places one: it hands it to Shoko's
                # import folder and Shoko does the rest. So nothing here ever
                # stopped a torrent, and the hourly sweep was the only thing
                # that would have - which build 120 had quietly blinded.
                if settle_client is not None and settings is not None:
                    from backend.app.services.importer import _settle_torrent

                    _settle_torrent(
                        settle_client, episode.download, settings.after_import_action
                    )
            summary.resolved += 1
            summary.changes.append(f"{anime.display_name} #{episode.number}: {found.name}")
            # Per episode, as the standalone importer does (build 125): this
            # is the moment the file reached the library, whoever moved it.
            log_activity(
                session,
                kind="EPISODE_ACQUIRED",
                message=f"Episode {episode.number} is now in the library as {found.name}",
                details={"episode": episode.number, "filename": found.name, "via": "shoko"},
                anime_id=anime.id,
                episode_id=episode.id,
            )

        summary.linked += _relink_moved(session, roots)

        # The pass itself no longer writes an entry (build 125); each
        # episode wrote its own above. Kept for the cases that are *not* one
        # episode arriving - files relinked after a move, and hand-overs Shoko
        # has visibly lost, which are about the installation.
        if summary.linked or summary.stuck:
            log_activity(
                session,
                kind="RECONCILED",
                message=_describe(summary),
                details={
                    "waiting": summary.waiting,
                    "resolved": summary.resolved,
                    "linked": summary.linked,
                    "stuck": summary.stuck,
                },
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if settle_client is not None:
            settle_client.close()
    return {
        "waiting": summary.waiting,
        "resolved": summary.resolved,
        "linked": summary.linked,
        "stuck": summary.stuck,
    }


def _relink_moved(session: Session, roots: StorageRoots) -> int:
    """Find episodes whose recorded file is no longer where it was recorded.

    Shoko renames and reorganises on its own schedule, and an anime carried
    over from a version that invented its folder name never had the right one.
    Either way the file is still in the library under the name Shoko gave it,
    which does not change when a folder does - so it is looked for by name.

    Costs nothing until something is actually missing: the library is walked
    only when at least one recorded file has gone from where it was.
    """
    stranded = [
        episode
        for episode in session.scalars(
            select(Episode).where(Episode.media_filename.is_not(None))
        )
        if not _recorded_path_exists(roots, episode)
    ]
    if not stranded:
        return 0

    media_roots = _media_roots(session, roots)
    by_name: dict[str, list[Path]] = {}
    for root in media_roots:
        for path in _walk_media(root, skip=_others(media_roots, root)):
            by_name.setdefault(path.name, []).append(path)

    repaired = 0
    for episode in stranded:
        matches = by_name.get(episode.media_filename or "", [])
        if len(matches) != 1:
            # None means it really is gone, and the reconcile pass will say so.
            # Several means two folders hold the same filename, which is not
            # evidence about either.
            continue
        located = _where_is(media_roots, matches[0])
        folder = located[1] if located else None
        anime = episode.anime
        if located is None or anime is None:
            continue
        _follow_to_library(session, anime, located[0])
        if anime.media_folder == folder:
            continue
        logger.info(
            "%s moved to %s (was %r); relinked by filename",
            anime.display_name, folder, anime.media_folder,
        )
        anime.media_folder = folder
        refresh_episode_status(episode, roots=roots, check_disk=True)
        repaired += 1
    return repaired


def _recorded_path_exists(roots: StorageRoots, episode: Episode) -> bool:
    """Whether the file this episode records is still where it was recorded."""
    anime = episode.anime
    if anime is None or not anime.media_folder or not episode.media_filename:
        return False
    return (roots_for(anime, roots).media_root / anime.media_folder / episode.media_filename).is_file()


def _raise_stuck(session: Session, episode: Episode, roots: StorageRoots) -> None:
    """Tell the user about a file Shoko has not dealt with.

    Which of two things happened is worth distinguishing, because they point
    at different places to look:

    * the file is **still in the import folder** - Shoko has not taken it,
      which usually means it could not identify it;
    * the file is **gone from the import folder** and is nowhere in the
      library - Shoko took it and did something else with it, most likely
      treating it as a duplicate of something already in the collection.

    Deliberately *not* an episode error in either case: nothing went wrong in
    AniFlow. The episode keeps reading as IMPORTING and the download row
    carries the reason, so the Downloads list and the anime's own screen can
    say "this is waiting on Shoko, go and look" rather than implying a fault
    on this side.
    """
    reason = (
        "shoko_not_imported" if _still_in_import(roots, episode) else "shoko_file_gone"
    )
    download = episode.download
    if download is not None:
        download.attention_reason = reason
    raise_attention(
        session,
        kind=AttentionKind.IMPORT_BLOCKED,
        anime_id=episode.anime_id,
        download_id=download.id if download is not None else None,
        detail=reason,
    )
    logger.warning(
        "%s: %s", episode.import_filename,
        "still in the import folder; Shoko Server has not taken it"
        if reason == "shoko_not_imported"
        else "gone from the import folder but not in the library; "
             "Shoko Server may have treated it as a duplicate",
    )


def _describe(summary: ShokoSummary) -> str:
    if summary.changes:
        head = "; ".join(summary.changes[:3])
        extra = len(summary.changes) - 3
        return f"{head} (+{extra} more)" if extra > 0 else head
    return f"Shoko pass: {summary.resolved} found, {summary.stuck} still waiting"


__all__ = ["MAX_DEPTH", "STUCK_AFTER", "ShokoSummary", "resolve_pending"]
