"""The torrents nobody settled, swept up on the hour (build 94).

Reported: "There are still sometimes torrents which are not stopped."

The after-import action - pause, remove, or leave it - is applied by the
import pass, at the moment the file is placed. Every step of that can be
missed once: the client is unreachable for the ten seconds that matters, the
import happens while qBittorrent is restarting, AniFlow itself restarts
between placing the file and settling the torrent, or Shoko takes the file
from the download folder and AniFlow's importer never runs at all. None of
those is a bug that can be fixed at the moment it happens, because the moment
has passed.

So this asks the question again from the other end, every hour: *are there
torrents in the client carrying AniFlow's tag that AniFlow considers
finished?* Any that are get the after-import action they should have had.

Two conditions, both required, because the cost of being wrong is deleting
somebody's download:

**AniFlow has to know the torrent.** Only torrents whose tag matches a
download row are touched. An untagged torrent, or one whose row is gone, is
somebody else's business - this sweep never deletes what it cannot account
for.

**The torrent has to be finished.** Its own progress must be complete *and*
the episode's file must be in the library. Either alone is not enough: a
torrent at 100% that has not been imported is still waiting for the import
pass, and an imported episode whose torrent is at 40% is a second copy
arriving.

``keep`` does nothing, which is what it means.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.logging import for_component
from backend.app.integrations.torrent_client import TorrentClientError
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.services.app_settings import load_app_settings

logger = for_component("Downloads")

#: Statuses whose torrent is not the sweep's business. A row still moving is
#: the sync pass's, and one already settled has nothing left to settle.
_NOT_OURS = (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)


@dataclass
class SweepSummary:
    """What the sweep found and did."""

    #: Torrents in the client carrying an AniFlow tag.
    tagged: int = 0
    #: Of those, the ones AniFlow could match to a download row.
    recognised: int = 0
    #: Finished, and dealt with as the settings say.
    removed: int = 0
    paused: int = 0
    #: Finished, and left alone because the setting says to.
    kept: int = 0
    #: Not finished yet, so not the sweep's business this hour.
    still_working: int = 0
    #: Could not be dealt with - the client refused. Reported, not retried.
    failed: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)


def sweep_settled_torrents(
    session_factory: sessionmaker[Session],
    *,
    client=None,
) -> SweepSummary:
    """Apply the after-import action to any torrent that never got it."""
    from backend.app.services.downloads import _client_from, _download_tag, describe_download

    summary = SweepSummary()
    session = session_factory()
    owns_client = client is None
    effective = None
    try:
        settings = load_app_settings(session)
        action = settings.after_import_action
        if action == "keep":
            # Nothing to do, and saying so beats connecting to find out.
            return summary
        try:
            effective = _client_from(settings, client)
        except Exception as exc:  # noqa: BLE001 - no client is a normal state
            logger.info("no torrent client for the sweep: %s", exc)
            return summary
        if effective is None:
            return summary

        # Every row that might still own a torrent, by its tag. Built once:
        # the alternative is a client lookup per row.
        rows = {
            _download_tag(row.id): row
            for row in session.scalars(
                select(TorrentDownload).where(TorrentDownload.status.not_in(_NOT_OURS))
            )
        }

        for torrent in _aniflow_torrents(effective, settings.qb_category):
            summary.tagged += 1
            row = _row_for(torrent, rows)
            if row is None:
                # Tagged but unaccounted for: a row that has been cleared, or
                # a tag from an older install. Never touched.
                continue
            summary.recognised += 1
            if not _is_finished(torrent, row):
                summary.still_working += 1
                continue
            _settle(effective, torrent, row, action, summary, describe_download)

        if summary.removed or summary.paused or summary.failed:
            logger.warning(
                "swept %s settled torrent(s) of %s tagged: %s removed, %s paused, %s failed",
                summary.removed + summary.paused,
                summary.tagged,
                summary.removed,
                summary.paused,
                len(summary.failed),
            )
        session.commit()
        return summary
    finally:
        session.close()
        if owns_client and effective is not None:
            effective.close()


def _aniflow_torrents(client, category: str) -> list:
    """Every torrent in the client that carries one of AniFlow's tags.

    Asked of the client by category first, because that is one request; the
    tag is then read off each torrent. A client that cannot list is reported
    by returning nothing rather than raising - the sweep is a safety net, and
    a safety net that throws is worse than one that waits an hour.
    """
    from backend.app.services.downloads import _TAG_PREFIX

    try:
        found = client.find_torrents(category=category)
    except TorrentClientError as exc:
        logger.info("the client would not list its torrents: %s", exc)
        return []
    return [
        torrent
        for torrent in found
        if any(tag.startswith(_TAG_PREFIX) for tag in _tags_of(torrent))
    ]


def _tags_of(torrent) -> list[str]:
    """The torrent's tags, however the client spelled them."""
    raw = getattr(torrent, "tags", None) or ""
    if isinstance(raw, str):
        return [tag.strip() for tag in raw.split(",") if tag.strip()]
    return [str(tag).strip() for tag in raw if str(tag).strip()]


def _row_for(torrent, rows: dict[str, TorrentDownload]) -> TorrentDownload | None:
    """The download row this torrent belongs to, by tag then by hash."""
    for tag in _tags_of(torrent):
        row = rows.get(tag)
        if row is not None:
            return row
    # A tag can be lost - the client's tag list is editable by hand - so the
    # hash is the fallback. Still only ever a row AniFlow already has.
    for row in rows.values():
        if row.info_hash and row.info_hash == getattr(torrent, "info_hash", None):
            return row
    return None


def _is_finished(torrent, row: TorrentDownload) -> bool:
    """Whether this torrent has nothing left to deliver.

    Both halves, as the module docstring says: the torrent is complete and the
    episode's file is in the library. A torrent at 100% that has not been
    imported is waiting for the import pass, not for this.
    """
    progress = float(getattr(torrent, "progress", 0.0) or 0.0)
    if progress < 0.999:
        return False
    episode = row.episode
    if episode is None or not episode.media_filename:
        return False
    # A batch torrent serves several episodes (build 213): it is finished when every one of them has its file,
    # not when the first does. An ordinary row is a group of one.
    from sqlalchemy.orm import object_session

    from backend.app.services.downloads import batch_is_placed

    session = object_session(row)
    return session is None or batch_is_placed(session, row)


def _settle(client, torrent, row, action: str, summary: SweepSummary, describe) -> None:
    """Do to this torrent what the import pass should have done."""
    from backend.app.services.downloads import _download_tag

    info_hash = getattr(torrent, "info_hash", None) or row.info_hash
    if not info_hash:
        return
    try:
        if action == "remove":
            # Never the files: they are the copy in the download folder, and
            # deleting them is a separate decision the user makes elsewhere.
            client.delete_torrent(info_hash, delete_files=False)
            summary.removed += 1
        else:
            client.pause_torrent(info_hash)
            summary.paused += 1
    except TorrentClientError as exc:
        summary.failed.append(f"{describe(row)}: {exc}")
        # A warning, not an info line (build 115). The sweep is the safety
        # net; a safety net that fails quietly every hour for weeks is the
        # thing that was reported, and the log was where it should have said
        # so. The row carries it too, so the Downloads page can show it.
        logger.warning("could not %s %s: %s", action, info_hash, exc)
        row.error = (
            f"The torrent could not be"
            f" {'removed' if action == 'remove' else 'stopped'}: {exc}"
        )[:500]
        return

    summary.changes.append(
        f"{describe(row)}: finished long ago, torrent"
        f" {'removed' if action == 'remove' else 'stopped'} by the hourly sweep"
    )
    if action == "remove":
        # The tag names a torrent that no longer exists. qBittorrent keeps it
        # in its global list otherwise.
        try:
            client.delete_tags([_download_tag(row.id)])
        except TorrentClientError:
            pass
    row.status = DownloadStatus.COMPLETED
    row.attention_reason = None


__all__ = ["SweepSummary", "sweep_settled_torrents"]
