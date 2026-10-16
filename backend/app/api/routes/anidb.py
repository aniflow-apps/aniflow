"""The AniDB page's calls (build 258): what AniDB's My List export says about the files, by question.

The export and the title list themselves are still served under ``/api/shoko`` (``/anidb-mylist``,
``/anidb-export``, ``/anidb-titles``) - older than this page, and nothing outside AniFlow reads them, so they stay
where they are rather than move for tidiness. This router answers the three questions the page's other tabs ask:
which anime have episodes on the disk that nobody watched, which have a file whose My List state is unknown, and
which hold one episode twice from two release groups.

AniFlow never asks AniDB; everything here is read from the file the user uploaded.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.anime import Anime
from backend.app.services import anidb_mylist

router = APIRouter()


class GroupCount(BaseModel):
    #: AniDB's release group id. The export carries no names.
    id: str
    files: int


class ProblemRow(BaseModel):
    aid: int
    title: str
    english: str | None = None
    year: str = ""
    type: str
    held: int
    total: int
    #: How many things are wrong under this question (episodes, or groups when asked for those).
    count: int
    #: The regular episodes concerned, and how many specials were.
    episodes: list[int] = []
    specials: int = 0
    groups: list[GroupCount] = []
    watchlist_id: str | None = None


class ProblemsResponse(BaseModel):
    items: list[ProblemRow] = []
    has_export: bool = False
    #: False for an upload made before AniFlow read the files section: upload the export again.
    files_read: bool = False
    uploaded_at: str | None = None
    considered: int = 0


class ExportStatus(BaseModel):
    has_export: bool = False
    uploaded_at: str | None = None
    filename: str | None = None
    #: Anime in the export.
    considered: int = 0
    #: See ProblemsResponse.files_read.
    files_read: bool = False


@router.get("/export", response_model=ExportStatus)
def export_status() -> ExportStatus:
    """What was uploaded, without the list: the AniDB page's Library tab reads this."""
    stored = anidb_mylist.load()
    if stored is None:
        return ExportStatus()
    return ExportStatus(
        has_export=True, uploaded_at=stored.uploaded_at, filename=stored.filename,
        considered=len(stored.entries), files_read=stored.files_read,
    )


@router.get("/problems", response_model=ProblemsResponse)
def problems(
    kind: str,
    adult: str = "hide",
    doubled: bool = True,
    db: Session = Depends(get_db),
) -> ProblemsResponse:
    """The anime of the uploaded export with something under ``kind`` (unwatched | unknown | groups).

    ``doubled`` (groups only): list an anime only when one episode is held twice by two groups, which is where a
    duplicate is a real decision; without it, every anime with files from two or more groups.
    """
    if kind not in anidb_mylist.PROBLEMS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(anidb_mylist.PROBLEMS)}")
    stored = anidb_mylist.load()
    if stored is None:
        return ProblemsResponse()
    response = ProblemsResponse(
        has_export=True, files_read=stored.files_read, uploaded_at=stored.uploaded_at, considered=len(stored.entries)
    )
    if not stored.files_read:
        return response

    watching = {
        int(anidb_id): anime_id
        for anime_id, anidb_id in db.execute(sql_select(Anime.id, Anime.anidb_id).where(Anime.anidb_id.is_not(None)))
    }
    for entry in anidb_mylist.select_problems(stored.entries, kind, adult=adult, only_doubled=doubled):
        episodes = {"unwatched": entry.unwatched, "unknown": entry.unknown_state, "groups": entry.doubled}[kind]
        specials = {"unwatched": entry.unwatched_specials, "unknown": entry.unknown_specials, "groups": 0}[kind]
        response.items.append(
            ProblemRow(
                aid=entry.aid, title=entry.title, english=entry.english, year=entry.year, type=entry.type,
                held=entry.held, total=entry.total,
                count=anidb_mylist.problem_count(entry, kind, only_doubled=doubled),
                episodes=list(episodes), specials=specials,
                groups=[GroupCount(id=gid, files=files) for gid, files in entry.groups.items()] if kind == "groups" else [],
                watchlist_id=watching.get(entry.aid),
            )
        )
    return response
