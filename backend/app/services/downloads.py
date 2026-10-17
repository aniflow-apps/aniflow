"""Queueing releases into qBittorrent and monitoring their states.

Business rules implemented here (AGENTS.md):

- A release is queued at most once (unique constraint on ``release_id``).
- The same torrent is never queued twice (unique ``info_hash`` plus a version
  check against the episode's existing downloads).
- Versions V2/V3 arriving after an owned V1 are never downloaded
  automatically; they become ATTENTION records for the user.
- Torrents that vanish from the client or error out surface as ATTENTION.
- Local media is never deleted implicitly; removal deletes the torrent only.

Every download belongs to exactly one episode by foreign key. Episode status
is never written here - it is derived in ``services.episode_state`` from the
download's state, which is why a download can no longer silently fail to
update its episode.

All functions are synchronous and run inside worker threads.
"""
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.integrations.qbittorrent.base import download_state
from backend.app.integrations.torrent_client import TorrentClient, TorrentClientError, client_for
from backend.app.models.activity import AttentionKind
from backend.app.models.anime import (
    DOWNLOADABLE_STATUSES,
    Anime,
    AnimeLifecycle,
    Episode,
    EpisodeStatus,
)
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.models.episode_release import EpisodeRelease
from backend.app.models.rss import Release, ReleaseDecision
from backend.app.services.app_settings import load_app_settings
from backend.app.services.attention import (
    describe_changes,
    log_activity,
    raise_attention,
    resolve_open,
)
from backend.app.services.storage import locate_episode_file, storage_roots
from backend.app.services.episode_state import (
    episode_number_for,
    episode_number_from_label,
    record_download_filename,
    refresh_episode_status,
    resolve_episode,
)
from backend.app.services.matching import VersionAction, decide_version
from backend.app.services.release_parser import parse_release_name

logger = for_component("Download")

#: How long a torrent may be absent from the client before anybody is told.
#: Long enough to cover being added, hash-checked, categorised and picked up -
#: which is what made the warning look like a fault when it was raised on the
#: first sync pass a few seconds after queueing. A torrent that really is gone
#: is still reported, five minutes later.
MISSING_GRACE = timedelta(minutes=5)

#: Pause between two single-torrent hand-overs in the same pass (166).
#: Reported live: qBittorrent visibly adding torrents one at a time and
#: stopping after six, watched directly in its own UI - the same "N rapid
#: requests" shape the batch endpoint (build 120) exists to avoid, here for
#: whatever the batch did not bring in. The same rule already in force for
#: the indexer APIs (services/calendar_pacer.py): sequential and spaced.
SINGLE_HANDOVER_PACING_SECONDS = 1.0

#: How many magnets go in one wave of the batch hand-over, and how long to
#: wait before the next wave (166). Found live, watching qBittorrent's own Web
#: UI: *"Browser Web UI Connection Limit ... your browser enforces a strict
#: default limit of 6 concurrent HTTP connections per domain ... do not force
#: the limit ... at 5, wait like 3 sec and add the next 5."*  Kept under that
#: limit rather than at it, on purpose.
BATCH_CHUNK_SIZE = 5
BATCH_CHUNK_PACING_SECONDS = 3.0


def _pause_before_hand_over(already: int) -> None:
    """The pause before the next single hand-over, once ``already`` have gone (item 1).

    The same waves the batch uses: a short gap between torrents and a longer one after every
    ``BATCH_CHUNK_SIZE``. Used by every loop that hands torrents over one at a time - the queue pass's
    fallback, the retry of stranded rows and the retry of missing ones. The last two had no pacing at
    all and handed a whole season over back to back, which is how only six arrived at first and the
    rest a minute or two later.
    """
    if already <= 0:
        return
    if already % BATCH_CHUNK_SIZE == 0:
        time.sleep(BATCH_CHUNK_PACING_SECONDS)
    else:
        time.sleep(SINGLE_HANDOVER_PACING_SECONDS)


def _as_utc(moment: datetime) -> datetime:
    """A stored timestamp made comparable.

    SQLite hands back naive datetimes, and comparing one of those with an
    aware "now" raises. Assumed UTC, which is what everything here stores.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)

_TRACKED_LIFECYCLES = (AnimeLifecycle.ACTIVE, AnimeLifecycle.UPCOMING)

#: Prefix of the marker tag written into qBittorrent for every download row.
_TAG_PREFIX = "aniflow-"

#: The tag every batched hand-over carries (build 131).
#:
#: Build 120 sent the batch with a category and no tag, on the reasoning that
#: the magnet's own hash identifies the row afterwards. That is true, and it is
#: not what the tag is for: services/torrent_sweep.py decides which torrents
#: are AniFlow's *by tag*, and filters on that before it ever tries to match a
#: row by hash. No tag meant the hourly sweep - the safety net built in build
#: 94 for exactly the complaint that came back - could not see a single one of
#: them.
#:
#: One value for the whole batch is enough: qBittorrent takes one `tags` per
#: request, and identifying the row is the hash's job. It starts with
#: _TAG_PREFIX so the sweep's existing filter recognises it unchanged.
BATCH_TAG = f"{_TAG_PREFIX}batch"

#: Download states that still occupy an episode (no re-queue while one is set).
_OCCUPYING = (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.COMPLETED)


def _blocking(episode: Episode) -> TorrentDownload | None:
    """The download that makes queueing this episode pointless, if any.

    A QUEUED or DOWNLOADING row always blocks: the episode is on its way, and
    a second copy of the same file helps nobody.

    A COMPLETED row is different, and getting that wrong is what was reported
    (build 117): *"The media file is gone - it can be downloaded again"*, a
    magnet ready, and the Download button answering "already owned or
    downloading". The row was true once. Then the file went - deleted by a
    release swap, or removed outside AniFlow and noticed by the reconcile pass
    - the episode became MISSING, and the row stayed COMPLETED for ever. So a
    finished download blocked the one case the whole MISSING state exists for.

    A COMPLETED row for an episode with no file is therefore history rather
    than an obstacle. It still blocks an episode that is COMPLETE or on its
    way into the library, where it is exactly right.
    """
    live = [row for row in episode.downloads if row.status in _OCCUPYING]
    if not live:
        return None
    if episode.status is EpisodeStatus.MISSING:
        # The file is gone. Only something still in flight can block.
        in_flight = [
            row
            for row in live
            if row.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)
        ]
        return in_flight[0] if in_flight else None
    return live[0]

#: The client's word for "connected, wanting data, and nobody is sending any".
#:
#: Deliberately not `stalledUP`, which is a finished torrent seeding with
#: nobody asking - the normal resting state of everything AniFlow has ever
#: downloaded, and reporting it would teach people to ignore the report.
STALLED_STATE = "stalledDL"

#: How long a download has to be getting nowhere before it is worth saying so.
#: Every torrent stalls briefly while it looks for peers; six hours of nothing
#: is a release nobody is seeding.
STALLED_AFTER = timedelta(hours=6)


@dataclass
class QueueSummary:
    considered: int = 0
    queued: int = 0
    skipped: int = 0
    attention: int = 0
    #: Releases recorded for an anime whose Automate switch is off. The episode
    #: becomes available to download; nothing is handed to the client.
    manual: int = 0
    # Named, so Activity and the log can say which episode was queued.
    changes: list[str] = field(default_factory=list)
    failed: int = 0


@dataclass
class SyncSummary:
    checked: int = 0
    updated: int = 0
    completed: int = 0
    attention: int = 0
    #: Marker tags removed from the client during this pass.
    tags_pruned: int = 0
    # What actually changed, in words - the counters alone say nothing about
    # which anime a log line is talking about.
    changes: list[str] = field(default_factory=list)


def describe_download(row: TorrentDownload) -> str:
    """A human label for one download: "Frieren - Episode 5".

    Falls back through the names that exist, so a row is always identifiable
    even before the client has reported anything about it.
    """
    anime = row.anime.display_name if row.anime is not None else None
    episode = row.episode.number if row.episode is not None else None
    if anime and episode is not None:
        return f"{anime} - Episode {episode}"
    if anime:
        return anime
    return row.label or row.info_hash or row.id


def _download_tag(download_id: str) -> str:
    """Marker tag written to qBittorrent so a torrent can be found again."""
    return f"{_TAG_PREFIX}{download_id}"


def _tags_carried(client: TorrentClient) -> set[str] | None:
    """Every tag some torrent of the client carries, in any category; None when the client cannot say."""
    lister = getattr(client, "list_torrents", None)
    if lister is None:
        return None
    try:
        torrents = lister()
    except TorrentClientError:
        return None
    carried: set[str] = set()
    for torrent in torrents:
        raw = getattr(torrent, "tags", None) or ()
        carried.update(
            tag.strip() for tag in (raw.split(",") if isinstance(raw, str) else raw) if str(tag).strip()
        )
    return carried


def prune_download_tags(session: Session, client: TorrentClient) -> int:
    """Delete the marker tags qBittorrent no longer needs.

    A tag is the only handle on a torrent until the client reports its hash;
    afterwards nothing reads it again. qBittorrent keeps a tag in its global
    list once created, so removing a torrent after import left the tag behind
    - one dead entry per episode, which fills the client's sidebar.

    Kept: tags whose download row still exists *and* has no hash yet - and, for a
    row that is no longer waiting on the client (failed, say), only while a
    torrent in the client actually carries the tag, which is how a torrent that
    was added but never reported is found again. The rest - imported, cleared,
    never ours, or the tag of a failed attempt that left no torrent behind (the
    ones that stayed for ever until build 218) - goes. A client that cannot say
    which torrents carry what keeps the old rule. Returns how many were deleted.
    """
    try:
        tags = [tag for tag in client.list_tags() if tag.startswith(_TAG_PREFIX)]
    except TorrentClientError:
        return 0  # the client will still be there next cycle
    if not tags:
        return 0
    waiting = (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)
    carried = _tags_carried(client)
    still_needed = {
        row_id
        for row_id, status in session.execute(
            select(TorrentDownload.id, TorrentDownload.status).where(TorrentDownload.info_hash.is_(None))
        )
        if status in waiting or carried is None or f"{_TAG_PREFIX}{row_id}" in carried
    }
    stale = [tag for tag in tags if tag[len(_TAG_PREFIX):] not in still_needed]
    if not stale:
        return 0
    try:
        client.delete_tags(stale)
    except TorrentClientError as exc:
        logger.info("could not delete %s stale marker tag(s): %s", len(stale), exc)
        return 0
    logger.info("deleted %s stale marker tag(s) from the client", len(stale))
    return len(stale)


def batch_group(session: Session, row: TorrentDownload) -> list[TorrentDownload]:
    """The rows that share one torrent with this one - the owner first - or just this row (build 213).

    A batch torrent serves several episodes: the first keeps the torrent, the rest point at it through
    ``batch_download_id``. An ordinary row is a group of one.
    """
    owner_id = row.batch_download_id or row.id
    owner = row if row.id == owner_id else session.get(TorrentDownload, owner_id)
    if owner is None:
        return [row]
    siblings = list(
        session.scalars(select(TorrentDownload).where(TorrentDownload.batch_download_id == owner.id))
    )
    return [owner, *[sibling for sibling in siblings if sibling.id != owner.id]]


#: Rows that no longer have a torrent to wait for: they must not hold a batch's torrent open.
_NOT_WAITING = (DownloadStatus.FAILED, DownloadStatus.REMOVED, DownloadStatus.SKIPPED)


def batch_is_placed(session: Session, row: TorrentDownload) -> bool:
    """Whether every episode this row's torrent was chosen for has its file (build 213).

    What decides when a batch torrent may be paused or removed: not when the first episode lands, but when
    the last wanted one has. An ordinary row is a group of one, so this is just "its episode has a file".
    """
    for member in batch_group(session, row):
        if member.status in _NOT_WAITING:
            continue
        episode = member.episode
        if episode is None or not (episode.media_filename or episode.import_filename):
            return False
    return True


def _sync_batch_siblings(
    session: Session, live: dict, summary: "SyncSummary", now: datetime
) -> None:
    """Keep each further episode of a batch in step with the torrent its owner holds (build 213).

    A sibling has no hash, so the loop above cannot find it in the client - which it would read as "the
    torrent vanished". It takes the owner's torrent instead, and the state, progress and completion that
    go with it. Missing is reported on the owner, not repeated here.
    """
    siblings = list(
        session.scalars(
            select(TorrentDownload).where(
                TorrentDownload.batch_download_id.is_not(None),
                (
                    TorrentDownload.status.in_([DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING])
                    | (
                        (TorrentDownload.status == DownloadStatus.ATTENTION)
                        & (TorrentDownload.attention_reason == "missing_in_client")
                    )
                ),
            )
        )
    )
    for row in siblings:
        owner = session.get(TorrentDownload, row.batch_download_id)
        torrent = live.get(owner.info_hash) if owner is not None and owner.info_hash else None
        if torrent is None:
            if row.missing_since is None:
                row.missing_since = now
            elif now - _as_utc(row.missing_since) >= MISSING_GRACE and row.status is not DownloadStatus.ATTENTION:
                row.status = DownloadStatus.ATTENTION
                row.attention_reason = "missing_in_client"
                summary.updated += 1
                refresh_episode_status(row.episode)
            continue
        row.missing_since = None
        if row.attention_reason == "missing_in_client":
            row.attention_reason = None
        new_state = DownloadStatus(download_state(torrent.state))
        if (
            row.qb_state != torrent.state
            or abs(row.progress - torrent.progress) > 0.0001
            or row.status != new_state
        ):
            row.qb_state = torrent.state
            row.progress = torrent.progress
            row.status = new_state
            summary.updated += 1
            if new_state is DownloadStatus.COMPLETED:
                summary.completed += 1
            refresh_episode_status(row.episode)


def _new_download(**values) -> TorrentDownload:
    """Create a download row with its id assigned up front.

    The id is needed *before* the INSERT, both for the qBittorrent marker tag
    and for attaching Attention records, so it cannot be left to the column
    default.
    """
    return TorrentDownload(id=str(uuid.uuid4()), **values)


def _episode_version_on_disk(episode: Episode) -> int | None:
    """The version this episode already has, from media or a live download.

    ``None`` means *nothing is on disk*, and ``decide_version`` acts on it:
    nothing to compare with is a plain download. So a file that is on disk
    must never answer None, whatever is or is not recorded about it (135).

    That was the state of every episode filed by Shoko Server: it recorded the
    media file without a version, so ``media_version`` was NULL, and the only
    thing standing between the episode and being downloaded again was a live
    download row supplying the version - which Clear all removes. Reported as
    downloads coming back for episodes already in the library.

    services/shoko.py records the version now, but that cannot reach the files
    it filed before this build, and a backfill would only cover the rows that
    exist today. So the question is answered here instead, where every caller
    goes through: a file with no version behind it is **v1**, the same
    convention ``_new_download(version=... or 1)`` and the parser both use.
    A real v2 release then reads as newer and raises a NEW_VERSION record for
    the user to decide, which is the right kind of wrong answer - visible, and
    not a silent re-download of something they already have.
    """
    if episode.media_version is not None:
        return episode.media_version
    versions = [
        download.version for download in episode.downloads if download.status in _OCCUPYING
    ]
    if versions:
        return max(versions)
    return 1 if episode.media_filename else None


#: The last torrent-client state that was reported, so only changes are.
#: "unset" until the first pass, which is why the first answer always logs.
_last_client_state: str = "unset"


def _report_client_state(state: str, detail: str = "") -> None:
    """Log the torrent client's state, but only when it changed.

    A routine pass that reaches the client says nothing at all: at one pass
    every thirty seconds, "still connected" is what buries the line that
    matters. Losing the connection, regaining it, and running with none
    configured are all news, and each is said once.
    """
    global _last_client_state
    if state == _last_client_state:
        return
    previous, _last_client_state = _last_client_state, state
    if state == "unconfigured":
        logger.warning(
            "no torrent client is configured, so nothing can be downloaded - "
            "set the qBittorrent URL on the Settings page"
        )
    elif state == "unreachable":
        logger.warning("the torrent client could not be reached: %s", detail)
    elif previous in ("unreachable", "unconfigured"):
        logger.info("the torrent client is reachable again")


def _client_from(settings, provided: TorrentClient | None) -> TorrentClient | None:
    if provided is not None:
        return provided
    if not settings.qb_url:
        _report_client_state("unconfigured")
        return None
    return client_for(
        settings.torrent_client,
        settings.qb_url,
        settings.qb_username,
        settings.qb_password,
        timeout=get_settings().external_request_timeout_seconds,
    )


def info_hash_of(url: str | None) -> str | None:
    """The info hash inside a magnet link, if it is one (build 120).

    A magnet *is* its hash - ``magnet:?xt=urn:btih:<40 hex>`` - so a torrent
    added from one can be recognised without asking the client which torrent
    is which. That is what makes handing a whole season over in a single
    request possible: the per-row marker tag existed to answer "which of
    these is mine", and for magnets the link answers it before anything is
    sent.

    Returns None for a .torrent URL, whose hash is inside the file and only
    knowable after the client has read it - those keep the one-at-a-time path.
    """
    if not url or not url.lower().startswith("magnet:"):
        return None
    found = re.search(r"xt=urn:btih:([0-9a-zA-Z]+)", url)
    if not found:
        return None
    raw = found.group(1)
    if len(raw) == 40 and all(character in "0123456789abcdefABCDEF" for character in raw):
        return raw.lower()
    if len(raw) == 32:
        # Base32, which older clients and a few trackers still publish.
        import base64

        try:
            return base64.b32decode(raw.upper()).hex()
        except Exception:  # noqa: BLE001 - an unreadable magnet is not a crash
            return None
    return None


def _claim_hash(session: Session, row: TorrentDownload, info_hash: str) -> bool:
    """Give this row the torrent, unless another row already owns it (118).

    Reported as a red banner on the Downloads page that had been there for
    builds: ``UNIQUE constraint failed: downloads.info_hash``. One torrent had
    two download rows pointing at it, the column is unique, and the whole
    transaction was rejected - including a hand-over that had already
    succeeded, which is why pressing Download said "Internal error" about a
    torrent qBittorrent had just accepted.

    Two rows for one torrent is not exotic. Nothing stops a second row for an
    episode whose first row is FAILED or ATTENTION - neither counts as
    occupying, on purpose, because both mean "this did not happen" - and
    handing the same magnet over twice gets the same torrent back. qBittorrent
    is right to have one copy; the database is right to allow one row; what
    was missing was anybody deciding which row that is.

    So it is decided here, on the rule the requester gave: *"if they have
    similar stuff then you can ignore one, if one has missing stuff and the
    other not, use the one without missing stuff"*. The row that knows more
    keeps the torrent, and the other is left without a hash and told why, so
    nothing is silently deleted while somebody is looking at it.

    Returns True when this row now owns the hash.
    """
    if not info_hash:
        return False
    if row.info_hash == info_hash:
        return True

    owner = session.scalar(
        select(TorrentDownload).where(
            TorrentDownload.info_hash == info_hash, TorrentDownload.id != row.id
        )
    )
    if owner is None:
        row.info_hash = info_hash
        return True

    if _knows_more(row, owner):
        # This row is the better record of the same download. The other one
        # gives up the hash rather than being deleted underneath the page.
        owner.info_hash = None
        owner.status = DownloadStatus.SKIPPED
        owner.attention_reason = "duplicate_of_another_row"
        owner.error = (
            "Another download row owns this torrent, and had more of the story."
        )
        # Written before the claim, not with it: the column is unique, and a
        # flush that sets the new owner before clearing the old one hits the
        # constraint on its way through - which is the very error this
        # function exists to prevent.
        session.flush()
        row.info_hash = info_hash
        logger.info(
            "torrent %s moved to the download row that knows more about it", info_hash
        )
        return True

    row.info_hash = None
    row.status = DownloadStatus.SKIPPED
    row.attention_reason = "duplicate_of_another_row"
    row.error = f"The same torrent is already download {owner.id}."
    logger.info(
        "download %s is a duplicate of %s - same torrent", row.id, owner.id
    )
    return False


def _knows_more(candidate: TorrentDownload, other: TorrentDownload) -> int:
    """Whether ``candidate`` is the fuller record of the two.

    Counted rather than ranked: progress, a filename, a release it came from,
    an episode it belongs to. A row with three of those beats a row with one,
    and a tie leaves the row that already had the torrent alone.
    """
    def known(row: TorrentDownload) -> int:
        return sum(
            1
            for value in (
                row.progress,
                row.download_filename if hasattr(row, "download_filename") else None,
                row.episode_release_id,
                row.release_id,
                row.episode_id,
                row.label,
            )
            if value
        )

    return known(candidate) > known(other)


def _hand_over_together(
    session: Session,
    client: TorrentClient,
    plan: list[tuple[TorrentDownload, str]],
    category: str,
) -> dict[str, str | None]:
    """Send every magnet in a few waves, then say which rows arrived (120, 166).

    Reported three times, most recently as: *"only 6 torrents are added to the
    client, while it has 21 episodes. But after like 1-2 mins all of the rest
    was added"*. The retry pass was doing its job; the burst was the problem.
    Twenty-one POSTs in a second, through an HTTPS reverse proxy, is a shape
    that gets rate-limited - and the client offers exactly the alternative the
    report asked about: a newline-separated list, one request.

    One request for the whole season turned out to have the same shape one
    level up: found live, watching qBittorrent's own Web UI - *"Browser Web UI
    Connection Limit ... your browser enforces a strict default limit of 6
    concurrent HTTP connections per domain."*  A season-sized newline list is
    still one POST, but resolving each magnet is several requests behind the
    scenes (metadata, tracker announces, the Web UI's own refresh), and a
    dozen of those at once hits the same wall a dozen separate POSTs would
    have. So the list itself now goes in waves of ``BATCH_CHUNK_SIZE``, paused
    ``BATCH_CHUNK_PACING_SECONDS`` apart - *"do not force the limit ... wait
    like 3 sec and add the next 5"* - rather than as one list, still in the one
    call this function makes.

    Identifying them afterwards is the part that used to need a tag per row,
    and magnets make it free: the hash is in the link. So each row is verified
    against what the client now holds, and anything the batch did not
    (yet - or ever) bring in is returned for the caller to hand over singly.
    The single path is what actually tells the two apart now (build 164): it
    checks for the same hash again before adding anything, so a torrent that
    was merely slow to show up here is picked up there instead of being
    handed to the client a second time.

    The batch still carries **one** tag, though - see BATCH_TAG. Build 120
    dropped the tag entirely, having established that it was not needed to
    identify a row, and that stopped the hourly sweep recognising these
    torrents as AniFlow's: it filters by tag before it matches by hash.

    Returns row id -> error, with None meaning it arrived.
    """
    results: dict[str, str | None] = {}
    if not plan:
        return results

    urls = [url for _row, url in plan]
    chunks = [urls[i : i + BATCH_CHUNK_SIZE] for i in range(0, len(urls), BATCH_CHUNK_SIZE)]
    accepted = False
    error: str | None = None
    for index, chunk in enumerate(chunks):
        try:
            ok = client.add_torrents(
                chunk,
                category=category,
                # Not for identifying the rows - the hash below does that - but
                # so the hourly sweep can tell these are AniFlow's at all. See
                # BATCH_TAG (build 131).
                tag=BATCH_TAG,
            )
            accepted = accepted or ok
        except TorrentClientError as exc:
            error = str(exc)[:255]
        except Exception as exc:  # noqa: BLE001 - never worse than one at a time
            logger.info("one wave of the batch hand-over could not be used: %s", exc)
            error = str(exc)[:255]
        if index < len(chunks) - 1:
            time.sleep(BATCH_CHUNK_PACING_SECONDS)

    live: dict[str, object] = {}
    if accepted:
        try:
            live = {
                torrent.info_hash: torrent
                for torrent in client.find_torrents(category=category)
            }
        except TorrentClientError:
            live = {}

    for row, url in plan:
        wanted = info_hash_of(url)
        torrent = live.get(wanted) if wanted else None
        if torrent is None:
            # Not there, or not recognisable from the link. Left for the
            # single hand-over - which, since build 164, checks for this
            # exact hash again (by then more time has passed) before ever
            # adding it a second time. See _hand_to_client.
            results[row.id] = error or "not in the client after the batch"
            continue
        if not _claim_hash(session, row, str(wanted)):
            results[row.id] = None  # another row owns it; the download is happening
            continue
        row.qb_state = getattr(torrent, "state", None)
        row.progress = getattr(torrent, "progress", None)
        row.status = DownloadStatus(download_state(getattr(torrent, "state", "")))
        row.error = None
        results[row.id] = None
    return results


def _hand_to_client(
    client: TorrentClient,
    row: TorrentDownload,
    url: str,
    category: str,
    session: Session | None = None,
) -> str | None:
    """Add one torrent and reconcile the row with what the client reports.

    Returns an error message, or None on success. A rejected add is verified
    against the marker tag first: qBittorrent answers "Fails." for a duplicate
    that is in fact already there, and treating that as a failure used to mark
    working downloads FAILED.

    For a magnet, checked by its own hash *before* adding anything (build
    164). This is the path a batched hand-over falls back to when the client
    has not shown a torrent back yet - which can mean it is merely slow to
    register, or that it was genuinely never accepted (a proxy or the client
    itself can drop part of a large batch; see test_a_season_in_one_request's
    ``BatchingClient(limit=...)``). Those two look identical from here, and
    guessing wrong in either direction is a real bug: assume "merely slow"
    always and a genuinely-dropped torrent never gets added at all; assume
    "dropped" always (the old behaviour) and a torrent the client already has
    gets added a second time under a fresh tag it will never actually carry,
    orphaning the row from any way to find it again - reported "torrent gone"
    forever for a torrent that was there the whole time. Asking again, right
    before adding, costs one request and answers honestly instead of guessing.
    """
    known_hash = info_hash_of(url)
    if known_hash:
        try:
            already = {t.info_hash: t for t in client.find_torrents(category=category)}
        except TorrentClientError:
            already = {}
        torrent = already.get(known_hash)
        if torrent is not None:
            if not _claim_hash(session, row, known_hash):
                # Another row owns this torrent and knows more about it.
                return None
            row.qb_state = torrent.state
            row.progress = torrent.progress
            row.status = DownloadStatus(download_state(torrent.state))
            row.error = None
            return None

    tag = _download_tag(row.id)
    accepted = False
    add_error: str | None = None
    try:
        accepted = client.add_torrent(url, category=category, tag=tag)
    except TorrentClientError as exc:
        add_error = str(exc)[:255]

    # The client is the authority: look for the tag either way.
    try:
        found = client.find_torrents(category=category, tag=tag)
    except TorrentClientError:
        found = []

    if found:
        torrent = found[0]
        if not _claim_hash(session, row, torrent.info_hash):
            # Another row owns this torrent and knows more about it. Not an
            # error: the download is happening, just not on this row.
            return None
        row.qb_state = torrent.state
        row.progress = torrent.progress
        row.status = DownloadStatus(download_state(torrent.state))
        row.error = None
        return None

    if accepted:
        # Accepted but not visible yet; the periodic sync resolves the hash.
        row.status = DownloadStatus.QUEUED
        row.error = None
        return None

    row.status = DownloadStatus.FAILED
    row.error = add_error or "client rejected the torrent"
    return row.error


def queue_pending_downloads(
    session_factory: sessionmaker[Session],
    *,
    client: TorrentClient | None = None,
) -> QueueSummary:
    """Queue MATCHED RSS releases for anime whose Automate switch is on.

    Every MATCHED release is mirrored onto its episode either way, which is
    what turns the episode AVAILABLE and lights up the Download button. Only
    the hand-off to the torrent client is gated: automation off means AniFlow
    finds and reports, never downloads.
    """
    summary = QueueSummary()
    owns_client = client is None
    effective_client = None

    session = session_factory()
    try:
        from backend.app.services.rss_scan import _hunting

        if not _hunting(session, "queueing"):
            # Basic mode. Nothing here should mirror a release onto an episode:
            # that is where the magnet links and Download buttons on a
            # watchlist-only install came from (build 140).
            return summary
        settings = load_app_settings(session)
        effective_client = _client_from(settings, client)
        statement = (
            select(Release)
            .where(Release.decision == ReleaseDecision.MATCHED)
            .outerjoin(TorrentDownload, TorrentDownload.release_id == Release.id)
            .where(TorrentDownload.id.is_(None))
            # And not one this pass has already finished with (build 135).
            # Without this the *absence of a row* is the only memory the pass
            # has, so Clear all wipes it and every release is reconsidered -
            # which is what made clearing look like it did nothing.
            .where(Release.settled_at.is_(None))
            .order_by(Release.created_at)
        )
        pending = list(session.scalars(statement))
        summary.considered = len(pending)
        touched: set[str] = set()

        for release in pending:
            anime = session.get(Anime, release.anime_id) if release.anime_id else None
            if anime is None or anime.lifecycle not in _TRACKED_LIFECYCLES:
                continue

            number = episode_number_for(anime, release.episode)
            if number is None:
                # Batches and unparseable episode labels have no single owner.
                # Surfaced instead of silently attached to the wrong episode.
                # Not AMBIGUOUS_RELEASE (build 168): anime_id is right there on
                # the line above - which anime this is is not in question, only
                # which episode, and "assign to anime" was never a fix for that.
                raise_attention(
                    session,
                    kind=AttentionKind.EPISODE_NOT_IDENTIFIABLE,
                    anime_id=anime.id,
                    release_id=release.id,
                    detail="episode_not_identifiable",
                )
                # Settled (168): the name is not going to reparse itself, so
                # without this every future pass re-read the same release
                # forever - harmless on its own since raise_attention just
                # refreshes the same open record, but exactly the gap that
                # would have let this record drift from a reclassified kind
                # again the next time the identity it dedupes on changes.
                release.settled_at = datetime.now(UTC)
                summary.attention += 1
                # Named, so the Activity entry is findable (build 118). The
                # pass used to record "attention 2" and nothing else: two
                # things needed a person and the log said neither what nor
                # which, which is not a report, it is a number.
                summary.changes.append(
                    f"{anime.display_name} - {release.raw_name}:"
                    " the episode number could not be read from the name"
                )
                continue

            episode = resolve_episode(session, anime.id, number)
            touched.add(episode.id)
            # Mirror the RSS match into the episode's release list so the
            # episode-oriented view shows where the download came from.
            episode_release = _upsert_rss_episode_release(session, episode, release)

            if episode.skip_reason:
                # The user's AniDB list has it (item 36b): found and recorded, never fetched on its own.
                summary.manual += 1
                continue

            if not anime.automation_enabled:
                # The Automate switch on the detail page is the gate. The find
                # is still recorded and the episode still turns AVAILABLE (the
                # refresh at the end of this pass does that), so the Download
                # button offers it - but nothing reaches the client until
                # someone asks for it.
                summary.manual += 1
                continue

            action = decide_version(release.version, _episode_version_on_disk(episode))
            # The safety check, from the far end of the pipeline backwards
            # (build 136): library, then import folder, then download folder.
            #
            #     "it should first check is the file in the target - file is
            #     there check and then stop. if not, check is the file in the
            #     import folder ... if not, check is the file in the download
            #     folder ... It should not go from the front, because the
            #     folder could be somewhere else."
            #
            # It can only ever *refuse*: a file nobody can find is a problem to
            # report, never a reason to fetch it again, and an unmounted volume
            # looks exactly like a deleted file. So the question asked here is
            # "is there any reason to believe this episode is already dealt
            # with", and a file anywhere in the pipeline is that reason.
            #
            # The exception is a genuinely newer version, which is the one case
            # where a release matters for an episode that is already in hand -
            # that falls through to the branch below.
            located = locate_episode_file(
                storage_roots(), episode, category=settings.qb_category
            )
            if located and action is VersionAction.DOWNLOAD_NEW:
                # The database had nothing to compare with - no recorded
                # version, no live row - and the disk says otherwise. This is
                # what downloaded three episodes that were sitting in the
                # library, over and over.
                release.settled_at = datetime.now(UTC)
                logger.info(
                    "%s: not downloading %s - the file is already in the %s folder (%s)",
                    anime.display_name,
                    release.raw_name,
                    located.stage,
                    located.path,
                )
                summary.skipped += 1
                continue

            if action in (VersionAction.SKIP_DUPLICATE, VersionAction.SKIP_SUPERSEDED):
                reason = (
                    "duplicate_episode"
                    if action is VersionAction.SKIP_DUPLICATE
                    else "superseded_version"
                )
                # Settled on the release rather than recorded as a download
                # row (build 135). The row was informational - nothing to
                # download, no button that does anything - and it was also
                # the pass's only memory of this release, which is what made
                # Clear all refill the page a minute later. The episode still
                # shows where the release was seen, through the
                # episode-release mirror above.
                release.settled_at = datetime.now(UTC)
                logger.info(
                    "%s: nothing to do for %s (%s)",
                    anime.display_name,
                    release.raw_name,
                    reason,
                )
                summary.skipped += 1
                continue

            if action is VersionAction.ATTENTION_NEW_VERSION:
                # Business rule: newer versions need explicit user action.
                row = _new_download(
                    release_id=release.id,
                    episode_release_id=episode_release.id if episode_release else None,
                    episode_id=episode.id,
                    anime_id=anime.id,
                    version=release.version,
                    label=release.raw_name[:500],
                    status=DownloadStatus.ATTENTION,
                    attention_reason="new_version_needs_review",
                )
                session.add(row)
                episode.downloads.append(row)
                session.flush()  # the attention FK needs the download row
                raise_attention(
                    session,
                    kind=AttentionKind.NEW_VERSION,
                    anime_id=anime.id,
                    release_id=release.id,
                    # Reaches the episode the same way IMPORT_BLOCKED does
                    # (build 90) - without it, nothing tying this decision to
                    # its episode or download row existed at all, despite the
                    # flush above being for exactly this.
                    episode_id=episode.id,
                    download_id=row.id,
                    detail="new_version_needs_review",
                )
                summary.attention += 1
                continue

            if effective_client is None:
                continue  # not configured yet; leave the release pending

            row = _new_download(
                release_id=release.id,
                episode_release_id=episode_release.id if episode_release else None,
                episode_id=episode.id,
                anime_id=anime.id,
                version=release.version,
                label=release.raw_name[:500],
                status=DownloadStatus.QUEUED,
            )
            session.add(row)
            # Onto the episode's own list as well as by id (build 238). The same
            # release arrives once per feed - Nyaa, Tokyo Tosho, a magnet feed -
            # and the next one in this very pass asks `episode.downloads` what is
            # already in flight. A list loaded before this row existed answered
            # "nothing", so one torrent was queued three times in 100 ms.
            episode.downloads.append(row)
            session.flush()
            error = _hand_to_client(
                effective_client, row, release.torrent_url or "", settings.qb_category, session
            )
            if error:
                logger.warning("could not queue release %s: %s", release.raw_name, error)
                summary.failed += 1
                continue
            record_download_filename(
                episode, release.raw_name, folder=settings.qb_category
            )
            summary.queued += 1
            summary.changes.append(f"{describe_download(row)}: queued")

        _queue_indexer_releases(
            session, settings, effective_client, summary, touched
        )

        _refresh_episodes(session, touched)
        session.commit()
        # Build 125: no per-pass entry. The counters go back to the caller
        # and onto the Downloads status box; what was queued shows up as the
        # episodes' own state.
        if summary.queued or summary.failed or summary.attention:
            logger.info(
                "queue pass: %s queued, %s skipped, %s attention, %s failed, %s awaiting the user",
                summary.queued,
                summary.skipped,
                summary.attention,
                summary.failed,
                summary.manual,
            )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if owns_client and effective_client is not None:
            effective_client.close()
    return summary


def _queue_indexer_releases(
    session: Session,
    settings,
    client: TorrentClient | None,
    summary: QueueSummary,
    touched: set[str],
) -> None:
    """Queue what the indexer found, for anime whose Automate switch is on.

    The same rules as the RSS path, applied to the other source: the release
    profile decides, the version rules decide, and automation off means the
    episode stays AVAILABLE with its Download button and nothing is sent.

    Only episodes with nothing yet are considered - no file, no download - so
    this never competes with a release RSS already handled.
    """
    from backend.app.services.discovery import INDEXER_PROVIDER
    from backend.app.services.matching import evaluate_release, unknown_is_fine
    from backend.app.services.rss_scan import _candidate_titles

    pending = session.scalars(
        select(EpisodeRelease)
        .join(Episode, Episode.id == EpisodeRelease.episode_id)
        .outerjoin(TorrentDownload, TorrentDownload.episode_id == Episode.id)
        .where(EpisodeRelease.provider == INDEXER_PROVIDER)
        .where(Episode.media_filename.is_(None))
        .where(TorrentDownload.id.is_(None))
        .order_by(Episode.number, EpisodeRelease.seeders.desc())
    )

    seen: set[str] = set()
    for candidate in pending:
        episode = candidate.episode
        if episode is None or episode.id in seen:
            continue  # one release per episode: the best-seeded comes first
        anime = episode.anime
        if anime is None or anime.lifecycle not in _TRACKED_LIFECYCLES:
            continue
        profile = anime.profile
        if profile is None:
            continue  # nothing to judge it by, so nothing is downloaded

        try:
            parsed = parse_release_name(candidate.raw_name or "")
        except ValueError:
            continue
        verdict = evaluate_release(
            parsed,
            subgroups=list(profile.subgroups),
            resolution=profile.resolution,
            media_source=profile.media_source,
            edition=profile.edition or [],
            edition_not=profile.edition_not or [],
            censor_preference=profile.censor_preference.value,
            candidate_titles=_candidate_titles(anime),
            require_title_match=profile.require_title_match,
            require_resolution_known=profile.require_resolution_known,
            require_source_known=profile.require_source_known,
            require_censor_known=profile.require_censor_known,
            tolerate_unknown=unknown_is_fine(anime, profile),
        )
        if not verdict.matched:
            continue  # reported on the episode either way; never downloaded

        seen.add(episode.id)
        touched.add(episode.id)
        if not anime.automation_enabled or episode.skip_reason:
            summary.manual += 1
            continue

        action = decide_version(candidate.version or 1, _episode_version_on_disk(episode))
        if action is not VersionAction.DOWNLOAD_NEW:
            # A back catalogue has no version history to argue with; anything
            # other than a plain download is left for the RSS path or the user.
            continue
        if client is None:
            continue  # not configured yet; the release stays pending

        row = _new_download(
            release_id=None,
            episode_release_id=candidate.id,
            episode_id=episode.id,
            anime_id=anime.id,
            version=candidate.version or 1,
            label=(candidate.raw_name or "")[:500],
            status=DownloadStatus.QUEUED,
        )
        session.add(row)
        episode.downloads.append(row)
        session.flush()
        error = _hand_to_client(
            client,
            row,
            candidate.torrent_url or candidate.magnet_url or "",
            settings.qb_category,
            session,
        )
        if error:
            logger.warning("could not queue %s: %s", candidate.raw_name, error)
            summary.failed += 1
            continue
        record_download_filename(episode, candidate.raw_name, folder=settings.qb_category)
        summary.queued += 1
        summary.changes.append(f"{describe_download(row)}: queued")


def _upsert_rss_episode_release(
    session: Session, episode: Episode, release: Release
) -> EpisodeRelease | None:
    """Record an RSS release as one of the episode's known releases.

    The feed's own ``releases`` table stays the audit log with verdicts; this
    is the episode-oriented view of the same item.
    """
    existing = session.scalar(
        select(EpisodeRelease).where(
            EpisodeRelease.episode_id == episode.id,
            EpisodeRelease.provider == "rss",
            EpisodeRelease.provider_id == release.id,
        )
    )
    target = existing or EpisodeRelease(
        episode_id=episode.id, provider="rss", provider_id=release.id
    )
    target.raw_name = release.raw_name[:500]
    target.torrent_url = (release.torrent_url or None) and release.torrent_url[:500]
    target.subgroup = release.subgroup
    target.resolution = release.resolution
    target.version = release.version or 1
    if existing is None:
        session.add(target)
        session.flush()
    return target


def _refresh_episodes(session: Session, episode_ids: set[str]) -> None:
    """Re-derive the status of the given episodes.

    The ids are collected explicitly rather than scanned off the session: a
    flush moves pending objects out of ``session.new``, so inspecting the
    session afterwards silently finds nothing.
    """
    session.flush()
    for episode_id in episode_ids:
        episode = session.get(Episode, episode_id)
        if episode is not None:
            session.refresh(episode)
            refresh_episode_status(episode)


def _recover_failed_rows(
    session: Session,
    client: TorrentClient,
    failed_rows: list[TorrentDownload],
    category: str,
    summary: SyncSummary,
) -> None:
    """Adopt FAILED rows whose torrent actually made it into the client.

    A rejected add attempt sometimes still results in a live torrent
    (duplicate adds, retries, transient fetch problems). The client is the
    authority: when the marker tag is found, the row becomes a normal tracked
    download again.
    """
    for row in failed_rows:
        try:
            tagged = client.find_torrents(category=category, tag=_download_tag(row.id))
        except TorrentClientError:
            continue  # client unreachable - try again on the next pass
        if not tagged:
            continue
        torrent = tagged[0]
        if not _claim_hash(session, row, torrent.info_hash):
            continue
        row.qb_state = torrent.state
        row.progress = torrent.progress
        row.status = DownloadStatus(download_state(torrent.state))
        row.error = None
        summary.updated += 1
        summary.changes.append(f"{describe_download(row)}: recovered in the client")


def _note_stall(row: TorrentDownload, torrent, changed: bool) -> None:
    """Start or stop the clock on a download that is getting nowhere.

    ``changed`` is the sync's own answer to "did anything move" - reusing it
    rather than comparing progress again keeps one definition of movement.
    """
    stalled = torrent.state == STALLED_STATE and torrent.progress < 0.999
    if not stalled or changed:
        # Either it is moving, or it is not stalled. Both clear the clock: a
        # torrent that found a peer for one minute has not been stuck for
        # six hours, and saying it has would be worse than saying nothing.
        row.stalled_since = None
        return
    if row.stalled_since is None:
        row.stalled_since = datetime.now(UTC)


def stalled_downloads(session: Session, *, now: datetime | None = None) -> list[TorrentDownload]:
    """Every download that has been getting nowhere for long enough to say so."""
    moment = now or datetime.now(UTC)
    rows = session.scalars(
        select(TorrentDownload).where(TorrentDownload.stalled_since.is_not(None))
    )
    found = []
    for row in rows:
        since = row.stalled_since
        if since is None:
            continue
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if moment - since >= STALLED_AFTER:
            found.append(row)
    return found


def report_stalled_downloads(session: Session, *, now: datetime | None = None) -> int:
    """Raise a record for each download nobody is seeding, and close the rest.

    The fix is the one the detail page already offers - change the subgroup -
    so this says what is wrong and leaves the choice alone. A release with no
    seeders is not going to start on its own.
    """
    stuck = {row.id: row for row in stalled_downloads(session, now=now)}
    for row in stuck.values():
        raise_attention(
            session,
            kind=AttentionKind.DOWNLOAD_STALLED,
            anime_id=row.anime_id,
            download_id=row.id,
            detail=describe_download(row)[:120],
        )
    # Anything that was reported and has since moved closes itself here, so a
    # record cannot outlive the stall it describes.
    for row in session.scalars(
        select(TorrentDownload).where(TorrentDownload.stalled_since.is_(None))
    ):
        resolve_open(session, kind=AttentionKind.DOWNLOAD_STALLED, download_id=row.id)
    session.commit()
    if stuck:
        logger.info("%s download(s) have been stalled for %s", len(stuck), STALLED_AFTER)
    return len(stuck)


def sync_download_states(
    session_factory: sessionmaker[Session],
    *,
    client: TorrentClient | None = None,
) -> SyncSummary:
    """Refresh QUEUED/DOWNLOADING rows from the live client state."""
    summary = SyncSummary()
    owns_client = client is None

    session = session_factory()
    effective_client = None
    try:
        settings = load_app_settings(session)
        effective_client = _client_from(settings, client)
        active_rows = list(
            session.scalars(
                select(TorrentDownload).where(
                    TorrentDownload.status.in_([DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING])
                )
            )
        )
        failed_rows = list(
            session.scalars(
                select(TorrentDownload).where(TorrentDownload.status == DownloadStatus.FAILED)
            )
        )
        summary.checked = len(active_rows)
        if effective_client is None:
            return summary

        # Sweep before the early return below: with nothing downloading this
        # pass would exit immediately, and the tag left behind by the last
        # import of the day would sit in the client until the next download.
        try:
            summary.tags_pruned = prune_download_tags(session, effective_client)
        except TorrentClientError as exc:
            # The first thing every pass asks of the client, so it is where an
            # unreachable one shows up.
            _report_client_state("unreachable", str(exc))
            raise
        _report_client_state("connected")
        if not active_rows and not failed_rows:
            session.commit()
            return summary

        if failed_rows:
            _recover_failed_rows(session, effective_client, failed_rows, settings.qb_category, summary)

        live = {
            torrent.info_hash: torrent
            for torrent in effective_client.find_torrents(category=settings.qb_category)
        }

        for row in active_rows:
            if row.batch_download_id is not None:
                continue  # a further episode of a batch: follows its owner, see _sync_batch_siblings
            torrent = live.get(row.info_hash) if row.info_hash else None

            if torrent is None and row.info_hash is None:
                # Hash still unresolved (e.g. client was offline when queued):
                # retry through the marker tag.
                tagged = effective_client.find_torrents(
                    category=settings.qb_category, tag=_download_tag(row.id)
                )
                if tagged:
                    if _claim_hash(session, row, tagged[0].info_hash):
                        torrent = tagged[0]
                    else:
                        # Another row owns it. This one is a duplicate and
                        # has just been marked as such; nothing more to sync.
                        continue

            if torrent is None:
                # A tracked torrent is not in the client. Not necessarily gone:
                # a torrent that has just been handed over is added, checked,
                # categorised and picked up, and none of that is instant. So
                # the first miss is only remembered - the user hears about it
                # once it has lasted long enough to mean something.
                now = datetime.now(UTC)
                if row.missing_since is None:
                    row.missing_since = now
                    summary.updated += 1
                    logger.debug(
                        "%s is not in the client yet; waiting %s minutes before reporting it",
                        describe_download(row),
                        int(MISSING_GRACE.total_seconds() // 60),
                    )
                    continue
                if now - _as_utc(row.missing_since) < MISSING_GRACE:
                    continue
                # Long enough. Never assume anything about the media: this is
                # reported and nothing local is touched.
                row.status = DownloadStatus.ATTENTION
                row.attention_reason = "missing_in_client"
                raise_attention(
                    session,
                    kind=AttentionKind.DOWNLOAD_MISSING,
                    anime_id=row.anime_id,
                    download_id=row.id,
                    detail="missing_in_client",
                )
                summary.attention += 1
                summary.updated += 1
                summary.changes.append(f"{describe_download(row)}: missing in the client")
                refresh_episode_status(row.episode)
                continue

            # Torrent is present again - a prior "missing" record clears itself,
            # and so does the clock that would have reported it.
            if row.missing_since is not None:
                row.missing_since = None
            resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)

            new_state = DownloadStatus(download_state(torrent.state))
            changed = (
                row.qb_state != torrent.state
                or abs(row.progress - torrent.progress) > 0.0001
                or row.status != new_state
            )
            _note_stall(row, torrent, changed)
            row.qb_state = torrent.state
            row.progress = torrent.progress
            if changed:
                row.status = new_state
                summary.updated += 1
                summary.changes.append(
                    f"{describe_download(row)}: {torrent.state} {row.progress * 100:.0f}%"
                )
                if new_state is DownloadStatus.COMPLETED:
                    summary.completed += 1
                    # The client knows the real filename now; the earlier value
                    # was only a guess from the release name.
                    _adopt_content_name(row, torrent)
                refresh_episode_status(row.episode)

        _sync_batch_siblings(session, live, summary, datetime.now(UTC))
        session.commit()
        # Build 125: likewise. "Download sync: 4 checked, 4 updated" was
        # the clearest example of an entry that told nobody anything.
        if summary.updated or summary.completed or summary.attention:
            logger.info(
                "sync pass: %s updated, %s completed, %s attention",
                summary.updated,
                summary.completed,
                summary.attention,
            )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if owns_client and effective_client is not None:
            effective_client.close()
    return summary


def _adopt_content_name(row: TorrentDownload, torrent) -> None:
    """Take the authoritative download filename from the client's content path.

    Only a single media file yields a filename; a folder torrent keeps whatever
    guess was stored, and the importer resolves the real file when it runs.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    from backend.app.services.storage import is_media_file

    content_path = getattr(torrent, "content_path", None)
    if not content_path:
        return
    # qBittorrent may report either separator depending on its host.
    pure = PureWindowsPath(content_path) if "\\" in content_path else PurePosixPath(content_path)
    if pure.name and is_media_file(pure.name):
        # Only the filename: the folder cannot be derived from the parent name
        # alone (the parent of /downloads/x.mkv is "downloads", not a category
        # subfolder). The importer records it relative to the download root.
        record_download_filename(row.episode, pure.name)


def queue_episode_releases(
    session: Session,
    *,
    anime_id: str,
    episode: int | None = None,
    episodes: list[int] | None = None,
    all_available: bool = False,
    client: TorrentClient | None = None,
) -> dict[str, object]:
    """Queue the best known release for one episode, a given set, or every episode.

    "Best" is the most seeded release, newest as tie-break. Episodes that are
    already complete or already have a live download are skipped. Without a
    configured client the rows are still created as ATTENTION so the Downloads
    page shows the intent end to end.

    ``episodes`` exists for ``queue_automated_anime`` (build 166): a caller
    that has already worked out exactly which episodes of one anime are
    eligible, and wants them handed over as the one batch request that is the
    whole reason batching exists - rather than the one-by-one loop that used
    to call this once per episode number and reproduce the very burst the
    batch endpoint was built to avoid.
    """
    if episode is None and episodes is None and not all_available:
        raise ValueError("Provide episode, episodes or all_available=true")

    anime = session.get(Anime, anime_id)
    if anime is None:
        raise ValueError("Anime not found")

    if episode is not None:
        target = resolve_episode(session, anime_id, episode, create=False)
        if target is None:
            raise ValueError(f"Episode {episode} does not exist for this anime")
        # Pressed on this very episode: the user wants it, whatever their AniDB list says (item 36b).
        target.skip_reason = None
        candidates = [target]
    elif episodes is not None:
        candidates = [
            found
            for number in episodes
            if (found := resolve_episode(session, anime_id, number, create=False)) is not None
            and not found.skip_reason
        ]
    else:
        # "Every episode" leaves out the ones the user's AniDB list has; the anime's own button fetches those.
        candidates = sorted((item for item in anime.episodes if not item.skip_reason), key=lambda item: item.number)

    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = _client_from(settings, client)

    queued = 0
    skipped = 0
    errors: list[str] = []
    # Why each skipped episode was skipped, counted (build 117). "Nothing to
    # queue - this episode is already owned or downloading" was the only
    # answer the page could give, and for the reported case it was not even
    # one of the two: the episode was neither owned nor downloading, it was
    # blocked by a finished download whose file no longer existed. A refusal
    # that cannot say which of three things happened is a refusal nobody can
    # act on.
    reasons: dict[str, int] = {}

    def because(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    def keep() -> str | None:
        """Write down what just happened. Returns the reason it could not be.

        Per episode, not per request (build 118). The reported symptom was
        "Download this episode" answering *"that did not work: Internal
        error"* about a torrent qBittorrent had already accepted, and then
        working on the second press. One commit at the end of the whole pass
        is what made that possible: one row the database refused - a duplicate
        info_hash, in the reported case - rolled back every row before it,
        including the ones whose torrents were already gone to the client.
        The client cannot be rolled back, so the database must not pretend it
        can.
        """
        try:
            session.commit()
            return None
        except Exception as exc:  # noqa: BLE001 - the caller reports this
            session.rollback()
            logger.warning("a download row could not be written: %s", exc)
            return str(exc)

    # Everything that can go in one request, in one request (build 120).
    # Only for a run over several episodes - a single Download button is one
    # request either way - and only for magnets, whose hash is in the link.
    batched: dict[str, str | None] = {}
    #: Episodes the batch has dealt with, so the loop below leaves them alone.
    settled: set[str] = set()
    #: And the ones it could not, whose rows are removed again so the single
    #: hand-over can write its own.
    dropped: list[str] = []
    # `add_torrents` is the seam the batch needs. Checked rather than
    # assumed: the client is a replaceable object - the tests put their own
    # in, and a build older than 120 has only the single add - and a missing
    # method must mean "hand them over one at a time", not an exception.
    can_batch = callable(getattr(effective_client, "add_torrents", None))
    if effective_client is not None and can_batch and len(candidates) > 1:
        plan: list[tuple[TorrentDownload, str]] = []
        for episode_row in candidates:
            if episode_row.status is EpisodeStatus.COMPLETE or _blocking(episode_row):
                continue
            release = episode_row.best_release
            url = release.download_url if release is not None else None
            if not url or info_hash_of(url) is None:
                continue
            row = _new_download(
                episode_id=episode_row.id,
                anime_id=anime_id,
                episode_release_id=release.id,
                version=release.version or 1,
                label=release.raw_name[:500],
                status=DownloadStatus.QUEUED,
            )
            session.add(row)
            session.flush()
            session.expire(episode_row, ["downloads"])
            record_download_filename(
                episode_row, release.raw_name, folder=settings.qb_category
            )
            plan.append((row, url))
        if plan:
            batched = _hand_over_together(session, effective_client, plan, settings.qb_category)
            for episode_row in candidates:
                refresh_episode_status(episode_row)
            keep_failure = keep()
            if keep_failure:
                errors.append(keep_failure)
            for row, _url in plan:
                failure = batched.get(row.id)
                if failure is None:
                    queued += 1
                    # Handled. The loop below must not count it again, or a
                    # season that worked would report itself as skipped.
                    settled.add(row.episode_id)
                else:
                    # The batch did not bring this one in. The row goes and
                    # the single path below tries it properly, by tag - which
                    # is the only way for a .torrent link anyway.
                    session.delete(row)
                    dropped.append(row.episode_id)
            session.flush()
            for episode_id in dropped:
                # The episode's loaded collection still holds the row that
                # was just deleted, and _blocking would read it as a live
                # download - so the single hand-over would skip the very
                # episode it exists to rescue.
                stale = session.get(Episode, episode_id)
                if stale is not None:
                    session.expire(stale, ["downloads"])
            keep()

    handed_over_singly = 0
    try:
        for episode_row in candidates:
            if episode_row.id in settled:
                continue  # the batch above already handed this one over
            if episode_row.status is EpisodeStatus.COMPLETE:
                because("already_owned")
                continue
            blocker = _blocking(episode_row)
            if blocker is not None:
                because(
                    "importing"
                    if blocker.status is DownloadStatus.COMPLETED
                    else "in_flight"
                )
                continue
            release = episode_row.best_release
            if release is None:
                because("no_release")
                continue
            url = release.download_url
            if not url:
                errors.append(f"Episode {episode_row.number}: no magnet or torrent link")
                continue

            row = _new_download(
                episode_id=episode_row.id,
                anime_id=anime_id,
                episode_release_id=release.id,
                version=release.version or 1,
                label=release.raw_name[:500],
                status=DownloadStatus.QUEUED,
            )
            session.add(row)
            session.flush()
            # The episode's loaded collection predates this row; expire it so
            # the status derivation below actually sees the new download.
            session.expire(episode_row, ["downloads"])
            record_download_filename(
                episode_row, release.raw_name, folder=settings.qb_category
            )

            if effective_client is None:
                row.status = DownloadStatus.ATTENTION
                row.attention_reason = "qb_not_configured"
                session.flush()  # the attention FK needs the download row
                raise_attention(
                    session,
                    kind=AttentionKind.DOWNLOAD_MISSING,
                    anime_id=anime_id,
                    download_id=row.id,
                    detail="qb_not_configured",
                )
                refresh_episode_status(episode_row)
                failure = keep()
                if failure:
                    errors.append(f"Episode {episode_row.number}: {failure}")
                    continue
                queued += 1
                continue

            if handed_over_singly:
                # Sequential, spaced, and in waves (item 1) - the same rule already in force for the
                # indexer APIs (services/calendar_pacer.py), applied here for
                # the same reason: asked directly, live, watching qBittorrent
                # add torrents one at a time and stop after six - "is it
                # possible to add them after each other, like a queue?"  This
                # loop is the fallback for whatever the batch above did not
                # bring in (or the whole list, for a client too old to
                # batch at all); a burst of single adds here is the same
                # shape as the one the batch endpoint exists to avoid, just
                # smaller, so it gets the same treatment.
                _pause_before_hand_over(handed_over_singly)
            error = _hand_to_client(
                effective_client, row, url, settings.qb_category, session
            )
            handed_over_singly += 1
            refresh_episode_status(episode_row)
            if error:
                errors.append(f"Episode {episode_row.number}: {error}")
                keep()  # the FAILED row is worth keeping too
                continue
            failure = keep()
            if failure:
                # The torrent is in the client either way - the row is what
                # could not be written - so this is reported rather than
                # raised, and the count says one fewer than the client has.
                errors.append(f"Episode {episode_row.number}: {failure}")
                continue
            queued += 1
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()

    return {"queued": queued, "skipped": skipped, "errors": errors, "reasons": reasons}


def remove_download(
    session: Session,
    download_id: str,
    *,
    delete_files: bool = False,
    client: TorrentClient | None = None,
) -> TorrentDownload:
    """Remove a torrent from the client. Local media is only touched when
    ``delete_files`` is explicitly True."""
    download = session.get(TorrentDownload, download_id)
    if download is None:
        raise LookupError("Download not found")

    if download.info_hash:
        settings = load_app_settings(session)
        effective_client = _client_from(settings, client)
        if effective_client is not None:
            try:
                effective_client.delete_torrent(download.info_hash, delete_files=delete_files)
            finally:
                if client is None:
                    effective_client.close()

    download.status = DownloadStatus.REMOVED
    refresh_episode_status(download.episode)
    session.commit()
    return download



def _settle_releases(session: Session, rows: list[TorrentDownload]) -> int:
    """Mark the RSS releases behind these rows as dealt with (build 135).

    Clearing a download row is a person saying *"I have dealt with this"*, and
    the queue pass has to hear it: its pending query is "a MATCHED release with
    no download row", so a cleared row is indistinguishable from a release
    nobody has looked at yet. That is why Clear all came back - *"the downloads
    are fully back"* - and it is the same contradiction build 128 fixed for
    orphan adoption, in the other of the two paths that refill the list.

    An episode that still wants a file is not stranded by this: the Download
    button on the episode goes through ``queue_episode_releases``, which works
    from the episode's own candidates and is not gated by ``settled_at``.
    """
    from backend.app.models.rss import Release

    ids = {row.release_id for row in rows if row.release_id}
    if not ids:
        return 0
    settled = session.execute(
        update(Release)
        .where(Release.id.in_(ids), Release.settled_at.is_(None))
        .values(settled_at=datetime.now(UTC))
    ).rowcount
    return int(settled or 0)


def _remember_cleared(session: Session, rows: list[TorrentDownload]) -> int:
    """Write down the torrents somebody just told AniFlow to forget.

    Without this, clearing is undone by the next half-hourly pass: orphan
    adoption walks the client for finished torrents no row knows about, which
    after a clear is all of them. See models/cleared_torrent.py.

    Only rows that reached the client are worth remembering - one with no hash
    was never adopted from anything, so there is nothing for the orphan pass
    to find.
    """
    from backend.app.models.cleared_torrent import ClearedTorrent

    written = 0
    for row in rows:
        if not row.info_hash:
            continue
        if session.get(ClearedTorrent, row.info_hash) is not None:
            continue
        session.add(ClearedTorrent(info_hash=row.info_hash, label=row.label))
        written += 1
    return written

def clear_download(session: Session, download_id: str) -> str:
    """Forget AniFlow's download row. Nothing outside AniFlow is touched.

    The torrent stays in the client (paused after import) and the files stay on
    disk - this only tidies the Downloads list. The episode keeps its media
    file, so an imported episode stays COMPLETE; one that never finished falls
    back to AVAILABLE and can be queued again.

    Returns the episode id whose status was re-derived.
    """
    download = session.get(TorrentDownload, download_id)
    if download is None:
        raise LookupError("Download not found")
    episode = download.episode
    # So the orphan pass does not adopt it straight back (build 128), and
    # the queue pass does not re-create the row from its release (build 135).
    _remember_cleared(session, [download])
    _settle_releases(session, [download])
    session.delete(download)
    session.flush()
    if episode is not None:
        session.refresh(episode)
        refresh_episode_status(episode)
    session.commit()
    logger.info("cleared download %s from the list", download_id)
    return episode.id if episode is not None else ""


def live_torrents_for(session: Session, anime_id: str) -> list[TorrentDownload]:
    """This anime's downloads that AniFlow believes are still in the client.

    What the confirmation is built from: a count of nothing means nothing to
    ask about, so the page can delete straight away.
    """
    return list(
        session.scalars(
            select(TorrentDownload).where(
                TorrentDownload.anime_id == anime_id,
                TorrentDownload.info_hash.is_not(None),
                TorrentDownload.status != DownloadStatus.REMOVED,
            )
        )
    )


def remove_torrents_for(
    session: Session, anime_id: str, *, client: TorrentClient | None = None
) -> dict[str, object]:
    """Take this anime's torrents out of the client. Files are never touched.

    Only this anime's: each row carries the info hash of the torrent AniFlow
    handed over, so nothing else in the client is looked at, let alone
    removed. A torrent somebody added by hand is not AniFlow's to delete.

    Best effort per torrent - one that has already gone by hand is not a
    failure, and a client that will not answer must not stop the anime being
    deleted. The caller gets the counts either way.
    """
    rows = live_torrents_for(session, anime_id)
    if not rows:
        return {"removed": 0, "failed": 0, "errors": []}

    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = _client_from(settings, client)
    if effective_client is None:
        return {"removed": 0, "failed": len(rows), "errors": ["qb_not_configured"]}

    removed = 0
    errors: list[str] = []
    try:
        for row in rows:
            try:
                # delete_files stays False: removing a torrent and deleting
                # what it downloaded are different decisions, and this asks
                # only the first. An imported episode is the library's file.
                effective_client.delete_torrent(row.info_hash, delete_files=False)
                row.status = DownloadStatus.REMOVED
                removed += 1
            except TorrentClientError as exc:
                errors.append(str(exc)[:200])
        session.commit()
        logger.info("removed %s torrent(s) belonging to anime %s", removed, anime_id)
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()
    return {"removed": removed, "failed": len(rows) - removed, "errors": errors}


def _retry_release_url(session: Session, row: TorrentDownload) -> str | None:
    """Which link to hand this row's torrent over from, or None if there isn't one.

    The release the row was written for, or the episode's current best if
    that one has since gone: the point is to get *this* episode downloading,
    and a stale link helps nobody. Shared by the bulk stranded-retry below and
    the user's own "Retry now" button (services/attention.py:retry_download_now)
    - one place deciding which release a row's magnet comes from.
    """
    release = (
        session.get(EpisodeRelease, row.episode_release_id)
        if row.episode_release_id
        else None
    )
    if release is None and row.episode is not None:
        release = row.episode.best_release
    return release.download_url if release is not None else None


def retry_one_download(
    session: Session, client: TorrentClient, row: TorrentDownload, category: str
) -> str | None:
    """Hand one row to the client again, by the same link `_retry_release_url` finds.

    Idempotent, the same way every hand-over here is: qBittorrent answers
    "already present" for a torrent it still has, so retrying one that has
    quietly come back on its own costs nothing.

    Returns the error - "no release link known for this download" if there is
    nothing to send it from - or None on success.
    """
    url = _retry_release_url(session, row)
    if not url:
        return "no release link known for this download"
    error = _hand_to_client(client, row, url, category, session)
    if row.episode is not None:
        refresh_episode_status(row.episode)
    return error


def retry_stranded_downloads(
    session: Session, *, client: TorrentClient | None = None
) -> dict[str, object]:
    """Hand over the downloads that were written down but never accepted.

    Reported as "it found ten episodes and only six reached the client", and
    the deployment's own rows said it plainly: six with an info hash, four
    QUEUED with none. A row with no hash never reached qBittorrent.

    What made it permanent is the rule one level up - ``queue_automated_anime``
    skips an episode that already has a download row, on the sound grounds
    that retrying one every cycle would pile up duplicates. Sound for a row
    that *was* handed over; for one that was not, the episode was stranded for
    good, and the only symptom was a number that did not add up.

    So the question is asked from the row's own evidence: no info hash means
    the client never took it, whatever the row's status says. Adding is
    idempotent - qBittorrent answers "already present" for a torrent it has,
    and :func:`_hand_to_client` treats that as the success it is - so a retry
    cannot produce a second copy.
    """
    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = _client_from(settings, client)
    tried = 0
    handed = 0
    errors: list[str] = []
    if effective_client is None:
        return {"tried": 0, "handed": 0, "errors": ["qb_not_configured"]}
    try:
        stranded = list(
            session.scalars(
                select(TorrentDownload).where(
                    TorrentDownload.info_hash.is_(None),
                    # A further episode of a batch has no hash by design: its owner holds the torrent.
                    TorrentDownload.batch_download_id.is_(None),
                    TorrentDownload.status.in_(
                        [
                            DownloadStatus.QUEUED,
                            DownloadStatus.ATTENTION,
                            # And the one this was written for and did not
                            # cover (build 117). FAILED is exactly "could not
                            # be handed to the client at all" - the same
                            # stranding, one status along - and it was the
                            # only one the retry left out. Reported twice as
                            # "it added six torrents and not the other ten":
                            # the six were taken, the rest were refused, their
                            # rows went FAILED, and because a row existed the
                            # automated pass skipped those episodes for ever.
                            # Download all worked because it does not treat a
                            # failed row as occupying.
                            DownloadStatus.FAILED,
                        ]
                    ),
                )
            )
        )
        for row in stranded:
            url = _retry_release_url(session, row)
            if not url:
                # Nothing to hand over. Left alone rather than failed: the
                # release may come back on the next feed scan.
                continue
            _pause_before_hand_over(tried)
            tried += 1
            error = _hand_to_client(
                effective_client, row, url, settings.qb_category, session
            )
            if row.episode is not None:
                refresh_episode_status(row.episode)
            if error:
                errors.append(f"{row.id}: {error}")
                continue
            handed += 1
        if tried:
            session.commit()
            logger.info(
                "retried %s stranded download(s), %s reached the client", tried, handed
            )
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()
    return {"tried": tried, "handed": handed, "errors": errors}


def retry_missing_downloads(
    session: Session, *, client: TorrentClient | None = None
) -> dict[str, object]:
    """Hand a torrent AniFlow believes is missing to the client again, on its own.

    The promise the DOWNLOAD_MISSING record makes: *"AniFlow will hand this to
    your download client again on the next pass."* Nothing was actually doing
    that (166): the retry above only reconsiders a *stranded* row - one that
    never got a hash in the first place. A torrent that *was* claimed and then
    the client stopped reporting it keeps that hash, so it never matched that
    check, and the record just sat there - self-healing in name only - until a
    person pressed Retry now by hand.

    Idempotent the same way every retry here is: qBittorrent answers "already
    present" for a torrent it still has, so a retry against one that quietly
    came back on its own costs nothing.
    """
    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = _client_from(settings, client)
    tried = 0
    handed = 0
    errors: list[str] = []
    if effective_client is None:
        return {"tried": 0, "handed": 0, "errors": ["qb_not_configured"]}
    try:
        missing = list(
            session.scalars(
                select(TorrentDownload).where(
                    TorrentDownload.status == DownloadStatus.ATTENTION,
                    TorrentDownload.attention_reason == "missing_in_client",
                    TorrentDownload.batch_download_id.is_(None),
                )
            )
        )
        for row in missing:
            _pause_before_hand_over(tried)
            tried += 1
            row.missing_since = None
            row.attention_reason = None
            error = retry_one_download(session, effective_client, row, settings.qb_category)
            if error:
                # _hand_to_client sets FAILED on a hard refusal, which would
                # read as a harder problem than this still is - a retry that
                # could not reach the client is the same waiting condition,
                # not a new one.
                row.status = DownloadStatus.ATTENTION
                row.attention_reason = "missing_in_client"
                errors.append(f"{row.id}: {error}")
                continue
            resolve_open(session, kind=AttentionKind.DOWNLOAD_MISSING, download_id=row.id)
            handed += 1
        if tried:
            session.commit()
            logger.info(
                "retried %s missing download(s), %s reached the client", tried, handed
            )
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()
    return {"tried": tried, "handed": handed, "errors": errors}


def queue_automated_anime(
    session_factory: sessionmaker[Session], *, client: TorrentClient | None = None
) -> dict[str, object]:
    """Queue what every automated anime has available.

    The per-anime switch the detail page offers: with it on, an episode that
    becomes AVAILABLE (from a feed or an indexer search) is queued on the next
    worker cycle instead of waiting for someone to press a button. Dropped and
    archived anime are skipped - automation is not a reason to ignore lifecycle.

    Reported live (166): a newly added anime with several episodes already
    aired queued six torrents and left the rest unrecognised in the client -
    the very "N rapid POSTs" burst the batch hand-over (build 120) exists to
    avoid. It still happened here because this loop called
    ``queue_episode_releases`` once *per episode*, each call a single-torrent
    request of its own - the anime's own eligible episodes were never batched
    together, only a manual "download all" press was. Now every eligible
    episode of one anime is worked out first and handed over in the one
    batch request that call already knows how to make.
    """
    session = session_factory()
    queued = 0
    names: list[str] = []
    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = _client_from(settings, client)
    try:
        automated = list(
            session.scalars(
                select(Anime).where(
                    Anime.automation_enabled.is_(True),
                    Anime.lifecycle.in_([AnimeLifecycle.ACTIVE, AnimeLifecycle.UPCOMING]),
                    # Nothing to queue for a tracker entry: no release name,
                    # no profile, and no episodes to look for.
                    Anime.entry_mode != "basic",
                    # Nor for one the user has asked to be left alone
                    # (build 123).
                    Anime.releases_ignored.is_(False),
                )
            )
        )
        for anime in automated:
            eligible: list[int] = []
            for episode_row in sorted(anime.episodes, key=lambda item: item.number):
                # An existing row means this episode has been handed over
                # once already, and retrying that every cycle would pile up
                # duplicates. A row with no info hash was *not* handed over,
                # though - the client never took it - and those are retried by
                # retry_stranded_downloads rather than queued again here,
                # which would write a second row for the same episode.
                #
                # "Handed over" is the client's word for it, not the row's
                # (build 117): a row with an info hash is one qBittorrent has
                # confirmed. Counting rows it never took made a refused add
                # permanent - the episode had a row, so this pass skipped it
                # every cycle from then on.
                if any(row.info_hash for row in episode_row.downloads):
                    continue
                if any(
                    row.status in (DownloadStatus.QUEUED, DownloadStatus.ATTENTION)
                    for row in episode_row.downloads
                ):
                    # Written down and waiting on the retry above, which hands
                    # the existing row over rather than writing a second one.
                    continue
                if episode_row.status not in DOWNLOADABLE_STATUSES:
                    continue
                if episode_row.skip_reason:
                    continue  # the user's AniDB list has it (item 36b)
                eligible.append(episode_row.number)
            if not eligible:
                continue
            try:
                result = queue_episode_releases(
                    session, anime_id=anime.id, episodes=eligible, client=effective_client
                )
            except ValueError:
                continue
            count = int(result.get("queued") or 0)
            if count:
                queued += count
                names.append(f"{anime.display_name}: {count}")
        return {"anime": len(automated), "queued": queued, "changes": names}
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()
        session.close()


def forget_cleared(session: Session, info_hash: str) -> None:
    """Stop skipping this torrent - it is being adopted on purpose.

    Called when the user presses something that asks for adoption outright, so
    the last explicit instruction wins over the earlier one.
    """
    from backend.app.models.cleared_torrent import ClearedTorrent

    row = session.get(ClearedTorrent, info_hash)
    if row is not None:
        session.delete(row)


def adopt_orphan_torrents(
    session: Session,
    *,
    client: TorrentClient | None = None,
    include_cleared: bool = False,
) -> dict[str, object]:
    """Take charge of finished torrents in AniFlow's category that it forgot.

    Reported after five episodes stayed missing with their files already
    downloaded: *"they are downloaded and in the torrent client, they were
    never moved to import"*. Nothing was wrong with the import - it only ever
    places files it has a download row for, and those rows were gone. A
    cleared Downloads list, a swap that forgot them, a database restored from
    before the download: the cause does not matter to the fix.

    The rule is the one the report asked for: *"if its clear (and the automate
    is enabled) then yes please adopt and import them. if its not clear or
    some kind of mismatch, then report it in the activity window"*.

    Clear means all of this:

    * the torrent is finished - a part-file is the queue's business, not this;
    * its name parses to an episode number;
    * exactly one watchlist anime matches the name;
    * that episode does not already have its file;
    * and that anime's Automate switch is on, because adopting a torrent is
      handing a file to the library and that switch is what says AniFlow may.

    Anything else is written to the activity log, named, with the reason. It
    is never guessed at: a filename match is good enough to *offer*, and this
    one acts, so it takes only the cases with one answer.
    """
    from backend.app.models.anime import DOWNLOADABLE_STATUSES
    from backend.app.services.release_identity import matching_anime
    from backend.app.services.release_parser import parse_release_name

    settings = load_app_settings(session)
    owns_client = client is None
    effective_client = _client_from(settings, client)
    result: dict[str, object] = {"seen": 0, "adopted": 0, "reported": 0}
    if effective_client is None:
        return result

    if not settings.qb_category.strip():
        # Cannot happen through load_app_settings, which refuses a blank
        # category - and checked anyway, because this is the one pass where
        # being wrong means touching torrents that are not AniFlow's at all.
        # qBittorrent answers an empty category filter with every
        # *uncategorised* torrent in the client, and this pass needs no tag by
        # design: a forgotten torrent is exactly what it looks for. See
        # clean_category (build 133).
        logger.warning("no torrent category is set; not looking for stray torrents")
        return result

    try:
        try:
            live = effective_client.find_torrents(category=settings.qb_category)
        except TorrentClientError as exc:
            logger.info("the client could not be asked about stray torrents: %s", exc)
            return result

        known = {
            row.info_hash
            for row in session.scalars(select(TorrentDownload))
            if row.info_hash
        }
        # And the ones somebody deliberately forgot (build 128). Without this
        # the pass undoes every Clear: a forgotten row is precisely what it
        # looks for, so after a Clear all it adopts the whole list back.
        #
        # `include_cleared` is for the paths where the user asked for adoption
        # outright - Fix missing episodes - because the last explicit
        # instruction should win over the earlier one.
        skipped_cleared = 0
        if not include_cleared:
            from backend.app.models.cleared_torrent import ClearedTorrent

            known |= {row.info_hash for row in session.scalars(select(ClearedTorrent))}
            skipped_cleared = len(
                [row for row in session.scalars(select(ClearedTorrent))]
            )
        tracked = list(
            session.scalars(
                select(Anime).where(Anime.lifecycle.in_(_TRACKED_LIFECYCLES))
            )
        )
        adopted = 0
        reported = 0
        for torrent in live:
            if torrent.info_hash in known:
                continue
            if torrent.progress < 0.999:
                # Still downloading. The queue owns unfinished torrents; this
                # is about files that are sitting there done.
                continue
            result["seen"] = int(result["seen"]) + 1

            why = None
            episode = None
            anime = None
            try:
                parsed = parse_release_name(torrent.name)
            except ValueError:
                parsed = None
            if parsed is None:
                why = "the name could not be read"
            else:
                candidates = matching_anime(parsed, tracked)
                if not candidates:
                    why = "no anime on the watchlist matches this name"
                elif len(candidates) > 1:
                    why = (
                        "the name fits "
                        + " and ".join(item.display_name for item in candidates[:3])
                    )
                else:
                    anime = candidates[0]
                    number = episode_number_for(anime, parsed.episode)
                    if number is None:
                        why = "the episode number could not be read from the name"
                    elif not anime.automation_enabled:
                        why = f"{anime.display_name} has Automate switched off"
                    else:
                        episode = resolve_episode(session, anime.id, number, create=False)
                        if episode is None:
                            why = f"{anime.display_name} has no episode {number}"
                        elif episode.media_filename:
                            why = (
                                f"{anime.display_name} episode {number} already has"
                                " its file"
                            )
                        elif episode.status not in DOWNLOADABLE_STATUSES:
                            why = (
                                f"{anime.display_name} episode {number} is not waiting"
                                " for a file"
                            )

            if why is not None or anime is None or episode is None:
                reported += 1
                log_activity(
                    session,
                    kind="TORRENT_UNCLAIMED",
                    message=f"{torrent.name}: {why}",
                    details={"info_hash": torrent.info_hash, "name": torrent.name},
                    anime_id=anime.id if anime is not None else None,
                    episode_id=episode.id if episode is not None else None,
                )
                continue

            row = _new_download(
                episode_id=episode.id,
                anime_id=anime.id,
                info_hash=torrent.info_hash,
                qb_state=torrent.state,
                progress=torrent.progress,
                version=parsed.version or 1,
                label=torrent.name[:500],
                status=DownloadStatus(download_state(torrent.state)),
            )
            session.add(row)
            session.flush()
            record_download_filename(episode, torrent.name, folder=settings.qb_category)
            refresh_episode_status(episode)
            adopted += 1
            log_activity(
                session,
                kind="TORRENT_ADOPTED",
                message=(
                    f"{anime.display_name} episode {episode.number}: adopted the"
                    f" finished torrent {torrent.name}"
                ),
                details={"info_hash": torrent.info_hash},
                anime_id=anime.id,
                episode_id=episode.id,
            )
        if adopted or reported:
            session.commit()
            logger.info(
                "stray torrents: %s adopted, %s reported", adopted, reported
            )
        result["adopted"] = adopted
        result["reported"] = reported
        # Reported so "why did it adopt nothing" has an answer on the page.
        result["skipped_cleared"] = skipped_cleared
        return result
    finally:
        if owns_client and effective_client is not None:
            effective_client.close()


def clear_all_downloads(session: Session) -> int:
    """Forget every download row. Nothing outside AniFlow is touched.

    Same contract as :func:`clear_download`, in bulk: torrents stay in the
    client, files stay on disk, and each episode's status is re-derived so an
    imported episode stays COMPLETE while an unfinished one becomes queueable
    again.
    """
    rows = list(session.scalars(select(TorrentDownload)))
    episodes = [row.episode for row in rows if row.episode is not None]
    # The reason Clear all appeared not to work (build 128): every finished
    # torrent it forgot was re-adopted by the next orphan pass, because a
    # forgotten row is exactly what that pass looks for.
    remembered = _remember_cleared(session, rows)
    # The second refill path, and the one that was still open in build 134:
    # the queue pass re-creates a row for every MATCHED release that has none.
    settled = _settle_releases(session, rows)
    for row in rows:
        session.delete(row)
    session.flush()
    for episode in episodes:
        session.refresh(episode)
        refresh_episode_status(episode)
    session.commit()
    logger.info(
        "cleared %s download(s) from the list; %s torrent(s) will not be"
        " re-adopted and %s release(s) will not be re-queued",
        len(rows),
        remembered,
        settled,
    )
    return len(rows)


def summarize_queue(summary: QueueSummary) -> dict[str, object]:
    return asdict(summary)


def summarize_sync(summary: SyncSummary) -> dict[str, object]:
    return asdict(summary)


__all__ = [
    "QueueSummary",
    "info_hash_of",
    "adopt_orphan_torrents",
    "STALLED_AFTER",
    "live_torrents_for",
    "remove_torrents_for",
    "report_stalled_downloads",
    "retry_stranded_downloads",
    "stalled_downloads",
    "clear_download",
    "SyncSummary",
    "episode_number_from_label",
    "queue_episode_releases",
    "queue_pending_downloads",
    "prune_download_tags",
    "remove_download",
    "resolve_episode",
    "summarize_queue",
    "summarize_sync",
    "sync_download_states",
    "clear_all_downloads",
    "queue_automated_anime",
]
