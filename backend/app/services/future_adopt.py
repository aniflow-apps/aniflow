"""A future entry whose files are already on disk becomes a real one (item 47).

An anime added before its season is a *future* entry: it sits in Upcoming, which has no folder, with no
release name and no profile. When the season starts and the first episode is already in a library - put
there by hand, by Shoko, by anything else - the entry kept asking the user to "choose a release" while
episode 1 sat in a folder next to it.

This looks for that folder (by an exact name, in exactly one library - never a similar one), records the
files, learns whatever the file names say (the group, the quality, the name releases are matched by) and
turns the entry into an ordinary one. What the names do not say stays empty: nothing is guessed. An entry
whose names teach nothing is left as it is, with its files recorded.

It only reads the disk. The anime moves to that library in the database; no file moves.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime
from backend.app.models.library import Library
from backend.app.services.storage import is_media_file

logger = for_component("Library")


def _home(db: Session, anime: Anime) -> tuple[Library, str] | None:
    """The one library folder that holds a folder named exactly like this anime and has media in it."""
    from backend.app.services import library_match

    keys = {library_match._key(name) for name in library_match.names_of(anime)} - {""}
    homes: list[tuple[Library, str]] = []
    for library in db.scalars(select(Library).where(Library.folder != "")):
        root = Path(library.folder)
        try:
            children = [child for child in root.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            if library_match._key(child.name) not in keys:
                continue
            try:
                has_media = any(entry.is_file() and is_media_file(entry) for entry in child.iterdir())
            except OSError:
                continue
            if has_media:
                homes.append((library, child.name))
    return homes[0] if len(homes) == 1 else None


def adopt_local_files(db: Session, anime: Anime) -> bool:
    """Set up this future entry from the files already on disk. Returns whether it was converted."""
    from backend.app.services import library_match  # noqa: F401 - imported for _home
    from backend.app.services.anime import _automate_new_anime, ensure_episode_rows
    from backend.app.services.app_mode import EntryMode
    from backend.app.services.attention import log_activity
    from backend.app.services.library import scan_anime_folder
    from backend.app.services.lifecycle_auto import activate_when_it_starts
    from backend.app.services.shoko_adopt import _fill_profile

    if anime.entry_mode != EntryMode.FUTURE.value:
        return False
    home = _home(db, anime)
    if home is None:
        return False
    library, folder = home

    before = (anime.library_id, anime.library, anime.media_folder)
    anime.library_id, anime.library, anime.media_folder = library.id, library, folder[:255]
    try:
        scan_anime_folder(db, anime)
    except (FileNotFoundError, OSError) as exc:
        logger.info("could not read %s for %s: %s", folder, anime.display_name, exc)
        anime.library_id, anime.library, anime.media_folder = before
        return False
    names = [episode.media_filename for episode in anime.episodes if episode.media_filename]
    if not names:
        anime.library_id, anime.library, anime.media_folder = before
        return False

    _fill_profile(db, anime, names)
    log_activity(
        db,
        kind="LIBRARY_MOVED",
        message=f"{anime.display_name}: its files were already in {library.name}, so it was set up from them",
        details={"to": library.name, "outcome": "adopted", "files": len(names)},
        anime_id=anime.id,
    )
    if not anime.release_name:
        # The names taught nothing to match releases by; the files are recorded, the entry waits for the user.
        db.commit()
        return False

    anime.entry_mode = EntryMode.ADVANCED.value
    anime.automation_enabled = _automate_new_anime()
    activate_when_it_starts(anime)
    ensure_episode_rows(db, anime, anime.effective_episode_count)
    db.commit()
    logger.info("%s was set up from the files already in %s", anime.display_name, library.name)
    return True
