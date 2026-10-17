"""Libraries: named target folders (build 192, 0.8.5).

See docs/libraries-plan.md. This is build 1: the libraries exist, the user can
make and edit them, and a folder picker can show what is mounted. Nothing here
moves a file or changes where an anime lives - that is builds 2 to 4.

Two rules that keep it safe:

* A library's folder must be an existing directory under the browse root and
  not one of the container's own directories. A library folder is somewhere
  AniFlow will later write media, so ``/etc`` must not be pickable.
* The system libraries always exist and are repaired, not recreated: reading
  the list seeds any that are missing, so a database that predates them (or a
  hand-edited one) heals itself.
"""
from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.models.anime import Anime
from backend.app.models.library import (
    SYSTEM_AIRING_NOW,
    SYSTEM_EARLIER,
    SYSTEM_UPCOMING,
    Library,
)
from backend.app.services.storage import safe_relative

#: Top-level directories of the container that are never a library: the
#: operating system, AniFlow's own code, and the mounts that already have a
#: job. Everything else mounted (``/media``, ``/series1``, ``/mnt/x``) is fair.
HIDDEN_TOP_LEVEL = frozenset(
    {
        "app", "backend", "frontend", "bin", "boot", "dev", "etc", "home", "lib",
        "lib32", "lib64", "libx32", "opt", "proc", "root", "run", "sbin", "sys",
        "tmp", "usr", "var", "config", "data", "downloads",
    }
)

ADULT_VALUES = ("adult", "not_adult")
SEASON_VALUES = ("current", "past")

#: Upcoming is the one special library: it holds anime that have not started,
#: nothing is downloaded for them yet, so it has no folder - and it cannot be
#: removed or renamed. Every other library is the user's own.
UPCOMING_NAME = "Upcoming"

#: What a fresh install gets besides Upcoming: one library with a folder, which
#: is also the default. Its season tag is a hint only (see ``fits_season``).
FIRST_LIBRARY_NAME = "Airing now"

#: Until build 201 these two were built in. They are ordinary libraries now;
#: an installation that has them keeps them, with the tag they stood for.
_FORMER_SYSTEM = {SYSTEM_AIRING_NOW: "current", SYSTEM_EARLIER: "past"}


class LibraryError(ValueError):
    """The request cannot be honoured; the message says why, for the user."""


def browse_root() -> Path:
    return Path(get_settings().library_browse_root)


def _is_pickable(path: Path) -> bool:
    relative = safe_relative(browse_root(), path)
    if relative is None:
        return False
    parts = relative.parts
    return not (parts and parts[0].lower() in HIDDEN_TOP_LEVEL)


@dataclass(frozen=True)
class FolderEntry:
    name: str
    path: str
    writable: bool


@dataclass(frozen=True)
class Browsing:
    path: str
    parent: str | None
    entries: list[FolderEntry]
    #: False at the browse root and inside a hidden directory, where nothing
    #: may be chosen.
    selectable: bool
    writable: bool


def browse(path: str | None = None) -> Browsing:
    """The sub-folders of ``path`` (the browse root when omitted)."""
    root = browse_root()
    target = Path(os.path.normpath(path)) if path else root
    if not _is_pickable(target):
        raise LibraryError("That folder cannot be used as a library.")
    if not target.is_dir():
        raise LibraryError("That folder does not exist.")
    at_root = safe_relative(root, target) == Path()
    entries: list[FolderEntry] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise LibraryError(f"That folder cannot be read: {exc.strerror or exc}") from exc
    for child in children:
        if child.name.startswith(".") or not child.is_dir():
            continue
        if not _is_pickable(child):
            continue
        entries.append(FolderEntry(child.name, str(child), os.access(child, os.W_OK)))
    parent = None if at_root else str(target.parent)
    return Browsing(
        path=str(target),
        parent=parent,
        entries=entries,
        selectable=not at_root,
        writable=os.access(target, os.W_OK),
    )


def check_folder(folder: str) -> Path:
    """The folder as a normalised path, or ``LibraryError`` saying what is wrong."""
    if not folder or not folder.strip():
        raise LibraryError("Choose a folder.")
    path = Path(os.path.normpath(folder.strip()))
    if not _is_pickable(path) or safe_relative(browse_root(), path) == Path():
        raise LibraryError("That folder cannot be used as a library.")
    if not path.is_dir():
        raise LibraryError("That folder does not exist in AniFlow's container - is it mounted?")
    return path


def folder_is_writable(folder: str) -> bool:
    return os.access(folder, os.W_OK)


def _first_with_folder(db: Session) -> Library | None:
    return db.scalar(
        select(Library).where(Library.folder != "").order_by(Library.sort_order, Library.id).limit(1)
    )


def ensure_system(db: Session) -> None:
    """Make sure Upcoming exists and that there is at least one library with a folder.

    A fresh install gets "Airing now" (the default) and Upcoming. Nothing else is
    seeded, and nothing is ever re-created because the user removed it.
    """
    # The two libraries that used to be built in become ordinary ones, tagged.
    for library in db.scalars(select(Library).where(Library.key.in_(tuple(_FORMER_SYSTEM)))):
        library.fits_season = library.fits_season or _FORMER_SYSTEM[library.key]
        library.key = None
    db.flush()

    created_upcoming = False
    if db.scalar(select(Library).where(Library.key == SYSTEM_UPCOMING)) is None:
        db.add(Library(key=SYSTEM_UPCOMING, name=UPCOMING_NAME, folder="", sort_order=2, is_default=False))
        created_upcoming = True
    if _first_with_folder(db) is None:
        db.add(
            Library(
                name=FIRST_LIBRARY_NAME,
                folder=str(get_settings().anime_library_root),
                sort_order=0,
                is_default=True,
                fits_season="current",
            )
        )
    db.flush()
    if db.scalar(select(func.count()).select_from(Library).where(Library.is_default.is_(True))) == 0:
        first = _first_with_folder(db)
        if first is not None:
            first.is_default = True
    db.flush()
    if created_upcoming:
        _file_future_entries(db)
    _retire_the_old_libraries(db)
    _assign_the_unassigned(db)
    db.commit()


#: Built-in libraries that no longer exist. "Needs attention" was where strays
#: were to wait; nothing ever needed it, and a place nobody looks at is not a fix.
_RETIRED_KEYS = ("needs_attention",)


def _retire_the_old_libraries(db: Session) -> None:
    """Remove a retired built-in library, handing its anime to the default one.

    Idempotent, and run with the rest of the start-up setup, so a database that
    still has one loses it the next time anything reads the libraries.
    """
    default = db.scalar(select(Library).where(Library.is_default.is_(True)))
    if default is None:
        return
    for library in db.scalars(select(Library).where(Library.key.in_(_RETIRED_KEYS))):
        if library.id == default.id:
            continue
        db.execute(sql_update(Anime).where(Anime.library_id == library.id).values(library_id=default.id))
        db.delete(library)
    db.flush()


def upcoming_library(db: Session) -> Library:
    """The built-in library for anime that have not started."""
    ensure_system(db)
    return db.scalar(select(Library).where(Library.key == SYSTEM_UPCOMING))  # type: ignore[return-value]


def _file_future_entries(db: Session) -> None:
    """The first time Upcoming exists, the future releases already on the watchlist go in it.

    Once, when the library is made - not on every read, or a future release the
    user deliberately moved elsewhere would be pulled back.
    """
    upcoming = db.scalar(select(Library).where(Library.key == SYSTEM_UPCOMING))
    if upcoming is None:
        return
    db.execute(sql_update(Anime).where(Anime.entry_mode == "future").values(library_id=upcoming.id))


def _assign_the_unassigned(db: Session) -> None:
    """Anime that predate libraries go to the default one, so nothing moves.

    The default library starts out pointing at the media root they already
    live under. Idempotent: only rows with no library are touched.
    """
    default = db.scalar(select(Library).where(Library.is_default.is_(True)))
    if default is None:
        return
    db.execute(sql_update(Anime).where(Anime.library_id.is_(None)).values(library_id=default.id))


def folders_of_all_libraries(db: Session) -> list[Path]:
    """Every distinct library folder, for searches that must look everywhere.

    Shoko renames and moves files, so "where is this anime" is answered by
    looking in all of them, not only in the one the anime was given.
    """
    ensure_system(db)
    seen: dict[str, Path] = {}
    for (folder,) in db.execute(select(Library.folder).where(Library.folder != "").order_by(Library.sort_order)):
        seen.setdefault(folder, Path(folder))
    return list(seen.values())


def covers_a_library(path: Path, folders: Iterable[Path]) -> bool:
    """Whether ``path`` is, or contains, the folder of a library.

    With one ``/media`` mount the libraries are its subfolders, so a library's
    folder (``/media``) holds other libraries' folders (``/media/adult_shows``).
    Those are never anime folders and never "unknown" ones.
    """
    return any(safe_relative(path, folder) is not None for folder in folders)


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.normpath(path.absolute()))


def list_libraries(db: Session) -> list[Library]:
    ensure_system(db)
    rows = list(db.scalars(select(Library)))
    # The default first (so it is first in every picker), the special Upcoming last, and the user's own
    # order.
    rows.sort(key=lambda lib: (not lib.is_default, lib.is_system, lib.sort_order, lib.name.lower()))
    return rows


def default_library(db: Session) -> Library:
    ensure_system(db)
    return db.scalar(select(Library).where(Library.is_default.is_(True)))  # type: ignore[return-value]


def _clean_name(db: Session, name: str, *, ignore_id: int | None = None) -> str:
    clean = " ".join((name or "").split())
    if not clean:
        raise LibraryError("Give the library a name.")
    if len(clean) > 80:
        raise LibraryError("The name is too long (80 characters at most).")
    others = db.scalars(select(Library).where(func.lower(Library.name) == clean.lower()))
    if any(other.id != ignore_id for other in others):
        raise LibraryError(f'There is already a library called "{clean}".')
    return clean


def _clean_fits(adult: str | None, season: str | None) -> dict[str, str | None]:
    for value, allowed, label in (
        (adult, ADULT_VALUES, "adult"),
        (season, SEASON_VALUES, "season"),
    ):
        if value not in (None, "") and value not in allowed:
            raise LibraryError(f"Unknown {label} value: {value}.")
    return {
        "fits_adult": adult or None,
        "fits_season": season or None,
    }


def create(
    db: Session,
    *,
    name: str,
    folder: str,
    adult: str | None = None,
    season: str | None = None,
    shoko_folder_id: int | None = None,
) -> Library:
    ensure_system(db)
    clean = _clean_name(db, name)
    path = check_folder(folder)
    fits = _clean_fits(adult, season)
    top = db.scalar(select(func.max(Library.sort_order))) or 0
    library = Library(
        name=clean,
        folder=str(path),
        sort_order=max(top, 9) + 1,
        shoko_folder_id=shoko_folder_id,
        **fits,
    )
    db.add(library)
    db.commit()
    return library


def update(
    db: Session,
    library_id: int,
    *,
    name: str | None = None,
    folder: str | None = None,
    adult: str | None = None,
    season: str | None = None,
    shoko_folder_id: int | None = None,
    clear_shoko_folder: bool = False,
    fits_given: bool = False,
) -> Library:
    library = db.get(Library, library_id)
    if library is None:
        raise LibraryError("That library does not exist.")
    if name is not None and name != library.name:
        if library.is_system:
            raise LibraryError("Upcoming cannot be renamed.")
        library.name = _clean_name(db, name, ignore_id=library.id)
    if folder is not None:
        if not library.has_folder:
            raise LibraryError("This library has no folder - it holds anime that have not started yet.")
        library.folder = str(check_folder(folder))
    if fits_given:
        for column, value in _clean_fits(adult, season).items():
            setattr(library, column, value)
    if shoko_folder_id is not None and not library.has_folder:
        raise LibraryError("This library has no folder, so there is no Shoko folder to link it to.")
    if clear_shoko_folder:
        library.shoko_folder_id = None
    elif shoko_folder_id is not None:
        library.shoko_folder_id = shoko_folder_id
    db.commit()
    return library


def shift(db: Session, library_id: int, direction: str) -> None:
    """Move a library one place up or down among the user's own (build 204).

    The default stays first and Upcoming last whatever the numbers say, so only
    the libraries between them can change places; a move that would cross
    either is a no-op. The numbers are rewritten 10, 20, 30... so there is
    always room, and the same order is what every picker shows.
    """
    if direction not in ("up", "down"):
        raise LibraryError("Move a library up or down.")
    rows = [lib for lib in list_libraries(db) if not lib.is_system]
    ids = [lib.id for lib in rows]
    if library_id not in ids:
        raise LibraryError("That library does not exist or cannot be moved.")
    here = ids.index(library_id)
    there = here - 1 if direction == "up" else here + 1
    if 0 <= there < len(rows) and not rows[here].is_default and not rows[there].is_default:
        rows[here], rows[there] = rows[there], rows[here]
    for index, lib in enumerate(rows):
        lib.sort_order = (index + 1) * 10
    db.commit()


def make_default(db: Session, library_id: int) -> Library:
    library = db.get(Library, library_id)
    if library is None:
        raise LibraryError("That library does not exist.")
    if not library.has_folder:
        raise LibraryError("A library without a folder cannot be the default - new anime need somewhere to put their files.")
    for other in db.scalars(select(Library).where(Library.is_default.is_(True))):
        other.is_default = False
    library.is_default = True
    db.commit()
    return library


def anime_counts(db: Session) -> dict[int, int]:
    """How many anime live in each library, counted from what is stored."""
    return {
        library_id: count
        for library_id, count in db.execute(
            select(Anime.library_id, func.count()).where(Anime.library_id.is_not(None)).group_by(Anime.library_id)
        )
    }


def space_counts(db: Session) -> dict[int, tuple[int, int | None, int | None]]:
    """Per library: (bytes its files take, bytes free on its disk, bytes the disk holds).

    What the files take is what AniFlow recorded for each episode it placed or adopted - one query, exact for
    its own files, and no walk of a folder tree that may be terabytes on a network share. The free and total
    figures belong to the *disk*, so two libraries on one volume show the same free space.
    """
    import shutil

    from backend.app.models.anime import Episode

    used = {
        library_id: int(total or 0)
        for library_id, total in db.execute(
            select(Anime.library_id, func.sum(Episode.media_size_bytes))
            .join(Episode, Episode.anime_id == Anime.id)
            .where(Anime.library_id.is_not(None))
            .group_by(Anime.library_id)
        )
    }
    result: dict[int, tuple[int, int | None, int | None]] = {}
    for library in list_libraries(db):
        free = total = None
        if library.has_folder and os.path.isdir(library.folder):
            try:
                usage = shutil.disk_usage(library.folder)
                free, total = int(usage.free), int(usage.total)
            except OSError:
                pass
        result[library.id] = (used.get(library.id, 0), free, total)
    return result


def disk_counts(db: Session) -> dict[int, tuple[int, int]]:
    """Per library: (anime whose folder is really here, folders really here).

    Read from the disk each time - it is what the card shows next to the number
    of anime, so a difference between the two is visible without opening
    anything. A library whose folder cannot be read counts as nothing here.
    """
    every_folder = [Path(lib.folder) for lib in db.scalars(select(Library)) if lib.has_folder]
    # Resolved once: covers_a_library() resolves both paths on every call, and doing that for every
    # subfolder of every library is thousands of stats - seconds on a Windows bind mount.
    every_resolved = [_resolved(folder) for folder in every_folder]
    stored: dict[int, list[str]] = defaultdict(list)
    for library_id, folder in db.execute(
        select(Anime.library_id, Anime.media_folder).where(Anime.library_id.is_not(None))
    ):
        if folder:
            stored[library_id].append(folder)
    counts: dict[int, tuple[int, int]] = {}
    for library in db.scalars(select(Library)):
        if not library.has_folder:
            counts[library.id] = (0, 0)
            continue
        try:
            parent = _resolved(Path(library.folder))
            names = set()
            # scandir's is_dir() uses the type the directory listing already carries: no stat per entry.
            with os.scandir(library.folder) as entries:
                for entry in entries:
                    if entry.name.startswith(".") or not entry.is_dir():
                        continue
                    here = parent / entry.name
                    if entry.is_symlink():
                        if covers_a_library(Path(entry.path), every_folder):
                            continue
                    elif any(here == other or here in other.parents for other in every_resolved):
                        continue
                    names.add(entry.name)
        except OSError:
            names = set()
        counts[library.id] = (sum(1 for folder in stored[library.id] if folder in names), len(names))
    return counts


def delete(
    db: Session, library_id: int, *, move_to: int | None = None, move_files: bool = False
) -> None:
    """Remove a library. Its anime must go somewhere first (build 194).

    They used to fall to the default library without a word, which is rarely
    where somebody who has just repointed a folder means them to be. Now the
    caller says where: ``move_to``, and whether the files go too
    (``move_files``) or are already in place and only the library changes.
    If any of them cannot be moved the library stays, so nothing is stranded.
    """
    library = db.get(Library, library_id)
    if library is None:
        raise LibraryError("That library does not exist.")
    if library.is_system:
        raise LibraryError("Upcoming cannot be removed.")
    if library.has_folder and db.scalar(
        select(func.count()).select_from(Library).where(Library.folder != "", Library.id != library.id)
    ) == 0:
        raise LibraryError("AniFlow needs at least one library with a folder. Add another one before removing this one.")
    count = anime_counts(db).get(library.id, 0)
    if count:
        if move_to is None:
            raise LibraryError(f"{count} anime live in this library; choose where they go first.")
        if move_to == library.id:
            raise LibraryError("They cannot go to the library being removed.")
        from backend.app.services import library_move

        ids = [anime_id for (anime_id,) in db.execute(select(Anime.id).where(Anime.library_id == library.id))]
        results = library_move.move_anime(db, ids, move_to, move_files=move_files)
        refused = [r for r in results if r.outcome == "refused"]
        if refused:
            raise LibraryError(
                f"{len(refused)} of {count} anime could not be moved ({refused[0].name}: {refused[0].detail}); "
                "the library was not removed."
            )
    was_default = library.is_default
    db.delete(library)
    db.flush()
    if was_default:
        heir = _first_with_folder(db)
        if heir is not None:
            heir.is_default = True
    db.commit()
