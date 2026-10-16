"""One of every record, on demand, so they can be looked at (build 126).

Asked for: *"is it possible to create a debug test button in attention and
activity, if pressed it will display some fake warnings and errors, so I can
actually verify all of them and the look and the button solution etc. Because
else I always need to wait until a real time error appears. If possible to
list ALL possible errors."*

The problem is real and it is not only about convenience. Ten attention kinds
and sixteen activity kinds exist; most fire only when something specific goes
wrong on a live deployment, so *most of them have never been seen on screen*.
Three activity kinds turned out to have had no translation since build 119 for
exactly that reason - nothing ever rendered them, so nothing revealed that the
page was printing raw identifiers. Being able to summon the whole set turns
that class of defect from "found eventually, by accident" into "found by
looking".

How the samples stay safe
-------------------------

**Marked, not disguised.** Every record carries :data:`MARKER` - in the
attention note and in the activity details - and every message says "sample".
So they are identifiable in the database, removable exactly, and impossible to
mistake for a real fault while reading the page.

**No invented rows.** A sample never creates an anime, an episode or a
download. It links to real ones when the watchlist has any, and to nothing
when it does not. That matters: a fake download row would be picked up by the
sync pass and handed to qBittorrent, and a fake episode would change what the
watchlist says it owns. A sample that changes AniFlow's behaviour is not a
sample.

The consequence, worth knowing while testing: an action button on a sample
usually fails, because there is no real download behind it to delete or move.
The button being *there*, and what it says it will do, is what these are for.

**Debug mode only.** The route is refused unless ``debug_mode`` is on, the
same gate the Release Inspector and the database record sit behind.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.activity import Activity, Attention, AttentionKind, AttentionStatus
from backend.app.models.anime import Anime, Episode
from backend.app.services.activity_kinds import ALL_KINDS

logger = for_component("Debug")

#: What makes a sample a sample. Written into the attention note and the
#: activity details, so removal can be exact rather than "everything recent".
MARKER = "[aniflow-sample]"

#: A plausible `detail` for each attention kind, chosen to be the one worth
#: looking at: the code the interface has to put words to, or the sentence a
#: raiser really writes. Kinds whose detail is free text get a real-looking
#: sentence; the rest get a registered reason code.
_DETAILS: dict[AttentionKind, str | None] = {
    AttentionKind.NEW_VERSION: None,
    AttentionKind.AMBIGUOUS_RELEASE: None,
    AttentionKind.IMPORT_BLOCKED: "target_exists",
    AttentionKind.DOWNLOAD_MISSING: "missing_in_client",
    AttentionKind.FEED_FAILED: "the feed did not answer in time",
    AttentionKind.NEEDS_CONVERSION: "Sample Show was added without an indexer and has no release name",
    AttentionKind.NO_RELEASES: None,
    AttentionKind.DOWNLOAD_STALLED: None,
    AttentionKind.METADATA_CHANGED: "episode_count: Sample Show - anilist says 13, you set 12",
    AttentionKind.EPISODE_NOT_IDENTIFIABLE: "episode_not_identifiable",
}


#: What each sample says. Written as the real messages are - no anime name,
#: because the page prints that underneath - so the samples can be used to
#: judge the real wording rather than only the layout (build 127).
_SAMPLE_MESSAGES = {
    "EPISODE_ACQUIRED": "Episode 7 is now in the library as [SubsPlease] Sample - 07 (1080p).mkv",
    "EPISODE_BLOCKED": "Episode 7 finished downloading but could not be placed: target_exists",
    "RSS_EPISODE_FOUND": "Episode 7 was found on a feed and added to the watchlist",
    "LIBRARY_ADOPTED": "A file already in the library was adopted for episode 7",
    "LIBRARY_UNPLACED": "A file in the library belongs to no episode AniFlow tracks",
    "LIBRARY_SUPERSEDED": "The replaced file for episode 7 was deleted",
    "LIBRARY_SUPERSEDED_LEFT": "The replaced file for episode 7 was renamed to .backup and left for you",
    "FILES_RENAMED": "3 files were renamed after a release change",
    "LIBRARY_MOVED": "Moved from Airing now to Earlier seasons",
    "LIBRARY_MOVED_BY_SHOKO": "Shoko filed it in Movies instead of Series; AniFlow now follows it there",
    "TORRENT_ADOPTED": "A finished torrent nobody had a row for was taken on as episode 7",
    "TORRENT_UNCLAIMED": "A finished torrent could not be matched to any episode",
    "DOWNLOAD_APPROVED": "You approved v2 of episode 7, replacing the copy already here",
    "DUPLICATE_RESOLVED": "The duplicate of episode 7 was dealt with",
    "ASSIGN_MATCHED": "You assigned a release to this anime; it matched and was queued",
    "ASSIGN_REJECTED": "You assigned a release to this anime and it does not match its release profile, so nothing was queued",
    "INDEXER_UNREACHABLE": "The indexer could not be reached, so no releases were found for this anime: connection timed out",
    "INDEXER_MISSING": "Added without searching: no indexer is configured, so no releases were looked for",
    "RECONCILED": "Corrected after checking the disk: episode 7 was imported, and its torrent has gone",
}


def _subjects(session: Session) -> tuple[list[Anime], list[Episode]]:
    """Real anime and episodes to point the samples at, if there are any.

    Deliberately read-only. A sample links to what exists or to nothing; it
    never creates a subject, because a subject is the thing every pass in
    AniFlow walks.
    """
    anime = list(session.scalars(select(Anime).order_by(Anime.created_at).limit(4)))
    episodes = list(session.scalars(select(Episode).order_by(Episode.number).limit(4)))
    return anime, episodes


def seed(session: Session) -> dict[str, int]:
    """Create one attention record per kind, and one activity entry per kind.

    Idempotent in the way that matters: :func:`clear` first, so pressing the
    button twice leaves one set rather than two.
    """
    removed = clear(session)
    anime, episodes = _subjects(session)
    now = datetime.now(UTC)

    attentions = 0
    for index, kind in enumerate(AttentionKind):
        subject = anime[index % len(anime)] if anime else None
        episode = episodes[index % len(episodes)] if episodes else None
        session.add(
            Attention(
                kind=kind,
                status=AttentionStatus.OPEN,
                anime_id=subject.id if subject is not None else None,
                # The episode is part of an attention's subject, so several
                # kinds want one. A feed failure deliberately does not: it is
                # about a source, and pinning it to an episode would model it
                # wrongly even in a sample.
                episode_id=(
                    episode.id
                    if episode is not None and kind is not AttentionKind.FEED_FAILED
                    else None
                ),
                detail=_DETAILS.get(kind),
                note=MARKER,
                created_at=now - timedelta(minutes=index),
                updated_at=now - timedelta(minutes=index),
            )
        )
        attentions += 1

    # Both grouping cases, so each can be checked (build 128):
    #
    # * **two records on one anime** with one reason, which *should* collapse
    #   into a group - *"lets say it was Iron Wok Jan Episode 1 and Episode 3,
    #   then it makes sense to group them"*;
    # * **two records on different anime** with the same reason, which should
    #   stay apart - the bug reported against 127, where they did not.
    #
    # The per-kind records above already cover the second case for every other
    # kind, because they walk the anime list; this adds the first.
    grouped_subject = anime[0] if anime else None
    for offset in (1, 2):
        session.add(
            Attention(
                kind=AttentionKind.IMPORT_BLOCKED,
                status=AttentionStatus.OPEN,
                anime_id=grouped_subject.id if grouped_subject is not None else None,
                episode_id=episodes[offset % len(episodes)].id if episodes else None,
                detail="target_exists",
                note=MARKER,
                created_at=now - timedelta(minutes=30 + offset),
                updated_at=now - timedelta(minutes=30 + offset),
            )
        )
        attentions += 1

    # And one more of the same kind and reason on a *different* anime, so the
    # "these must not merge" case is visible beside the one that must.
    if len(anime) > 1:
        session.add(
            Attention(
                kind=AttentionKind.IMPORT_BLOCKED,
                status=AttentionStatus.OPEN,
                anime_id=anime[1].id,
                episode_id=episodes[0].id if episodes else None,
                detail="target_exists",
                note=MARKER,
                created_at=now - timedelta(minutes=33),
                updated_at=now - timedelta(minutes=33),
            )
        )
        attentions += 1

    activities = 0
    for index, kind in enumerate(ALL_KINDS):
        subject = anime[index % len(anime)] if anime else None
        name = subject.display_name if subject is not None else "Sample Show"
        session.add(
            Activity(
                kind=kind,
                message=_SAMPLE_MESSAGES.get(
                    kind, f"Sample entry for {kind.replace('_', ' ').lower()}"
                ),
                # The marker lives here rather than in the message so the
                # message reads like the real thing. The level a row is shown
                # at is derived from these counters, so one of each is seeded
                # to exercise the escalation rule as well.
                details={
                    "sample": MARKER,
                    "episode": 7,
                    **({"failed": 1} if kind.endswith("BLOCKED") else {}),
                },
                anime_id=subject.id if subject is not None else None,
                created_at=now - timedelta(minutes=index),
            )
        )
        activities += 1

    logger.info(
        "seeded %s sample attention record(s) and %s sample activity entr(ies)",
        attentions,
        activities,
    )
    return {"attention": attentions, "activity": activities, "removed": removed}


def clear(session: Session) -> int:
    """Remove every sample, and nothing else.

    Matched on :data:`MARKER` rather than on age or kind, so a real record
    that happens to share a kind with a sample is never touched.
    """
    removed = 0
    for record in session.scalars(select(Attention).where(Attention.note == MARKER)):
        session.delete(record)
        removed += 1
    for entry in session.scalars(select(Activity)):
        if (entry.details or {}).get("sample") == MARKER:
            session.delete(entry)
            removed += 1
    if removed:
        logger.info("removed %s sample record(s)", removed)
    return removed


def counts(session: Session) -> dict[str, int]:
    """How many samples are in place, for the button to say so."""
    attention = len(list(session.scalars(select(Attention).where(Attention.note == MARKER))))
    activity = sum(
        1
        for entry in session.scalars(select(Activity))
        if (entry.details or {}).get("sample") == MARKER
    )
    return {"attention": attention, "activity": activity}


__all__ = ["MARKER", "clear", "counts", "seed"]
