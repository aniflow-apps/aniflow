"""The In Progress page and its Archive (build 164-165)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.core.mode import is_shoko_mode
from backend.app.db.session import get_db
from backend.app.schemas.progress import (
    ProgressDismissResponse,
    ProgressHistoryEntry,
    ProgressItemResponse,
)
from backend.app.services.attention import actions_for, files_for
from backend.app.services.progress import ProgressItem, dismiss_progress, list_archived, list_in_progress

router = APIRouter()


def _to_response(item: ProgressItem, *, archived: bool) -> ProgressItemResponse:
    # Same two files the Attention page names for a duplicate (build 167):
    # this card's own Delete download / Replace library buttons need to say
    # which file is which, the same reason Attention always has. An archived
    # episode offers neither button, so there is nothing to look up for it.
    #
    # Imported here rather than at module level, same as services/attention.py
    # itself does it: a plain top-level import binds this module's own name to
    # the function object at import time, which a test that patches
    # services.storage.storage_roots afterwards (to point it at a temp
    # directory) would never see.
    download_file = library_file = None
    if not archived and item.attention is not None:
        from backend.app.services.storage import storage_roots

        download = item.episode.download
        download_file, library_file = files_for(
            item.attention, download, item.episode, storage_roots()
        )
    older_files: list[str] = []
    if not archived and item.attention is not None and item.attention.kind.value == "DUPLICATE_FILES":
        # The current file and the copies next to it - names, as for a
        # duplicate download. Read from the notes the swap or the import wrote.
        from sqlalchemy.orm import object_session

        from backend.app.services.superseded import live_copies

        session = object_session(item.episode)
        library_file = item.episode.media_filename
        older_files = [row.filename for row in live_copies(session, item.episode.id)] if session else []
    return ProgressItemResponse(
        episode_id=item.episode.id,
        anime_id=item.anime.id,
        anime_name=item.anime.display_name,
        episode_number=item.episode.number,
        state=item.view.state,
        detail=item.view.detail,
        progress=item.view.progress,
        since=item.since,
        archived_at=item.episode.progress_archived_at if archived else None,
        release_label=item.release_label,
        # Reuses the server's own decision about which buttons an attention
        # offers (services/attention.py:actions_for) - the same fix button
        # that appears on the Attention page appears here too, for the same
        # underlying record. An archived episode is done; it gets neither.
        actions=[] if archived or item.attention is None else actions_for(item.attention),
        attention_id=None if archived or item.attention is None else item.attention.id,
        attention_kind=None if archived or item.attention is None else item.attention.kind.value,
        download_file=download_file,
        library_file=library_file,
        older_files=older_files,
        history=[
            ProgressHistoryEntry(
                from_state=entry.from_state, to_state=entry.to_state, created_at=entry.created_at,
            )
            for entry in item.history
        ],
    )


@router.get("", response_model=list[ProgressItemResponse])
def get_progress(db: Session = Depends(get_db)) -> list[ProgressItemResponse]:
    items = list_in_progress(db, shoko_mode=is_shoko_mode())
    return [_to_response(item, archived=False) for item in items]


@router.get("/archive", response_model=list[ProgressItemResponse])
def get_archive(db: Session = Depends(get_db)) -> list[ProgressItemResponse]:
    items = list_archived(db, shoko_mode=is_shoko_mode())
    return [_to_response(item, archived=True) for item in items]


@router.post("/{episode_id}/dismiss", response_model=ProgressDismissResponse)
def dismiss(episode_id: str, db: Session = Depends(get_db)) -> ProgressDismissResponse:
    dismiss_progress(db, episode_id)
    db.commit()
    return ProgressDismissResponse()
