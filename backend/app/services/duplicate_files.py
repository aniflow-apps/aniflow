"""Two copies of one episode, and the person who has to choose (build 189).

An episode normally has one file. Two things can leave it with two:

``kept_original``
    "Keep as original file" in Change the release: the old file is left where
    it is, a playable ``.mkv``, while the new release is fetched.

``previous_version``
    A v2 was imported next to a v1 that was already in the library. AniFlow
    used to leave the v1 lying there with nothing remembering it; now it is
    written down when the v2 lands.

Neither is deleted by AniFlow. Asked for exactly that way: *"it will keep both
files in, but should give an ! on the anime card ... and in the In Progress
have an attention for this kind of error, where the user needs to do an
action. Like getting the option to delete v1 or delete v2."*  So this raises a
``DUPLICATE_FILES`` attention per episode, which is what puts the episode
(back) on the In Progress page with its two buttons, and closes it when only
one file is left.

Raised only once the episode has its new file. Between the swap and the new
release arriving the kept original is the *only* file, and that is not a
duplicate, it is the plan.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.activity import Attention, AttentionKind, AttentionStatus
from backend.app.models.anime import Anime, Episode
from backend.app.models.superseded import SupersededFile
from backend.app.services.attention import raise_attention
from backend.app.services.superseded import DUPLICATE_KINDS, live_copies

logger = for_component("Library")


def _main_file(session: Session, episode: Episode, anime: Anime) -> Path | None:
    from backend.app.services.storage import media_path, roots_for, storage_roots

    if not episode.media_filename or not anime.media_folder:
        return None
    return media_path(roots_for(anime, storage_roots()), anime.media_folder, episode.media_filename)


def review(session: Session) -> dict[str, int]:
    """Raise and close the duplicate-file attentions. Returns what it did."""
    raised = 0
    closed = 0

    episode_ids = {
        row
        for row in session.scalars(
            select(SupersededFile.episode_id).where(
                SupersededFile.kind.in_(DUPLICATE_KINDS),
                SupersededFile.removed_at.is_(None),
            )
        )
    }
    qualifying: set[str] = set()
    for episode_id in episode_ids:
        episode = session.get(Episode, episode_id)
        if episode is None or episode.anime is None:
            continue
        copies = live_copies(session, episode_id)
        main = _main_file(session, episode, episode.anime)
        if not copies or main is None or not main.exists():
            continue
        qualifying.add(episode_id)
        existing = session.scalar(
            select(Attention).where(
                Attention.kind == AttentionKind.DUPLICATE_FILES,
                Attention.status == AttentionStatus.OPEN,
                Attention.episode_id == episode_id,
            )
        )
        raise_attention(
            session,
            kind=AttentionKind.DUPLICATE_FILES,
            anime_id=episode.anime_id,
            episode_id=episode_id,
            detail=str(len(copies)),
        )
        if existing is None:
            raised += 1
            # It was done and archived; it is news again, so it goes back on
            # the In Progress page where its two buttons are.
            episode.progress_archived_at = None

    for attention in session.scalars(
        select(Attention).where(
            Attention.kind == AttentionKind.DUPLICATE_FILES,
            Attention.status == AttentionStatus.OPEN,
        )
    ):
        if attention.episode_id not in qualifying:
            attention.status = AttentionStatus.RESOLVED
            attention.resolved_at = datetime.now(UTC)
            attention.note = "only one file is left"
            closed += 1
    if raised or closed:
        session.flush()
    return {"raised": raised, "closed": closed}


def review_in(session_factory) -> dict[str, int]:
    session = session_factory()
    try:
        outcome = review(session)
        session.commit()
        return outcome
    finally:
        session.close()


def _episode_of(session: Session, attention: Attention) -> Episode:
    from fastapi import HTTPException

    if attention.kind is not AttentionKind.DUPLICATE_FILES or not attention.episode_id:
        raise HTTPException(status_code=409, detail="This is not a duplicate-files case")
    episode = session.get(Episode, attention.episode_id)
    if episode is None or episode.anime is None:
        raise HTTPException(status_code=404, detail="The episode no longer exists")
    return episode


def _finish(session: Session, attention: Attention, note: str) -> None:
    review(session)
    if attention.status is AttentionStatus.OPEN:
        # Another copy is still there: the case stays open, with what is left.
        return
    attention.note = note


def delete_older(session: Session, attention: Attention) -> int:
    """Delete every older copy of the episode. Returns how many went."""
    from backend.app.services.superseded import delete_copy

    episode = _episode_of(session, attention)
    removed = 0
    for row in live_copies(session, episode.id):
        if delete_copy(session, row):
            removed += 1
    from backend.app.services.attention import log_activity

    if removed:
        log_activity(
            session,
            kind="LIBRARY_DUPLICATE_DELETED",
            message=f"Deleted {removed} older file(s) of episode {episode.number} on request",
            details={"episode_id": episode.id},
            anime_id=episode.anime_id,
            episode_id=episode.id,
        )
    _finish(session, attention, "the older file was deleted by request")
    return removed


def delete_newer(session: Session, attention: Attention) -> str:
    """Delete the file AniFlow calls the episode's, and keep the older one.

    The older copy (the most recently written down, when there are several)
    becomes the episode's file. Its version is not known, so it reads as v1 -
    the convention everywhere else - which means a later v2 raises NEW_VERSION
    again rather than being fetched silently.
    """
    from fastapi import HTTPException

    from backend.app.services.attention import log_activity
    from backend.app.services.episode_state import refresh_episode_status
    from backend.app.services.storage import media_folder_path, roots_for, storage_roots
    from backend.app.services.superseded import live_copies as copies_of

    episode = _episode_of(session, attention)
    anime = episode.anime
    older = sorted(copies_of(session, episode.id), key=lambda row: row.recorded_at)
    if not older:
        raise HTTPException(status_code=409, detail="There is no older copy to keep")
    keep = older[-1]
    main = _main_file(session, episode, anime)
    if main is not None and main.exists():
        try:
            main.unlink()
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"The newer file could not be deleted: {exc}") from exc
    deleted_name = episode.media_filename

    keep_path = Path(keep.path)
    folder = media_folder_path(roots_for(anime, storage_roots()), anime.media_folder or "")
    try:
        recorded = keep_path.relative_to(folder).as_posix() if folder is not None else keep_path.name
    except ValueError:
        recorded = keep_path.name
    try:
        size = keep_path.stat().st_size
    except OSError:
        size = keep.size_bytes
    episode.media_filename = recorded
    episode.media_size_bytes = size
    episode.media_version = None
    episode.media_imported_at = datetime.now(UTC)
    refresh_episode_status(episode)
    keep.removed_at = datetime.now(UTC)
    keep.error = None

    log_activity(
        session,
        kind="LIBRARY_DUPLICATE_DELETED",
        message=f"Deleted {deleted_name} and kept {keep.filename} for episode {episode.number} on request",
        details={"episode_id": episode.id},
        anime_id=episode.anime_id,
        episode_id=episode.id,
    )
    _finish(session, attention, "the newer file was deleted by request")
    return keep.filename


__all__ = ["delete_newer", "delete_older", "review", "review_in"]
