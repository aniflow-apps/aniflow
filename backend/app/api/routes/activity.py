"""Attention workflow actions and the Activity log."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.core.mode import is_shoko_mode
from backend.app.db.session import get_db
from backend.app.models.activity import Activity, Attention, AttentionStatus
from backend.app.models.anime import Anime, Episode
from backend.app.models.download import TorrentDownload
from backend.app.models.rss import FeedSource, Release
from backend.app.schemas.activity import (
    ActivityClearResponse,
    ActivityResponse,
    AssignAnimeRequest,
    AttentionResolutionRequest,
    AttentionResponse,
)
from backend.app.services.attention import (
    NOTABLE,
    PROBLEM,
    ROUTINE,
    activity_level,
    approve_new_version_download,
    assign_ambiguous_release,
    dismiss_attention,
)
from backend.app.services.debug_samples import MARKER as SAMPLE_MARKER
from backend.app.services.progress import in_progress_episode_ids

router = APIRouter()
logger = for_component("Activity")


def _decorate(
    attention: Attention,
    anime_name: str | None,
    feed_name: str | None,
    episode_number: int | None = None,
    release_label: str | None = None,
    episode_label: str | None = None,
    *,
    db: Session | None = None,
) -> AttentionResponse:
    payload = AttentionResponse.model_validate(attention)
    payload.anime_name = anime_name
    payload.feed_name = feed_name
    payload.episode_number = episode_number
    payload.release_label = release_label
    payload.episode_label = episode_label
    if db is not None:
        from backend.app.services.attention import actions_for, files_for
        from backend.app.services.storage import storage_roots

        download = (
            db.get(TorrentDownload, attention.download_id) if attention.download_id else None
        )
        episode = download.episode if download is not None else None
        payload.actions = actions_for(attention)
        # The filesystem is consulted here rather than remembered anywhere: a
        # file the user has already dealt with by hand must not still be
        # offered for deletion.
        payload.download_file, payload.library_file = files_for(
            attention, download, episode, storage_roots()
        )
    return payload


@router.get("/attention", response_model=list[AttentionResponse])
def list_attention(
    status_filter: str = Query(default="OPEN", pattern="^(OPEN|RESOLVED|DISMISSED|ALL)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[AttentionResponse]:
    # The download and its episode are joined in so a row can name the
    # episode it is about. Three rows reading "Saga of Tanya the Evil II" are
    # indistinguishable without it, and the point of the list is to be acted
    # on.
    statement = (
        select(
            Attention,
            Anime.display_name,
            FeedSource.name,
            Episode.number,
            # The name qBittorrent knows a download by, or - before there is
            # one to ask, such as EPISODE_NOT_IDENTIFIABLE, which never gets
            # that far - the release's own raw name (build 168), so a row
            # about a release is never silent about which one.
            func.coalesce(TorrentDownload.label, Release.raw_name),
            Release.episode,
            # Either route to the episode: a record about a download reaches
            # it through that row, and one about a file in the library names
            # it directly (build 90).
            func.coalesce(Attention.episode_id, TorrentDownload.episode_id),
        )
        .outerjoin(Anime, Attention.anime_id == Anime.id)
        .outerjoin(FeedSource, Attention.feed_source_id == FeedSource.id)
        .outerjoin(TorrentDownload, Attention.download_id == TorrentDownload.id)
        .outerjoin(Release, Attention.release_id == Release.id)
        .outerjoin(
            Episode,
            func.coalesce(Attention.episode_id, TorrentDownload.episode_id) == Episode.id,
        )
        .order_by(Attention.created_at.desc())
        .limit(limit)
    )
    if status_filter != "ALL":
        statement = statement.where(Attention.status == AttentionStatus[status_filter])

    # An episode already on In Progress tells this same story there - the
    # card, its fix buttons and its own full history (build 167). Left out
    # here rather than shown twice: *"something which is episode based and
    # appears in In Progress, should NOT appear double."*
    #
    # A debug sample is exempt (services/debug_samples.py): it links to a real
    # episode when one exists, purely to have something to render, and that
    # episode's actual pipeline state is not the point of pressing the
    # button - hiding the sample because the real episode happens to be
    # downloading would defeat the reason the button exists.
    in_progress = in_progress_episode_ids(db, shoko_mode=is_shoko_mode())

    # One row per episode and kind of problem (build 89). Reported as Iron Wok
    # Jan episode 10 appearing twice: two download rows for that episode, each
    # with its own warning, both about the same file and both offering the
    # same two buttons. Collapsed here rather than in the database, because
    # both records are true - it is the *list* that should not say it twice.
    #
    # Only rows that name an episode collapse. A feed failure or an ambiguous
    # release has no episode, and two of those are two different problems.
    seen: dict[tuple[str, str, str | None], AttentionResponse] = {}
    rows: list[AttentionResponse] = []
    for (
        attention,
        anime_name,
        feed_name,
        episode_number,
        release_label,
        episode_label,
        episode_id,
    ) in db.execute(statement).all():
        if episode_id is not None and episode_id in in_progress and attention.note != SAMPLE_MARKER:
            continue
        payload = _decorate(
            attention, anime_name, feed_name, episode_number, release_label, episode_label, db=db
        )
        if episode_id is None:
            rows.append(payload)
            continue
        key = (payload.kind, payload.detail or "", episode_id)
        kept = seen.get(key)
        if kept is None:
            seen[key] = payload
            rows.append(payload)
            continue
        # The newest is already first - the query orders by created_at
        # descending - so the row on screen is the most recent statement of
        # the problem and the count says how many older ones it stands for.
        kept.duplicates += 1
    return rows


class SampleResponse(BaseModel):
    """How many sample records are in place."""

    attention: int = 0
    activity: int = 0
    removed: int = 0


def _require_debug_mode(db: Session) -> None:
    """Refuse unless debug mode is on.

    The same gate the Release Inspector and the database record sit behind.
    Not because samples are dangerous - they create no anime, episode or
    download - but because a button that invents warnings has no business on
    an installation somebody is using to watch television.
    """
    from backend.app.services.app_settings import load_app_settings

    if not load_app_settings(db).debug_mode:
        raise HTTPException(status_code=403, detail="debug_mode is off")


@router.post("/debug/samples", response_model=SampleResponse)
def seed_samples(db: Session = Depends(get_db)) -> SampleResponse:
    """One attention record per kind, one activity entry per kind (build 126).

    Asked for because most of them cannot otherwise be seen: *"else I always
    need to wait until a real time error appears."*  Ten attention kinds and
    sixteen activity kinds exist and most fire only when something specific
    goes wrong, so most have never been rendered - which is how three activity
    kinds kept no translation for six builds.

    See services/debug_samples.py for what makes them safe to create.
    """
    from backend.app.services import debug_samples

    _require_debug_mode(db)
    result = debug_samples.seed(db)
    db.commit()
    return SampleResponse(**result)


@router.delete("/debug/samples", response_model=SampleResponse)
def clear_samples(db: Session = Depends(get_db)) -> SampleResponse:
    """Remove every sample, matched on its marker and nothing else."""
    from backend.app.services import debug_samples

    _require_debug_mode(db)
    removed = debug_samples.clear(db)
    db.commit()
    return SampleResponse(removed=removed)


@router.get("/debug/samples", response_model=SampleResponse)
def count_samples(db: Session = Depends(get_db)) -> SampleResponse:
    """Whether any are in place, so the button can say which way it goes."""
    from backend.app.services import debug_samples

    _require_debug_mode(db)
    return SampleResponse(**debug_samples.counts(db))


@router.post("/attention/{attention_id}/delete-download", response_model=AttentionResponse)
def delete_download(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Throw away the file AniFlow downloaded and close the record.

    The download folder is AniFlow's own working space, so this deletes from
    it. The library is never touched here - that is replace-library.
    """
    from backend.app.services.attention import delete_downloaded_file

    try:
        delete_downloaded_file(db, attention_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"The file could not be deleted: {exc}") from exc
    db.commit()
    attention = db.get(Attention, attention_id)
    return _decorate(attention, None, None, db=db)


@router.post("/attention/{attention_id}/replace-library", response_model=AttentionResponse)
def replace_library(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Keep the downloaded file: remove the library's copy and import the new one.

    The old file is deleted because it is what blocks the new one. The new
    file is not moved here - it goes through the ordinary import, which in
    Shoko Server mode hands it to the import folder for Shoko to identify.
    """
    from backend.app.services.attention import replace_library_file

    try:
        replace_library_file(db, attention_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"The file could not be removed: {exc}") from exc
    db.commit()
    attention = db.get(Attention, attention_id)
    return _decorate(attention, None, None, db=db)


@router.post("/attention/{attention_id}/delete-older", response_model=AttentionResponse)
def delete_older_file(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Duplicate files: delete the older copy and keep the episode's own file."""
    from backend.app.services.attention import _get_or_404
    from backend.app.services.duplicate_files import delete_older

    attention = _get_or_404(db, attention_id)
    delete_older(db, attention)
    db.commit()
    return _decorate(attention, None, None, db=db)


@router.post("/attention/{attention_id}/delete-newer", response_model=AttentionResponse)
def delete_newer_file(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Duplicate files: delete the episode's current file and keep the older copy."""
    from backend.app.services.attention import _get_or_404
    from backend.app.services.duplicate_files import delete_newer

    attention = _get_or_404(db, attention_id)
    delete_newer(db, attention)
    db.commit()
    return _decorate(attention, None, None, db=db)


@router.post("/attention/{attention_id}/move-now", response_model=AttentionResponse)
def move_now(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Hand a downloaded file that is waiting back to the import pass."""
    from backend.app.services.attention import retry_import_now

    try:
        retry_import_now(db, attention_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    from backend.app.worker.runtime import worker_state

    worker_state.request_run("torrent_monitor")
    attention = db.get(Attention, attention_id)
    return _decorate(attention, None, None, db=db)


@router.post("/attention/{attention_id}/retry-now", response_model=AttentionResponse)
def retry_now(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Hand a missing torrent to the client again, on request."""
    from backend.app.services.attention import retry_download_now

    retry_download_now(db, attention_id)
    db.commit()
    attention = db.get(Attention, attention_id)
    return _decorate(attention, None, None, db=db)


class DismissedAllResponse(BaseModel):
    """How many records one press dealt with."""

    dismissed: int


@router.post("/attention/dismiss-all", response_model=DismissedAllResponse)
def dismiss_every_attention(
    payload: AttentionResolutionRequest | None = None,
    db: Session = Depends(get_db),
) -> DismissedAllResponse:
    """Dismiss every open record (build 94).

    Declared *before* the ``{attention_id}`` routes on purpose: FastAPI
    matches in order, and "dismiss-all" would otherwise be read as an id.
    """
    from backend.app.services.attention import dismiss_all

    count = dismiss_all(db, payload.note if payload else None)
    db.commit()
    return DismissedAllResponse(dismissed=count)


@router.post("/attention/{attention_id}/dismiss", response_model=AttentionResponse)
def dismiss(
    attention_id: str,
    request: AttentionResolutionRequest,
    db: Session = Depends(get_db),
) -> AttentionResponse:
    attention = dismiss_attention(db, attention_id, request.note)
    db.commit()
    return _decorate(attention, None, None)


@router.post("/attention/{attention_id}/accept-metadata", response_model=AttentionResponse)
def accept_metadata(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Take the provider's value and drop the override it disagreed with.

    The Fix half of *"With a Fix button to allow the change or Skip the change
    and keep the user modification."*  Skip is the Dismiss button every record
    already has: the override stays, and nothing is said about it again until
    the provider changes its mind a second time.
    """
    from backend.app.services.metadata_fill import accept_provider_value

    try:
        accept_provider_value(db, attention_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    attention = db.get(Attention, attention_id)
    return _decorate(attention, None, None, db=db)


@router.post("/attention/{attention_id}/approve-download", response_model=AttentionResponse)
def approve_download(attention_id: str, db: Session = Depends(get_db)) -> AttentionResponse:
    """Explicit user override for the V2/V3-after-V1 business rule."""
    approve_new_version_download(db, attention_id)  # commits internally
    attention = db.get(Attention, attention_id)
    return _decorate(attention, None, None)


@router.post("/attention/{attention_id}/assign-anime")
def assign_anime(attention_id: str, request: AssignAnimeRequest, db: Session = Depends(get_db)):
    result = assign_ambiguous_release(db, attention_id, request.anime_id)  # commits internally
    attention = db.get(Attention, attention_id)
    return {"attention": _decorate(attention, None, None), "result": result}


@router.get("", response_model=list[ActivityResponse])
def list_activity(
    limit: int = Query(default=100, ge=1, le=500),
    level: str | None = Query(
        default=None,
        pattern="^(routine|notable|problem)$",
        description="Only entries at least this important: routine < notable < problem.",
    ),
    db: Session = Depends(get_db),
) -> list[ActivityResponse]:
    statement = (
        select(Activity, Anime.display_name)
        .outerjoin(Anime, Activity.anime_id == Anime.id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    order = {ROUTINE: 0, NOTABLE: 1, PROBLEM: 2}
    minimum = order.get(level or ROUTINE, 0)
    # Same rule as the Attention list, and the same reason (build 167): an
    # episode still on In Progress already has this event in its own history,
    # so the diary entry is left out while it does rather than said twice.
    in_progress = in_progress_episode_ids(db, shoko_mode=is_shoko_mode())
    results: list[ActivityResponse] = []
    for activity, anime_name in db.execute(statement).all():
        if activity.episode_id is not None and activity.episode_id in in_progress:
            continue
        entry_level = activity_level(activity.kind, activity.details)
        if order[entry_level] < minimum:
            continue
        payload = ActivityResponse.model_validate(activity)
        payload.anime_name = anime_name
        payload.level = entry_level
        results.append(payload)
    return results


@router.delete("", response_model=ActivityClearResponse)
def clear_activity(db: Session = Depends(get_db)) -> ActivityClearResponse:
    """Empty the activity log.

    The log is a diary, not state: it only records what already happened, so
    deleting it changes nothing about episodes, downloads or attention records.
    Open Attention items are deliberately untouched - they are work, not history.
    """
    deleted = db.execute(delete(Activity)).rowcount or 0
    db.commit()
    logger.info("activity log cleared: %s entries removed", deleted)
    return ActivityClearResponse(deleted=deleted)
