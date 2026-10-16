"""Place completed downloads into the anime media library.

Safety rules (AGENTS.md) implemented here:

- Every source and destination is validated to stay inside its configured
  root; anything pointing outside becomes an ATTENTION record, never an import.
- Local media is never destroyed. ``file_operation`` decides how a file gets
  to its next stage: ``copy`` (default, ``shutil.copy2``) leaves the download
  in place so the torrent can keep seeding, and needs periodic manual cleanup
  of the download folder; ``move`` (``shutil.move``) relocates the file, so the
  episode exists exactly once and seeding cannot continue - which is why
  ``after_import_action="keep"`` is rejected next to it. Afterwards the torrent
  is stopped, removed or left alone according to ``after_import_action``;
  removal deletes the torrent *entry* only (``delete_files=False``).
- In Shoko Server mode the destination is the import root instead of the
  library, and AniFlow invents no name for anything: the file keeps the name
  the release gave it, and Shoko does the renaming. Where it ended up is
  answered later by services/shoko.py, not guessed at here.
- An episode is imported once: a byte-identical file at the destination counts
  as already imported, a different one is a conflict for the user to resolve.

The move is deterministic and built entirely from stored data (§14):

    source      = download_root / [download_folder] / episode.download_filename
    destination = media_root / anime.media_folder / episode.media_filename
                  (standalone mode)
                = import_root / episode.download_filename
                  (Shoko Server mode - one flat folder, Shoko scans it)

Nothing is re-derived from the anime title. When the download filename is not
known yet, it is resolved once from qBittorrent's content path and stored.

All functions are synchronous and run inside worker threads.
"""
import os
import errno
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.mode import AppMode, current_mode
from backend.app.integrations.torrent_client import TorrentClient, TorrentClientError, client_for
from backend.app.models.activity import AttentionKind
from backend.app.models.anime import Anime, Episode, EpisodeStatus, EpisodeType
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.services.app_settings import load_app_settings
from backend.app.services.attention import (
    describe_changes,
    log_activity,
    raise_attention,
    resolve_open,
)
from backend.app.services.downloads import batch_group, batch_is_placed, describe_download
from backend.app.services.anime import preferred_folder_name
from backend.app.services.library import (
    drop_franchise_twins,
    episode_number_of,
    season_number_of_file,
)
from backend.app.services.episode_state import (
    expected_media_filename,
    media_file_exists,
    record_import_file,
    record_download_filename,
    record_media_file,
    refresh_episode_status,
    set_error,
)
from backend.app.services.storage import (
    roots_for,
    StorageRoots,
    download_path_candidates,
    import_path,
    is_media_file,
    media_folder_path,
    media_path,
    pick_media_file,
    safe_relative,
    storage_roots,
    within_root,
)

logger = for_component("Import")

#: How many files one pass hands to Shoko Server. Shoko asks AniDB about each
#: file it is given, and AniDB bans an account that asks too fast - a ban that
#: stalls the whole library for hours and looks like an AniFlow fault. Handing
#: over a few per pass keeps a finished season trickling in instead.
HANDOVER_PER_PASS = 3

# Import blockers that may disappear later (the user fixes something) and are
# therefore retried on every pass.
_RETRYABLE_REASONS = {
    "media_folder_missing",
    "path_outside_root",
    "source_unknown",
    "source_missing",
    "no_media_found",
    "ambiguous_multi_video",
    "target_exists",
    # A folder that is not writable yet (build 231): the person fixes the permissions and the
    # next pass places the file, without anybody pressing anything.
    "library_not_writable",
    "library_write_failed",
    # Hardlinks (build 272): the person changes the mounts or the setting and the next pass places the file.
    "hardlink_cross_device",
    "hardlink_unsupported",
}


@dataclass
class ImportSummary:
    checked: int = 0
    imported: int = 0
    attention: int = 0
    waiting: int = 0
    # Named, so a log line says which episode moved rather than just "1".
    changes: list[str] = field(default_factory=list)


def summarize_import(summary: ImportSummary) -> dict[str, object]:
    return asdict(summary)


def _translate_remote_path(
    remote_path: str, remote_root: str | None, local_root: Path, category: str | None = None
) -> Path:
    """Map a path from the qBittorrent container view onto the local view.

    Two automatic corrections keep the simple "2 folders" config working:

    1. If the qB content path is under ``<download_root>/<category>/`` (e.g.
       ``/downloads/AniFlow/foo.mkv``) the category subdir is stripped so the
       file lands at ``/downloads/foo.mkv`` (AniFlow's view).
    2. If ``remote_root`` is given, that prefix is stripped instead.

    Both transforms are no-ops when they don't apply, so the helper still works
    when both containers mount the folder on the same absolute path.
    """
    if not remote_path:
        return local_root
    candidate = Path(remote_path)
    if remote_root:
        try:
            relative = candidate.relative_to(remote_root)
        except ValueError:
            return candidate
        return local_root / relative
    if category:
        prefix = local_root / category
        try:
            relative = candidate.relative_to(prefix)
        except ValueError:
            return candidate
        return local_root / relative
    return candidate


def _remote_path_candidates(remote_path: str, local_root: Path, category: str | None) -> list[Path]:
    """Local paths a reported content path could correspond to, best first.

    Both containers usually mount the download folder on the same absolute
    path, in which case the reported path is already correct. Some setups
    instead expose the client's ``<root>/<category>/`` as AniFlow's ``<root>/``,
    so the category-stripped form is offered as a second guess. The caller
    picks whichever actually exists.
    """
    candidates = [Path(remote_path)]
    stripped = _translate_remote_path(remote_path, None, local_root, category=category)
    if stripped not in candidates:
        candidates.append(stripped)
    return candidates


def _resolve_client(settings, env, client: TorrentClient | None):
    if client is not None:
        return client, False
    if settings.qb_url:
        return client_for(
            settings.torrent_client,
            settings.qb_url,
            settings.qb_username,
            settings.qb_password,
            timeout=env.external_request_timeout_seconds,
        ), True
    return None, False


def _live_torrent(client, category: str, info_hash: str | None):
    if client is None or not info_hash:
        return None
    try:
        live = {torrent.info_hash: torrent for torrent in client.find_torrents(category=category)}
    except TorrentClientError as exc:
        logger.info("could not list torrents: %s", exc)
        return None
    return live.get(info_hash)


def _resolve_source(
    episode: Episode,
    torrent,
    roots: StorageRoots,
    category: str,
) -> tuple[Path | None, str | None]:
    """Find the file this episode was downloaded to.

    Preference order:

    1. the stored name, probed against the stored folder, the download root
       and the category subfolder - the deterministic path from §14;
    2. qBittorrent's content path, which is also where the authoritative
       filename and folder come from the first time.

    Layouts are *probed*, never assumed: whether AniFlow sees the client's
    files at ``<download_root>/<category>/…`` or at ``<download_root>/…``
    depends on how the two containers are mounted, and guessing wrong used to
    fail the import with ``source_missing``.

    Returns ``(path, reason)`` with exactly one of the two set.
    """
    for candidate in download_path_candidates(
        roots,
        episode.download_filename,
        download_folder=episode.download_folder,
        category=category,
    ):
        if candidate.is_file():
            return candidate, None

    content_path = getattr(torrent, "content_path", None) if torrent else None
    if not content_path:
        return None, "source_missing" if episode.download_filename else "source_unknown"

    escaped = False
    for translated in _remote_path_candidates(str(content_path), roots.download_root, category):
        if safe_relative(roots.download_root, translated) is None:
            escaped = True
            continue
        if translated.is_file():
            if not is_media_file(translated):
                return None, "no_media_found"
            return translated, None
        if translated.is_dir():
            picked, reason = pick_media_file(translated)
            return (picked, None) if picked else (None, reason or "no_media_found")
    return None, "path_outside_root" if escaped else "source_missing"


def _resolve_batch_source(
    row: TorrentDownload,
    torrent,
    roots: StorageRoots,
    category: str,
) -> tuple[Path | None, str | None]:
    """Find one file of a batch torrent: the one this row's episode is (build 213).

    ``_resolve_source`` expects one video per torrent and refuses a folder with several
    (``ambiguous_multi_video``). A batch row names its file - the path inside the torrent, starting with the
    torrent's own folder - so the answer is exact: that path, under wherever the client's content path points.
    Probed, never assumed: the client may or may not have put the torrent in a folder of its own.
    """
    content_path = getattr(torrent, "content_path", None) if torrent else None
    if not content_path or not row.batch_file:
        return None, "source_unknown"
    inner = row.batch_file.replace("\\", "/")
    parts = [part for part in inner.split("/") if part]
    escaped = False
    for translated in _remote_path_candidates(str(content_path), roots.download_root, category):
        # The torrent's folder is the content path, so the file is beside it by its full inner path, or - if
        # the client was told not to make a folder - directly inside the content path without its first part.
        for candidate in (translated.parent.joinpath(*parts), translated.joinpath(*parts[1:])):
            if safe_relative(roots.download_root, candidate) is None:
                escaped = True
                continue
            if candidate.is_file():
                return (candidate, None) if is_media_file(candidate) else (None, "no_media_found")
    return None, "path_outside_root" if escaped else "source_missing"


def _block(
    session: Session,
    row: TorrentDownload,
    episode: Episode,
    reason: str,
    summary: ImportSummary,
    previous_reason: str | None = None,
) -> int:
    """Record an import blocker on both the download and its episode.

    Returns 1 when this is a *new* blocker (or a changed reason) and 0 when the
    same blocker is merely being retried, so callers can avoid logging an
    Activity entry on every pass.
    """
    row.status = DownloadStatus.ATTENTION
    row.attention_reason = reason
    set_error(episode, reason)
    raise_attention(
        session,
        kind=AttentionKind.IMPORT_BLOCKED,
        anime_id=row.anime_id,
        download_id=row.id,
        detail=reason,
    )
    summary.attention += 1
    if previous_reason == reason:
        # The same blocker, still there. Counted, but not reported again: the
        # import pass runs every thirty seconds, and one unresolved blocker
        # would otherwise write the same line to the log for ever.
        return 0
    summary.changes.append(f"{describe_download(row)}: blocked ({reason})")
    # And into the history, once per new or changed reason (build 125).
    #
    # This used to reach Activity through the pass-level IMPORT_PASSED entry,
    # which is gone. The Attention record beside it says the import *is*
    # blocked; it says nothing once somebody resolves it, so without this the
    # fact that it ever happened would leave no trace. The dedup is the same
    # one this function already computes - a retried blocker returns above.
    from backend.app.services.attention import log_activity

    log_activity(
        session,
        kind="EPISODE_BLOCKED",
        message=f"Episode {episode.number} finished downloading but could not be placed: {reason}",
        details={"reason": reason, "episode": episode.number},
        anime_id=row.anime_id,
        episode_id=episode.id,
    )
    return 1


def _recorded_file_is_gone(episode: Episode, roots: StorageRoots, *, shoko: bool) -> bool:
    """Whether the file the episode says it has is really not there any more.

    Without this, a re-download of a deleted episode could never be imported
    (reported after build 205: "target_exists, but it does not"). The episode
    still carries the name of the file it once had - which is how it can be
    downloaded again - and the check below went by timestamps alone: a download
    newer than the recorded copy was "a second file for an episode already in
    the library", and was blocked as a duplicate of a file that no longer exists.

    A file Shoko files under its own name is not looked for here: in hand-over
    mode the recorded name is Shoko's to change, so absence proves nothing. A
    mount that cannot be read says nothing either - the old behaviour stands.
    """
    if shoko and not places_directly(episode.anime):
        return False
    try:
        return media_file_exists(episode, roots=roots) is False
    except Exception:  # noqa: BLE001 - an unreadable mount must not turn into a re-import
        return False


def _arrived_after_the_library_copy(row: TorrentDownload, episode: Episode) -> bool:
    """Whether this download is a *second* file for an episode already placed.

    The distinction the import pass was missing (build 136). An episode that
    already has its media file normally has exactly one download row behind
    it - the one that placed the file - and there is nothing left to do for it.
    A row that appeared **after** the file reached the library is a different
    thing: another copy of an episode that is already in the library, which
    AniFlow will never import and which nothing was saying anything about.

    The timestamps answer it exactly, with no filesystem check and no guessing
    about which file is which. It also means copy mode is safe: a file left in
    the download folder there is deliberate, and the row that left it is older
    than the library copy it produced.

    Without ``media_imported_at`` the question cannot be answered - an episode
    filed before AniFlow recorded that - so the answer is no, and the pass
    keeps its old silence rather than inventing a duplicate.
    """
    if episode.media_imported_at is None or row.created_at is None:
        return False

    def utc(moment: datetime) -> datetime:
        # Everything stored is UTC; a value read back before the type
        # decorator has attached that is naive, and comparing the two raises.
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)

    return utc(row.created_at) > utc(episode.media_imported_at)


def places_directly(anime) -> bool:
    """Whether this anime's file goes straight into its library folder.

    Build 192 (0.8.5). Standalone always does. In Shoko Server mode the file is
    handed over through the import folder *unless* the anime's library is
    linked to one of Shoko's own folders: then that folder is one Shoko
    watches, so the file can be written where it belongs and Shoko recognises
    it there. The link is the user's statement that this is so - the
    Libraries page warns when Shoko says the folder is not watched.
    """
    library = getattr(anime, "library", None)
    return library is not None and library.shoko_folder_id is not None


def run_import(
    session_factory: sessionmaker[Session],
    *,
    client: TorrentClient | None = None,
    library_root=None,
    download_root_override=None,
    import_root_override=None,
    mode: AppMode | None = None,
    only_download_id: str | None = None,
) -> ImportSummary:
    """Place every completed download that has no media file yet.

    ``only_download_id`` restricts the pass to a single row (manual imports).
    """
    env = get_settings()
    summary = ImportSummary()
    owns_client = client is None
    effective_client = None

    session = session_factory()
    placed_for_shoko = False
    try:
        settings = load_app_settings(session)
        # Read from the real configuration, not from env: the mode is not
        # one of the runtime settings a caller may substitute, and tests that
        # need another pipeline pass mode= explicitly.
        active_mode = mode or current_mode()
        shoko = active_mode is AppMode.SHOKO_SERVER
        roots = storage_roots(
            download_root=download_root_override,
            media_root=library_root,
            import_root=import_root_override,
        )
        # Shoko queries AniDB once per file it is given, and AniDB bans hard.
        # Handing over a whole season at once is how a user ends up with a
        # temporary ban and a pipeline that looks broken, so the pass hands
        # over a few files and comes back on the next tick.
        budget = HANDOVER_PER_PASS if shoko else None
        effective_client, owns = _resolve_client(settings, env, client)
        owns_client = owns

        statement = select(TorrentDownload).where(
            TorrentDownload.status.in_([DownloadStatus.COMPLETED, DownloadStatus.ATTENTION])
        )
        if only_download_id:
            statement = statement.where(TorrentDownload.id == only_download_id)
        rows = list(session.scalars(statement.order_by(TorrentDownload.created_at)))
        # Activity records events, not heartbeats: a blocker that is simply
        # retried every pass must not produce a new entry each time.
        newly_blocked = 0

        for row in rows:
            episode = row.episode
            if episode is None:
                continue
            # Retry only blockers that can clear; a permanent one stays put.
            if row.status is DownloadStatus.ATTENTION and row.attention_reason not in _RETRYABLE_REASONS:
                continue
            if (
                episode.media_filename
                and not only_download_id
                and not _recorded_file_is_gone(episode, roots, shoko=shoko)
            ):
                # Already placed - by this row, normally, and then there is
                # nothing to do. A row that arrived *after* the library copy
                # is another matter: it is a second file for an episode that
                # is already in the library, and skipping it silently is what
                # left three of them sitting in the download folder with no
                # record anywhere (build 136).
                #
                # Reported as the duplicate it is, which is a record that
                # already exists and already offers the two ways out: delete
                # the download, or replace the library file.
                if _arrived_after_the_library_copy(row, episode):
                    summary.checked += 1
                    newly_blocked += _block(
                        session,
                        row,
                        episode,
                        "target_exists",
                        summary,
                        row.attention_reason,
                    )
                continue  # already placed
            summary.checked += 1

            anime = episode.anime
            anime_roots = roots_for(anime, roots)
            previous_reason = row.attention_reason
            dest_dir = None
            # Shoko mode hands over through the import folder, except for an
            # anime whose library is linked to a Shoko folder (places_directly).
            hand_over = shoko and not places_directly(anime)
            if not hand_over:
                # In Shoko Server mode there is no folder yet, by design: Shoko
                # creates it when it files the first episode. A directly placed
                # one is named by AniFlow, like in standalone mode.
                if shoko and not anime.media_folder:
                    anime.media_folder = anime.local_name
                if not anime.media_folder:
                    newly_blocked += _block(session, row, episode, "media_folder_missing", summary, previous_reason)
                    continue
                dest_dir = media_folder_path(anime_roots, anime.media_folder)
                if dest_dir is None:
                    newly_blocked += _block(session, row, episode, "path_outside_root", summary, previous_reason)
                    continue

            if shoko and episode.import_filename:
                # Already with Shoko. Checked before the source is resolved,
                # because the downloaded file is legitimately gone by now -
                # moved out, or its torrent removed - and looking for it would
                # report a missing source for an episode that is simply
                # waiting.
                summary.waiting += 1
                continue

            # A further episode of a batch has no hash: the torrent is its owner's.
            torrent_hash = row.info_hash
            if torrent_hash is None and row.batch_download_id:
                batch_owner = session.get(TorrentDownload, row.batch_download_id)
                torrent_hash = batch_owner.info_hash if batch_owner is not None else None
            torrent = _live_torrent(effective_client, settings.qb_category, torrent_hash)
            if row.batch_file:
                source, reason = _resolve_batch_source(row, torrent, roots, settings.qb_category)
            else:
                source, reason = _resolve_source(episode, torrent, roots, settings.qb_category)
            if source is None:
                newly_blocked += _block(session, row, episode, reason or "source_unknown", summary, previous_reason)
                continue
            # First time through, this is where the guessed name is replaced
            # by the real one the client wrote - and where the actual folder is
            # recorded, computed relative to the download root so it is exact.
            relative = safe_relative(roots.download_root, source)
            parent = str(relative.parent) if relative is not None else "."
            record_download_filename(
                episode, source.name, folder=None if parent in (".", "") else parent
            )

            if hand_over:
                if budget is not None and budget <= 0:
                    summary.waiting += 1  # deliberately left for the next pass
                    continue
                reason = _hand_over(
                    session,
                    row,
                    episode,
                    source,
                    roots,
                    settings.file_operation,
                    settings,
                    client=effective_client,
                )
                if reason is not None:
                    newly_blocked += _block(session, row, episode, reason, summary, previous_reason)
                    continue
                if budget is not None:
                    budget -= 1
                resolve_open(session, kind=AttentionKind.IMPORT_BLOCKED, download_id=row.id)
                row.attention_reason = None
                row.status = DownloadStatus.COMPLETED
                summary.imported += 1
                summary.changes.append(
                    f"{describe_download(row)}: handed to Shoko Server as {episode.import_filename}"
                )
                _settle_superseded(session, episode, summary)
                _settle_after_import(session, effective_client, row, settings.after_import_action)
                continue

            media_filename = episode.media_filename or expected_media_filename(source.name)
            target = media_path(anime_roots, anime.media_folder, media_filename)
            if target is None:
                newly_blocked += _block(session, row, episode, "path_outside_root", summary, previous_reason)
                continue

            if target.exists():
                if target.stat().st_size == source.stat().st_size:
                    # Byte-identical: this episode is already imported.
                    record_media_file(
                        episode,
                        filename=target.name,
                        size_bytes=target.stat().st_size,
                        version=row.version,
                    )
                    resolve_open(session, kind=AttentionKind.IMPORT_BLOCKED, download_id=row.id)
                    row.attention_reason = None
                    row.status = DownloadStatus.COMPLETED
                    summary.imported += 1
                    summary.changes.append(f"{describe_download(row)}: already in the library")
                    _acquired(session, anime, episode, target.name, already=True)
                    _settle_superseded(session, episode, summary)
                    continue
                newly_blocked += _block(session, row, episode, "target_exists", summary, previous_reason)
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                _place_file(source, target, settings.file_operation)
            except OSError as exc:
                # One folder AniFlow cannot write to used to fail the whole pass: a PermissionError
                # escaped this loop, rolled back every other episode and left a traceback that only the
                # container log shows (build 231). It is this download's blocker now, with a reason on
                # In Progress, retried on every pass.
                reason = _placing_failed(source, target, exc, settings.file_operation)
                newly_blocked += _block(session, row, episode, reason, summary, previous_reason)
                continue
            _keep_previous_version(session, anime, episode, roots, target.name)

            record_media_file(
                episode,
                filename=target.name,
                size_bytes=target.stat().st_size,
                version=row.version,
            )
            resolve_open(session, kind=AttentionKind.IMPORT_BLOCKED, download_id=row.id)
            row.attention_reason = None
            row.status = DownloadStatus.COMPLETED
            summary.imported += 1
            summary.changes.append(f"{describe_download(row)}: imported as {target.name}")
            _acquired(session, anime, episode, target.name)
            if shoko:
                placed_for_shoko = True

            _settle_superseded(session, episode, summary)
            _settle_after_import(session, effective_client, row, settings.after_import_action)

        # Files written straight into a Shoko-watched library folder (build
        # 192): Shoko would find them in its own time, so it is asked once.
        if placed_for_shoko:
            ask_shoko_to_look()

        # No pass-level entry any more (build 125): each episode that
        # reached the library wrote its own, above. The pass's counters are
        # still returned to the caller and shown on the Downloads status box.
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if owns_client and effective_client is not None:
            effective_client.close()
    return summary


def ask_shoko_to_look() -> None:
    """Have Shoko run its import now. Best effort: it also watches on its own."""
    from backend.app.integrations.shoko.client import ShokoError, client_from_settings

    try:
        client_from_settings().run_import()
    except ShokoError as exc:
        logger.info("could not ask Shoko to look at the new files: %s", exc)


#: ``link()`` answers these when the filesystem cannot make a hardlink at all (as opposed to "not allowed here").
_NO_HARDLINKS = tuple(
    code for code in (
        getattr(errno, "EPERM", None), getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOTSUP", None), getattr(errno, "ENOSYS", None), getattr(errno, "EMLINK", None),
    ) if code is not None
)


def hardlink_reason(exc: OSError) -> str | None:
    """Why a hardlink could not be made, as an import reason - None when the cause is an ordinary write failure.

    Two answers a person can act on: ``EXDEV`` (two mounts or two filesystems, which is not "the same disk" - the
    rule for a link is the mount) and a filesystem that does not do links. Anything else - a read-only folder, a full
    disk - is told the way a failed copy is.
    """
    if exc.errno == errno.EXDEV:
        return "hardlink_cross_device"
    if exc.errno in _NO_HARDLINKS:
        return "hardlink_unsupported"
    return None


def _placing_failed(source: Path, target: Path, exc: OSError, operation: str = "copy") -> str:
    """Why a file could not be put in the library, as an import reason; and a half-written copy removed.

    ``library_not_writable`` is the one a person can fix (the folder belongs to somebody else, or the share is
    read-only); anything else - a full disk, a share that went away - is ``library_write_failed``, and the log
    has the exact error either way.
    """
    # Only ever a file this pass was about to create: it did not exist a moment ago, and a copy that stopped
    # halfway is not an episode. Never the source, which is still there when a copy failed.
    try:
        if source.exists() and target.exists():
            target.unlink()
    except OSError:
        pass
    logger.warning("could not place %s in %s (%s): %s", source.name, target.parent, operation, exc)
    if operation == "hardlink":
        reason = hardlink_reason(exc)
        if reason is not None:
            return reason
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "library_not_writable"
    return "library_write_failed"


def _place_file(source: Path, target: Path, operation: str) -> None:
    """Put the episode in the library, by copy (default), by move or by hardlink.

    Copy keeps the download where it is so the torrent can keep seeding; move
    relocates the file, which is why "leave it seeding" is rejected alongside
    it (see services/app_settings.validate_combination); a hardlink does what
    a copy does for seeding without the second copy. In every case the media
    itself is never destroyed.
    """
    if operation == "move":
        shutil.move(str(source), str(target))
        logger.info("imported %s -> %s (moved out of the download folder)", source, target)
    elif operation == "hardlink":
        # One file's data under two names: the library's own (renamed) name and the download's original one, so
        # the torrent keeps seeding at no extra space. Never a silent copy when the link cannot be made - a copy
        # is the disk use the person chose a hardlink to avoid, so the import stops with the reason instead.
        os.link(str(source), str(target))
        logger.info("imported %s -> %s (hardlink; the download keeps its name and can keep seeding)", source, target)
    else:
        shutil.copy2(str(source), str(target))
        logger.info("imported %s -> %s (original kept in the download folder)", source, target)



def _acquired(session, anime, episode, filename: str, *, already: bool = False) -> None:
    """One entry per episode that reached the library (build 125).

    The one meaningful event a successful acquisition makes. It replaced a
    per-*pass* ``IMPORT_PASSED`` that said "4 imported, 0 blocked" - a number
    nobody could act on, for a pass nobody asked about.

    ``already`` covers the byte-identical case: the file was in place before
    AniFlow got there. Worth recording as its own wording, because "acquired"
    for something that was already present reads as a fetch that did not
    happen.
    """
    from backend.app.services.attention import log_activity

    # No anime name in the message (build 127): the page prints `anime_name`
    # on its own line underneath, so a message that starts with it says the
    # same thing twice and leaves no room for what happened.
    log_activity(
        session,
        kind="EPISODE_ACQUIRED",
        message=(
            f"Episode {episode.number} was already in the library as {filename}"
            if already
            else f"Episode {episode.number} is now in the library as {filename}"
        ),
        details={"episode": episode.number, "filename": filename},
        anime_id=anime.id if anime is not None else None,
        episode_id=episode.id,
    )

def _keep_previous_version(session: Session, anime, episode: Episode, roots, new_name: str) -> None:
    """Write down the file a newer one has just been placed next to (build 189).

    A v2 is imported under its own name, so the v1 it improves on stays on disk
    - and used to be forgotten the moment the episode row pointed at the v2.
    Both copies are in the library either way; this makes sure something
    remembers the older one, so the duplicate can be shown and the user can
    choose which to delete. Nothing is deleted here.
    """
    old_name = episode.media_filename
    if not old_name or old_name == new_name:
        return
    try:
        old = media_path(roots_for(anime, roots), anime.media_folder, old_name)
        if old is None or not old.exists():
            return
        from backend.app.services.superseded import remember

        remember(session, anime, episode, old, kind="previous_version")
    except Exception as exc:  # noqa: BLE001 - never fail an import over a note
        logger.warning("could not write down the previous version %s: %s", old_name, exc)


def _settle_superseded(session: Session, episode: Episode, summary) -> None:
    """Delete the copy this import replaced, if it replaced one (build 116).

    A swap in ``when_replaced`` mode leaves the old file on disk and writes
    down what it was, precisely so the episode keeps working while the new
    release downloads. This is the moment that note was waiting for: the
    replacement is in the library, so the copy it replaced can go.

    Almost every import replaces nothing, and for those this is one indexed
    lookup that finds nothing. Best effort either way - an old file that
    cannot be deleted must not fail an import that has already happened, and
    its row keeps the reason.
    """
    try:
        from backend.app.services.superseded import settle, waiting_for

        removed = settle(session, episode)
        left = [row for row in waiting_for(session, episode.id) if row.error]
    except Exception as exc:  # noqa: BLE001 - never fail a finished import
        logger.warning("the replaced copy could not be dealt with: %s", exc)
        return
    if removed:
        summary.changes.append(
            f"deleted {removed} replaced file(s) now that the new release is in"
        )
        # AGENTS.md: never silently delete local media. The user chose this
        # mode and the panel said what it would do, which makes the deletion
        # asked for - but it happens days later and while nobody is looking,
        # so it goes in the log where a deletion can be found afterwards.
        log_activity(
            session,
            kind="LIBRARY_SUPERSEDED",
            message=(
                f"Deleted {removed} replaced file(s) for episode"
                f" {episode.number} now that the new release has been imported"
            ),
            details={"removed": removed, "episode_id": episode.id},
            anime_id=episode.anime_id,
            episode_id=episode.id,
        )
    for row in left:
        # A file that could not be deleted is a copy of a release the user
        # rejected, sitting in the library next to its replacement. Both
        # Shoko and Jellyfin will index it. Somebody has to be told, and a
        # warning in the log is not being told - see AGENTS.md.
        log_activity(
            session,
            kind="LIBRARY_SUPERSEDED_LEFT",
            message=(
                f"{row.filename} was replaced but could not be deleted:"
                f" {row.error}"
            ),
            details={"path": row.path, "episode_id": episode.id},
            anime_id=row.anime_id,
            episode_id=episode.id,
        )


def _settle_after_import(session: Session, client, row: TorrentDownload, action: str) -> None:
    """Settle the torrent once everything chosen from it has been placed (build 213).

    An ordinary torrent is one episode, so this is ``_settle_torrent``. A batch serves several: pausing or
    removing it when the first lands would cut off the rest, so nothing happens until the last wanted
    episode has its file - and then it is the owner's torrent that is settled, once.
    """
    group = batch_group(session, row)
    if len(group) == 1:
        _settle_torrent(client, row, action)
        return
    if not batch_is_placed(session, row):
        logger.info("torrent %s keeps running: more of its episodes are still on the way", group[0].info_hash)
        return
    _settle_torrent(client, group[0], action)


def _settle_torrent(client, row: TorrentDownload, action: str) -> None:
    """What happens to the torrent once its episode is in the library.

    ``pause`` stops seeding but keeps the torrent listed, which is the visible
    confirmation that AniFlow saw the download finish and acted on it.
    ``remove`` deletes the torrent entry only - never the files, per AGENTS.md.
    ``keep`` leaves it running.

    Failures here are logged, never fatal: the episode is already imported and
    that is the part that matters.
    """
    if client is None or not row.info_hash or action == "keep":
        return
    try:
        if action == "remove":
            client.delete_torrent(row.info_hash, delete_files=False)
            logger.info("removed torrent %s after import", row.info_hash)
        else:
            client.pause_torrent(row.info_hash)
            logger.info("paused torrent %s after import", row.info_hash)
        return
    except TorrentClientError as exc:
        # Reported after weeks of it: torrents that were neither stopped nor
        # removed, and not one line anywhere saying why. This was an info log
        # in a stream nobody reads (build 115).
        logger.warning(
            "torrent %s could not be %sd after import: %s", row.info_hash, action, exc
        )
        failure = str(exc)

    if action == "remove":
        # Removal refused. Stopping it is the lesser half of what was asked -
        # the episode is in the library, so seeding it is the only thing still
        # happening - and it is worth trying before giving up.
        try:
            client.pause_torrent(row.info_hash)
            logger.info("stopped torrent %s instead of removing it", row.info_hash)
            failure += " The torrent was stopped instead."
        except TorrentClientError as exc:
            logger.warning("torrent %s could not be stopped either: %s", row.info_hash, exc)
            failure += f" Stopping it failed as well: {exc}"

    _report_settle_failure(row, action, failure)


def _report_settle_failure(row: TorrentDownload, action: str, detail: str) -> None:
    """Say on the download row that its torrent was left behind (build 115).

    Not an attention record: the episode imported perfectly well and nothing
    about the library needs a decision. What is wrong is in the client, and
    the place it belongs is the download it belongs to - where the Downloads
    page already shows what each row is doing.
    """
    row.error = (
        f"The episode imported, but the torrent could not be"
        f" {'removed' if action == 'remove' else 'stopped'}. {detail}"
    )[:500]


def _hand_over(
    session: Session,
    row: TorrentDownload,
    episode: Episode,
    source: Path,
    roots: StorageRoots,
    operation: str,
    settings=None,
    client=None,
) -> str | None:
    """Put a finished download in the import folder for Shoko Server.

    The file keeps its release name. Renaming is Shoko's job, and a name
    AniFlow invented here would only have to be un-invented on the other side.

    **A moved file settles its torrent here** (build 132), rather than waiting
    for the file to reach the library. Put plainly by the report that led to
    it: *"The torrent could be removed when it lands in the import field, or
    when it leaves the download folder, no need to involve shoko or rely on
    the target folder, because after the file leaves the download folder the
    torrent can not work anyway."*

    Exactly so, and the timing matters twice over. A moved file kills its
    torrent the instant it moves, so everything after that is a broken torrent
    sitting in the client - for however long Shoko takes, and for ever if
    Shoko never files it. Waiting also made the action depend on another
    program succeeding, which it never needed to.

    Copying is the other half of the same reasoning, and the reason this is
    conditional: the original stays in the download folder, so the torrent
    still works, and ending it at hand-over would cut seeding short. That case
    still settles when the episode reaches the library - see
    services/shoko.py.

    Returns a blocker reason, or None on success.
    """
    if roots.import_root is None:
        return "import_root_missing"
    # A folder per anime rather than one flat pile: Shoko reads subfolders in a
    # drop source, and when it fails to identify something the folder it is
    # sitting in says what it was meant to be. Set lazily so an anime added
    # before this gets one on its first hand-over.
    anime = episode.anime
    if not anime.import_folder:
        # AniFlow names this one, so the title preference applies.
        anime.import_folder = preferred_folder_name(anime, settings)
    target = import_path(roots, anime.import_folder, source.name)
    if target is None or not within_root(roots.import_root, target):
        return "path_outside_root"
    if target.exists():
        # Already handed over on an earlier pass - the record is what matters,
        # not a second copy of the file.
        if episode.import_filename == target.name:
            return None
        return "target_exists"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _place_file(source, target, operation)
    except OSError as exc:
        # A link that cannot be made is this download's blocker, with a reason the person can act on - it must not
        # escape the pass and take every other episode with it (the same lesson as build 231).
        return _placing_failed(source, target, exc, operation)
    stat = target.stat()
    record_import_file(
        episode,
        filename=target.name,
        # The inode is how this exact file is recognised once Shoko has
        # renamed it and moved it somewhere AniFlow did not choose.
        inode=getattr(stat, "st_ino", None) or None,
        size_bytes=stat.st_size,
    )
    logger.info("handed %s to Shoko Server (import folder)", target.name)
    if operation == "move" and settings is not None:
        # The file has left the download folder, so the torrent cannot serve
        # anything and there is nothing to wait for. `move` + `keep` is
        # rejected by validate_combination for the same reason, so the action
        # here is always pause or remove.
        _settle_after_import(session, client, row, settings.after_import_action)
    return None


def inspect_download(
    session: Session,
    *,
    download_id: str,
    client=None,
    library_root=None,
    download_root_override=None,
    mode: AppMode | None = None,
) -> dict:
    """Read-only diagnostics for one download.

    Shows where qBittorrent says the file is, where AniFlow would put it,
    whether the target already exists, and what an import would do - without
    mutating anything. The resolution mirrors ``run_import`` exactly so the
    preview cannot disagree with the real pass.
    """
    env = get_settings()
    settings = load_app_settings(session)
    active_mode = mode or current_mode()
    roots = storage_roots(download_root=download_root_override, media_root=library_root)
    # The library root the anime would use is worked out below, once it is loaded.

    def flags(path):
        exists = os.path.isdir(path)
        return {"exists": exists, "writable": exists and os.access(path, os.W_OK)}

    result: dict[str, object] = {
        "download_id": download_id,
        "library_root": {"path": str(roots.media_root), **flags(roots.media_root)},
        "download_root": {"path": str(roots.download_root), **flags(roots.download_root)},
        # Kept (always null) because the frontend's DownloadInspect type still
        # declares it; qBittorrent's own view is auto-detected now.
        "remote_root": None,
        "mode": active_mode.value,
        "download_folder_entries": browse_download_folder(roots.download_root),
        "qb_content_path": None,
        "source_path": None,
        "source_entries": [],
        "dest_dir": None,
        "target_path": None,
        "target_exists": None,
        "would": "unknown",
        "warning": None,
    }

    row = session.get(TorrentDownload, download_id)
    if row is None:
        result["would"] = "row_missing"
        result["warning"] = "Download row not found"
        return result
    result["status"] = row.status.value
    result["attention_reason"] = row.attention_reason

    episode = row.episode
    anime = episode.anime if episode else None
    if episode is None or anime is None:
        result["would"] = "blocked"
        result["warning"] = "anime_missing"
        return result
    result["episode_number"] = episode.number
    result["download_filename"] = episode.download_filename

    if not anime.media_folder and active_mode is not AppMode.SHOKO_SERVER:
        result["would"] = "blocked"
        result["warning"] = "media_folder_missing"
        return result
    if anime.media_folder:
        # Unknown in Shoko Server mode until Shoko files the first episode,
        # which is why the check above skips it there.
        dest_dir = media_folder_path(roots_for(anime, roots), anime.media_folder)
        if dest_dir is None:
            result["would"] = "blocked"
            result["warning"] = "path_outside_root"
            return result
        result["dest_dir"] = str(dest_dir)

    effective_client, owns = _resolve_client(settings, env, client)
    torrent = None
    try:
        torrent = _live_torrent(effective_client, settings.qb_category, row.info_hash)
        if torrent is not None and getattr(torrent, "content_path", None):
            result["qb_content_path"] = torrent.content_path
    finally:
        if owns and effective_client is not None:
            effective_client.close()

    source, reason = _resolve_source(episode, torrent, roots, settings.qb_category)
    if source is not None:
        result["source_path"] = str(source)
        if source.parent != roots.download_root:
            try:
                result["source_entries"] = sorted(item.name for item in source.parent.iterdir())[:20]
            except OSError:
                pass

    if active_mode is AppMode.SHOKO_SERVER:
        # No media path to predict: the name and folder are Shoko's to choose.
        # What can be said is where the file will be put for it to find.
        target = (roots.import_root / source.name) if (roots.import_root and source) else None
        result["target_path"] = str(target) if target else None
        result["target_exists"] = target.exists() if target else None
        if roots.import_root is None:
            result["would"] = "blocked"
            result["warning"] = "import_root_missing"
        elif source is None:
            result["would"] = "blocked"
            result["warning"] = reason or "source_unknown"
        elif episode.import_filename:
            result["would"] = "already_handed_over"
        else:
            result["would"] = "hand_over_to_shoko"
        return result

    media_filename = episode.media_filename or expected_media_filename(
        episode.download_filename or (source.name if source else None)
    )
    target = media_path(roots_for(anime, roots), anime.media_folder, media_filename)
    result["target_path"] = str(target) if target else None
    if target is not None:
        result["target_exists"] = target.exists()

    if source is None:
        result["would"] = "blocked"
        result["warning"] = reason or "source_unknown"
        return result
    result["source_size"] = source.stat().st_size
    if target is None:
        result["would"] = "blocked"
        result["warning"] = "path_outside_root"
    elif target.exists() and target.stat().st_size == source.stat().st_size:
        result["would"] = "already_imported"
    elif target.exists():
        result["would"] = "blocked_target_exists"
        result["warning"] = "Target exists with different content. Delete it manually first."
    else:
        result["would"] = "ready_to_move"
    return result


def browse_download_folder(download_root=None) -> list[dict[str, object]]:
    """Shallow listing of the download root, for the Downloads debug panel."""
    roots = storage_roots(download_root=download_root)
    root = roots.download_root
    if not root.is_dir():
        return []
    out: list[dict[str, object]] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)[:50]
    except OSError:
        return []
    for child in children:
        entry: dict[str, object] = {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        if child.is_file():
            try:
                entry["size"] = child.stat().st_size
            except OSError:
                entry["size"] = None
        else:
            children_names: list[dict[str, object]] = []
            try:
                for sub in sorted(child.iterdir(), key=lambda path: path.name)[:20]:
                    sub_entry: dict[str, object] = {
                        "name": sub.name,
                        "type": "dir" if sub.is_dir() else "file",
                    }
                    if sub.is_file():
                        sub_entry["size"] = sub.stat().st_size
                    children_names.append(sub_entry)
            except OSError:
                pass  # unreadable folder - show it without content
            entry["children"] = children_names
        out.append(entry)
    return out


def move_download_single(
    session_factory: sessionmaker[Session],
    *,
    download_id: str,
    client: TorrentClient | None = None,
    library_root=None,
    download_root_override=None,
    mode: AppMode | None = None,
) -> dict[str, object]:
    """Run the guarded importer for exactly one download and report.

    The ``move``/``moved`` naming is kept for API compatibility with the
    frontend client; what actually happens to the file is ``file_operation`` 
    (see the module docstring).
    """
    summary = run_import(
        session_factory,
        client=client,
        library_root=library_root,
        download_root_override=download_root_override,
        mode=mode,
        only_download_id=download_id,
    )
    session = session_factory()
    try:
        row = session.get(TorrentDownload, download_id)
        if row is None:
            return {"outcome": "missing", "moved": False, "summary": summarize_import(summary)}
        episode = row.episode
        placed = bool(episode and episode.media_filename)
        blocked = row.attention_reason if row.status is DownloadStatus.ATTENTION else None
        outcome = (
            "moved"
            if placed
            else ("blocked:" + blocked if blocked else
                  ("waiting" if row.status is DownloadStatus.COMPLETED else "pending"))
        )
        return {
            "outcome": outcome,
            "moved": placed,
            "target": episode.media_filename if placed else None,
            "attention_reason": blocked,
            "summary": summarize_import(summary),
        }
    finally:
        session.close()


#: Media folder mtimes as of the last walk, keyed by anime id.
#:
#: A directory's mtime changes when a file is added or removed inside it, so
#: this turns "walk every library folder every 30 seconds" into one stat call
#: per anime. Process-local on purpose: after a restart every folder is walked
#: once, which is cheap and self-correcting - the alternative is a column that
#: can disagree with the disk.
_FOLDER_MTIMES: dict[str, float] = {}


def _adopt_new_files(
    session: Session, anime: Anime, roots: StorageRoots, counts: dict[str, int]
) -> list[str]:
    """Record library files AniFlow did not place, for episodes it knows.

    Returns a description of anything found that could not be placed, so the
    caller can report it rather than swallowing it.
    """
    folder = media_folder_path(roots_for(anime, roots), anime.media_folder or anime.local_name)
    if folder is None or not folder.is_dir():
        return []
    try:
        mtime = folder.stat().st_mtime
    except OSError:
        return []
    if _FOLDER_MTIMES.get(anime.id) == mtime:
        return []  # nothing has been added or removed since the last walk

    episodes = {
        episode.number: episode
        for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id))
    }
    # The copies a swap is deliberately keeping until their replacements
    # arrive (build 116). They look exactly like a file somebody added by
    # hand - in the folder, named with the episode number, against an episode
    # with no file - so without this the next reconcile would adopt the old
    # release back and the swap would quietly undo itself.
    from backend.app.services.superseded import waiting_paths

    superseded_paths = waiting_paths(session, anime.id)
    known_count = anime.episode_count or (max(episodes) if episodes else 0)
    out_of_range: list[int] = []
    try:
        # Recursive: a folder Shoko named may nest a season inside it, and a
        # file one level down is still this anime's episode.
        entries = sorted(folder.rglob("*"), key=lambda path: path.name)
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file() or not is_media_file(entry):
            continue
        if str(entry) in superseded_paths:
            continue  # being replaced, not newly added
        # Relative to the anime's folder, so `media_path` can rebuild it: for
        # a file sitting directly in the folder this is just its name, and for
        # one in a season subfolder it is "Season 1/name.mkv".
        recorded = entry.relative_to(folder).as_posix()
        number = season_number_of_file(anime, episode_number_of(entry.name))
        if number is None:
            continue
        episode = episodes.get(number)
        if episode is None:
            if number > known_count:
                # Could be a batch, a movie, or a typo in a filename. Reported,
                # never acted on: creating rows from a filename on a timer is
                # how one bad name becomes fifty phantom episodes.
                out_of_range.append(number)
                continue
            episode = Episode(
                anime_id=anime.id, number=number, episode_type=EpisodeType.REGULAR,
                status=EpisodeStatus.UNAVAILABLE,
            )
            session.add(episode)
            episodes[number] = episode
        if episode.media_filename == recorded:
            continue  # already known
        record_media_file(episode, filename=recorded, size_bytes=_size_of(entry))
        refresh_episode_status(episode, roots=roots, check_disk=True)
        counts["adopted"] += 1
        logger.info(
            "adopted %s for %s episode %s (added outside AniFlow)",
            recorded, anime.display_name, number,
        )

    _FOLDER_MTIMES[anime.id] = mtime
    if out_of_range:
        return [
            f"{anime.display_name}: file(s) for episode(s) "
            f"{', '.join(str(number) for number in sorted(set(out_of_range)))} "
            f"are above the known episode count ({known_count}) and were left alone"
        ]
    return []


def _size_of(path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def reconcile_media(
    session_factory: sessionmaker[Session],
    *,
    library_root=None,
) -> dict[str, int]:
    """Reconcile the library on disk with what the database believes.

    Both directions:

    * an episode whose file is gone becomes MISSING, so it can be offered for
      download again - this is what catches a deletion made outside AniFlow;
    * a file the user added themselves is adopted, so the episode reads
      COMPLETE without anyone pressing a button.

    The second half only walks folders whose mtime changed since the last pass.
    """
    roots = storage_roots(media_root=library_root)
    session = session_factory()
    counts = {"checked": 0, "missing": 0, "restored": 0, "adopted": 0, "forgotten": 0}
    try:
        # Before the status walk, so a leftover row never gets to be reported
        # as a file that went missing (build 238).
        for anime in session.scalars(select(Anime).where(Anime.episode_offset > 0)):
            drop_franchise_twins(session, anime, roots)
        session.flush()
        episodes = list(
            session.scalars(select(Episode).where(Episode.media_filename.is_not(None)))
        )
        for episode in episodes:
            counts["checked"] += 1
            before = episode.status
            after = refresh_episode_status(episode, roots=roots, check_disk=True)
            if after is EpisodeStatus.MISSING and before is not EpisodeStatus.MISSING:
                counts["missing"] += 1
            elif before is EpisodeStatus.MISSING and after is EpisodeStatus.COMPLETE:
                counts["restored"] += 1

        notes: list[str] = []
        for anime in session.scalars(select(Anime)):
            notes += _adopt_new_files(session, anime, roots, counts)
        # And the notes for files that went a month ago (build 116). Kept
        # after the deletion so it can be looked up, not kept for ever.
        from backend.app.services.superseded import forget_old

        counts["forgotten"] = forget_old(session)
        session.commit()
        if counts["adopted"]:
            log_activity(
                session,
                kind="LIBRARY_ADOPTED",
                message=f"Adopted {counts['adopted']} file(s) added to the library outside AniFlow",
                details={"adopted": counts["adopted"]},
            )
            session.commit()
        for note in notes:
            log_activity(session, kind="LIBRARY_UNPLACED", message=note, details={})
        if notes:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return counts


__all__ = [
    "ImportSummary",
    "browse_download_folder",
    "inspect_download",
    "move_download_single",
    "reconcile_media",
    "run_import",
    "summarize_import",
]
