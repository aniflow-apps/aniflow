"""Anime library folder linkage and folder scans.

Business rules implemented here (AGENTS.md):

- The media library is authoritative for physical file existence. A scan
  reconciles the database with what is actually on disk, in both directions:
  a file that appears makes an episode COMPLETE, a file that vanished makes it
  MISSING. Nothing else about the episode is touched.
- All filesystem paths are validated to stay inside the configured roots.
- Folders are created on explicit user action only, never implicitly.
- Folder names are stored on the anime (``media_folder``), never re-derived
  from the title at use time.
"""
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, Episode, EpisodeStatus, EpisodeType
from backend.app.services.anime import normalize_local_name
from backend.app.services.episode_state import refresh_episode_status
from backend.app.services.release_parser import parse_release_name
from backend.app.services.storage import (
    roots_for,
    MEDIA_SUFFIXES,
    is_media_file,
    media_folder_path,
    storage_roots,
)

logger = for_component("Library")

# Kept as a module-level name because callers imported it from here before the
# suffix set moved into services/storage.py.
VIDEO_SUFFIXES = MEDIA_SUFFIXES


def cleaned_release_folder(parsed) -> str:
    """Folder name derived from a parsed release: "<Title> S<season>"."""
    base = normalize_local_name(parsed.title or "")
    if parsed.season:
        return f"{base} S{parsed.season}"
    return base


def episode_number_of(name: str) -> int | None:
    """The episode number a filename refers to, or None when unparseable."""
    try:
        parsed = parse_release_name(name)
    except ValueError:
        return None
    if not parsed.episode:
        return None
    episode = parsed.episode.strip()
    # Ranges ("01-04") describe a batch, not one episode - never guess.
    return int(episode) if episode.isdigit() else None


def season_number_of_file(anime, number: int | None) -> int | None:
    """An anime's own episode number for a number read off a file in its folder (build 238).

    A folder can hold both kinds of name: the ones AniFlow wrote, which already
    count from this season's episode 1 ("... - 02 - Episode 2"), and the source
    release a download came as, which counts the way its group does
    ("[SubsPlease] ... - 50"). Reading the second raw is how a two-episode
    season grew a row 50 and 38 empty rows below it. ``anime.episode_offset``
    says where the group's counting sits, so a number above it is the group's
    and comes down by it; one at or below it is already this season's.

    Ambiguous only for a season longer than its own offset, which is not a
    season any group numbers this way.
    """
    if number is None:
        return None
    offset = int(getattr(anime, "episode_offset", 0) or 0)
    if offset > 0 and number > offset:
        return number - offset
    return number


def drop_franchise_twins(session: Session, anime, roots=None) -> int:
    """Remove rows an older folder scan made out of a group's own numbering (build 238).

    ``scan_anime_folder`` used to read ``... - 50`` off a source file as this
    season's episode 50. The file was later renamed into its real place as
    episode 2, and row 50 was left holding a name that no longer exists - a
    second card on In Progress reading "File is gone" for an episode that is in
    the library. Only that exact shape goes: a row above the offset that has no
    download, no file on disk and nothing waiting, whose episode one offset
    below it is the one that actually has the file. Returns how many went.
    """
    from backend.app.services.episode_state import media_file_exists

    offset = int(getattr(anime, "episode_offset", 0) or 0)
    if offset <= 0:
        return 0
    by_number = {
        episode.number: episode
        for episode in anime.episodes
        if episode.episode_type is EpisodeType.REGULAR
    }
    removed = 0
    for number, episode in list(by_number.items()):
        twin = by_number.get(number - offset) if number > offset else None
        if twin is None or not episode.media_filename:
            continue
        if episode.downloads or episode.copies or episode.import_filename:
            continue
        if not (twin.media_filename or twin.downloads):
            continue
        if media_file_exists(episode, roots=roots) is not False:
            continue  # the file is there (or cannot be checked): a real row
        session.delete(episode)
        removed += 1
    if removed:
        session.flush()
    return removed


def episode_numbers_in(names: list[str]) -> list[int | None]:
    """The episode number of each file name in one folder, read together (build 213).

    One name at a time a range ("01-03") is a batch and is never guessed at. But a folder is more
    than one name: Shoko's renamer wrote ``Show - 01-01 - Complete Movie``, ``... - 01-02 - ...`` and
    so on, where the first number is the part and the second the episode, and every file of such a
    folder reads as the range 1 to N. Read as a set the pattern is unmistakable - the same first
    number on every file, a different second number on each, and either no gap in the seconds or a
    file whose range is a single number - while a real batch (one file, or "01-06" beside "01-12")
    is not. Only then is the second number the episode. Everything else stays as it was.
    """
    numbers = [episode_number_of(name) for name in names]
    pairs: dict[int, tuple[int, int]] = {}
    for index, name in enumerate(names):
        if numbers[index] is None and (pair := _part_and_episode(name)) is not None:
            pairs[index] = pair
    if len(pairs) < 2:
        return numbers
    starts = {start for start, _end in pairs.values()}
    ends = sorted(end for _start, end in pairs.values())
    distinct = len(set(ends)) == len(ends)
    contiguous = ends == list(range(ends[0], ends[0] + len(ends)))
    one_number_range = any(start == end for start, end in pairs.values())
    if len(starts) == 1 and distinct and (contiguous or one_number_range):
        for index, (_start, end) in pairs.items():
            numbers[index] = end
    return numbers


#: "01-03" standing alone in a name: not part of a longer number (a year, "2026-01") or a word.
_PAIR = re.compile(r"(?<![\w])(\d{1,3})-(\d{1,3})(?![\w])")
_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _part_and_episode(name: str) -> tuple[int, int] | None:
    """The one "NN-MM" in a file name (bracketed tags left out), or None when there is none or several."""
    found = _PAIR.findall(_BRACKETED.sub(" ", name.rsplit(".", 1)[0]))
    if len(found) != 1:
        return None
    first, second = int(found[0][0]), int(found[0][1])
    return (first, second) if second >= 1 else None


def ensure_anime_folder(anime: Anime, *, media_root: str | Path | None = None) -> Path | None:
    """Create the anime's media folder if it does not exist yet.

    Returns the folder, or None when the stored name would escape the media
    root (left for the user to fix rather than silently corrected).
    """
    roots = roots_for(anime, storage_roots(media_root=media_root))
    folder = media_folder_path(roots, anime.media_folder or anime.local_name)
    if folder is None:
        logger.warning(
            "skipped folder creation for anime %s: unsafe media_folder %r", anime.id, anime.media_folder
        )
        return None
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def link_release_folder(
    session: Session,
    anime: Anime,
    raw_name: str,
    *,
    media_root: str | Path | None = None,
) -> dict[str, object]:
    """Derive the media folder from one selected release and link the anime.

    This is an explicit user action, so the derived name is *stored* on the
    anime afterwards; nothing recomputes it later. The episode number of the
    chosen release is irrelevant here.
    """
    try:
        parsed = parse_release_name(raw_name)
    except ValueError as exc:
        raise ValueError("The release name contains no recognizable title") from exc
    if not parsed.title:
        raise ValueError("The release name contains no recognizable title")

    folder_name = cleaned_release_folder(parsed)
    roots = roots_for(anime, storage_roots(media_root=media_root))
    folder = media_folder_path(roots, folder_name)
    if folder is None:
        raise ValueError("The folder name would escape the configured media root")
    folder.mkdir(parents=True, exist_ok=True)

    anime.local_name = folder_name
    anime.media_folder = folder_name
    # The user just pointed at a real release, so this is authoritatively how
    # providers name the show - keep feed matching in step with the folder.
    anime.release_name = folder_name
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("This folder name is already linked to another anime") from exc
    session.refresh(anime)
    logger.info("linked anime %s to media folder %s", anime.id, folder)
    return {
        "selected_file": raw_name,
        "cleaned_filename": folder_name,
        "folder_path": str(folder),
    }


#: Folders inside an anime's folder that hold things that are not its episodes (item 24).
EXTRAS_FOLDERS = frozenset(
    {"extras", "extra", "featurettes", "bonus", "samples", "sample", "trailers", "nced", "ncop", "creditless",
     "behind the scenes", "scans", "artbook", "subs", "subtitles", "fonts"}
)
#: How far down an anime's folder is read, and how many files at most: a season folder holds a few hundred
#: files, never a few thousand; anything past that is not an anime's folder.
MAX_SCAN_DEPTH = 4
MAX_SCAN_FILES = 3000


def media_files_below(folder: Path) -> list[Path]:
    """Every video file in this folder and its subfolders, the folder's own first (item 24).

    Shoko files an anime into season subfolders (``Show/Season 1/...``), and the scan used to read only the
    top level and find nothing. Files directly in the folder come first, then those in subfolders by
    depth and name, so when two files claim one episode the one nearest the top wins. Folders that hold
    extras (``Extras``, ``NCED`` ...) and hidden ones are skipped. Unreadable folders are skipped quietly.
    """
    found: list[tuple[int, str, Path]] = []

    def walk(directory: Path, depth: int) -> None:
        if len(found) >= MAX_SCAN_FILES:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError:
            return
        for child in children:
            if child.is_file() and is_media_file(child):
                found.append((depth, child.relative_to(folder).as_posix(), child))
            elif child.is_dir() and depth < MAX_SCAN_DEPTH:
                name = child.name.casefold()
                if name.startswith(".") or name in EXTRAS_FOLDERS:
                    continue
                walk(child, depth + 1)

    walk(folder, 0)
    found.sort(key=lambda item: (item[0], item[1]))
    return [path for _depth, _name, path in found]


def scan_anime_folder(
    session: Session,
    anime: Anime,
    *,
    media_root: str | Path | None = None,
) -> dict[str, object]:
    """Reconcile the anime's media folder with the database.

    For every media file found, the matching episode records the filename and
    becomes COMPLETE. Episodes that recorded a file which is no longer there
    become MISSING - the folder is the authority, so knowledge flows both ways.
    Missing episode rows are created (including the gaps below the highest
    number found) so the list is contiguous.
    """
    roots = roots_for(anime, storage_roots(media_root=media_root))
    folder = media_folder_path(roots, anime.media_folder or anime.local_name)
    if folder is None:
        raise FileNotFoundError("The media folder name escapes the configured media root")
    if not folder.is_dir():
        raise FileNotFoundError(f"The linked folder does not exist: {folder}")

    files_scanned = 0
    unparseable = 0
    found: dict[int, Path] = {}
    # Subtitles and stray files are not an episode's media. Read together, so a folder named
    # "- 01-01 -", "- 01-02 -" ... is understood as a set (build 213).
    entries = media_files_below(folder)
    for entry, raw in zip(entries, episode_numbers_in([entry.name for entry in entries])):
        files_scanned += 1
        number = season_number_of_file(anime, raw)
        if number is None:
            unparseable += 1
            continue
        # A later file for the same episode wins only if none was recorded yet.
        found.setdefault(number, entry)

    episodes = {
        episode.number: episode
        for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id))
    }

    created = 0

    def get_or_create(number: int) -> Episode:
        nonlocal created
        existing = episodes.get(number)
        if existing is not None:
            return existing
        episode = Episode(
            anime_id=anime.id,
            number=number,
            episode_type=EpisodeType.REGULAR,
            status=EpisodeStatus.UNAVAILABLE,
        )
        session.add(episode)
        episodes[number] = episode
        created += 1
        return episode

    completed = 0
    for number, path in sorted(found.items()):
        episode = get_or_create(number)
        # Relative to the anime's folder, so a file in a season subfolder resolves (item 24); for the usual
        # file directly in the folder this is just its name.
        episode.media_filename = path.relative_to(folder).as_posix()
        try:
            episode.media_size_bytes = path.stat().st_size
        except OSError:
            episode.media_size_bytes = None
        if episode.status is not EpisodeStatus.COMPLETE:
            completed += 1

    # Fill the gaps so the episode list is contiguous up to the highest file.
    max_found = max(found) if found else 0
    for number in range(1, max_found + 1):
        get_or_create(number)

    if max_found > (anime.episode_count or 0):
        anime.episode_count = max_found

    # Files that disappeared: clear the recorded name so the status derivation
    # reports MISSING (and the episode can be downloaded again).
    vanished = 0
    for number, episode in episodes.items():
        if number in found or not episode.media_filename:
            continue
        target = folder / episode.media_filename
        if not target.is_file():
            vanished += 1

    session.flush()
    for episode in episodes.values():
        refresh_episode_status(episode, roots=roots, check_disk=True)
    session.commit()

    return {
        "folder_path": str(folder),
        "files_scanned": files_scanned,
        "unparseable": unparseable,
        "episodes_found": sorted(found),
        "completed": completed,
        "vanished": vanished,
        "created": created,
        "max_episode": max_found,
    }


__all__ = [
    "VIDEO_SUFFIXES",
    "cleaned_release_folder",
    "ensure_anime_folder",
    "episode_number_of",
    "episode_numbers_in",
    "season_number_of_file",
    "link_release_folder",
    "scan_anime_folder",
]
