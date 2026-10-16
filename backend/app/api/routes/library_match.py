"""Find an anime's files in the library: the three calls the dialog makes.

Read, read, then write - in that order and never fewer. The write is the only
one that changes anything, and it happens after the user has seen exactly what
it will do, including the files it will leave alone. See
services/library_match.py for why that matters.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.anime import Anime
from backend.app.services import library_match

router = APIRouter()


class FolderCandidateResponse(BaseModel):
    name: str
    score: int
    matched: str | None = None
    files: int
    episodes: int


class FilePlanResponse(BaseModel):
    filename: str
    episode: int | None = None
    placed: bool
    reason: str | None = None
    quality: str | None = None
    subgroup: str | None = None


class MatchPreviewResponse(BaseModel):
    folder: str
    plans: list[FilePlanResponse]
    placeable: int
    unplaceable: int
    subgroup: str | None = None
    quality: str | None = None
    release_name: str | None = None


class LinkRequest(BaseModel):
    folder: str = Field(min_length=1, max_length=255)


class LinkResponse(BaseModel):
    folder: str
    placed: int
    unplaced: int
    learned: dict[str, str | None]


def _anime(db: Session, anime_id: str) -> Anime:
    anime = db.get(Anime, anime_id)
    if anime is None:
        raise HTTPException(status_code=404, detail="Anime not found")
    return anime


@router.get("/{anime_id}/library/folders", response_model=list[FolderCandidateResponse])
def list_folders(
    anime_id: str,
    all_folders: bool = Query(default=False, alias="all"),
    db: Session = Depends(get_db),
) -> list[FolderCandidateResponse]:
    """The library's folders, the ones that look like this anime first."""
    found = library_match.candidates(_anime(db, anime_id), all_folders=all_folders)
    return [FolderCandidateResponse(**item.as_dict()) for item in found]


@router.get("/{anime_id}/library/preview", response_model=MatchPreviewResponse)
def preview_folder(
    anime_id: str,
    folder: str = Query(min_length=1, max_length=255),
    db: Session = Depends(get_db),
) -> MatchPreviewResponse:
    """What linking this folder would do. Writes nothing."""
    try:
        plan = library_match.preview(_anime(db, anime_id), folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MatchPreviewResponse(**plan.as_dict())


@router.post("/{anime_id}/library/link", response_model=LinkResponse)
def link_folder(
    anime_id: str, payload: LinkRequest, db: Session = Depends(get_db)
) -> LinkResponse:
    """Record the folder and its files against this anime."""
    try:
        result = library_match.link(db, _anime(db, anime_id), payload.folder)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LinkResponse(**result)


@router.delete("/{anime_id}/library/link", status_code=204)
def unlink_folder(anime_id: str, db: Session = Depends(get_db)) -> None:
    """Forget the folder and its files. Nothing on disk is touched."""
    library_match.unlink(db, _anime(db, anime_id))
