"""Attention lifecycle: raising, deduplicating, auto-resolving, actions.

Every exceptional state surfaces exactly once as an OPEN Attention record
(repeated occurrences refresh the existing one). When a condition clears on
its own - e.g. a broken feed works again or an import blocker disappears -
the corresponding record resolves automatically.
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.integrations.torrent_client import TorrentClient, client_for
from backend.app.models.activity import Activity, Attention, AttentionKind, AttentionStatus
from backend.app.models.anime import Anime
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.models.rss import Release, ReleaseDecision
from backend.app.services.app_settings import load_app_settings
from backend.app.services.matching import evaluate_release
from backend.app.services.release_parser import parse_release_name

logger = for_component("Attention")


# How much attention an entry deserves. The Activity page filters on this:
# "Download sync: 4 checked" is bookkeeping, a blocked import is not.
ROUTINE = "routine"
NOTABLE = "notable"
PROBLEM = "problem"

_LEVEL_BY_KIND = {
    # Build 125: the four per-pass kinds are gone - see
    # services/activity_kinds.py:RETIRED_KINDS. What is left is per-episode or
    # per-decision, so almost all of it is notable by definition and the
    # interesting column is which ones are *not*.
    "EPISODE_ACQUIRED": NOTABLE,
    "RSS_EPISODE_FOUND": NOTABLE,
    "DOWNLOAD_APPROVED": NOTABLE,
    "ASSIGN_MATCHED": NOTABLE,
    "ASSIGN_REJECTED": PROBLEM,
    # A file in the library that no episode claims: worth seeing, not urgent.
    "LIBRARY_UNPLACED": ROUTINE,
    # A stray torrent nobody could place. It needs a decision eventually.
    "TORRENT_UNCLAIMED": PROBLEM,
}

# Counters whose presence turns an otherwise routine entry into a problem.
_PROBLEM_COUNTERS = ("failed", "attention", "blocked", "missing", "errors")


def activity_level(kind: str, details: dict[str, object] | None = None) -> str:
    """Importance of one activity entry.

    Derived rather than stored: the kind and its own counters already say it,
    and deriving means older entries get the same treatment as new ones.
    """
    level = _LEVEL_BY_KIND.get(kind, NOTABLE)
    if kind.endswith("_FAILED") or kind.endswith("_ERROR"):
        return PROBLEM
    for counter in _PROBLEM_COUNTERS:
        value = (details or {}).get(counter)
        if isinstance(value, int) and value > 0:
            return PROBLEM
        if isinstance(value, list) and value:
            return PROBLEM
    return level


def describe_changes(changes: object, *, limit: int = 1, fallback: str = "") -> str:
    """A message built from named changes instead of counters.

    "Download sync: 4 checked, 4 updated" told nobody *what* changed; the
    summaries already carry the episode names, so use them and keep the counts
    in the details.

    One change, not three. Three release names joined with semicolons made a
    600-character sentence that the column then cut off mid-filename - three
    episodes of the same anime read as one unintelligible entry. The rest are
    counted here and listed in full in the details, one per line.
    """
    if not isinstance(changes, list) or not changes:
        return fallback
    shown = "; ".join(str(change) for change in changes[:limit])
    remaining = len(changes) - limit
    if remaining > 0:
        shown = f"{shown} (+{remaining} more)"
    return shown[:250]


def raise_attention(session: Session, *, kind: AttentionKind,
                    anime_id: str | None = None, release_id: str | None = None,
                    download_id: str | None = None, feed_source_id: str | None = None,
                    episode_id: str | None = None,
                    detail: str | None = None) -> Attention | None:
    """Create or refresh the OPEN attention for this (kind, subject).

    The episode is part of the subject (build 90), so two episodes of one
    anime with the same problem are two records. Before that they were one,
    which for a record naming a file meant three of the four files went
    unmentioned.
    """
    existing = session.scalar(
        select(Attention).where(
            Attention.kind == kind,
            Attention.status == AttentionStatus.OPEN,
            Attention.anime_id == anime_id,
            Attention.release_id == release_id,
            Attention.download_id == download_id,
            Attention.feed_source_id == feed_source_id,
            Attention.episode_id == episode_id,
        )
    )
    if existing is not None:
        if detail is not None:
            existing.detail = detail[:120]
        return existing
    attention = Attention(
        kind=kind, status=AttentionStatus.OPEN,
        anime_id=anime_id, release_id=release_id,
        download_id=download_id, feed_source_id=feed_source_id,
        episode_id=episode_id,
        detail=detail[:120] if detail else None,
    )
    session.add(attention)
    return attention


def resolve_open(session: Session, *, kind: AttentionKind, **subject) -> int:
    """Auto-resolve OPEN attentions matching the subject; returns count."""
    statement = select(Attention).where(
        Attention.kind == kind,
        Attention.status == AttentionStatus.OPEN,
    )
    for column, value in subject.items():
        statement = statement.where(getattr(Attention, column) == value)
    count = 0
    for attention in session.scalars(statement):
        attention.status = AttentionStatus.RESOLVED
        attention.resolved_at = datetime.now(UTC)
        count += 1
    return count


def log_activity(session: Session, *, kind: str, message: str,
                 details: dict[str, object] | None = None, anime_id: str | None = None,
                 episode_id: str | None = None) -> None:
    session.add(Activity(
        kind=kind[:40], message=message[:255], details=details or {},
        anime_id=anime_id, episode_id=episode_id,
    ))


def dismiss_all(session: Session, note: str | None = None) -> int:
    """Dismiss every open record. Returns how many.

    Dismissed, not resolved: the difference is the point of having both.
    Resolved means the thing was dealt with; dismissed means somebody decided
    it did not need dealing with, which is exactly what pressing one button
    for a whole list means.

    Nothing outside the list is touched. A dismissed duplicate leaves both
    files where they are, and a dismissed blocked import will be raised again
    by the next pass if it is still blocked - which is the honest behaviour:
    this clears a *list*, not a problem.
    """
    now = datetime.now(UTC)
    records = list(
        session.scalars(select(Attention).where(Attention.status == AttentionStatus.OPEN))
    )
    for record in records:
        record.status = AttentionStatus.DISMISSED
        record.resolved_at = now
        if note:
            record.note = note[:500]
    if records:
        logger.info("dismissed %s open attention record(s) at once", len(records))
    return len(records)


def dismiss_attention(session: Session, attention_id: str, note: str | None = None) -> Attention:
    attention = _get_or_404(session, attention_id)
    attention.status = AttentionStatus.DISMISSED
    attention.resolved_at = datetime.now(UTC)
    if note:
        attention.note = note[:500]
    return attention


#: Which resolutions an attention offers, beyond resolve and dismiss. The
#: server decides, because the answer depends on facts the browser does not
#: have - whether the file is still on disk, whether the library has one.
def actions_for(attention, *, download=None, episode=None, source_exists: bool = False) -> list[str]:
    """The buttons this attention should show, in the order they should read.

    "Mark resolved" is not among them since build 129. It and Dismiss closed
    the open row and did nothing else - neither stopped the condition being
    raised again, because `raise_attention` dedupes on open records only - so
    two buttons offered one behaviour under two names.

    Dismiss is the one that stayed: *"I would keep Dismiss only, because we
    already have dismiss all."*  ``RESOLVED`` now means exactly one thing,
    which is that AniFlow dealt with it.
    """
    if attention.kind is AttentionKind.IMPORT_BLOCKED and attention.detail == "target_exists":
        # A duplicate. Both ways out are destructive in one direction, so both
        # are named for what they delete rather than for what they keep.
        return ["delete_download", "replace_library", "dismiss"]
    if attention.kind is AttentionKind.IMPORT_BLOCKED and attention.detail == "ready_to_move":
        return ["move_now", "delete_download", "dismiss"]
    if attention.kind is AttentionKind.NEW_VERSION:
        return ["approve", "dismiss"]
    if attention.kind is AttentionKind.DUPLICATE_FILES:
        # Named for what they delete, like the duplicate-download pair above.
        return ["delete_older", "delete_newer", "dismiss"]
    if attention.kind is AttentionKind.AMBIGUOUS_RELEASE:
        return ["assign", "dismiss"]
    if attention.kind is AttentionKind.DOWNLOAD_MISSING:
        # Waits by default (the queue pass re-hands it on its own), but a
        # person impatient enough to be looking at this page gets a button
        # too - see retry_download_now.
        return ["retry_now", "dismiss"]
    return ["dismiss"]


def _where(path, root) -> str:
    """One file as "root/folder/name".

    Relative to its storage root rather than absolute (build 130): the user
    has to recognise the file, not resolve it, and inside a container the
    absolute path is not the one they would see anyway. The root's name is
    what answers *which* of the three places this is - which was the missing
    half of "this episode is already in your library": *"does this mean this
    file exist twice somewhere. But WHERE, and thats my problem."*
    """
    try:
        return path.relative_to(root.parent).as_posix()
    except (ValueError, AttributeError):
        return getattr(path, "name", str(path))


def files_for(attention, download, episode, roots) -> tuple[str | None, str | None]:
    """The two files a duplicate is about: the new one and the one in the way.

    Both as paths relative to their storage root, folder included, because the
    question the warning has to answer is *where* - reported as *"does this
    mean this file exist twice somewhere. But WHERE, and thats my problem."*

    A file that is *not* on disk is still reported as absent, by returning
    None - which is deliberate and older than this build. The filesystem is
    consulted at read time so that a copy the user has already deleted by hand
    stops being offered for deletion, and test_duplicate_resolutions.py guards
    it by name. Build 130 tried to report missing files as "recorded, not
    there now" and that test caught it: a helpful string in this slot is a
    delete button for a file that is gone.
    """
    from backend.app.services.storage import download_path_candidates, media_path, roots_for

    if download is None or episode is None:
        return None, None

    new_file: str | None = None
    candidates = [
        *download_path_candidates(
            roots,
            episode.download_filename,
            download_folder=episode.download_folder,
        ),
        *download_path_candidates(roots, download.label),
    ]
    for candidate in candidates:
        if candidate.exists():
            new_file = _where(candidate, roots.download_root)
            break

    anime = episode.anime
    library_roots = roots_for(anime, roots)
    old = media_path(library_roots, anime.media_folder if anime else None, episode.media_filename)
    library_file = (
        _where(old, library_roots.media_root) if old is not None and old.exists() else None
    )
    return new_file, library_file


def delete_downloaded_file(session: Session, attention_id: str) -> str:
    """Throw away the file AniFlow downloaded, and close the attention.

    Only ever the download folder, which is AniFlow's own working space. The
    library is not touched by this - that is the other button.
    """
    from backend.app.services.downloads import describe_download

    from backend.app.services.storage import download_path_candidates, storage_roots

    attention = _get_or_404(session, attention_id)
    download = session.get(TorrentDownload, attention.download_id) if attention.download_id else None
    if download is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="This attention has no download to clean up")
    episode = download.episode
    roots = storage_roots()
    deleted: str | None = None
    for candidate in (
        *download_path_candidates(
            roots,
            episode.download_filename if episode else None,
            download_folder=episode.download_folder if episode else None,
        ),
        *download_path_candidates(roots, download.label),
    ):
        if candidate.is_file():
            candidate.unlink()
            deleted = candidate.name
            break
    # The row goes with it: it describes a file that no longer exists, and
    # leaving it would have the importer looking for it for ever.
    label = describe_download(download)
    session.delete(download)
    session.flush()
    if episode is not None:
        session.refresh(episode)
        from backend.app.services.episode_state import refresh_episode_status

        refresh_episode_status(episode)
    attention.status = AttentionStatus.RESOLVED
    attention.resolved_at = datetime.now(UTC)
    attention.note = f"deleted the downloaded file: {deleted}" if deleted else "the file was already gone"
    log_activity(
        session,
        kind="DUPLICATE_RESOLVED",
        message=f"{label}: deleted the downloaded copy, kept the one in the library",
        details={"deleted": deleted},
        anime_id=attention.anime_id,
        episode_id=episode.id if episode is not None else None,
    )
    logger.info("%s: deleted the downloaded duplicate %s", label, deleted)
    return deleted or ""


def replace_library_file(session: Session, attention_id: str) -> str:
    """Keep the new file: remove the one in the library and let the new one in.

    The old file is deleted from the media folder because it is what blocks
    the new one - two files for one episode is what Shoko Server would have to
    resolve, and it is what the user asked to avoid. The new file is *not*
    moved here: it goes through the normal import on the next pass, which in
    Shoko Server mode means the import folder and Shoko identifying it. One
    pipeline, one set of rules.
    """
    from backend.app.services.downloads import describe_download

    from backend.app.services.storage import media_path, roots_for, storage_roots

    attention = _get_or_404(session, attention_id)
    download = session.get(TorrentDownload, attention.download_id) if attention.download_id else None
    episode = download.episode if download is not None else None
    if download is None or episode is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="This attention has no download to place")
    anime = episode.anime
    roots = roots_for(anime, storage_roots())
    target = media_path(roots, anime.media_folder if anime else None, episode.media_filename)
    removed: str | None = None
    if target is not None and target.is_file():
        removed = target.name
        target.unlink()
    # The episode has no file any more, so the import pass sees an episode
    # that needs one - which is exactly the state it knows how to handle.
    episode.media_filename = None
    episode.media_size_bytes = None
    episode.media_imported_at = None
    download.status = DownloadStatus.COMPLETED
    download.attention_reason = None
    download.missing_since = None
    from backend.app.services.episode_state import refresh_episode_status

    refresh_episode_status(episode)
    attention.status = AttentionStatus.RESOLVED
    attention.resolved_at = datetime.now(UTC)
    attention.note = f"removed {removed} and queued the new file" if removed else "queued the new file"
    log_activity(
        session,
        kind="DUPLICATE_RESOLVED",
        message=f"{describe_download(download)}: removed the old file and queued the new one",
        details={"removed": removed},
        anime_id=attention.anime_id,
        episode_id=episode.id,
    )
    logger.info(
        "%s: removed %s from the library; the new file goes through the normal import",
        describe_download(download),
        removed,
    )
    return removed or ""


def retry_import_now(session: Session, attention_id: str) -> None:
    """Hand a waiting file back to the import pass.

    The Move button. Nothing is moved here - the importer owns every rule
    about folders, versions, the Shoko hand-over and what happens to the
    torrent afterwards, and a second implementation of that would drift.
    """
    from backend.app.services.downloads import describe_download

    attention = _get_or_404(session, attention_id)
    download = session.get(TorrentDownload, attention.download_id) if attention.download_id else None
    if download is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="This attention has no download to move")
    download.status = DownloadStatus.COMPLETED
    download.attention_reason = None
    download.missing_since = None
    attention.status = AttentionStatus.RESOLVED
    attention.resolved_at = datetime.now(UTC)
    attention.note = "queued for import"
    logger.info("%s: queued for import on request", describe_download(download))


def retry_download_now(session: Session, attention_id: str, *, client=None) -> None:
    """User override for DOWNLOAD_MISSING: hand the torrent to the client again.

    The Retry now button. The queue pass already does this on its own every
    half hour - this is for the person looking at the page who does not want
    to wait. Idempotent the same way that pass is: qBittorrent answers
    "already present" for a torrent it still has, so a retry against one that
    quietly came back on its own cannot produce a duplicate.
    """
    from fastapi import HTTPException

    from backend.app.services.downloads import _client_from, describe_download, retry_one_download

    attention = _get_or_404(session, attention_id)
    download = session.get(TorrentDownload, attention.download_id) if attention.download_id else None
    if download is None:
        raise HTTPException(status_code=409, detail="This attention has no download to retry")
    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = client or _client_from(settings, None)
    if effective_client is None:
        raise HTTPException(status_code=503, detail="qBittorrent is not configured")
    try:
        download.missing_since = None
        download.attention_reason = None
        error = retry_one_download(session, effective_client, download, settings.qb_category)
        if error:
            # _hand_to_client sets FAILED on a hard refusal, which would read
            # as a harder problem than this still is - a retry that could not
            # reach the client is the same waiting condition, not a new one.
            download.status = DownloadStatus.ATTENTION
            download.attention_reason = "missing_in_client"
            download.error = error
            logger.info("%s: retry did not recover it (%s)", describe_download(download), error)
        else:
            attention.status = AttentionStatus.RESOLVED
            attention.resolved_at = datetime.now(UTC)
            attention.note = "handed to the client again by request"
            logger.info("%s: handed to the client again on request", describe_download(download))
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()


def approve_new_version_download(
    session: Session,
    attention_id: str,
    *,
    client=None,
) -> TorrentDownload:
    """User override for NEW_VERSION: queue the newer release explicitly."""
    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = client or (
        client_for(settings.torrent_client, settings.qb_url, settings.qb_username, settings.qb_password,
                   timeout=get_settings().external_request_timeout_seconds)
        if settings.qb_url else None
    )
    try:
        attention = _get_or_404(session, attention_id)
        if attention.kind is not AttentionKind.NEW_VERSION:
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail="This attention is not a new-version case")
        release = session.get(Release, attention.release_id) if attention.release_id else None
        if release is None or not release.torrent_url:
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail="The referenced release no longer exists")
        if effective_client is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="qBittorrent is not configured")

        accepted = effective_client.add_torrent(
            release.torrent_url, category=settings.qb_category, tag=f"aniflow-{release.id}"
        )
        # The queue pass already created an ATTENTION row for this release;
        # approval flips it to QUEUED instead of inserting a duplicate.
        row = session.scalar(select(TorrentDownload).where(TorrentDownload.release_id == release.id))
        if row is None:
            episode = _episode_for_release(session, release)
            if episode is None:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=409,
                    detail="The release's episode number could not be determined",
                )
            row = TorrentDownload(release_id=release.id, episode_id=episode.id)
        row.anime_id = release.anime_id
        row.version = release.version
        row.status = DownloadStatus.QUEUED if accepted else DownloadStatus.FAILED
        row.error = None if accepted else "client rejected the torrent"
        row.attention_reason = None
        session.add(row)
        attention.status = AttentionStatus.RESOLVED
        attention.resolved_at = datetime.now(UTC)
        attention.note = "new version approved by user"
        log_activity(
            session,
            kind="DOWNLOAD_APPROVED",
            message=f"User approved v{release.version} of {release.raw_name}",
            details={"release_id": release.id},
            anime_id=release.anime_id,
            episode_id=row.episode_id,
        )
        session.commit()
        session.refresh(row)
        return row
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()


def assign_ambiguous_release(
    session: Session,
    attention_id: str,
    anime_id: str,
    *,
    client=None,
):
    """User picks which tracked anime an AMBIGUOUS_RELEASE belongs to.

    The release is re-evaluated deterministically against the chosen anime's
    profile. A match queues it; a mismatch reports the failure codes without
    changing anything persistent except the resolved attention.
    """
    from fastapi import HTTPException

    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = client or (
        client_for(settings.torrent_client, settings.qb_url, settings.qb_username, settings.qb_password,
                   timeout=get_settings().external_request_timeout_seconds)
        if settings.qb_url else None
    )
    try:
        attention = _get_or_404(session, attention_id)
        if attention.kind is not AttentionKind.AMBIGUOUS_RELEASE:
            raise HTTPException(status_code=409, detail="This attention is not an ambiguous-release case")
        release = session.get(Release, attention.release_id) if attention.release_id else None
        anime = session.get(Anime, anime_id)
        if release is None or anime is None or anime.profile is None:
            raise HTTPException(status_code=409, detail="Release or anime profile is missing")
        try:
            parsed = parse_release_name(release.raw_name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="The release name can no longer be parsed") from exc

        titles = [
            anime.display_name, anime.local_name, anime.title_english or "",
            anime.title_romaji or "", anime.title_native or "", *(anime.aliases or []),
        ]
        result = evaluate_release(
            parsed,
            subgroups=list(anime.profile.subgroups),
            resolution=anime.profile.resolution,
            media_source=anime.profile.media_source,
            edition=anime.profile.edition or [],
            edition_not=anime.profile.edition_not or [],
            censor_preference=anime.profile.censor_preference.value,
            candidate_titles=titles,
            require_title_match=anime.profile.require_title_match,
            require_resolution_known=anime.profile.require_resolution_known,
            require_source_known=anime.profile.require_source_known,
            require_censor_known=anime.profile.require_censor_known,
        )

        attention.status = AttentionStatus.RESOLVED
        attention.resolved_at = datetime.now(UTC)
        attention.note = f"assigned to {anime.display_name}"

        if not result.matched:
            release.failure_reasons = result.failures
            log_activity(
                session, kind="ASSIGN_REJECTED",
                # *"Assignment refused: I have no idea what this is."*  It is
                # the outcome of assigning an ambiguous release by hand: the
                # release was re-checked against that anime's own profile and
                # did not fit, so nothing was queued.
                message=(
                    "You assigned a release to this anime and it does not "
                    "match its release profile, so nothing was queued"
                ),
                details={"failures": result.failures}, anime_id=anime.id,
            )
            session.commit()
            return {"matched": False, "failures": result.failures}

        release.decision = ReleaseDecision.MATCHED
        release.anime_id = anime.id
        release.failure_reasons = []
        release.ambiguous_anime_ids = []
        episode = _episode_for_release(session, release, anime_id=anime.id)
        log_activity(
            session, kind="ASSIGN_MATCHED",
            message="You assigned a release to this anime; it matched and was queued",
            details={"release_id": release.id}, anime_id=anime.id,
            episode_id=episode.id if episode is not None else None,
        )
        if effective_client is not None and release.torrent_url and episode is not None:
            accepted = effective_client.add_torrent(
                release.torrent_url, category=settings.qb_category, tag=f"aniflow-{release.id}"
            )
            session.add(
                TorrentDownload(
                    release_id=release.id,
                    anime_id=anime.id,
                    episode_id=episode.id,
                    version=release.version,
                    status=DownloadStatus.QUEUED if accepted else DownloadStatus.FAILED,
                    error=None if accepted else "client rejected the torrent",
                )
            )
        session.commit()
        return {"matched": True, "failures": []}
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()


def _episode_for_release(session: Session, release, *, anime_id: str | None = None):
    """The episode row an RSS release belongs to, created if needed.

    A download cannot exist without an episode, so a release whose episode
    number is not a single number (a batch, say) yields None and the caller
    reports that instead of attaching it somewhere arbitrary.
    """
    from backend.app.services.downloads import resolve_episode
    from backend.app.services.episode_state import episode_number_for, episode_number_from_label

    target_anime = anime_id or release.anime_id
    if target_anime is None:
        return None
    anime_row = session.get(Anime, target_anime)
    number = episode_number_for(anime_row, release.episode)
    if number is None:
        # An ambiguous release may never have been parsed into columns; fall
        # back to its name rather than refusing the user's explicit decision.
        try:
            number = episode_number_for(
                anime_row, parse_release_name(release.raw_name).episode
            )
        except ValueError:
            number = None
    if number is None:
        return None
    return resolve_episode(session, target_anime, number)


def _get_or_404(session: Session, attention_id: str) -> Attention:
    attention = session.get(Attention, attention_id)
    if attention is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Attention not found")
    return attention


__all__ = [
    "dismiss_all",
    "approve_new_version_download",
    "assign_ambiguous_release",
    "dismiss_attention",
    "log_activity",
    "raise_attention",
    "resolve_open",
    "retry_download_now",
]
