"""Work out what actually happened to a download that stopped moving (build 79).

Two downloads sat on the Downloads page at 0%, both saying *"qBittorrent no
longer has this torrent"*. Neither statement was useful and one was wrong:

* one episode was already in the library, so the file that had been fetched
  could never be placed - the warning should have said *duplicate*;
* the other had finished downloading and was sitting in the download folder
  untouched, because the row had been marked ATTENTION and the importer only
  retries a short list of blockers - ``missing_in_client`` is not one of them.
  Nothing would ever have looked at it again.

The cause of both was the same narrow question. The sync pass asks "is this
torrent in the configured category right now?", and answers "no" with
"missing". But a torrent can be *in the client under another category*, and a
file can be *on disk with no torrent at all* - and in both cases the download
is fine and something else is stuck.

So this pass asks the wider questions, in the order that can settle them:

1. **Is the torrent in the client at all?** By hash, across every category,
   then by AniFlow's own marker tag. Found means adopt it and carry on - which
   is what fixes a row stuck at 0% because the category was changed.
2. **Is the file on disk?** The recorded name first, then the release name,
   because a row that never got a progress update never recorded a filename.
3. **Is this episode already in the library?** Then either the file on disk
   *is* the one in the library - a copy left behind - or it is a rival
   release, and the user gets a choice: throw the new one away, or keep it and
   let the old one go. Told apart by identifying the file rather than by
   guessing (build 89; see ``_is_the_library_file``).
4. **Otherwise** it really is missing, which is what the message says.

Everything here is a *report*, with one exception: a file waiting to be placed
for an anime with Automate on is placed, because that is what Automate means.
With Automate off it waits for a button, like everything else does.

Runs on the worker's slow tick and on demand from the Downloads page. Nothing
here deletes anything: the two destructive resolutions live behind buttons in
services/attention.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.integrations.qbittorrent.base import download_state
from backend.app.integrations.torrent_client import TorrentClientError
from backend.app.models.activity import AttentionKind
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.services.app_settings import load_app_settings
from backend.app.services.attention import log_activity, raise_attention, resolve_open
from backend.app.services.episode_state import refresh_episode_status
from backend.app.services.storage import download_path_candidates, storage_roots

logger = for_component("Reconcile")

#: The states worth re-examining. A row that is imported, skipped or removed
#: is finished business; everything else is either moving or stuck.
_UNSETTLED = (
    DownloadStatus.QUEUED,
    DownloadStatus.DOWNLOADING,
    DownloadStatus.COMPLETED,
    DownloadStatus.ATTENTION,
)


@dataclass
class ReconcileSummary:
    checked: int = 0
    #: Torrents found in the client after all - usually under another category.
    readopted: int = 0
    #: Files found on disk and handed back to the importer.
    requeued: int = 0
    #: Episodes the library already has as a *different* file, so the user
    #: has to choose which one to keep.
    duplicates: int = 0
    #: Downloads whose leftover file is the very file already in the library.
    #: Finished business, and reported as such rather than as a conflict.
    same_file: int = 0
    #: Files waiting for the user to decide (Automate is off).
    waiting: int = 0
    #: Genuinely gone: no torrent, no file.
    missing: int = 0
    #: Torrents settled late - the after-import action that never ran.
    settled: int = 0
    changes: list[str] = field(default_factory=list)


def summarize(summary: ReconcileSummary) -> dict[str, object]:
    return asdict(summary)


def reconcile_downloads(
    session_factory: sessionmaker[Session],
    *,
    client=None,
    only_download_id: str | None = None,
) -> ReconcileSummary:
    """Re-examine every download that is not finished business."""
    from backend.app.services.downloads import _client_from, _download_tag, describe_download

    summary = ReconcileSummary()
    session = session_factory()
    owns_client = client is None
    effective_client = None
    try:
        settings = load_app_settings(session)
        roots = storage_roots()
        try:
            effective_client = _client_from(settings, client)
        except Exception as exc:  # noqa: BLE001 - a client that cannot be reached
            logger.info("no torrent client for this pass: %s", exc)
            effective_client = None

        statement = select(TorrentDownload).where(TorrentDownload.status.in_(_UNSETTLED))
        if only_download_id:
            statement = statement.where(TorrentDownload.id == only_download_id)
        rows = list(session.scalars(statement.order_by(TorrentDownload.created_at)))

        for row in rows:
            episode = row.episode
            if episode is None:
                continue
            summary.checked += 1

            torrent = _find_anywhere(effective_client, row, _download_tag(row.id), settings.qb_category)
            if torrent is not None:
                _readopt(
                    session,
                    row,
                    torrent,
                    settings,
                    summary,
                    describe_download,
                    client=effective_client,
                )
                continue

            # No torrent. That is not an answer about the file.
            already_placed = bool(episode.media_filename)
            source = _find_file(roots, row, episode, settings.qb_category)

            if already_placed and source is not None:
                recognised = _is_the_library_file(episode, source)
                if recognised is not None:
                    _finish_leftover(
                        session, row, source, recognised, summary, describe_download
                    )
                else:
                    _report_duplicate(session, row, episode, summary, describe_download)
                continue
            if already_placed:
                # Imported, torrent gone, nothing left over: the normal end of
                # a download. Nothing to report; the row is simply finished.
                _finish(session, row, summary, describe_download)
                continue
            if source is not None:
                _place_or_ask(session, row, episode, summary, describe_download)
                continue

            _report_missing(session, row, summary, describe_download)

        if _worth_recording(summary):
            log_activity(
                session,
                kind="RECONCILED",
                message=_headline(summary),
                details=asdict(summary),
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if owns_client and effective_client is not None:
            effective_client.close()
    return summary


def _find_anywhere(client, row: TorrentDownload, tag: str, category: str):
    """The torrent, wherever it is in the client. None means it is not there.

    Deliberately wider than the sync pass: by hash across every category,
    because a torrent whose category was changed - by hand, or by a client
    upgrade - is present and working while the sync pass reports it missing.
    """
    if client is None:
        return None
    try:
        if row.info_hash:
            found = client.find_by_hash(row.info_hash)
            if found is not None:
                return found
        # No hash resolved yet: the marker tag is how a torrent added while
        # the client was busy is recognised later.
        tagged = client.find_torrents(category=category, tag=tag)
        return tagged[0] if tagged else None
    except TorrentClientError as exc:
        logger.info("could not ask the client about %s: %s", row.id, exc)
        return None


def _find_file(roots, row: TorrentDownload, episode, category: str):
    """The downloaded file, if it is on disk.

    Two names are tried, and the second is the one that matters here: a row
    that never received a progress update never recorded a filename, so the
    only name AniFlow has for it is the release it queued. That is also what
    the client names the file, which is why looking for it works.
    """
    candidates = list(
        download_path_candidates(
            roots,
            episode.download_filename,
            download_folder=episode.download_folder,
            category=category,
        )
    )
    candidates += download_path_candidates(roots, row.label, category=category)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            from backend.app.services.storage import pick_media_file

            picked, _ = pick_media_file(candidate)
            if picked is not None:
                return picked
    return None


def _readopt(session, row, torrent, settings, summary, describe, *, client=None) -> None:
    """The torrent was there all along. Pick the row back up."""
    category = settings.qb_category
    # Imported, and the torrent is still running. The after-import action -
    # pause or remove - is applied by the import pass, and it could not find
    # the torrent at the time, so it never ran: the user was left stopping
    # their own torrents by hand. It runs now.
    episode = row.episode
    if episode is not None and episode.media_filename and settings.after_import_action != "keep":
        from backend.app.services.importer import _settle_torrent

        _settle_torrent(client, row, settings.after_import_action)
        row.status = DownloadStatus.COMPLETED
        row.attention_reason = None
        row.missing_since = None
        resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)
        summary.settled += 1
        summary.changes.append(
            f"{describe(row)}: imported, and its torrent has now been"
            f" {'removed' if settings.after_import_action == 'remove' else 'stopped'}"
        )
        return

    row.missing_since = None
    row.qb_state = torrent.state
    row.progress = float(getattr(torrent, "progress", 0.0) or 0.0)
    new_status = DownloadStatus(download_state(torrent.state))
    if row.status is DownloadStatus.ATTENTION and row.attention_reason == "missing_in_client":
        row.attention_reason = None
        row.status = new_status
        resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)
        summary.readopted += 1
        where = getattr(torrent, "category", "") or "no category"
        summary.changes.append(
            f"{describe(row)}: found in the client again (category: {where})"
        )
        if where != category:
            logger.info(
                "%s is in the client under %r, not %r - that is why it looked missing",
                describe(row),
                where,
                category,
            )
    refresh_episode_status(row.episode)


def _is_the_library_file(episode, source) -> str | None:
    """How ``source`` was recognised as the file already in the library.

    Returns "crc32", "size" or None - None meaning it is a different file and
    therefore a real choice between two releases.

    The question matters because the two answers need opposite handling. A
    rival release is a judgement AniFlow cannot make. A copy of the file
    that is already imported is not a judgement at all, and warning about it
    asks the user to pick between a file and itself. Four of the six warnings
    open on the real deployment were this.

    Two ways a copy comes to be lying there. ``file_operation: copy`` leaves
    one by definition. The other is what actually happened on that
    deployment, which is set to ``move``: Shoko Server watches the download
    folder itself, so it had taken and filed the file before AniFlow's
    importer ever reached it - every one of those episodes had an empty
    ``import_filename`` and a full ``media_filename``.

    Identified rather than matched, in the order services/shoko.py identifies
    a file - and for the same reason. The inode is not asked for here: a copy
    has its own by definition, so it can only ever answer "different", which
    is the answer this must not get wrong.
    """
    from backend.app.services.shoko import _crc32

    library_name = episode.media_filename or ""
    wanted = _crc32(library_name)
    if wanted and _crc32(source.name) == wanted:
        # Release names carry a CRC32 and Shoko's renamer keeps it, so this is
        # a statement about the bytes, not about the name.
        return "crc32"

    if episode.media_size_bytes:
        try:
            if source.stat().st_size == episode.media_size_bytes:
                return "size"
        except OSError:
            return None
    return None


def _finish_leftover(session, row, source, recognised, summary, describe) -> None:
    """The file on disk is the file in the library. The download is done.

    The leftover is not deleted. It exists because the user asked for copies
    rather than moves, and this pass does not delete anything - what it can
    do is stop calling a copy a conflict.
    """
    row.status = DownloadStatus.COMPLETED
    row.attention_reason = None
    row.missing_since = None
    resolve_open(session, kind=AttentionKind.IMPORT_BLOCKED, download_id=row.id)
    resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)
    summary.same_file += 1
    summary.changes.append(
        f"{describe(row)}: already imported - the file left in the download"
        f" folder is the same one ({recognised})"
    )
    logger.info("%s: leftover copy recognised by %s, not a duplicate", describe(row), recognised)


def _report_duplicate(session, row, episode, summary, describe) -> None:
    """The library already has this episode, and the download is still here.

    Reported rather than resolved: which of the two files to keep is a
    judgement about quality and release group that AniFlow cannot make. The
    attention carries both paths and two buttons.
    """
    row.status = DownloadStatus.ATTENTION
    row.attention_reason = "target_exists"
    row.missing_since = None
    raise_attention(
        session,
        kind=AttentionKind.IMPORT_BLOCKED,
        anime_id=row.anime_id,
        download_id=row.id,
        detail="target_exists",
    )
    summary.duplicates += 1
    summary.changes.append(f"{describe(row)}: already in the library, the download is a duplicate")


def _place_or_ask(session, row, episode, summary, describe) -> None:
    """The file is here and the library has nothing. Move it, or ask.

    Automate on means "queue and place what you find", so placing it is what
    the switch was turned on for. Automate off means nothing happens without a
    button, and this is no exception - so it becomes an attention with the two
    buttons the user needs.
    """
    anime = episode.anime
    if anime is not None and anime.automation_enabled:
        # Handing it back to the importer rather than moving it here: that
        # pass owns every rule about folders, Shoko hand-over, versions and
        # the after-import action, and a second implementation would drift.
        row.status = DownloadStatus.COMPLETED
        row.attention_reason = None
        row.missing_since = None
        resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)
        resolve_open(session, kind=AttentionKind.IMPORT_BLOCKED, download_id=row.id)
        summary.requeued += 1
        summary.changes.append(f"{describe(row)}: downloaded and not placed - queued for import")
        refresh_episode_status(episode)
        return

    row.status = DownloadStatus.ATTENTION
    row.attention_reason = "ready_to_move"
    row.missing_since = None
    raise_attention(
        session,
        kind=AttentionKind.IMPORT_BLOCKED,
        anime_id=row.anime_id,
        download_id=row.id,
        detail="ready_to_move",
    )
    summary.waiting += 1
    summary.changes.append(f"{describe(row)}: downloaded and waiting for you to place it")


def _report_missing(session, row, summary, describe) -> None:
    """No torrent, no file. The one case the old message was right about."""
    if row.status is DownloadStatus.ATTENTION and row.attention_reason == "missing_in_client":
        summary.missing += 1
        return
    row.status = DownloadStatus.ATTENTION
    row.attention_reason = "missing_in_client"
    row.missing_since = row.missing_since or datetime.now(UTC)
    raise_attention(
        session,
        kind=AttentionKind.DOWNLOAD_MISSING,
        anime_id=row.anime_id,
        download_id=row.id,
        detail="missing_in_client",
    )
    summary.missing += 1
    summary.changes.append(f"{describe(row)}: no torrent and no file")
    refresh_episode_status(row.episode)


def _finish(session, row, summary, describe) -> None:
    """Imported, and the torrent has gone. Nothing is wrong."""
    if row.attention_reason or row.status is DownloadStatus.ATTENTION:
        row.attention_reason = None
        row.status = DownloadStatus.COMPLETED
        row.missing_since = None
        resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)
        resolve_open(session, kind=AttentionKind.IMPORT_BLOCKED, download_id=row.id)
        summary.settled += 1
        summary.changes.append(f"{describe(row)}: imported, and the torrent is gone - all done")


def _worth_recording(summary: ReconcileSummary) -> bool:
    """An idle pass writes nothing. Most passes are idle."""
    return bool(
        summary.readopted
        or summary.requeued
        or summary.duplicates
        or summary.same_file
        or summary.waiting
        or summary.settled
        or summary.changes
    )


def _headline(summary: ReconcileSummary) -> str:
    """What the half-hourly pass put right, in one line.

    *"Recheck: I do not understand this message."*  Fair: the tag said
    "recheck" and the message was a bare list of changes, so it read as though
    something had gone wrong rather than been fixed. The tag is "Put right"
    now, and the line says what was compared before listing what changed -
    this pass exists to find what happened while AniFlow was not looking (a
    file deleted by hand, a torrent removed in the client) and to correct the
    database to match.
    """
    from backend.app.services.attention import describe_changes

    changed = describe_changes(summary.changes, fallback="")
    if not changed:
        return f"Checked {summary.checked} download(s) against the disk - all correct"
    return f"Corrected after checking the disk: {changed}"


__all__ = ["ReconcileSummary", "reconcile_downloads", "summarize"]
