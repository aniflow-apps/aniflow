"""Libraries: the page's calls (build 192, 0.8.5). See services/libraries.py."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.integrations.shoko.client import ShokoError, client_from_settings
from backend.app.models.library import Library
from backend.app.services import libraries, shoko_library

router = APIRouter()


class LibraryResponse(BaseModel):
    id: int
    key: str | None = None
    name: str
    folder: str
    is_system: bool
    is_default: bool
    fits_adult: str | None = None
    fits_season: str | None = None
    shoko_folder_id: int | None = None
    #: How many anime live here, counted on the server (build 194).
    anime_count: int = 0
    #: Of those, how many have their folder in this library's folder, and how
    #: many folders are really there - read from the disk (build 196).
    anime_with_folder: int = 0
    folders_on_disk: int = 0
    #: Bytes the files of this library's anime take, as AniFlow recorded them; and what is free on, and the size
    #: of, the disk the folder is on (null when it cannot be read). Build 233.
    used_bytes: int = 0
    free_bytes: int | None = None
    total_bytes: int | None = None
    #: False for Upcoming: no folder, because nothing is downloaded for it (build 198).
    has_folder: bool = True
    #: Whether AniFlow can write there; the page warns when it cannot.
    writable: bool = True
    exists: bool = True


class LibraryListResponse(BaseModel):
    libraries: list[LibraryResponse]


class LibraryBody(BaseModel):
    name: str = Field(max_length=80)
    folder: str
    fits_adult: str | None = None
    fits_season: str | None = None
    shoko_folder_id: int | None = None


class LibraryPatch(BaseModel):
    name: str | None = None
    folder: str | None = None
    fits_adult: str | None = None
    fits_season: str | None = None
    shoko_folder_id: int | None = None
    clear_shoko_folder: bool = False


class FolderEntryResponse(BaseModel):
    name: str
    path: str
    writable: bool


class BrowseResponse(BaseModel):
    path: str
    parent: str | None
    entries: list[FolderEntryResponse]
    selectable: bool
    writable: bool


class ShokoFolderResponse(BaseModel):
    id: int | None = None
    name: str | None = None
    path: str | None = None
    drop_type: str | None = None
    watching: bool | None = None


class ShokoFoldersResponse(BaseModel):
    reachable: bool
    detail: str | None = None
    folders: list[ShokoFolderResponse] = []


def _view(
    library: Library,
    count: int = 0,
    disk: tuple[int, int] = (0, 0),
    space: tuple[int, int | None, int | None] = (0, None, None),
) -> LibraryResponse:
    import os

    return LibraryResponse(
        used_bytes=space[0],
        free_bytes=space[1],
        total_bytes=space[2],
        anime_count=count,
        anime_with_folder=disk[0],
        folders_on_disk=disk[1],
        id=library.id,
        key=library.key,
        name=library.name,
        folder=library.folder,
        is_system=library.is_system,
        is_default=library.is_default,
        fits_adult=library.fits_adult,
        fits_season=library.fits_season,
        shoko_folder_id=library.shoko_folder_id,
        has_folder=library.has_folder,
        # No folder is not a missing or read-only one, so no warning for it.
        writable=os.access(library.folder, os.W_OK) if library.has_folder else True,
        exists=os.path.isdir(library.folder) if library.has_folder else True,
    )


def _refused(exc: libraries.LibraryError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("", response_model=LibraryListResponse)
def list_libraries(
    quick: bool = Query(default=False), db: Session = Depends(get_db)
) -> LibraryListResponse:
    """``quick`` leaves out the walk of each library's folder (anime_with_folder, folders_on_disk stay 0).

    That walk costs a stat per subfolder, which is slow on a bind mount from a Windows host; the page asks
    for the quick list first so the libraries show at once, then for the full one.
    """
    rows = libraries.list_libraries(db)
    counts = libraries.anime_counts(db)
    disk = {} if quick else libraries.disk_counts(db)
    space = libraries.space_counts(db)
    return LibraryListResponse(
        libraries=[
            _view(lib, counts.get(lib.id, 0), disk.get(lib.id, (0, 0)), space.get(lib.id, (0, None, None)))
            for lib in rows
        ],
    )


@router.get("/browse", response_model=BrowseResponse)
def browse(path: str | None = Query(default=None)) -> BrowseResponse:
    try:
        result = libraries.browse(path)
    except libraries.LibraryError as exc:
        raise _refused(exc) from exc
    return BrowseResponse(
        path=result.path,
        parent=result.parent,
        entries=[FolderEntryResponse(name=e.name, path=e.path, writable=e.writable) for e in result.entries],
        selectable=result.selectable,
        writable=result.writable,
    )


@router.get("/shoko-folders", response_model=ShokoFoldersResponse)
def shoko_folders() -> ShokoFoldersResponse:
    """Shoko's import folders, for linking a library and for the watching warning."""
    try:
        client = client_from_settings()
        folders = shoko_library.read_import_folders(client)
    except ShokoError as exc:
        return ShokoFoldersResponse(reachable=False, detail=str(exc))
    return ShokoFoldersResponse(
        reachable=True,
        folders=[
            ShokoFolderResponse(
                id=f.get("id"),  # type: ignore[arg-type]
                name=f.get("name"),  # type: ignore[arg-type]
                path=f.get("path"),  # type: ignore[arg-type]
                drop_type=f.get("drop_type"),  # type: ignore[arg-type]
                watching=f.get("watching"),  # type: ignore[arg-type]
            )
            for f in folders
        ],
    )


@router.post("", response_model=LibraryResponse, status_code=201)
def create_library(body: LibraryBody, db: Session = Depends(get_db)) -> LibraryResponse:
    try:
        library = libraries.create(
            db,
            name=body.name,
            folder=body.folder,
            adult=body.fits_adult,
            season=body.fits_season,
            shoko_folder_id=body.shoko_folder_id,
        )
    except libraries.LibraryError as exc:
        raise _refused(exc) from exc
    return _view(library)


@router.patch("/{library_id}", response_model=LibraryResponse)
def update_library(library_id: int, body: LibraryPatch, db: Session = Depends(get_db)) -> LibraryResponse:
    given = body.model_fields_set
    try:
        library = libraries.update(
            db,
            library_id,
            name=body.name,
            folder=body.folder,
            adult=body.fits_adult,
            season=body.fits_season,
            shoko_folder_id=body.shoko_folder_id,
            clear_shoko_folder=body.clear_shoko_folder,
            fits_given=bool(given & {"fits_adult", "fits_season"}),
        )
    except libraries.LibraryError as exc:
        raise _refused(exc) from exc
    return _view(library, libraries.anime_counts(db).get(library.id, 0))


@router.post("/{library_id}/shift", status_code=204)
def shift_library(library_id: int, direction: str = Query(...), db: Session = Depends(get_db)) -> None:
    """Move a library one place up or down in the list."""
    try:
        libraries.shift(db, library_id, direction)
    except libraries.LibraryError as exc:
        raise _refused(exc) from exc


@router.post("/{library_id}/default", response_model=LibraryResponse)
def make_default(library_id: int, db: Session = Depends(get_db)) -> LibraryResponse:
    try:
        library = libraries.make_default(db, library_id)
        return _view(library, libraries.anime_counts(db).get(library.id, 0))
    except libraries.LibraryError as exc:
        raise _refused(exc) from exc


@router.delete("/{library_id}", status_code=204)
def delete_library(
    library_id: int,
    move_to: int | None = Query(default=None),
    move_files: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> None:
    try:
        libraries.delete(db, library_id, move_to=move_to, move_files=move_files)
    except libraries.LibraryError as exc:
        raise _refused(exc) from exc


class MoveBody(BaseModel):
    anime_ids: list[str] = Field(min_length=1, max_length=500)
    library_id: int
    #: False when the files are already in place and only the library changes.
    move_files: bool = True


class MoveResultResponse(BaseModel):
    anime_id: str
    name: str
    outcome: str
    detail: str | None = None


@router.post("/move", response_model=list[MoveResultResponse])
def move_to_library(body: MoveBody, db: Session = Depends(get_db)) -> list[MoveResultResponse]:
    """Move anime into another library: their folder goes with them."""
    from backend.app.services import library_move

    try:
        results = library_move.move_anime(db, body.anime_ids, body.library_id, move_files=body.move_files)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [
        MoveResultResponse(anime_id=r.anime_id, name=r.name, outcome=r.outcome, detail=r.detail)
        for r in results
    ]


class RefreshResultResponse(BaseModel):
    library_id: int
    name: str
    checked: int
    moved_in: list[str]
    moved_out: list[str]
    missing: list[str]
    unknown_folders: list[str]
    linked: list[str]
    files_found: int
    files_gone: int
    problems: list[str]


def _refreshed(results) -> list[RefreshResultResponse]:
    return [
        RefreshResultResponse(
            library_id=r.library_id, name=r.name, checked=r.checked, moved_in=r.moved_in,
            moved_out=r.moved_out, missing=r.missing, unknown_folders=r.unknown_folders, linked=r.linked,
            files_found=r.files_found, files_gone=r.files_gone, problems=r.problems,
        )
        for r in results
    ]


@router.post("/refresh", response_model=list[RefreshResultResponse])
def refresh_all_libraries(db: Session = Depends(get_db)) -> list[RefreshResultResponse]:
    """Check every library against the disk and fix the database. Moves no file."""
    from backend.app.services import library_refresh

    return _refreshed(library_refresh.refresh(db))


@router.post("/{library_id}/refresh", response_model=list[RefreshResultResponse])
def refresh_one_library(library_id: int, db: Session = Depends(get_db)) -> list[RefreshResultResponse]:
    """Check one library against the disk and fix the database. Moves no file."""
    from backend.app.services import library_refresh

    if db.get(Library, library_id) is None:
        raise HTTPException(status_code=404, detail="That library does not exist.")
    return _refreshed(library_refresh.refresh(db, [library_id]))


class ImportCandidateResponse(BaseModel):
    library_id: int
    library_name: str
    folder: str
    files: int
    episodes: list[int]
    #: The AniDB entry the folder's name settled on; null when it cannot be settled (the person picks one).
    anidb_id: int | None = None
    title: str | None = None
    #: The watchlist entry that already has that AniDB id; such a folder is shown but cannot be imported.
    tracked_as: str | None = None


class ImportBody(BaseModel):
    library_id: int
    folder: str
    anidb_id: int = Field(ge=1)


class ImportResultResponse(BaseModel):
    folder: str
    outcome: str
    detail: str | None = None
    anime_id: str | None = None
    name: str | None = None
    files: int = 0


class AnidbMatchResponse(BaseModel):
    aid: int
    title: str
    romaji: str | None = None
    native: str | None = None
    #: Which search found it: ``full`` (the name as typed), ``clean`` (no season marker, group or year) or
    #: ``shorter`` (the beginning of it) - and the text that search used.
    via: str = "full"
    via_text: str | None = None


@router.get("/import-candidates", response_model=list[ImportCandidateResponse])
def import_candidates(db: Session = Depends(get_db)) -> list[ImportCandidateResponse]:
    """The folders in the libraries that no anime has, each with the AniDB entry its name settles on (if any)."""
    from backend.app.services import folder_import

    return [ImportCandidateResponse(**vars(candidate)) for candidate in folder_import.candidates(db)]


@router.get("/import-search", response_model=list[AnidbMatchResponse])
def import_search(q: str = Query(min_length=2, max_length=120)) -> list[AnidbMatchResponse]:
    """AniDB entries whose names contain the text - for a folder whose name settled on nothing."""
    from backend.app.services import folder_import

    return [AnidbMatchResponse(**row) for row in folder_import.search(q)]


@router.post("/import-folder", response_model=ImportResultResponse)
def import_folder(body: ImportBody, db: Session = Depends(get_db)) -> ImportResultResponse:
    """Make one folder a watchlist entry. Records the files where they are; no file is moved or downloaded."""
    from backend.app.services import folder_import

    return ImportResultResponse(**vars(folder_import.import_folder(db, body.library_id, body.folder, body.anidb_id)))
