"""Application settings API. Secrets are write-only and never echoed back."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component, reconfigure_logging
from backend.app.db.session import get_db
from backend.app.schemas.settings import (
    ExitAddressResponse,
    PathCheckRequest,
    PathCheckResponse,
    QBittorrentTestRequest,
    ShokoTestRequest,
    SettingsResponse,
    SettingsUpdate,
    SettingsTestResponse,
)
from backend.app.core.mode import AppMode, current_mode
from backend.app.services.storage import path_status
from backend.app.services.config_file import config_path
from backend.app.services.app_settings import (
    load_app_settings,
    mask_secrets,
    save_app_settings,
    validate_combination,
)
from backend.app.integrations.torrent_client import TorrentClientError, client_for, client_name
from backend.app.worker.runtime import reschedule_changed_tasks

router = APIRouter()
logger = for_component("Settings")

_ALLOWED_URL_PREFIXES = ("http://", "https://")


def _validate_url(field: str, value: str | None) -> None:
    if value and not value.lower().startswith(_ALLOWED_URL_PREFIXES):
        raise HTTPException(status_code=422, detail=f"{field} must be an http(s) URL")


def _with_derived(values: dict) -> dict:
    """Add the read-only fields the Settings page needs beside the inputs.

    The timezone in force is one of them: the field's placeholder shows it, so
    an empty setting still says which zone is being followed.
    """
    from backend.app.services.clock import timezone_name, unresolved_timezone

    # The resolved mode, because the override alone does not say what the
    # interface is offering - the indexers decide when it is empty.
    from backend.app.services.app_mode import current_mode as app_mode_now, has_indexer
    from backend.app.db.session import SessionLocal

    session = SessionLocal()
    try:
        values["app_mode"] = app_mode_now(session).value
        values["has_indexer"] = has_indexer(session)
    finally:
        session.close()

    values["anime_library_root"] = str(get_settings().anime_library_root)
    values["download_root"] = str(get_settings().download_root)
    values["config_file"] = str(config_path())
    # Read-only, and shown as such: the mode is a deployment decision, not a
    # setting, because changing it on an existing library is not survivable.
    values["mode"] = current_mode().value
    env = get_settings()
    import_root = env.import_root
    values["import_root"] = str(import_root) if import_root else None
    # The roots as folders on the host, which is what the user recognises -
    # /media is the same on every install and identifies nothing.
    shoko = current_mode() is AppMode.SHOKO_SERVER
    values["storage_paths"] = [
        {"key": "media", **path_status(env.anime_library_root), "used": True},
        {"key": "downloads", **path_status(env.download_root), "used": True},
        {"key": "import", **path_status(import_root), "used": shoko},
    ]
    values["effective_timezone"] = timezone_name()
    values["timezone_unresolved"] = unresolved_timezone()
    return values


@router.get("", response_model=SettingsResponse)
def read_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    cfg = load_app_settings(db)
    return SettingsResponse(**_with_derived(mask_secrets(asdict(cfg))))


@router.put("", response_model=SettingsResponse)
def update_settings(request: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsResponse:
    updates = request.model_dump(exclude_unset=True)
    previous_provider = load_app_settings(db).metadata_provider
    for url_field in ("qb_url",):
        if url_field in updates:
            _validate_url(url_field, updates[url_field])
    # A partial update has to be judged against the result, not the payload:
    # sending only file_operation="move" can contradict a stored "keep".
    effective = asdict(load_app_settings(db)) | updates
    try:
        validate_combination(effective)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        save_app_settings(db, updates)
    except OSError as exc:
        # The config folder is not writable (missing mount, wrong owner). Say
        # so plainly - the alternative is a 500 with no hint what to fix.
        raise HTTPException(
            status_code=503,
            detail=f"Settings could not be written to {config_path()}: {exc}",
        ) from exc
    db.commit()
    # So a lowered interval (or a task switched back on) takes effect right
    # away instead of only after that task's next run under whatever was
    # already scheduled (build 163, STATE.md Open item 9).
    reschedule_changed_tasks(set(updates))
    cfg = load_app_settings(db)
    if updates.get("metadata_provider") and updates["metadata_provider"] != previous_provider:
        # Nothing to discard since build 143. The calendar is built from every
        # source at once and kept in its own database, so this setting no
        # longer decides what is on the calendar - it decides who is asked
        # when an anime is searched for or added.
        logger.info("metadata source for search and add is now %s", cfg.metadata_provider)
    # The log level was stored and then ignored until the next restart, so
    # setting WARNING and pressing the test button still printed INFO. It is
    # the one setting people reach for *because* something is happening now,
    # which makes "takes effect after a redeploy" the wrong answer.
    reconfigure_logging(cfg)
    return SettingsResponse(**_with_derived(mask_secrets(asdict(cfg))))


@router.post("/test/shoko", response_model=SettingsTestResponse)
def test_shoko(
    request: ShokoTestRequest | None = None, db: Session = Depends(get_db)
) -> SettingsTestResponse:
    """Reach Shoko, then try the key - and say which of the two failed.

    Two questions, asked separately on purpose: a wrong address and a wrong key
    are the two mistakes people actually make, and one combined "could not
    connect" cannot tell them apart. The URL is checked without the key first,
    so "Shoko is there but refused the key" is a sentence AniFlow can say.

    Draft values are accepted, so this works before anything is saved.
    """
    from backend.app.integrations.shoko.client import ShokoClient, ShokoError

    stored = load_app_settings(db)
    draft = request or ShokoTestRequest()
    url = draft.url if draft.url not in (None, "") else stored.shoko_url
    key = draft.api_key if draft.api_key is not None else stored.shoko_api_key
    if not url:
        raise HTTPException(status_code=409, detail="Enter the Shoko Server URL first")
    _validate_url("shoko_url", url)

    try:
        client = ShokoClient(url, key or "")
        version = client.version()
    except ShokoError as exc:
        return SettingsTestResponse(ok=False, detail=str(exc))

    if not key:
        return SettingsTestResponse(
            ok=False, detail=f"Shoko Server {version} answered, but no API key is set"
        )
    try:
        stats = client.check_key()
    except ShokoError as exc:
        return SettingsTestResponse(ok=False, detail=f"Shoko Server {version}: {exc}")

    series = stats.get("SeriesCount") or stats.get("SeriesAdded") or "?"
    files = stats.get("FileCount") or stats.get("FilesWithDuplicates") or "?"
    return SettingsTestResponse(
        ok=True, detail=f"Shoko Server {version} - {series} series, {files} files"
    )


@router.post("/test/proxy", response_model=ExitAddressResponse)
def test_proxy(db: Session = Depends(get_db)) -> ExitAddressResponse:
    """Ask an echo service which address it sees, through the proxy in force.

    Uses the *stored* settings rather than a draft: unlike a URL or a password,
    a proxy is only meaningfully tested once it is the one being used, and a
    half-applied proxy is exactly the state worth catching.
    """
    from backend.app.services.outbound import describe_exit

    return ExitAddressResponse(**describe_exit(load_app_settings(db)))


@router.post("/test/qbittorrent", response_model=SettingsTestResponse)
def test_qbittorrent(
    request: QBittorrentTestRequest | None = None,
    db: Session = Depends(get_db),
) -> SettingsTestResponse:
    stored = load_app_settings(db)
    draft = request or QBittorrentTestRequest()
    kind = draft.client or stored.torrent_client
    url = draft.url if draft.url not in (None, "") else stored.qb_url
    username = draft.username if draft.username not in (None, "") else stored.qb_username
    password = draft.password if draft.password is not None else stored.qb_password
    if not url:
        raise HTTPException(status_code=409, detail=f"Enter a {client_name(kind)} URL first (saving is not required for testing)")
    try:
        client = client_for(kind, url, username or "", password or "", timeout=5)
        version = client.version()
        client.close()
    except TorrentClientError as exc:
        return SettingsTestResponse(ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - diagnostics must never 500
        return SettingsTestResponse(ok=False, detail=f"{type(exc).__name__}: {exc}")
    return SettingsTestResponse(ok=True, detail=f"{client_name(kind)} {version}")



@router.post("/paths/check", response_model=PathCheckResponse)
def check_paths(
    request: PathCheckRequest | None = None,
    db: Session = Depends(get_db),
) -> PathCheckResponse:
    """Compare qBittorrent's download folder with AniFlow's and verify the
    local roots exist and are writable. Purely diagnostic.

    The connection details may be supplied by the caller, which is what makes
    the button work before the settings have ever been saved - it used to read
    only stored values and answer "qBittorrent is not configured".
    """
    import os


    cfg = load_app_settings(db)
    qb_url = (request.qb_url if request else None) or cfg.qb_url
    qb_username = (request.qb_username if request else None) or cfg.qb_username
    # An empty password field means "keep the stored one", as everywhere else.
    qb_password = (request.qb_password if request and request.qb_password else None) or cfg.qb_password
    kind = (request.client if request and request.client else None) or cfg.torrent_client
    env = get_settings()
    library_root = str(env.anime_library_root)
    download_root = str(env.download_root)

    def flags(path):
        exists = os.path.isdir(path)
        return exists, exists and os.access(path, os.W_OK)

    dl_exists, dl_writable = flags(download_root)
    lib_exists, lib_writable = flags(library_root)

    if not qb_url:
        # No client to compare against, which is not a reason to answer
        # nothing: the folder checks above are what most of this button is for
        # (build 137). Reported as *"Check paths button does not work on my
        # pi"* - it worked wherever a client happened to be configured.
        message = "Folders look fine. No torrent client is configured, so its save path was not checked."
        if not dl_exists:
            message = f"AniFlow's DOWNLOAD_ROOT '{download_root}' does not exist."
        elif not dl_writable:
            message = f"AniFlow's DOWNLOAD_ROOT '{download_root}' is not writable."
        elif not lib_exists:
            message = f"AniFlow's ANIME_LIBRARY_ROOT '{library_root}' does not exist."
        elif not lib_writable:
            message = f"AniFlow's ANIME_LIBRARY_ROOT '{library_root}' is not writable."
        sample_source, sample_target, sample_media_folder = _sample_move(
            db, cfg, download_root=download_root, media_root=library_root
        )
        return PathCheckResponse(
            ok=dl_exists and dl_writable and lib_exists and lib_writable,
            message=message,
            aniflow_download_root=download_root,
            aniflow_library_root=library_root,
            qb_category=cfg.qb_category,
            download_root_exists=dl_exists,
            download_root_writable=dl_writable,
            library_root_exists=lib_exists,
            library_root_writable=lib_writable,
            sample_source_path=sample_source,
            sample_target_path=sample_target,
            sample_media_folder=sample_media_folder,
        )

    qb_path = None
    sample_content = None
    try:
        client = client_for(kind, qb_url, qb_username, qb_password, timeout=5)
        try:
            client.version()
            qb_path = client.default_save_path()
            torrents = client.find_torrents(category=cfg.qb_category)
            if torrents:
                sample_content = torrents[0].content_path
        finally:
            client.close()
    except TorrentClientError as exc:
        return PathCheckResponse(
            ok=False,
            message=f"Could not read {client_name(kind)}'s save path: {exc}",
            aniflow_download_root=download_root,
            aniflow_library_root=library_root,
            qb_category=cfg.qb_category,
            download_root_exists=dl_exists,
            download_root_writable=dl_writable,
            library_root_exists=lib_exists,
            library_root_writable=lib_writable,
        )

    # AniFlow reads downloads from <download_root> directly. qBittorrent
    # places torrents in <save_path>/<category>/ when a category is set, so
    # the <category>/ prefix is the only difference that needs handling.
    qb_category_subdir = cfg.qb_category
    sample_translated = sample_within = None
    if sample_content:
        # Strip the configured torrent category subdir so the path lands
        # inside the download root that AniFlow watches.
        prefix = f"{download_root.rstrip('/')}/{qb_category_subdir}" if qb_category_subdir else None
        if prefix and sample_content.startswith(prefix + "/"):
            sample_translated = sample_content[len(prefix) + 1:]
        else:
            sample_translated = sample_content
        if sample_translated:
            root_norm = download_root.replace(chr(92), "/").rstrip("/")
            norm = sample_translated.replace(chr(92), "/")
            sample_within = norm.lower().startswith(root_norm.lower())

    # The full source -> target pair for a real download, built with the same
    # helpers the importer uses, so this panel can never disagree with what an
    # actual import would do (see services/importer.py and §14).
    sample_source, sample_target, sample_media_folder = _sample_move(
        db, cfg, download_root=download_root, media_root=library_root
    )

    ok = True
    message = "Paths look consistent."
    # Two clean fail modes for the 2-folder setup:
    # 1) qB's save_path differs from AniFlow's download_root (different mount)
    # 2) AniFlow's download_root is not writable (permissions)
    if qb_path and download_root and qb_path.rstrip("/") != download_root.rstrip("/"):
        category_path = f"{download_root.rstrip('/')}/{qb_category_subdir}" if qb_category_subdir else None
        if category_path and qb_path.rstrip("/") == category_path:
            # qB saves to /downloads/AniFlow - the only mismatch is the category
            # subdir which AniFlow auto-strips during import. This is the
            # expected/working setup; the file is found at /downloads/<file>.
            pass
        else:
            ok = False
            message = (
                f"qBittorrent default save path is '{qb_path}' but AniFlow's "
                f"DOWNLOAD_ROOT is '{download_root}'. "
                f"Set qBittorrent's default save path to '{download_root}' "
                f"(or update DOWNLOAD_ROOT in .env) and recreate the container."
            )

    if ok and not dl_writable:
        ok = False
        message = (
            f"AniFlow's DOWNLOAD_ROOT '{download_root}' is not writable. "
            f"Ensure the container can write to this path (e.g. fix ownership "
            f"of '{download_root}')."
        )
    elif ok and not lib_writable:
        ok = False
        message = (
            f"AniFlow's ANIME_LIBRARY_ROOT '{library_root}' is not writable. "
            f"Ensure the container can write to this path."
        )

    return PathCheckResponse(
        ok=ok,
        message=message,
        qb_default_save_path=qb_path,
        aniflow_download_root=download_root,
        aniflow_library_root=library_root,
        qb_category=cfg.qb_category,
        translated_example=sample_translated,
        sample_content_path=sample_content,
        sample_source_path=sample_source,
        sample_target_path=sample_target,
        sample_media_folder=sample_media_folder,
        sample_translated=sample_translated,
        sample_within_root=sample_within,
        download_root_exists=dl_exists,
        download_root_writable=dl_writable,
        library_root_exists=lib_exists,
        library_root_writable=lib_writable,
    )


def _sample_move(
    db: Session,
    cfg,
    *,
    download_root: str,
    media_root: str,
) -> tuple[str | None, str | None, str | None]:
    """Resolve the newest download into its source and destination paths.

    The roots are passed in rather than re-read, so the pair shown here is
    built from exactly the roots this panel reports.

    Returns ``(source, target, media_folder)``; any element is None when the
    information needed for it is not known yet. Purely diagnostic - nothing is
    read from or written to disk beyond checking which candidate exists.
    """
    from sqlalchemy import select

    from backend.app.models.anime import Anime, Episode
    from backend.app.models.download import TorrentDownload
    from backend.app.services.episode_state import expected_media_filename
    from backend.app.services.storage import (
        download_path_candidates,
        media_path,
        storage_roots,
    )

    row = db.scalar(
        select(TorrentDownload).order_by(TorrentDownload.created_at.desc()).limit(1)
    )
    if row is None:
        return None, None, None
    episode = db.get(Episode, row.episode_id) if row.episode_id else None
    if episode is None:
        return None, None, None
    anime = db.get(Anime, episode.anime_id)
    media_folder = anime.media_folder if anime else None

    roots = storage_roots(download_root=download_root, media_root=media_root)
    candidates = download_path_candidates(
        roots,
        episode.download_filename,
        download_folder=episode.download_folder,
        category=cfg.qb_category,
    )
    # Prefer a candidate that exists; otherwise show the first one so the
    # expected location is still visible.
    source = next((c for c in candidates if c.is_file()), candidates[0] if candidates else None)

    media_filename = episode.media_filename or expected_media_filename(episode.download_filename)
    target = media_path(roots, media_folder, media_filename)
    return (
        str(source) if source else None,
        str(target) if target else None,
        media_folder,
    )
