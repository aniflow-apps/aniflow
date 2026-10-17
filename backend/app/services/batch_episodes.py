"""Take single episodes out of a batch torrent (build 213, docs/batch-episodes-plan.md).

An anime missing episode 4 whose only source is a season-in-one-torrent used to be a dead end: the batch
was found and nothing could use it. Here the batch's file list is read from its ``.torrent`` file, the files
that are this anime's missing episodes are chosen, and the torrent is added to qBittorrent *stopped*, with
every other file set to "do not download", and only then started. Nothing extra reaches the disk.

The shape in the database is two nullable columns on ``downloads`` (see the migration): the first wanted
episode owns the torrent exactly as an ordinary download does, and every further wanted episode gets a row
with no hash of its own that points at it and names its file.

Rules kept from the rest of the project:

* **Only files that clearly give one episode of this show.** A special, NCOP/NCED, a trailer, a file whose
  number two files claim - none is guessed at. They are listed and left unselected.
* **Nothing is trusted that was not read back** - see ``TorrentClient.set_file_priority``.
* **A failure after the torrent was added leaves it stopped**, never running with its whole season selected.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.integrations.qbittorrent.base import is_stopped
from backend.app.integrations.torrent_client import TorrentClient, TorrentClientError
from backend.app.models.anime import Anime, Episode, EpisodeStatus, EpisodeType
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.services.torrent_files import MAX_TORRENT_BYTES, TorrentFormatError, TorrentMeta, parse_torrent

logger = for_component("Downloads")

#: One small request to the indexer for the .torrent file.
FETCH_TIMEOUT_SECONDS = 20.0

#: How long to wait for the client to list a torrent it has just been given.
APPEARS_WITHIN_SECONDS = 10.0

#: Statuses of a download row whose torrent is, or is about to be, in the client.
_LIVE = (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.COMPLETED)

#: Words that make a file something other than an episode: creditless openings and endings, previews,
#: extras. Whole words, either case; looked for in the file's name and in every folder above it.
_NOT_AN_EPISODE = re.compile(
    r"(?<![A-Za-z0-9])(?:NCOP|NCED|OP|ED|PV|CM|SP|OVA|OAD|Creditless|Trailer|Preview|Sample|Extras?|Bonus|"
    r"Specials?|Featurettes?|Menu|Clean(?:\s+(?:Opening|Ending))?)\d*(?![A-Za-z])",
    re.IGNORECASE,
)


class BatchError(Exception):
    """Something that stops a batch being used, in words a person can read."""


@dataclass
class BatchFile:
    index: int
    path: str
    name: str
    size: int
    #: This anime's episode number the file is, or None.
    episode: int | None
    #: wanted | have | not_needed | beyond_count | extra | unreadable | ambiguous | not_video
    status: str


@dataclass
class BatchPlan:
    name: str
    info_hash: str
    files: list[BatchFile] = field(default_factory=list)

    @property
    def wanted(self) -> list[BatchFile]:
        return [item for item in self.files if item.status == "wanted"]


@dataclass
class BatchResult:
    queued: int = 0
    episodes: list[int] = field(default_factory=list)
    note: str | None = None


def fetch_torrent_file(url: str | None) -> bytes:
    """Fetch a .torrent file from the indexer. One request, size-capped, refused if it is not a torrent."""
    if not url or not url.lower().startswith(("http://", "https://")):
        raise BatchError("This release has no .torrent file link to read the batch from.")
    from backend.app.services.outbound import ProxyError, client_kwargs

    try:
        # Through the proxy when one is set (build 235): this is a request to an indexer on the internet, and it
        # was the one that went out directly. A broken proxy fails the fetch instead of quietly becoming a direct one.
        with httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True, **client_kwargs(url)) as http:
            response = http.get(url, headers={"User-Agent": "AniFlow"})
    except ProxyError as exc:
        raise BatchError(f"The proxy is misconfigured, so the .torrent file was not fetched: {exc}") from exc
    except httpx.HTTPError as exc:
        raise BatchError(f"The indexer did not give the .torrent file ({type(exc).__name__}).") from exc
    if response.status_code != 200:
        raise BatchError(f"The indexer answered HTTP {response.status_code} for the .torrent file.")
    if len(response.content) > MAX_TORRENT_BYTES:
        raise BatchError("That is far too large to be a .torrent file.")
    return response.content


def episode_of_file(path: str, offset: int = 0) -> tuple[int | None, str]:
    """The anime's episode number a file inside a batch is, or ``(None, why)``.

    ``why`` is one of ``not_video``, ``extra``, ``unreadable``, or ``ok``.
    """
    from backend.app.services.release_parser import parse_release_name
    from backend.app.services.selection_scan import canonical_episode_number
    from backend.app.services.storage import is_media_file

    name = PurePosixPath(path).name
    if not is_media_file(name):
        return None, "not_video"
    if _NOT_AN_EPISODE.search(path):
        return None, "extra"
    try:
        parsed = parse_release_name(name)
    except ValueError:
        return None, "unreadable"
    number = canonical_episode_number(str(parsed.episode) if parsed.episode is not None else None)
    if number is None:
        return None, "unreadable"
    number -= max(0, offset)
    if number < 1:
        return None, "unreadable"
    return number, "ok"


#: Episode statuses that need nothing more: in the library, or on the way into it. Anything else - missing,
#: available, unavailable, an update - is something this anime still wants.
_NEEDS_NOTHING = (
    EpisodeStatus.COMPLETE,
    EpisodeStatus.DOWNLOADING,
    EpisodeStatus.DOWNLOADED,
    EpisodeStatus.IMPORTING,
)


def _has_it(episode: Episode) -> bool:
    """Whether this episode needs nothing: it is in the library, or a download for it is on its way."""
    from backend.app.services.downloads import _blocking

    return episode.status in _NEEDS_NOTHING or bool(episode.skip_reason) or _blocking(episode) is not None


def plan_batch(anime: Anime, meta: TorrentMeta, *, offset: int | None = None) -> BatchPlan:
    """Which files of this batch are this anime's missing episodes."""
    shift = anime.episode_offset if offset is None else offset
    episodes = {
        episode.number: episode
        for episode in anime.episodes
        if episode.episode_type is EpisodeType.REGULAR
    }
    count = anime.effective_episode_count or anime.episode_count or 0
    plan = BatchPlan(name=meta.name, info_hash=meta.info_hash)

    read = [(file, *episode_of_file(file.path, shift or 0)) for file in meta.files]
    claims: dict[int, int] = {}
    for _file, number, _why in read:
        if number is not None:
            claims[number] = claims.get(number, 0) + 1

    for file, number, why in read:
        status = why
        if why == "ok":
            if claims.get(number or 0, 0) > 1:
                status = "ambiguous"  # two files say they are the same episode; never a guess
            elif number in episodes and _has_it(episodes[number]):
                status = "have"
            elif number in episodes or (count and number <= count):
                status = "wanted"
            else:
                status = "beyond_count"
        plan.files.append(
            BatchFile(
                index=file.index, path=file.path, name=file.name, size=file.size,
                episode=number if why == "ok" else None, status=status,
            )
        )
    return plan


def preview_batch(anime: Anime, torrent_url: str | None, *, offset: int | None = None) -> BatchPlan:
    """What taking this batch's missing episodes would do, without adding anything."""
    data = fetch_torrent_file(torrent_url)
    try:
        meta = parse_torrent(data)
    except TorrentFormatError as exc:
        raise BatchError(f"That is not a torrent file ({exc}).") from exc
    return plan_batch(anime, meta, offset=offset)


def _wait_until_listed(client: TorrentClient, info_hash: str):
    deadline = time.monotonic() + APPEARS_WITHIN_SECONDS
    while True:
        torrent = client.find_by_hash(info_hash)
        if torrent is not None:
            return torrent
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.4)


#: qBittorrent says yes to stop and start a moment before it is so, and a torrent just added reports checking
#: states for a while. The state is read again, and the request repeated, until it is what was asked for (build 217).
SETTLE_SECONDS = 10.0
SETTLE_STEP = 0.5


def _make_safe(client: TorrentClient, info_hash: str, wanted_indexes: set[int]) -> bool:
    """Stop a torrent and hold every file but the wanted ones at "do not download"; whether that is now so.

    The state a failed attempt leaves a torrent in, so that it stays in the client for the person to see and
    for the next try to take over, without ever being able to fetch the whole season.
    """
    # Both are tried whatever the other did: stopped matters most, and either alone is worth having.
    held = stopped = True
    try:
        _settle(client, info_hash, stopped=True, ask=client.pause_torrent, seconds=3.0)
    except (BatchError, TorrentClientError):
        stopped = False
    try:
        files = client.torrent_files(info_hash)
        client.set_file_priority(info_hash, [file.index for file in files if file.index not in wanted_indexes], 0)
    except (BatchError, TorrentClientError):
        held = False
    return stopped and held


def _settle(client: TorrentClient, info_hash: str, *, stopped: bool, ask, seconds: float | None = None) -> None:
    """Ask the client to stop (or start) a torrent, and wait until its state says it has.

    Reported on build 216: a torrent added stopped was read once, straight away, while the client still
    called it something else, and the one read was taken as "it did not stop".
    """
    word = "stop" if stopped else "start"
    patience = SETTLE_SECONDS if seconds is None else seconds
    deadline = time.monotonic() + patience
    refusal: TorrentClientError | None = None
    while True:
        current = client.find_by_hash(info_hash)
        if current is None:
            raise BatchError("qBittorrent no longer lists the torrent.")
        if is_stopped(current.state) == stopped:
            return
        try:
            ask(info_hash)
        except TorrentClientError as exc:  # also "did not stop yet": the next read decides
            refusal = exc
        after = client.find_by_hash(info_hash)
        if after is not None and is_stopped(after.state) == stopped:
            return
        if time.monotonic() >= deadline:
            raise BatchError(
                f"qBittorrent did not {word} the torrent in {patience:.0f} seconds (it reports {current.state!r})"
                + (f": {refusal}" if refusal else "")
            )
        time.sleep(SETTLE_STEP)


def queue_batch(
    session: Session,
    anime: Anime,
    torrent_url: str | None,
    *,
    client: TorrentClient,
    category: str,
    episodes: list[int] | None = None,
    offset: int | None = None,
) -> BatchResult:
    """Add the batch stopped, choose the wanted files, start it, and record one row per wanted episode."""
    from backend.app.services.downloads import _download_tag, _new_download
    from backend.app.services.anime import ensure_episode_rows
    from backend.app.services.episode_state import refresh_episode_status

    data = fetch_torrent_file(torrent_url)
    try:
        meta = parse_torrent(data)
    except TorrentFormatError as exc:
        raise BatchError(f"That is not a torrent file ({exc}).") from exc
    if not meta.is_batch:
        raise BatchError("This torrent is a single file, not a batch.")

    plan = plan_batch(anime, meta, offset=offset)
    chosen = [item for item in plan.wanted if episodes is None or item.episode in episodes]
    if not chosen:
        return BatchResult(note="nothing_wanted")

    # Rows for episodes the anime has no row for yet (an unknown count that the batch just taught us).
    count = anime.effective_episode_count or anime.episode_count or 0
    top = max(count, *(item.episode or 0 for item in chosen))
    ensure_episode_rows(session, anime, top)
    session.flush()
    by_number = {
        episode.number: episode
        for episode in session.scalars(select(Episode).where(Episode.anime_id == anime.id))
        if episode.episode_type is EpisodeType.REGULAR
    }

    # Whoever already holds this torrent: the existing owner when it is live, otherwise a new one (and an
    # old finished record gives up the hash, as _claim_hash already does for a duplicate).
    try:
        present = client.find_by_hash(meta.info_hash) is not None
    except TorrentClientError as exc:
        raise BatchError(f"qBittorrent could not be asked ({exc}).") from exc
    holders = list(session.scalars(select(TorrentDownload).where(TorrentDownload.info_hash == meta.info_hash)))
    # Only a torrent that is really in the client can be added to; a COMPLETED row whose torrent was
    # removed after import is history (the setting "remove after import" leaves exactly that).
    owner = next(
        (row for row in holders if present and row.status in _LIVE and row.batch_download_id is None), None
    )
    for old in holders:
        if old is not owner:
            old.info_hash = None
    session.flush()

    ordered = sorted(chosen, key=lambda item: item.episode or 0)
    rows: list[TorrentDownload] = []
    for item in ordered:
        episode = by_number[item.episode]  # type: ignore[index]
        row = _new_download(
            episode_id=episode.id,
            anime_id=anime.id,
            label=meta.name[:500],
            status=DownloadStatus.QUEUED,
            batch_file=item.path,
        )
        session.add(row)
        rows.append(row)
    if owner is None:
        owner = rows[0]
        owner.info_hash = meta.info_hash
        siblings = rows[1:]
    else:
        siblings = rows
    for sibling in siblings:
        sibling.batch_download_id = owner.id
    attached = owner not in rows  # the torrent is already live under an earlier row
    # The torrent is in the client but no live row of ours holds it (an earlier attempt that failed after the
    # add, a torrent put there by hand): it is taken over rather than added again, which qBittorrent refuses
    # with "Fails." (build 214). It is not ours to delete if this attempt fails too.
    taken_over = present and not attached
    session.flush()

    wanted_indexes = {item.index for item in chosen}
    try:
        if not attached and not taken_over:
            added = client.add_torrent_file(
                data, f"{meta.info_hash}.torrent", category=category, tag=_download_tag(owner.id), stopped=True
            )
            # A refusal is not taken on its word: if the torrent is in the client now, it was added.
            if not added and client.find_by_hash(meta.info_hash) is None:
                raise BatchError(
                    "qBittorrent did not accept the torrent file "
                    "(it also refuses one it already has; look for it in qBittorrent)."
                )
        live = _wait_until_listed(client, meta.info_hash)
        if live is None:
            raise BatchError("qBittorrent did not list the torrent after it was added.")
        if not attached:
            # Held before any file is chosen: it was added stopped, but a client that ignored that, or one
            # still checking the torrent it was just given, is asked again until it is.
            _settle(client, meta.info_hash, stopped=True, ask=client.pause_torrent)
        files = client.torrent_files(meta.info_hash)
        if len(files) != len(meta.files):
            raise BatchError("qBittorrent lists a different number of files than the torrent file has.")
        if not attached:
            unwanted = [file.index for file in files if file.index not in wanted_indexes]
            client.set_file_priority(meta.info_hash, unwanted, 0)
        # Attached to a live torrent: only the new files are raised; what was already chosen stays chosen.
        client.set_file_priority(meta.info_hash, sorted(wanted_indexes), 1)
        _settle(client, meta.info_hash, stopped=False, ask=client.start_torrent)
    except (BatchError, TorrentClientError) as exc:
        # Never leave it running with the whole season selected. Stopped, it downloads nothing; removed, it
        # is gone (the files were never fetched, so none are deleted).
        logger.warning("batch %s could not be set up (%s)", meta.name, exc)
        if not attached:
            # Left in the client, stopped and with every file but the wanted ones held back, so it can be seen
            # there and the next try takes it over (build 217). Only a torrent that cannot be made safe like
            # that, and that is ours, is removed: it must never run with the whole season selected.
            if not _make_safe(client, meta.info_hash, wanted_indexes) and not taken_over:
                try:
                    client.delete_torrent(meta.info_hash, delete_files=False)
                except TorrentClientError:
                    pass
        for row in rows:
            row.status = DownloadStatus.FAILED
            row.error = str(exc)[:255]
            if not attached:
                row.info_hash = None
        session.commit()
        raise BatchError(str(exc)) from exc

    for row in rows:
        row.error = None
        row.qb_state = live.state
    session.commit()
    for row in rows:
        refresh_episode_status(row.episode)
    session.commit()
    logger.info(
        "queued %s of the batch %s: episode(s) %s", len(rows), meta.name, ", ".join(str(i.episode) for i in ordered)
    )
    return BatchResult(queued=len(rows), episodes=[item.episode for item in ordered if item.episode])
