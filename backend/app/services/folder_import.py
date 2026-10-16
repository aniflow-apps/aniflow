"""Turn the folders a library already holds into watchlist entries (build 254).

Reported while testing the install wizard on the Pi: a library that already has anime in it ended up with an
empty watchlist, and Refresh could only list the folders it did not know. Nothing made them anime.

This is the missing step, and it is **explicit**: AniFlow never decides what is on the watchlist (libraries-plan),
so the person sees every folder, ticks the ones they want, and nothing happens until they confirm.

*Which anime is a folder?* The AniDB title list answers by exact name - the same fold and the same season
spellings (``S3``, ``Season 3``, ``3rd Season``, ``III``) every other lookup here uses, and the same vote that
refuses two shows sharing a name. A folder it cannot settle is *unmatched*: the person picks the anime from a
search, or leaves it. Nothing fuzzy, ever (the user declined that more than once).

*What does an import do?* It reads the folder and records the files where they are. No file is moved, renamed
or downloaded. The entry is built the way Shoko adoption builds one (an AniDB id, AniDB's names, the calendar's
data), filed in the library the folder is in, and its release name and profile are learned from the file names
(``shoko_adopt._fill_profile``) so the feed carries on with the same group and quality. Automate is switched on
only for a show that is not finished - still airing, or with episodes missing - and only when the names taught
something to match releases by; a finished, complete show is marked Complete and left alone.

Shoko-linked libraries are not offered: Shoko's own adoption covers those.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle
from backend.app.models.library import Library

logger = for_component("Library")

#: How many episode numbers a candidate lists - a folder of hundreds is one finding.
MAX_EPISODES_SHOWN = 60


@dataclass
class FolderCandidate:
    library_id: int
    library_name: str
    folder: str
    #: Video files found in the folder (its season subfolders included).
    files: int = 0
    episodes: list[int] = field(default_factory=list)
    #: The AniDB entry the name settled on, with its names; ``None`` when the name cannot be settled.
    anidb_id: int | None = None
    title: str | None = None
    #: When the AniDB entry is already on the watchlist: that entry's name. Such a folder is not offered.
    tracked_as: str | None = None


@dataclass
class ImportResult:
    folder: str
    #: ``imported`` | ``skipped`` | ``failed``
    outcome: str
    detail: str | None = None
    anime_id: str | None = None
    name: str | None = None
    files: int = 0


def _importable_libraries(db: Session) -> list[Library]:
    """Libraries with a folder that AniFlow writes to itself - not the ones Shoko is linked to."""
    return [
        library
        for library in db.scalars(select(Library).order_by(Library.sort_order, Library.name))
        if library.has_folder and library.shoko_folder_id is None
    ]


def candidates(db: Session) -> list[FolderCandidate]:
    """Every folder in an importable library that no anime has, with what the title list says about it."""
    from backend.app.services import anidb_titles, library_refresh
    from backend.app.services.library import episode_numbers_in, media_files_below

    found: list[FolderCandidate] = []
    for library in _importable_libraries(db):
        root = Path(library.folder)
        try:
            names = library_refresh.unclaimed_folders(db, library)
        except OSError as exc:
            logger.info("import: %s could not be read: %s", library.folder, exc)
            continue
        for name in sorted(names, key=str.lower):
            try:
                files = media_files_below(root / name)
            except OSError:
                files = []
            numbers = sorted({n for n in episode_numbers_in([f.name for f in files]) if n is not None})
            found.append(
                FolderCandidate(
                    library_id=library.id,
                    library_name=library.name,
                    folder=name,
                    files=len(files),
                    episodes=numbers[:MAX_EPISODES_SHOWN],
                )
            )
    # A folder with no video in it is not an anime that exists on disk - an empty leftover, a folder of
    # extras. Not offered; Refresh still lists it among the unknown.
    found = [candidate for candidate in found if candidate.files > 0]

    ids = anidb_titles.resolve_many([[candidate.folder] for candidate in found])
    named = anidb_titles.titles_for_many([aid for aid in ids if aid])
    tracked = {
        int(aid): name
        for aid, name in db.execute(
            select(Anime.anidb_id, Anime.display_name).where(Anime.anidb_id.is_not(None))
        )
    }
    for candidate, aid in zip(found, ids):
        if aid is None:
            continue
        candidate.anidb_id = aid
        titles = named.get(aid)
        candidate.title = (titles.english or titles.primary) if titles else None
        candidate.tracked_as = tracked.get(aid)
    return found


#: A season marker at the end of a name, as a person or a release group writes it.
_SEASON_SUFFIX = re.compile(
    r"\s+(?:s\d{1,2}|seasons?\s+\d{1,2}|\d{1,2}(?:st|nd|rd|th)\s+seasons?|(?:part|cour)\s+\d{1,2}|"
    r"ii|iii|iv|vi|vii|viii|ix)\s*$",
    re.IGNORECASE,
)

#: A shortened search never goes below this many words or letters: two words of a title still point somewhere,
#: one common word points at thousands.
_MIN_WORDS = 2
_MIN_LETTERS = 8


def clean_name(query: str) -> str:
    """A folder's name without what a title never contains: [Group], (year), a trailing season marker."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", query)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    stripped = _SEASON_SUFFIX.sub("", text).strip(" -")
    return stripped or text


def _query_chain(query: str):
    """The searches to try, in order, each as ``(kind, text)``: the name as typed, the clean name, then shorter ones.

    A search is "which anime might you mean?" and the person picks, so it may be forgiving where a match may not:
    it stops at the first search that finds anything, and says which one it was.
    """
    from backend.app.services import anidb_titles

    yield "full", query
    seen = {anidb_titles.normalize(query)}
    clean = clean_name(query)
    if anidb_titles.normalize(clean) not in seen:
        seen.add(anidb_titles.normalize(clean))
        yield "clean", clean
    words = clean.split()
    while len(words) > _MIN_WORDS:
        words = words[:-1]
        shorter = " ".join(words)
        form = anidb_titles.normalize(shorter)
        if len(form.replace(" ", "")) < _MIN_LETTERS:
            return
        if form not in seen:
            seen.add(form)
            yield "shorter", shorter


def search(query: str, limit: int = 20) -> list[dict[str, object]]:
    """AniDB entries whose names contain ``query`` - what the person picks from for an unmatched folder.

    The full name first; when that finds nothing, the clean name (no season marker, no group or year), and then
    shorter and shorter beginnings of it. Every row says which of those found it (``via``) and with what text
    (``via_text``), so the window can say "nothing matched the full name, this is the search for ...".
    """
    from backend.app.services import anidb_titles

    for via, text in _query_chain(query):
        aids = anidb_titles.search(text, limit=limit)
        named = anidb_titles.titles_for_many(aids)
        rows = [
            {
                "aid": aid,
                "title": named[aid].english or named[aid].primary,
                "romaji": named[aid].primary,
                "native": named[aid].native,
                "via": via,
                "via_text": text,
            }
            for aid in aids
            if aid in named
        ]
        if rows:
            return rows
    return []


def import_folder(db: Session, library_id: int, folder: str, anidb_id: int) -> ImportResult:
    """One folder as one watchlist entry. Never raises: the answer says what happened."""
    result = ImportResult(folder=folder, outcome="skipped")
    library = db.get(Library, library_id)
    if library is None or not library.has_folder or library.shoko_folder_id is not None:
        result.detail = "That library cannot take an import."
        return result
    # The folder must be a direct child of the library's folder and really be there: this name came from the
    # browser, and a path is never built from it without looking.
    if not folder or folder in (".", "..") or "/" in folder or "\\" in folder:
        result.detail = "That is not a folder name."
        return result
    path = Path(library.folder) / folder
    if not path.is_dir():
        result.detail = "The folder is no longer there."
        return result

    from backend.app.services import library_refresh

    try:
        if folder.casefold() not in {name.casefold() for name in library_refresh.unclaimed_folders(db, library)}:
            result.detail = "An anime already has this folder."
            return result
    except OSError as exc:
        result.detail = f"The library's folder could not be read: {exc.strerror or exc}"
        return result

    existing = db.scalar(select(Anime).where(Anime.anidb_id == int(anidb_id)))
    if existing is not None:
        result.detail = f"AniDB {anidb_id} is already on the watchlist as {existing.display_name}."
        return result

    try:
        _build(db, library, folder, int(anidb_id), result)
    except Exception as exc:  # noqa: BLE001 - one folder must not stop the rest
        logger.warning("import of %s failed: %s", folder, exc)
        result.outcome = "failed"
        result.detail = f"{type(exc).__name__}: {exc}"[:300]
    return result


def _build(db: Session, library: Library, folder: str, anidb_id: int, result: ImportResult) -> None:
    from backend.app.services.shoko_adopt import _adopt_one

    entry = SimpleNamespace(name=folder, anidb_id=anidb_id, total_episodes=None, shoko_id=None)
    anime = _adopt_one(db, entry)
    if anime is None:
        result.detail = (
            "AniFlow's AniDB title list does not know this id yet - it updates weekly."
            if not _has_titles(anidb_id)
            else "A watchlist entry with this name or id already exists."
        )
        return
    try:
        _fill_in(db, anime, library, folder, result)
    except Exception:
        db.rollback()
        _undo(db, anime)
        raise


def _fill_in(db: Session, anime: Anime, library: Library, folder: str, result: ImportResult) -> None:
    from backend.app.services import lifecycle_auto
    from backend.app.services.anime import _automate_new_anime, _basic_entry
    from backend.app.services.attention import log_activity
    from backend.app.services.library import scan_anime_folder
    from backend.app.services.shoko_adopt import _fill_profile

    anime.library_id = library.id
    anime.library = library
    anime.media_folder = folder[:255]
    # Basic when there is no indexer (a tracker), advanced when there is - the same answer an ordinary add
    # gets - and never a future entry: there are files.
    basic = _basic_entry(db, anime, unaired=False)
    # Whether anything outside the folder said how many episodes there are. The scan raises the count to the
    # highest file found, which on its own proves nothing: a show whose total is unknown is not "complete".
    count_known = bool(anime.effective_episode_count)
    scanned = scan_anime_folder(db, anime)
    names = [episode.media_filename for episode in anime.episodes if episode.media_filename]
    if names and not basic:
        _fill_profile(db, anime, names)

    anime.lifecycle = AnimeLifecycle.ACTIVE
    if count_known and lifecycle_auto.should_complete(anime, datetime.now(UTC).date()):
        anime.lifecycle = AnimeLifecycle.COMPLETE
        anime.automation_enabled = False
    else:
        # Still airing, or episodes missing - but only with something to match releases by.
        anime.automation_enabled = bool(anime.release_name) and not basic and _automate_new_anime()

    log_activity(
        db,
        kind="LIBRARY_ADOPTED",
        message=f"{anime.display_name}: imported from the folder \"{folder}\" in {library.name} ({len(names)} file(s))",
        details={"library": library.name, "folder": folder, "files": len(names), "outcome": "imported"},
        anime_id=anime.id,
    )
    db.commit()
    result.outcome = "imported"
    result.anime_id = anime.id
    result.name = anime.display_name
    result.files = int(scanned["files_scanned"])  # type: ignore[call-overload]
    logger.info("imported %s from %s/%s", anime.display_name, library.name, folder)


def _has_titles(anidb_id: int) -> bool:
    from backend.app.services import anidb_titles

    return bool(anidb_titles.names_for(anidb_id))


def _undo(db: Session, anime: Anime | None) -> None:
    """A half-built entry is removed again: the scan commits as it goes, so a late failure leaves one behind."""
    if anime is None:
        return
    try:
        db.delete(anime)
        db.commit()
    except Exception:  # noqa: BLE001 - nothing more can be done; the next Refresh will show it
        db.rollback()
