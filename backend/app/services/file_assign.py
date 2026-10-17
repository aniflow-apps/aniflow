"""Let the user say which file is which episode (build 213).

A scan reads names, and names are whatever Shoko, a group or a person made of them: "Show - 01-03 -
Complete Movie", "Show - Final", "Show OVA". Some can be taught to the parser and some never will be.
For those the answer is the person's: they see the files in the folder and pick each one's episode.

It only records. No file is renamed, moved or deleted - the episode simply remembers which file in
the folder is its own, the same thing a scan writes. Nothing here guesses; the suggestions it offers
are the ones the scan would have made, and they are only suggestions.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.anime import Anime, Episode, EpisodeStatus, EpisodeType
from backend.app.services.episode_state import refresh_episode_status
from backend.app.services.library import episode_numbers_in
from backend.app.services.storage import media_folder_path, roots_for, storage_roots

#: Far above any real season; the guard against a typo, not a limit anybody meets.
MAX_EPISODE = 10000


def _folder_and_files(anime: Anime):
    from backend.app.services.library_match import _media_files

    roots = roots_for(anime, storage_roots())
    folder = media_folder_path(roots, anime.media_folder or anime.local_name)
    if folder is None:
        raise FileNotFoundError("The media folder name escapes the configured media root")
    if not folder.is_dir():
        raise FileNotFoundError(f"The linked folder does not exist: {folder}")
    return roots, folder, _media_files(folder)


def list_folder_files(db: Session, anime: Anime) -> dict[str, object]:
    """Every video file in the anime's folder, with the episode it is recorded as and the scan's own guess."""
    _roots, folder, files = _folder_and_files(anime)
    relative = [path.relative_to(folder).as_posix() for path in files]
    suggested = episode_numbers_in([path.name for path in files])
    recorded = {
        episode.media_filename: episode.number
        for episode in db.scalars(select(Episode).where(Episode.anime_id == anime.id))
        if episode.media_filename and episode.episode_type is EpisodeType.REGULAR
    }
    rows = []
    for path, name, guess in zip(files, relative, suggested):
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        rows.append({"filename": name, "size": size, "episode": recorded.get(name), "suggested": guess})
    return {
        "folder": str(folder),
        "files": rows,
        "episode_count": anime.effective_episode_count or anime.episode_count,
        "episodes": sorted(
            episode.number
            for episode in db.scalars(select(Episode).where(Episode.anime_id == anime.id))
            if episode.episode_type is EpisodeType.REGULAR
        ),
    }


def assign_files(db: Session, anime: Anime, assignments: list[tuple[str, int | None]]) -> dict[str, int]:
    """Record the given file -> episode choices. ``None`` means "this file is no episode's"."""
    roots, folder, files = _folder_and_files(anime)
    sizes = {}
    for path in files:
        try:
            sizes[path.relative_to(folder).as_posix()] = path.stat().st_size
        except OSError:
            sizes[path.relative_to(folder).as_posix()] = None

    chosen: dict[int, str] = {}
    freed: set[str] = set()
    for filename, number in assignments:
        if filename not in sizes:
            raise ValueError(f"{filename!r} is not a file in this anime's folder")
        if number is None:
            freed.add(filename)
            continue
        if not 1 <= number <= MAX_EPISODE:
            raise ValueError(f"Episode {number} is out of range")
        if number in chosen and chosen[number] != filename:
            raise ValueError(f"Two files are set to episode {number}")
        chosen[number] = filename

    episodes = {
        episode.number: episode
        for episode in db.scalars(select(Episode).where(Episode.anime_id == anime.id))
        if episode.episode_type is EpisodeType.REGULAR
    }
    touched: list[Episode] = []
    cleared = 0

    # A file the person unassigned, or moved to another episode, no longer belongs to where it was.
    moving = freed | set(chosen.values())
    for episode in episodes.values():
        if episode.media_filename in moving and chosen.get(episode.number) != episode.media_filename:
            episode.media_filename = None
            episode.media_size_bytes = None
            touched.append(episode)
            cleared += 1

    assigned = 0
    for number, filename in sorted(chosen.items()):
        episode = episodes.get(number)
        if episode is None:
            episode = Episode(
                anime_id=anime.id, number=number, episode_type=EpisodeType.REGULAR, status=EpisodeStatus.UNAVAILABLE
            )
            db.add(episode)
            episodes[number] = episode
        episode.media_filename = filename
        episode.media_size_bytes = sizes[filename]
        touched.append(episode)
        assigned += 1

    if chosen and max(chosen) > (anime.episode_count or 0):
        anime.episode_count = max(chosen)

    db.flush()
    for episode in touched:
        refresh_episode_status(episode, roots=roots, check_disk=True)
    db.commit()
    return {"assigned": assigned, "cleared": cleared}
