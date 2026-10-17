"""Scan an anime's folder and work out what the files are (build 198).

Asked for on the anime's page, next to "Fix missing episodes": *"scan the target
folder for files and try to find them and assign them to the episode list - the
actual filenames, the subgroup, the quality."*

It only reads. Nothing is downloaded, renamed or deleted, and nothing the user
set is overwritten: the subgroup and quality go into a profile only where it has
none, and the release name only where there is none.

Three steps:

1. **The folder.** The anime's own, if it exists. Otherwise the one folder in its
   library whose name matches one of its names *exactly* - never a similar one.
2. **The files.** Each media file is matched to its episode number and recorded
   on the episode (the existing folder scan), so the list says what is really
   there and what is gone.
3. **Who and how good.** The subgroup and the resolution are read from the file
   names; where a name has no resolution the file itself is measured.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime
from backend.app.services.storage import roots_for

logger = for_component("Library")


def _find_in_another_library(db: Session, anime: Anime):
    """The one other library whose folder holds this anime's folder by exact name, or None.

    The stored folder name first, then a folder named exactly like one of the
    anime's names. Two places, or a folder shared by several libraries, is not
    guessed. Moves the anime to that library in the database only.
    """
    from pathlib import Path

    from sqlalchemy import select

    from backend.app.models.library import Library
    from backend.app.services import library_match
    from backend.app.services.attention import log_activity

    own_root = roots_for(anime).media_root
    keys = {library_match._key(name) for name in library_match.names_of(anime)} - {""}
    homes: list[tuple[Library, str]] = []
    for library in db.scalars(select(Library).where(Library.folder != "")):
        path = Path(library.folder)
        if path == own_root:
            continue
        if anime.media_folder and (path / anime.media_folder).is_dir():
            homes.append((library, anime.media_folder))
            continue
        try:
            children = [child for child in path.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            if library_match._key(child.name) in keys:
                homes.append((library, child.name))
                break
    if len(homes) != 1:
        return None
    library, folder = homes[0]
    before = anime.library.name if anime.library is not None else None
    anime.library_id = library.id
    anime.library = library
    anime.media_folder = folder[:255]
    log_activity(
        db,
        kind="LIBRARY_MOVED",
        message=(f"Found in {library.name} by scanning the folder instead of {before}; the library was updated"
                 if before else f"Found in {library.name} by scanning the folder; the library was updated"),
        details={"from": before, "to": library.name, "outcome": "scan"},
        anime_id=anime.id,
    )
    logger.info("scan: %s is in %s on disk (was %s)", anime.display_name, library.name, before)
    return library


def scan_and_identify(db: Session, anime: Anime) -> dict[str, object]:
    """Link the folder, record its files, and find the subgroup and quality."""
    from backend.app.services import library_match
    from backend.app.services.library import scan_anime_folder
    from backend.app.services.release_parser import parse_release_name
    from backend.app.services.shoko_adopt import _fill_profile

    result: dict[str, object] = {
        "folder": anime.media_folder,
        "files_scanned": 0,
        "episodes_found": 0,
        "completed": 0,
        "created": 0,
        "vanished": 0,
        "found_subgroup": None,
        "found_resolution": None,
        "release_name": anime.release_name,
        "filled": [],
        "problems": [],
    }
    filled: list[str] = result["filled"]  # type: ignore[assignment]
    problems: list[str] = result["problems"]  # type: ignore[assignment]

    # A future entry whose files are already in a library is set up from them first (item 47).
    from backend.app.services.future_adopt import adopt_local_files

    adopt_local_files(db, anime)

    # Upcoming holds anime that have no files to find.
    if anime.library is not None and not anime.library.has_folder:
        problems.append(f"{anime.library.name} has no folder - there is nothing to scan.")
        return result

    # 1. the folder
    root = roots_for(anime).media_root
    if not (anime.media_folder and (root / anime.media_folder).is_dir()):
        # Not in its own library's folder: it may be in another one (a move that left it there, or
        # Shoko filed it there). Found by the exact name only, in exactly one place, and the anime
        # follows it - the database changes, no file does (build 207).
        home = _find_in_another_library(db, anime)
        if home is not None:
            result["folder"] = anime.media_folder
            filled.extend(["library"] + (["folder"] if "folder" not in filled else []))
            root = roots_for(anime).media_root
    if not (anime.media_folder and (root / anime.media_folder).is_dir()):
        exact = [c for c in library_match.candidates(anime, all_folders=True) if c.score == 100]
        if len(exact) == 1:
            anime.media_folder = exact[0].name[:255]
            result["folder"] = anime.media_folder
            filled.append("folder")
        else:
            problems.append(
                "No folder to scan: "
                + ("several folders match this anime's name exactly." if exact
                   else "none in this anime's library or in any other library is named like it.")
            )
            return result

    # 2. the files
    try:
        scanned = scan_anime_folder(db, anime)
    except (FileNotFoundError, OSError) as exc:
        problems.append(str(exc))
        return result
    result.update(
        files_scanned=scanned["files_scanned"],
        episodes_found=len(scanned["episodes_found"]),  # type: ignore[arg-type]
        completed=scanned["completed"],
        created=scanned["created"],
        vanished=scanned["vanished"],
    )
    if scanned["unparseable"]:
        problems.append(f"{scanned['unparseable']} file(s) have no episode number AniFlow can read.")
    if not scanned["files_scanned"]:
        problems.append("The folder has no video files.")

    # 3. who released it, and at what quality
    names = [episode.media_filename for episode in anime.episodes if episode.media_filename]
    groups: Counter[str] = Counter()
    heights: Counter[str] = Counter()
    for name in names:
        parsed = parse_release_name(name)
        if getattr(parsed, "subgroup", None):
            groups[parsed.subgroup] += 1
        if getattr(parsed, "resolution", None):
            heights[parsed.resolution] += 1
    if groups:
        result["found_subgroup"] = groups.most_common(1)[0][0]
    if heights:
        result["found_resolution"] = heights.most_common(1)[0][0]
    if names and _fill_profile(db, anime, names):
        filled.append("profile")
    if anime.release_name != result["release_name"]:
        filled.append("release_name")
        result["release_name"] = anime.release_name
    if names and not groups:
        problems.append("The file names carry no subgroup.")
    if names and not heights and anime.profile is not None and not anime.profile.resolution:
        problems.append("No quality in the file names, and the files could not be measured.")
    if filled or scanned["completed"] or scanned["created"]:
        logger.info("scanned %s: %s", anime.display_name, ", ".join(filled) or "files linked")
    db.commit()
    return result
