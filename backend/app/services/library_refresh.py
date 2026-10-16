"""Refresh a library from what is actually on the disk (build 195).

Somebody moves a folder by hand, deletes one, drops a new one in: the database
no longer matches the disk. *"Refresh is scanning the folder and checks which
animes are in there and fixes the database by checking what is actually on the
harddisk, this is helpful if user moved something manually."*

**Only the database changes. No file is moved, renamed or deleted** - the disk
is the authority here, and this is how the database catches up with it.

What it does, in order:

1. **Where is each anime?** An anime whose folder is no longer in its library's
   folder is looked for in the other libraries' folders. Found in exactly one
   place that exactly one library owns: the anime is filed in that library. Found
   nowhere: it is reported as not on disk, and left where it is. Found in
   several places, or in a folder several libraries share: reported, not guessed.
2. **What is in each folder?** Every anime of the library has its folder
   reconciled with its episodes (the existing folder scan): files found become
   episodes, files that are gone are noticed.
3. **Which folder is whose?** An anime with no folder - or whose stored folder is
   nowhere on disk (Shoko renamed it, an older adoption stored the wrong name) -
   is linked to the one folder nobody claims whose name matches one of its
   names *exactly* (case and punctuation aside). Nothing looser: two anime for
   one folder, or a folder with two candidates, are reported and left alone.
4. **What does AniFlow not know?** Folders in the library that no anime claims
   are listed - not adopted, not touched.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime
from backend.app.models.library import Library
from backend.app.services import libraries
from backend.app.services.attention import log_activity
from backend.app.services.library import scan_anime_folder
from backend.app.services.storage import roots_for

logger = for_component("Library")

#: How many names a result lists - a folder of hundreds is one finding.
MAX_NAMES = 50


@dataclass
class RefreshResult:
    library_id: int
    name: str
    #: Anime in this library once it has been sorted out.
    checked: int = 0
    moved_in: list[str] = field(default_factory=list)
    moved_out: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unknown_folders: list[str] = field(default_factory=list)
    #: Anime linked to a folder they were found to match by name ("Name -> folder").
    linked: list[str] = field(default_factory=list)
    files_found: int = 0
    files_gone: int = 0
    problems: list[str] = field(default_factory=list)


def _cap(names: list[str]) -> list[str]:
    return sorted(names, key=str.lower)[:MAX_NAMES]


def _name_of(anime: Anime) -> str:
    return anime.display_name or anime.local_name


def refresh(db: Session, library_ids: list[int] | None = None) -> list[RefreshResult]:
    """Refresh the given libraries (all of them when none are named)."""
    libraries.ensure_system(db)
    # A library without a folder (Upcoming) has nothing on a disk to check.
    everything = [lib for lib in db.scalars(select(Library).order_by(Library.sort_order, Library.name)) if lib.has_folder]
    targets = [lib for lib in everything if library_ids is None or lib.id in library_ids]
    results = {lib.id: RefreshResult(lib.id, libraries_name(lib)) for lib in targets}

    by_path: dict[Path, list[Library]] = defaultdict(list)
    for lib in everything:
        by_path[Path(lib.folder)].append(lib)

    _relocate(db, by_path, results)
    _link_by_name(db, by_path, results)

    for lib in targets:
        _look_inside(db, lib, results[lib.id])
    db.commit()
    return [results[lib.id] for lib in targets]


def libraries_name(library: Library) -> str:
    """The name shown to people: the built-in ones are named by their key."""
    return library.name


def _relocate(db: Session, by_path: dict[Path, list[Library]], results: dict[int, RefreshResult]) -> None:
    """Step 1: file each anime in the library whose folder holds it."""
    for anime in db.scalars(select(Anime).where(Anime.media_folder.is_not(None))):
        folder = anime.media_folder
        own = anime.library
        if own is not None and not own.has_folder:
            continue  # a library with no folder has nothing to be found in
        own_root = roots_for(anime).media_root
        if (own_root / folder).is_dir():
            continue

        homes = {path for path in by_path if path != own_root and (path / folder).is_dir()}
        if not homes:
            if own is not None and own.id in results:
                results[own.id].missing.append(_name_of(anime))
            continue

        owner_hint = results.get(own.id) if own is not None else None
        if len(homes) > 1:
            if owner_hint is not None:
                owner_hint.problems.append(
                    f"{_name_of(anime)}: its folder \"{folder}\" is in several libraries' folders - not guessed"
                )
            continue
        owners = by_path[next(iter(homes))]
        if len(owners) != 1:
            if owner_hint is not None:
                owner_hint.problems.append(
                    f"{_name_of(anime)}: found in a folder {len(owners)} libraries share - cannot tell which"
                )
            continue

        new = owners[0]
        before = own.name if own is not None else None
        anime.library_id = new.id
        anime.library = new
        log_activity(
            db,
            kind="LIBRARY_MOVED",
            message=(f"Found in {new.name} on disk instead of {before}; the library was updated"
                     if before else f"Found in {new.name} on disk; the library was updated"),
            details={"from": before, "to": new.name, "outcome": "refresh"},
            anime_id=anime.id,
        )
        logger.info("refresh: %s is in %s on disk (was %s)", _name_of(anime), new.name, before)
        if new.id in results:
            results[new.id].moved_in.append(_name_of(anime))
        if own is not None and own.id in results:
            results[own.id].moved_out.append(_name_of(anime))


def _link_by_name(db: Session, by_path: dict[Path, list[Library]], results: dict[int, RefreshResult]) -> None:
    """Step 3: give an anime without a findable folder the one folder that is plainly its own."""
    from backend.app.services.library_match import _key, names_of

    anime_rows = list(db.scalars(select(Anime)))
    claimed: dict[Path, set[str]] = defaultdict(set)
    for anime in anime_rows:
        if anime.media_folder and (roots_for(anime).media_root / anime.media_folder).is_dir():
            claimed[roots_for(anime).media_root].add(anime.media_folder)

    # Every folder in a library folder that no anime has - other libraries' folders are not anime folders.
    every_folder = list(by_path)
    unclaimed: list[tuple[Path, str]] = []
    for path in by_path:
        try:
            entries = [p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
        except OSError:
            continue
        unclaimed += [
            (path, name) for name in entries
            if name not in claimed[path] and not libraries.covers_a_library(path / name, every_folder)
        ]

    # Anime whose folder cannot be found: none stored, or stored but nowhere.
    wanting = [
        anime for anime in anime_rows
        if not anime.media_folder
        or not any((path / anime.media_folder).is_dir() for path in by_path)
    ]

    # One exact match each way, or nothing: folder -> anime and anime -> folder.
    matches: dict[Anime, list[tuple[Path, str]]] = {}
    takers: dict[tuple[Path, str], list[Anime]] = defaultdict(list)
    for anime in wanting:
        keys = {_key(name) for name in names_of(anime)} - {""}
        found = [(path, name) for path, name in unclaimed if _key(name) in keys]
        matches[anime] = found
        for item in found:
            takers[item].append(anime)

    for anime, found in matches.items():
        if len(found) != 1:
            continue
        path, name = found[0]
        if len(takers[(path, name)]) != 1:
            _note(results, anime, f"{_name_of(anime)}: more than one anime match the folder \"{name}\" - not guessed")
            continue
        owners = by_path[path]
        own_root = roots_for(anime).media_root
        if len(owners) != 1 and own_root != path:
            _note(results, anime, f"{_name_of(anime)}: its folder \"{name}\" is in a folder {len(owners)} libraries share - cannot tell which")
            continue

        before = anime.library
        anime.media_folder = name[:255]
        if own_root != path:
            new = owners[0]
            anime.library_id = new.id
            anime.library = new
            log_activity(
                db,
                kind="LIBRARY_MOVED",
                message=f"Found its folder \"{name}\" in {new.name}; the library was updated",
                details={"from": before.name if before else None, "to": new.name, "outcome": "refresh"},
                anime_id=anime.id,
            )
            holder = new
            if before is not None and before.id in results:
                results[before.id].moved_out.append(_name_of(anime))
            if new.id in results:
                results[new.id].moved_in.append(_name_of(anime))
        else:
            holder = before if before is not None else owners[0]
        if holder is not None and holder.id in results:
            results[holder.id].linked.append(f"{_name_of(anime)} -> {name}")
        # What was reported as missing a moment ago is not any more.
        for result in results.values():
            if _name_of(anime) in result.missing:
                result.missing.remove(_name_of(anime))
        logger.info("refresh: %s linked to its folder %s", _name_of(anime), name)


def _note(results: dict[int, RefreshResult], anime: Anime, text: str) -> None:
    own = anime.library
    if own is not None and own.id in results:
        results[own.id].problems.append(text)


def unclaimed_folders(db: Session, library: Library, claimed: set[str] | None = None) -> list[str]:
    """The folders in this library's folder that no anime has, in the order the disk gives them (build 254).

    A folder that belongs to an anime of *any* library sharing this folder is known, and so is one that holds
    another library's folder. Refresh lists the rest as unknown; Import offers to turn them into entries -
    one answer, so the two cannot disagree about which folders are unknown. Raises ``OSError`` when the
    folder cannot be read.
    """
    root = Path(library.folder)
    known = {name.casefold() for name in (claimed or set())}
    for other in db.scalars(select(Anime).where(Anime.media_folder.is_not(None))):
        if roots_for(other).media_root == root:
            known.add(other.media_folder.casefold())
    entries = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    every_folder = libraries.folders_of_all_libraries(db)
    return [
        p.name for p in entries
        if p.name.casefold() not in known and not libraries.covers_a_library(p, every_folder)
    ]


def _look_inside(db: Session, library: Library, result: RefreshResult) -> None:
    """Steps 2 and 3: reconcile each anime's folder, and list what nobody claims."""
    root = Path(library.folder)
    if not root.is_dir():
        result.problems.append("The folder does not exist in AniFlow's container - is it mounted?")
        return

    claimed: set[str] = set()
    anime_here = list(db.scalars(select(Anime).where(Anime.library_id == library.id)))
    result.checked = len(anime_here)
    for anime in anime_here:
        if not anime.media_folder:
            continue
        claimed.add(anime.media_folder.casefold())
        if not (root / anime.media_folder).is_dir():
            # Already reported as missing by the relocation step; nothing to scan.
            if _name_of(anime) not in result.missing:
                result.missing.append(_name_of(anime))
            continue
        try:
            scanned = scan_anime_folder(db, anime)
        except (FileNotFoundError, OSError) as exc:
            result.problems.append(f"{_name_of(anime)}: {exc}")
            continue
        result.files_found += len(scanned["episodes_found"])  # type: ignore[arg-type]
        result.files_gone += int(scanned["vanished"])  # type: ignore[arg-type]

    try:
        result.unknown_folders = _cap(unclaimed_folders(db, library, claimed))
    except OSError as exc:
        result.problems.append(f"The folder could not be read: {exc.strerror or exc}")
        return
    result.moved_in = _cap(result.moved_in)
    result.moved_out = _cap(result.moved_out)
    result.missing = _cap(result.missing)
    result.linked = _cap(result.linked)
