"""The old copy, waited on and then deleted (build 116).

The half of a subgroup swap that nothing was doing: *"we actually have to save
somewhere the previous filenames, so the delete stuff happens. And when its
done then we can remove it."*

Two entry points and nothing else. :func:`remember` writes down a file a swap
is leaving in place; :func:`settle` is called once an episode has a new file
and deletes whatever was being waited on for it.

Deliberately not a sweep on a timer. The question "has the replacement
arrived" has exactly one answer and exactly one moment - the import that puts
the new file in the library - so that is where it is asked. A timer would have
to guess, and guessing wrong here deletes somebody's only copy.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, Episode
from backend.app.models.superseded import SupersededFile

logger = for_component("Library")

#: The kinds that are a second copy of an episode, sitting next to its file
#: (build 189): the original kept by a swap, and the v1 left by a v2.
DUPLICATE_KINDS = ("kept_original", "previous_version")


def remember(
    session: Session,
    anime: Anime,
    episode: Episode,
    path: Path,
    *,
    subgroup: str | None = None,
    resolution: str | None = None,
    kind: str = "awaiting_replacement",
) -> SupersededFile:
    """Write down a file that is staying until its replacement arrives.

    The full path, not a folder and a name to be recombined later: the library
    root can be re-pointed and the folder renamed between the swap and the
    arrival, and a path rebuilt then could name a different file.
    """
    size: int | None = None
    try:
        size = path.stat().st_size
    except OSError:
        # Recorded without a size rather than not recorded. The size is for
        # reading, and a file that cannot be stat'ed is exactly the kind that
        # needs to be written down.
        size = None
    already = session.scalar(
        select(SupersededFile).where(
            SupersededFile.episode_id == episode.id,
            SupersededFile.path == str(path),
            SupersededFile.removed_at.is_(None),
        )
    )
    if already is not None:
        return already
    row = SupersededFile(
        anime_id=anime.id,
        episode_id=episode.id,
        path=str(path),
        filename=path.name,
        subgroup=subgroup,
        resolution=resolution,
        release_name=anime.release_name,
        size_bytes=size,
        kind=kind,
    )
    session.add(row)
    return row


def waiting_for(session: Session, episode_id: str) -> list[SupersededFile]:
    """The old copies still on disk for this episode."""
    return list(
        session.scalars(
            select(SupersededFile).where(
                SupersededFile.episode_id == episode_id,
                SupersededFile.removed_at.is_(None),
            )
        )
    )


def live_copies(session: Session, episode_id: str) -> list[SupersededFile]:
    """The second copies of this episode that are still on disk.

    A copy whose file has gone - deleted by hand, say - is closed here rather
    than offered for deletion: the filesystem is asked at read time, as it is
    for a duplicate download, so a delete button never points at nothing. Not
    closed when its whole folder is missing, which is more likely a share that
    is not mounted than a deletion.
    """
    live: list[SupersededFile] = []
    for row in waiting_for(session, episode_id):
        if row.kind not in DUPLICATE_KINDS:
            continue
        path = Path(row.path)
        if path.exists():
            live.append(row)
        elif path.parent.exists():
            row.removed_at = datetime.now(UTC)
    return live


def delete_copy(session: Session, row: SupersededFile) -> bool:
    """Delete one tracked copy from disk and close its note."""
    path = Path(row.path)
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        row.error = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("could not delete %s: %s", row.filename, exc)
        return False
    row.removed_at = datetime.now(UTC)
    row.error = None
    logger.info("deleted %s on request", row.filename)
    return True


def leftovers(session: Session, anime_id: str) -> list[SupersededFile]:
    """Every file this anime has left lying about, of either kind.

    For saying so. A renamed file nobody is told about is the manual step
    that nobody knows they have.
    """
    return list(
        session.scalars(
            select(SupersededFile).where(
                SupersededFile.anime_id == anime_id,
                SupersededFile.removed_at.is_(None),
            )
        )
    )


def prune_empty_folders(root: Path, folder: str | None) -> int:
    """Remove the anime's folder if deleting its files emptied it (119).

    *"It first needs to remove the files (and folders only if they are
    empty)"*. Only empty, only one level, and only below the root it was
    given - a folder with anything at all in it is somebody's, and this has
    no business deciding whose.
    """
    if not folder:
        return 0
    target = root / folder
    removed = 0
    try:
        if not target.is_dir() or target.parent != root:
            return 0
        # Season subfolders Shoko may have made, if they are empty too.
        for child in sorted(target.iterdir(), reverse=True):
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()
                removed += 1
        if not any(target.iterdir()):
            target.rmdir()
            removed += 1
            logger.info("removed the empty folder %s", target.name)
    except OSError as exc:
        logger.info("could not tidy %s: %s", target, exc)
    return removed


def waiting_paths(session: Session, anime_id: str) -> set[str]:
    """Every old copy still on disk for this anime, by full path.

    For the library reconciler, which walks the anime's folder and adopts any
    media file it finds for an episode that has none. A file kept by a swap is
    exactly that shape - it sits in the folder, its name carries the episode
    number, and the episode row was cleared by the swap - so without this the
    reconciler would adopt the old release back within the half hour, the
    episode would read COMPLETE again, nothing would download, and the swap
    would have quietly undone itself.

    Which is the reason ``backup`` mode renames its files to ``.backup``: a
    name nothing recognises as media. This mode cannot do that - the file has
    to keep playing - so it has to be named here instead.
    """
    return {
        row.path
        for row in session.scalars(
            select(SupersededFile).where(
                SupersededFile.anime_id == anime_id,
                SupersededFile.removed_at.is_(None),
            )
        )
    }


#: How long a closed note is kept, so "what happened to my old files" has an
#: answer for longer than the instant it happens in - and not forever.
KEEP_CLOSED_DAYS = 30


def forget_old(session: Session) -> int:
    """Drop notes whose files went a month ago. Returns how many."""
    cutoff = datetime.now(UTC) - timedelta(days=KEEP_CLOSED_DAYS)
    stale = list(
        session.scalars(
            select(SupersededFile).where(
                SupersededFile.removed_at.is_not(None),
                SupersededFile.removed_at < cutoff,
            )
        )
    )
    for row in stale:
        session.delete(row)
    return len(stale)


def settle(session: Session, episode: Episode) -> int:
    """The replacement is in. Delete what it replaced, and say so.

    Returns how many old copies went. Zero is the ordinary answer - almost no
    import is replacing anything - and cheap: one indexed lookup.

    A file that cannot be deleted keeps its row and records why, because the
    alternative is a copy of an old release sitting in the library with
    nothing anywhere remembering that it should not be.
    """
    # Only the ones this is allowed to delete. A renamed .backup file was
    # left for the user on purpose, and the arrival of a replacement is not
    # permission to throw it away (build 119).
    pending = [
        row for row in waiting_for(session, episode.id)
        if row.kind == "awaiting_replacement"
    ]
    if not pending:
        return 0

    removed = 0
    for row in pending:
        target = Path(row.path)
        if not target.exists():
            # Already gone - moved by hand, or cleaned up elsewhere. The note
            # is closed rather than kept: it has nothing left to describe.
            row.removed_at = datetime.now(UTC)
            row.error = None
            removed += 1
            continue
        try:
            target.unlink()
        except OSError as exc:
            row.error = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(
                "the replaced copy %s is still there: %s", row.filename, exc
            )
            continue
        row.removed_at = datetime.now(UTC)
        row.error = None
        removed += 1
        logger.info(
            "deleted %s now that its replacement has arrived", row.filename
        )
    return removed


__all__ = [
    "DUPLICATE_KINDS",
    "KEEP_CLOSED_DAYS",
    "delete_copy",
    "forget_old",
    "live_copies",
    "leftovers",
    "prune_empty_folders",
    "remember",
    "settle",
    "waiting_for",
    "waiting_paths",
]
