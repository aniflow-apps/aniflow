"""Torrent download inspection and control."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.mode import current_mode
from backend.app.services.app_settings import load_app_settings
from backend.app.db.session import SessionLocal, get_db
from backend.app.models.anime import Anime, Episode
from backend.app.models.download import TorrentDownload
from backend.app.models.rss import Release
from backend.app.schemas.download import (
    DownloadResponse,
    DownloadsStatusResponse,
    MediaFileResponse,
    QueueTriggerResponse,
)
from backend.app.services.downloads import remove_download
from backend.app.worker.runtime import worker_state

router = APIRouter()


@router.get("", response_model=list[DownloadResponse])
def list_downloads(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[DownloadResponse]:
    statement = (
        select(TorrentDownload, Anime.display_name, Release.raw_name, Episode.media_filename)
        .outerjoin(Anime, TorrentDownload.anime_id == Anime.id)
        .outerjoin(Release, TorrentDownload.release_id == Release.id)
        .outerjoin(Episode, TorrentDownload.episode_id == Episode.id)
        .order_by(TorrentDownload.created_at.desc())
        .limit(limit)
    )
    #: How far along each status is, so "the one that got furthest" is a
    #: defined thing rather than whichever row the database returned first.
    progress_of = {
        "COMPLETED": 6,
        "ATTENTION": 5,
        "DOWNLOADING": 4,
        "QUEUED": 3,
        "FAILED": 2,
        "SKIPPED": 1,
        "REMOVED": 0,
    }

    best: dict[tuple[str | None, int], DownloadResponse] = {}
    results: list[DownloadResponse] = []
    for download, anime_name, release_name, media_filename in db.execute(statement).all():
        payload = DownloadResponse.model_validate(download)
        payload.anime_name = anime_name
        payload.release_name = release_name
        # "Imported" is now a property of the episode: its media file is placed.
        payload.imported = bool(media_filename)

        # One row per episode and version (build 88). Two groups' copies of
        # one episode both reaching the client is normal - it is what a change
        # of release group produces - but the buttons on both rows do the same
        # thing to the same file, so a second row is a way to press it twice.
        key = (download.episode_id, download.version)
        if download.episode_id is None:
            results.append(payload)
            continue
        kept = best.get(key)
        if kept is None:
            best[key] = payload
            results.append(payload)
            continue
        kept.duplicates += 1
        # The response model declares status as a plain string, so Pydantic
        # has already turned the enum into one. Reading .value here is what
        # broke the whole Downloads page in build 88 - and only for somebody
        # who actually had two downloads of one episode, which is what this
        # code exists for and what no test had.
        if progress_of.get(payload.status, 0) > progress_of.get(kept.status, 0):
            # The newcomer got further, so it becomes the row on screen and
            # inherits the count of the ones it replaced.
            payload.duplicates = kept.duplicates
            results[results.index(kept)] = payload
            best[key] = payload
    return results


@router.post("/queue", response_model=QueueTriggerResponse)
def trigger_queueing() -> QueueTriggerResponse:
    """Ask the worker to queue all pending MATCHED releases on its next run."""
    started = worker_state.request_run("queue_downloads")
    return QueueTriggerResponse(started=started)


@router.post("/import", response_model=QueueTriggerResponse)
def trigger_import() -> QueueTriggerResponse:
    """Ask the worker to run an import pass on its next run."""
    started = worker_state.request_run("torrent_monitor")
    return QueueTriggerResponse(started=started)


@router.post("/sync", response_model=QueueTriggerResponse)
def trigger_sync() -> QueueTriggerResponse:
    """Force an immediate sync with qBittorrent: refresh progress/state for
    all tracked downloads so the Downloads overview matches the WebUI."""
    from backend.app.db.session import SessionLocal
    from backend.app.services.downloads import sync_download_states

    # Run synchronously so the next GET /api/downloads already shows live data
    try:
        sync_download_states(SessionLocal)
    except Exception as exc:  # noqa: BLE001 - surface as 503, never crash the handler
        raise HTTPException(status_code=503, detail=f"Sync failed: {exc}") from exc
    return QueueTriggerResponse(started=True)


@router.get("/{download_id}/inspect")
def inspect_download(download_id: str, db: Session = Depends(get_db)):
    """Read-only diagnostics: source path, destination, target conflicts."""
    from backend.app.services.importer import inspect_download

    return inspect_download(db, download_id=download_id)


@router.post("/{download_id}/move")
def move_download_now(download_id: str, db: Session = Depends(get_db)):
    """Run the guarded import for this single download right now."""
    from backend.app.services.importer import move_download_single

    result = move_download_single(SessionLocal, download_id=download_id)
    if result["outcome"].startswith("blocked"):
        return {"ok": False, **result}
    return {"ok": True, **result}


@router.get("/files", response_model=list[MediaFileResponse])
def list_library_files(
    anime_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[MediaFileResponse]:
    """Every episode that has a media file, newest first.

    Media facts live on the episode now, so this reads the episode table
    instead of a separate local_files cache.
    """
    statement = (
        select(Episode, Anime.display_name, Anime.media_folder)
        .join(Anime, Episode.anime_id == Anime.id)
        .where(Episode.media_filename.is_not(None))
        .order_by(Episode.media_imported_at.desc())
        .limit(limit)
    )
    if anime_id is not None:
        statement = statement.where(Episode.anime_id == anime_id)
    results: list[MediaFileResponse] = []
    for episode, anime_name, media_folder in db.execute(statement).all():
        relative = f"{media_folder}/{episode.media_filename}" if media_folder else episode.media_filename
        results.append(
            MediaFileResponse(
                id=episode.id,
                anime_id=episode.anime_id,
                anime_name=anime_name,
                episode=episode.number,
                relative_path=relative,
                size_bytes=episode.media_size_bytes,
                version=episode.media_version or 1,
                created_at=episode.media_imported_at,
            )
        )
    return results


@router.get("/status", response_model=DownloadsStatusResponse)
def downloads_status(db: Session = Depends(get_db)) -> DownloadsStatusResponse:
    # Read from the DB-backed settings so UI configuration is reflected here.
    cfg = load_app_settings(db)
    monitor_state = worker_state.tasks["torrent_monitor"]
    queue_state = worker_state.tasks["queue_downloads"]
    # torrent_monitor is one task covering sync, import and a conditional
    # sweep (see runtime.py); its result nests each step under its own key.
    monitor_result = monitor_state.last_result or {}
    last_sync_summary = monitor_result.get("sync")
    last_import_summary = monitor_result.get("import")
    last_error = (
        worker_state.tasks["rss_scan"].last_error
        or queue_state.last_error
        or monitor_state.last_error
    )
    for nested in (last_sync_summary, last_import_summary):
        if not last_error and isinstance(nested, dict) and "error" in nested:
            last_error = str(nested["error"])
    return DownloadsStatusResponse(
        configured=cfg.qb_configured,
        category=cfg.qb_category,
        mode=current_mode().value,
        queue_pending=queue_state.requested,
        last_queue_summary=queue_state.last_result,
        last_sync_summary=last_sync_summary,
        last_import_summary=last_import_summary,
        last_error=last_error,
        monitor_interval_seconds=cfg.torrent_monitor_interval_seconds,
        after_import_action=cfg.after_import_action,
        worker_running=worker_state.running,
    )


@router.post("/reconcile", response_model=dict)
def reconcile_now(db: Session = Depends(get_db)) -> dict[str, object]:
    """Work out what actually happened to every download that is not finished.

    The same pass the worker runs every half hour: is the torrent in the
    client under another category, is the file already on disk, is the episode
    already in the library. Offered as a button because the answer changes the
    moment the user fixes something in qBittorrent.
    """
    from backend.app.db.session import SessionLocal
    from backend.app.services.reconcile import reconcile_downloads, summarize

    return summarize(reconcile_downloads(SessionLocal))


@router.delete("/{download_id}", status_code=status.HTTP_204_NO_CONTENT)
def clear_download_row(download_id: str, db: Session = Depends(get_db)) -> Response:
    """Clear this entry from the Downloads list.

    AniFlow's row only: the torrent stays in the client and the files stay on
    disk. ``POST /{id}/remove-torrent`` is the separate action for dropping the
    torrent entry from qBittorrent (still never its files).
    """
    from backend.app.services.downloads import clear_download

    try:
        clear_download(db, download_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", response_model=dict)
def clear_all(db: Session = Depends(get_db)) -> dict[str, int]:
    """Empty the Downloads list. AniFlow's rows only - see DELETE /{id}."""
    from backend.app.services.downloads import clear_all_downloads

    return {"cleared": clear_all_downloads(db)}


@router.post("/{download_id}/remove-torrent", status_code=status.HTTP_204_NO_CONTENT)
def remove_torrent(download_id: str, db: Session = Depends(get_db)) -> Response:
    """Drop the torrent from qBittorrent, keeping AniFlow's row and the files."""
    try:
        remove_download(db, download_id, delete_files=False)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface client problems as 503
        raise HTTPException(status_code=503, detail=f"qBittorrent is unreachable: {exc}") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)

