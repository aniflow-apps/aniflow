"""Move anime between libraries (build 192, 0.8.5).

An anime's files live in ``<library folder>/<media_folder>``. Moving it to
another library is moving that one folder to the other library's folder and
pointing the anime at the new library. See docs/libraries-plan.md.

Rules, in the order they matter:

* **Nothing is merged.** If the destination already has a folder of that
  name, that anime is refused and says so - two folders silently joined is how
  episodes get lost.
* **Nothing is deleted before it has been copied and checked.** A rename on
  one filesystem; across two, a copy that is verified (every file there, same
  size) before the original is removed file by file. A failed or mismatching
  copy is removed again (the original was not touched); a failed removal of
  the original leaves the copy in place - both exist, nothing is lost
  (``_move_folder``).
* **One anime failing does not stop the rest.** Each answer is reported.
* Two libraries over the same folder need no file move at all - only the
  library changes.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, EpisodeStatus
from backend.app.models.library import Library
from backend.app.services.attention import log_activity
from backend.app.services.episode_state import refresh_episode_status
from backend.app.services.storage import roots_for, safe_relative, storage_roots

logger = for_component("Library")


@dataclass
class MoveResult:
    anime_id: str
    name: str
    #: ``moved`` (files moved), ``reassigned`` (library changed, nothing to
    #: move), ``unchanged`` (already there) or ``refused``.
    outcome: str
    detail: str | None = None


def move_anime(
    db: Session, anime_ids: list[str], library_id: int, *, move_files: bool = True, by_hand: bool = True
) -> list[MoveResult]:
    destination = db.get(Library, library_id)
    if destination is None:
        raise ValueError("That library does not exist.")
    results: list[MoveResult] = []
    moved_any = False
    for anime_id in anime_ids:
        anime = db.get(Anime, anime_id)
        if anime is None:
            results.append(MoveResult(anime_id, anime_id, "refused", "That anime does not exist."))
            continue
        result = _move_one(db, anime, destination, move_files)
        if result.outcome in ("moved", "reassigned") and destination.has_folder:
            # Out of Upcoming into a library with a folder: it is Active now (build 205).
            from backend.app.services.lifecycle_auto import activate_when_it_starts

            activate_when_it_starts(anime)
        if by_hand and result.outcome in ("moved", "reassigned"):
            # Put somewhere the season rollover would move it from: the user meant it (build 205).
            from backend.app.services.season_rollover import pin_if_against_the_rules

            pin_if_against_the_rules(db, anime)
        results.append(result)
        moved_any = moved_any or result.outcome == "moved"
    db.commit()
    if moved_any:
        _ask_shoko_to_look()
    return results


def _move_one(db: Session, anime: Anime, destination: Library, move_files: bool = True) -> MoveResult:
    name = anime.display_name or anime.local_name
    if anime.library_id == destination.id:
        return MoveResult(anime.id, name, "unchanged")

    if not destination.has_folder:
        # Upcoming: nothing is stored there, so only an anime with no files can go.
        if any(episode.media_filename for episode in anime.episodes):
            return MoveResult(
                anime.id, name, "refused",
                f"{destination.name} has no folder, and this anime already has files.",
            )
        anime.library_id = destination.id
        anime.library = destination
        return MoveResult(anime.id, name, "reassigned")
    old_root = roots_for(anime).media_root
    new_root = Path(destination.folder)
    folder = anime.media_folder

    def reassign(outcome: str, detail: str | None = None) -> MoveResult:
        before = anime.library.name if anime.library is not None else None
        anime.library_id = destination.id
        anime.library = destination
        had_files = {episode.id for episode in anime.episodes if episode.status is EpisodeStatus.COMPLETE}
        for episode in anime.episodes:
            refresh_episode_status(episode, roots=roots_for(anime, storage_roots()), check_disk=True)
        # Said out loud (reported after build 205): a file the re-check cannot find in the new
        # place used to turn into "missing" without a word, which looks exactly like a deleted file.
        lost = sorted(
            episode.number for episode in anime.episodes
            if episode.id in had_files and episode.status is EpisodeStatus.MISSING
        )
        details: dict[str, object] = {"from": before, "to": destination.name, "outcome": outcome}
        message = f"Moved from {before} to {destination.name}" if before else f"Moved to {destination.name}"
        if lost:
            numbers = ", ".join(str(n) for n in lost[:12]) + (" ..." if len(lost) > 12 else "")
            warning = (
                f"{len(lost)} episode file(s) were not found in the new place afterwards "
                f"(episodes {numbers}). Nothing was deleted by this move; look in the old and the new folder."
            )
            logger.warning("%s: %s", name, warning)
            details["not_found_afterwards"] = lost
            message += f" - {len(lost)} file(s) not found afterwards"
            detail = f"{detail} {warning}" if detail else warning
        log_activity(db, kind="LIBRARY_MOVED", message=message, details=details, anime_id=anime.id)
        return MoveResult(anime.id, name, outcome, detail)

    if Path(old_root) == new_root:
        return reassign("reassigned")
    if not move_files:
        # The files are already where they belong (a folder was repointed, say):
        # only the library changes. Episodes are re-checked, so anything that is
        # not actually there shows as missing instead of staying quietly wrong.
        return reassign("reassigned", "Only the library changed; the files were left where they are.")
    source = old_root / folder if folder else None
    if source is None or not source.is_dir():
        # Nothing on disk to move - a future release, or files that are gone.
        return reassign("reassigned", "There was no folder to move.")
    if safe_relative(old_root, source) is None or source.parent != old_root:
        return MoveResult(anime.id, name, "refused", "The anime's folder is not directly in its library.")
    if not new_root.is_dir():
        return MoveResult(anime.id, name, "refused", "The destination folder does not exist.")
    target = new_root / folder
    if target.exists():
        return MoveResult(
            anime.id, name, "refused",
            f'"{folder}" already exists in {destination.name}; nothing was moved.',
        )

    try:
        leftovers = _move_folder(source, target)
    except OSError as exc:
        logger.warning("could not move %s to %s: %s", source, target, exc)
        return MoveResult(anime.id, name, "refused", f"The move failed: {exc.strerror or exc}")
    logger.info("moved %s: %s -> %s", name, old_root, new_root)
    if leftovers:
        logger.warning("%s: %d file(s) could not be removed from %s after the copy", name, leftovers, source)
        return reassign(
            "moved",
            f"The files were copied, but {leftovers} could not be removed from the old folder "
            "(check its permissions). They exist in both places; nothing was lost.",
        )
    return reassign("moved")


def _move_folder(source: Path, target: Path) -> int:
    """Move a folder so the only copy of a file is never deleted. Returns files left behind in ``source``.

    A rename when both are on one filesystem. Otherwise a copy that is *checked*
    (every file there, same size) before the original is touched, and the
    original is then removed file by file. ``shutil.move`` did the same in one
    call, but when removing the original failed half way it raised - and the
    cleanup that followed deleted the finished copy, leaving files in neither
    place (reported after build 205). Now: a failed or mismatching copy is
    removed (the original has not been touched); a failed removal leaves the
    copy where it is.
    """
    try:
        source.rename(target)
        return 0
    except OSError:
        # Another filesystem, or the rename was refused. The source is intact
        # either way; carry on with a copy.
        if not source.exists() or target.exists():
            raise
    try:
        shutil.copytree(source, target, symlinks=True)
        problems = _copy_differences(source, target)
        if problems:
            raise OSError(f"the copy did not match the original ({problems[0]})")
    except (OSError, shutil.Error):
        # Nothing of the original has been removed yet; the half-written copy is ours to clean up.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return _remove_original(source)


def _copy_differences(source: Path, target: Path) -> list[str]:
    """Files of ``source`` that are missing from ``target`` or a different size."""
    problems: list[str] = []
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        twin = target / path.relative_to(source)
        try:
            if not twin.is_file() or (not path.is_symlink() and twin.stat().st_size != path.stat().st_size):
                problems.append(str(path.relative_to(source)))
        except OSError:
            problems.append(str(path.relative_to(source)))
    return problems


def _remove_original(source: Path) -> int:
    """Delete the original after a verified copy. Returns how many files could not be deleted."""
    left = 0
    for path in sorted(source.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            left += 1
    try:
        source.rmdir()
    except OSError:
        pass
    return left


def _ask_shoko_to_look() -> None:
    from backend.app.services.importer import ask_shoko_to_look
    from backend.app.core.mode import AppMode, current_mode

    if current_mode() is AppMode.SHOKO_SERVER:
        ask_shoko_to_look()
