"""What Shoko Server knows, in AniFlow's words (build 75).

Shoko has been the thing on the other side of a folder: AniFlow drops a file
in, Shoko identifies it and moves it. That relationship needs no credentials
and stays as it is.

With an API key, Shoko will also say what it *knows*, and the interesting part
is the gap it can see and AniFlow cannot: AniDB says a series has fourteen
episodes and Shoko holds ten, so four are missing - and Shoko knows exactly
which four, when they aired, and what they are called.

Read-only, and shaped here rather than in the browser: Shoko's own JSON is
wide, deeply nested and versioned to its own schedule, so one place translates
it and everything else works with plain values.

The numbers deliberately come from two sources that can disagree. Shoko's
dashboard counts "missing episodes" its own way (episodes it is collecting,
by its own rules); counting the per-series gap gives a different, larger
number that includes episodes nobody is collecting yet. Both are reported,
labelled, because a single reconciled figure would be a guess about which
definition the reader meant.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import PurePosixPath

from backend.app.core.logging import for_component
from backend.app.integrations.shoko.client import ShokoError, client_from_settings
from backend.app.services import shoko_status

logger = for_component("Shoko")

#: Shoko refuses a pageSize over 100 - "The field pageSize must be between 0
#: and 100" - so this is its limit, not a preference. Paged through rather
#: than truncated, because a library of 300 series is not unusual.
_PAGE_SIZE = 100
_MAX_PAGES = 20
_MAX_EPISODES = 100


@dataclass
class MissingEpisode:
    """One episode AniDB knows about and Shoko has no file for."""

    number: int | None
    title: str | None
    air_date: str | None
    kind: str | None


@dataclass
class ShokoSeries:
    """One series as Shoko holds it."""

    shoko_id: int
    name: str
    local_episodes: int
    total_episodes: int
    #: The specials AniDB lists ("12+1" on its list page) and how many are held.
    local_specials: int = 0
    total_specials: int = 0
    #: Shoko's own ids for this series, which is what makes matching possible
    #: without comparing titles - AniFlow already stores a MyAnimeList id for
    #: anything added through AniList.
    anidb_id: int | None = None
    mal_ids: list[int] = field(default_factory=list)
    tmdb_ids: list[int] = field(default_factory=list)
    missing: list[MissingEpisode] = field(default_factory=list)
    #: Set when the episode list could not be read; the counts still stand.
    episode_error: str | None = None

    @property
    def missing_count(self) -> int:
        """The gap between what exists and what is held."""
        return max(0, self.total_episodes - self.local_episodes)


def read_import_folders(client) -> list[dict[str, object]]:
    """Shoko's import folders, translated (shared by the Shoko page and Libraries)."""
    raw_folders = client.import_folders()
    entries = raw_folders.get("data") if isinstance(raw_folders, dict) else raw_folders
    folders: list[dict[str, object]] = []
    for folder in entries or []:
        if not isinstance(folder, dict):
            continue
        folders.append(
            {
                "id": folder.get("ID"),
                "name": folder.get("Name"),
                "path": folder.get("Path"),
                # "Source", "Destination" or "Both" - which tells AniFlow
                # whether the folder it drops files into is the one Shoko
                # is actually watching.
                "drop_type": folder.get("DropFolderType"),
                "watching": folder.get("WatchForNewFiles"),
                "size": folder.get("Size"),
            }
        )
    return folders


@dataclass
class ShokoOverview:
    """Everything the Shoko page shows."""

    reachable: bool
    detail: str | None = None
    state: str | None = None
    #: Shoko's dashboard figures, as it reports them.
    stats: dict[str, object] = field(default_factory=dict)
    import_folders: list[dict[str, object]] = field(default_factory=list)
    series: list[ShokoSeries] = field(default_factory=list)


def overview(*, with_episodes: bool = True) -> ShokoOverview:
    """Ask Shoko for its library, or say why that failed.

    Never raises: this drives a page whose entire job is to report the state
    of another service, and an exception page would tell the user less than
    the error itself.
    """
    try:
        client = client_from_settings()
    except ShokoError as exc:
        shoko_status.remember("disabled", str(exc))
        return ShokoOverview(reachable=False, detail=str(exc))

    try:
        status = client._get("/api/v3/Init/Status", authenticated=False)
        state = str(status.get("State") or "") or None
    except ShokoError as exc:
        # The sidebar's Shoko line comes from here for free: anything that
        # asks Shoko a real question tells the status what it learned, so the
        # indicator is usually right without anybody probing anything.
        shoko_status.remember("unreachable", str(exc))
        return ShokoOverview(reachable=False, detail=str(exc))

    try:
        stats = client.stats()
    except ShokoError as exc:
        # Reachable but not readable: almost always the key, and saying so
        # sends the user to the right screen.
        shoko_status.remember("unauthorised", str(exc))
        return ShokoOverview(reachable=False, state=state, detail=str(exc))

    folders: list[dict[str, object]] = []
    try:
        folders = read_import_folders(client)
    except ShokoError as exc:
        logger.warning("could not read Shoko's import folders: %s", exc)

    series: list[ShokoSeries] = []
    for page in range(1, _MAX_PAGES + 1):
        try:
            listing = client.series(page=page, size=_PAGE_SIZE)
        except ShokoError as exc:
            if series:
                # Some of the library is better than none, and the page says
                # what went wrong beside what it did get.
                logger.warning("stopped reading Shoko's series list: %s", exc)
                break
            return ShokoOverview(
                reachable=True, state=state, stats=stats, import_folders=folders, detail=str(exc)
            )
        entries = [entry for entry in listing.get("List") or [] if isinstance(entry, dict)]
        for entry in entries:
            row = _series_from(entry)
            if with_episodes and row.missing_count:
                # Only where there is a gap: asking for every episode of every
                # series would be one request per series for nothing.
                _fill_missing(client, row)
            series.append(row)
        if len(series) >= int(listing.get("Total") or 0) or not entries:
            break

    shoko_status.remember("ok")
    series.sort(key=lambda item: (-item.missing_count, item.name.lower()))
    return ShokoOverview(
        reachable=True, state=state, stats=stats, import_folders=folders, series=series
    )


@dataclass
class SeriesFacts:
    """What one Shoko series says about itself, for the watchlist (118).

    The card's Shoko half used to be red whenever Shoko held fewer files than
    AniDB has episodes, and that made almost every card red - reported as
    *"everything shokoserver related is red, I do not understand why"*.

    The count was not wrong; the question was. AniDB's total includes episodes
    that have not aired, so a show three weeks into a twelve-episode season is
    "missing" nine - which is not a problem, it is a Tuesday. What matters is
    an episode that **has aired** and is not there, and Shoko knows the air
    date of every episode it is missing.

    ``total_episodes`` is kept for a different question, and the one that made
    the rework worth doing: AniFlow's own episode count comes from AniList or
    Kitsu, and for older shows it is simply lower than AniDB's. Iron Wok Jan
    read 11 of 11 with an episode missing, because AniDB has twelve.
    """

    aid: int
    name: str
    total_episodes: int
    local_episodes: int
    #: Episodes Shoko is missing whose air date has passed. The red rule.
    missing_aired: int = 0
    #: Every missing episode as ``(number, air date)``, for reconciling
    #: AniFlow's own episode list against AniDB's.
    missing: tuple[tuple[int, str | None], ...] = ()
    #: True when the air dates could not be read - the counts still stand,
    #: but "has it aired" is then unanswerable and nothing is called red on
    #: a guess.
    dates_unknown: bool = False


def holdings() -> tuple[dict[int, SeriesFacts], bool]:
    """Everything Shoko holds, by AniDB id, and whether it answered.

    One walk of the series list plus one episode request per series that has
    a gap - which is what :func:`overview` already does. Called from the
    worker, never from a page load.
    """
    library = overview(with_episodes=True)
    if not library.reachable:
        return {}, False

    today = date.today().isoformat()
    facts: dict[int, SeriesFacts] = {}
    for series in library.series:
        if not series.anidb_id:
            continue
        missing: list[tuple[int, str | None]] = []
        aired = 0
        for episode in series.missing:
            if episode.number is None:
                continue
            missing.append((int(episode.number), episode.air_date))
            # A date in the past is the only thing that counts. No date at
            # all is left alone: AniDB not knowing when an episode airs is
            # not evidence that it has. And *today* is not the past (build
            # 153): AniDB dates an episode by its broadcast day, so on that
            # day it may not have aired yet and has certainly not been
            # subtitled - every weekly show went red once a week for a day.
            if episode.air_date and str(episode.air_date)[:10] < today:
                aired += 1
        facts[int(series.anidb_id)] = SeriesFacts(
            aid=int(series.anidb_id),
            name=series.name,
            total_episodes=series.total_episodes,
            local_episodes=series.local_episodes,
            missing_aired=aired,
            missing=tuple(missing),
            dates_unknown=bool(series.episode_error),
        )
    return facts, True


def shoko_id_for(anidb_id: int) -> int | None:
    """Shoko's own id for an AniDB id, or None if it does not hold it.

    Shoko is asked directly rather than the library walked: this is called
    per anime, and a walk per anime is a walk per card.
    """
    try:
        client = client_from_settings()
        payload = client.anidb_anime(int(anidb_id))
    except ShokoError as exc:
        logger.debug("Shoko does not know anidb %s: %s", anidb_id, exc)
        return None
    return shoko_id_in(payload)


def shoko_id_in(payload: dict) -> int | None:
    """Shoko's own id for a series out of its AniDB record, or None when it does not hold the series.

    Shoko 4 answers with the AniDB record and, when it holds the series, its own id beside it. The shape has moved
    between versions, so every place it has lived is checked rather than one.
    """
    ids = payload.get("IDs") if isinstance(payload.get("IDs"), dict) else {}
    for value in (payload.get("ShokoID"), ids.get("Shoko"), ids.get("ID")):
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, list) and value and isinstance(value[0], int):
            return value[0]
    return None


def file_names_for(anidb_id: int) -> list[str]:
    """The names of the files Shoko holds for one series (build 117).

    The only place a library AniFlow did not download itself says who
    released it and at what quality. Returns bare file names - the folders
    are Shoko's business and say nothing about the release.

    Empty for anything that goes wrong, which is the ordinary answer for a
    series Shoko has not got and for a Shoko that is restarting.
    """
    shoko_id = shoko_id_for(anidb_id)
    if shoko_id is None:
        return []
    try:
        rows = client_from_settings().series_files(shoko_id)
    except ShokoError as exc:
        logger.debug("no file list for anidb %s: %s", anidb_id, exc)
        return []

    names: list[str] = []
    for row in rows:
        for place in row.get("Locations") or []:
            if not isinstance(place, dict):
                continue
            path = place.get("RelativePath") or place.get("AbsolutePath") or place.get("Path")
            if path:
                names.append(PurePosixPath(str(path).replace("\\", "/")).name)
                break
        else:
            # Older shapes put the name on the file itself.
            direct = row.get("Filename") or row.get("Name") or row.get("Path")
            if direct:
                names.append(PurePosixPath(str(direct).replace("\\", "/")).name)
    return names


@dataclass
class EpisodeFile:
    """One file Shoko holds, and which episode it is (build 119).

    Reported after adoption worked: two adopted series showed every episode as
    *"Not available"* while their files were sitting in the library, and a
    third - adopted earlier, with a folder already recorded - showed them all
    as owned. *"It seems it did detect the subgroup but why not the episodes,
    it should at least detect the file names or not?"*

    It should, and could not. Nothing in AniFlow looked at the files Shoko
    already has: the Shoko resolver only ever considers episodes AniFlow
    itself handed over (``import_filename`` set), which for an adopted series
    is none of them. So this asks Shoko the one question that closes the gap -
    which file is which episode - and the answer carries what the file names
    would not say either way: the quality, from Shoko's own media info.
    """

    number: int
    filename: str
    #: The folder the file sits in, as Shoko names it. AniFlow follows this
    #: rather than inventing one: in this mode Shoko owns the naming.
    folder: str | None = None
    size: int | None = None
    #: Pixel height, when Shoko says. 1080 for a 1080p file.
    height: int | None = None
    #: Which of Shoko's import folders holds it (build 194). It is what lets
    #: an adopted series be filed in the library that is linked to that folder.
    import_folder_id: int | None = None


#: What AniDB's episode ``Type`` is called, depending on the Shoko version. Only
#: these are regular episodes; specials, credits and trailers are files too and
#: are not episode numbers in AniFlow's list. "Normal" was missing here, which
#: dropped every episode of a Shoko that says it that way.
_REGULAR_EPISODE_KINDS = {"episode", "normal", "regular"}


def episode_files_for(anidb_id: int) -> list[EpisodeFile]:
    """Every file Shoko holds for one series, by episode number.

    Looked up by AniDB id, which costs one extra request to find Shoko's own
    id. Prefer :func:`series_episode_files` when the id is already known.
    """
    shoko_id = shoko_id_for(anidb_id)
    if shoko_id is None:
        return []
    return series_episode_files(shoko_id)[0]


def series_episode_files(shoko_id: int) -> tuple[list[EpisodeFile], str | None]:
    """The files Shoko holds for one of its series, and why there are none.

    ``(files, None)`` on success; ``([], reason)`` otherwise, in words a person
    can act on. Adoption used to give up silently here - an empty list looks
    the same whether Shoko holds nothing or its answer was shaped differently
    than expected - which is why "it did not add any episode files" could not
    be diagnosed. Never raises.
    """
    try:
        payload = client_from_settings()._get(
            f"/api/v3/Series/{int(shoko_id)}/Episode",
            params={
                "includeFiles": "true",
                "includeDataFrom": "AniDB",
                "includeMediaInfo": "true",
                "pageSize": _MAX_EPISODES,
            },
        )
    except ShokoError as exc:
        logger.debug("no episode files for shoko series %s: %s", shoko_id, exc)
        return [], f"Shoko would not list the episodes ({exc})"

    entries = payload.get("List") if isinstance(payload, dict) else payload
    entries = [row for row in entries or [] if isinstance(row, dict)]
    if not entries:
        return [], "Shoko lists no episodes for this series"

    found: list[EpisodeFile] = []
    skipped_kind = 0
    for episode in entries:
        anidb = episode.get("AniDB") if isinstance(episode.get("AniDB"), dict) else {}
        kind = anidb.get("Type")
        if kind and str(kind).strip().lower() not in _REGULAR_EPISODE_KINDS:
            skipped_kind += 1
            continue
        number = anidb.get("EpisodeNumber")
        if not isinstance(number, int):
            number = episode.get("EpisodeNumber")
        if not isinstance(number, int):
            continue
        for entry in episode.get("Files") or episode.get("files") or []:
            if not isinstance(entry, dict):
                continue
            path, folder_id = _location_of(entry)
            if not path:
                continue
            place = PurePosixPath(str(path).replace("\\", "/"))
            # The folder is the first step below the import folder when the path
            # is relative to it ("Show/Season 1/ep.mkv" -> "Show"), else the
            # directory the file sits in.
            folder = place.parts[0] if len(place.parts) > 1 and not place.is_absolute() else place.parent.name
            found.append(
                EpisodeFile(
                    number=int(number),
                    filename=place.name,
                    folder=folder or None,
                    size=entry.get("Size") if isinstance(entry.get("Size"), int) else None,
                    height=_video_height(entry),
                    import_folder_id=folder_id,
                )
            )
            break
    if not found:
        if skipped_kind == len(entries):
            return [], f"Shoko lists {len(entries)} episodes but none is a regular episode"
        return [], "Shoko holds no files for this series"
    return found, None


def _location_of(entry: dict) -> tuple[str | None, int | None]:
    """A file's path and the import folder it is in, whichever way this Shoko says it."""
    for place in entry.get("Locations") or []:
        if not isinstance(place, dict):
            continue
        path = place.get("RelativePath") or place.get("AbsolutePath") or place.get("Path")
        if path:
            folder_id = place.get("ImportFolderID")
            if folder_id is None:
                folder_id = place.get("ImportFolderId")
            return str(path), int(folder_id) if isinstance(folder_id, int) else None
    direct = entry.get("Filename") or entry.get("Name") or entry.get("Path")
    return (str(direct) if direct else None), None


def _file_path(entry: dict) -> str | None:
    """Where one file is, whichever way this Shoko says it."""
    for place in entry.get("Locations") or []:
        if isinstance(place, dict):
            path = (
                place.get("RelativePath")
                or place.get("AbsolutePath")
                or place.get("Path")
            )
            if path:
                return str(path)
    direct = entry.get("Filename") or entry.get("Name") or entry.get("Path")
    return str(direct) if direct else None


def _video_height(entry: dict) -> int | None:
    """The picture height Shoko reports, if it reports one.

    Asked for directly: *"you can not detect the quality from the resolution?
    can this info not be obtainable?"*  From Shoko it can, and for nothing -
    it has read every file it holds. Probing the file ourselves stays the
    fallback for a library AniFlow can reach on disk.
    """
    media = entry.get("MediaInfo") if isinstance(entry.get("MediaInfo"), dict) else {}
    video = media.get("Video") or entry.get("Video")
    if isinstance(video, list):
        video = video[0] if video else None
    if isinstance(video, dict):
        for key in ("Height", "height", "PixelHeight"):
            value = video.get(key)
            if isinstance(value, int) and value > 0:
                return value
        # "1920x1080", which some versions report instead of the pair.
        resolution = video.get("Resolution") or video.get("resolution")
        if isinstance(resolution, str) and "x" in resolution:
            try:
                return int(resolution.split("x")[1])
            except (IndexError, ValueError):
                return None
    return None


def poster_bytes_for_series(shoko_id: int) -> bytes | None:
    """Shoko's poster for a series it already knows by its own id (build 180)."""
    try:
        client = client_from_settings()
        payload = client._get(f"/api/v3/Series/{int(shoko_id)}/Images")
    except ShokoError as exc:
        logger.debug("no artwork for shoko series %s: %s", shoko_id, exc)
        return None
    posters = payload.get("Posters") or payload.get("data") or []
    for poster in posters if isinstance(posters, list) else []:
        if not isinstance(poster, dict):
            continue
        source = poster.get("Source") or poster.get("source")
        image_id = poster.get("ID") or poster.get("id")
        if not source or image_id is None:
            continue
        try:
            data = client.image_bytes(f"/api/v3/Image/{source}/Poster/{image_id}")
        except ShokoError:
            continue
        if data:
            return data
    return None


def poster_bytes_for(anidb_id: int) -> bytes | None:
    """Shoko's own poster for a series, as bytes (build 117).

    Fetched and stored rather than linked: Shoko's image endpoints want the
    API key, which has no business in the browser, and a stored poster keeps
    working while Shoko restarts.
    """
    shoko_id = shoko_id_for(anidb_id)
    if shoko_id is None:
        return None
    try:
        client = client_from_settings()
        payload = client._get(f"/api/v3/Series/{shoko_id}/Images")
    except ShokoError as exc:
        logger.debug("no artwork for anidb %s: %s", anidb_id, exc)
        return None

    posters = payload.get("Posters") or payload.get("data") or []
    for poster in posters if isinstance(posters, list) else []:
        if not isinstance(poster, dict):
            continue
        source = poster.get("Source") or poster.get("source")
        image_id = poster.get("ID") or poster.get("id")
        if not source or image_id is None:
            continue
        try:
            data = client.image_bytes(f"/api/v3/Image/{source}/Poster/{image_id}")
        except ShokoError as exc:
            logger.debug("poster %s/%s could not be read: %s", source, image_id, exc)
            continue
        if data:
            return data
    return None


def _series_from(entry: dict) -> ShokoSeries:
    ids = entry.get("IDs") if isinstance(entry.get("IDs"), dict) else {}
    sizes = entry.get("Sizes") if isinstance(entry.get("Sizes"), dict) else {}
    local = sizes.get("Local") if isinstance(sizes.get("Local"), dict) else {}
    total = sizes.get("Total") if isinstance(sizes.get("Total"), dict) else {}
    return ShokoSeries(
        shoko_id=int(ids.get("ID") or 0),
        name=str(entry.get("Name") or "(unnamed)"),
        local_episodes=int(local.get("Episodes") or 0),
        total_episodes=int(total.get("Episodes") or 0),
        local_specials=int(local.get("Specials") or 0),
        total_specials=int(total.get("Specials") or 0),
        anidb_id=ids.get("AniDB") if isinstance(ids.get("AniDB"), int) else None,
        mal_ids=[value for value in ids.get("MAL") or [] if isinstance(value, int)],
        tmdb_ids=[
            value
            for value in ((ids.get("TMDB") or {}).get("Show") or [])
            if isinstance(value, int)
        ]
        if isinstance(ids.get("TMDB"), dict)
        else [],
    )


def _fill_missing(client, row: ShokoSeries) -> None:
    """Which episodes are missing, not just how many.

    ``Size`` is the number of files Shoko holds for an episode, so zero is the
    definition of missing. The AniDB block carries the number, the title and
    the air date - which is what makes this list actionable rather than a
    count.
    """
    try:
        payload = client._get(
            f"/api/v3/Series/{row.shoko_id}/Episode",
            params={
                "includeMissing": "true",
                "includeDataFrom": "AniDB",
                "pageSize": _MAX_EPISODES,
            },
        )
    except ShokoError as exc:
        row.episode_error = str(exc)
        return

    for episode in payload.get("List") or []:
        if not isinstance(episode, dict) or episode.get("Size"):
            continue
        anidb = episode.get("AniDB") if isinstance(episode.get("AniDB"), dict) else {}
        kind = anidb.get("Type")
        # Specials, credits and trailers are "missing" too, and listing them
        # beside the episodes would make every complete series look incomplete.
        if kind and str(kind) != "Episode":
            continue
        row.missing.append(
            MissingEpisode(
                number=anidb.get("EpisodeNumber") if isinstance(anidb.get("EpisodeNumber"), int) else None,
                title=str(episode.get("Name") or "") or None,
                air_date=str(anidb.get("AirDate") or "") or None,
                kind=str(kind) if kind else None,
            )
        )
    row.missing.sort(key=lambda item: (item.number is None, item.number or 0))


# --- what the Shoko page asks, remembered (build 180) -------------------------
#
# The page used to put the whole library to Shoko - the series list, an episode
# request for every series with a gap - on every visit. Nothing on it changes
# in the time it takes to click away and back, so the answer is kept for ten
# minutes and the Refresh button is the way to ask again. The workers call
# overview() directly and are unaffected.
_CACHE_SECONDS = 600
_lock = threading.Lock()
_overview_cache: tuple[float, datetime, ShokoOverview] | None = None
_detail_cache: dict[int, tuple[float, "SeriesDetail"]] = {}


def cached_overview(*, force: bool = False) -> tuple[ShokoOverview, datetime]:
    """The library as the page shows it, and when Shoko was actually asked.

    Only a complete answer is remembered: an unreachable Shoko must be asked
    again next time, not reported for ten minutes after it came back.
    """
    global _overview_cache
    with _lock:
        cached = _overview_cache
        if not force and cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
            return cached[2], cached[1]
        if force:
            _detail_cache.clear()
    fresh = overview()
    asked = datetime.now(UTC)
    if fresh.reachable and not fresh.detail:
        with _lock:
            _overview_cache = (time.monotonic(), asked, fresh)
    return fresh, asked


def forget_cache() -> None:
    """Drop what the page remembers (used by tests)."""
    global _overview_cache
    with _lock:
        _overview_cache = None
        _detail_cache.clear()



@dataclass
class DetailFile:
    """One file Shoko holds for an episode."""

    name: str
    size: int | None = None
    #: Picture height in pixels, when Shoko has read the file.
    height: int | None = None


@dataclass
class DetailEpisode:
    """One AniDB episode, and what Shoko holds for it."""

    number: int | None
    title: str | None
    air_date: str | None
    files: list[DetailFile] = field(default_factory=list)

    @property
    def missing(self) -> bool:
        return not self.files


@dataclass
class SeriesDetail:
    """Everything the series page shows."""

    shoko_id: int
    name: str
    anidb_id: int | None = None
    mal_ids: list[int] = field(default_factory=list)
    local_episodes: int = 0
    total_episodes: int = 0
    episodes: list[DetailEpisode] = field(default_factory=list)
    #: Set when the episode list could not be read; the header still stands.
    error: str | None = None


def series_detail(shoko_id: int, *, force: bool = False) -> SeriesDetail | None:
    """One series with every episode, or None when Shoko does not know it.

    Remembered like the overview, and for the same reason. Episodes are paged
    because a long-running show has more than one page of them.
    """
    with _lock:
        cached = _detail_cache.get(shoko_id)
        if not force and cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
            return cached[1]
    try:
        client = client_from_settings()
        entry = client._get(f"/api/v3/Series/{int(shoko_id)}")
    except ShokoError as exc:
        logger.debug("no series %s in Shoko: %s", shoko_id, exc)
        return None
    row = _series_from(entry) if isinstance(entry, dict) else ShokoSeries(int(shoko_id), "(unnamed)", 0, 0)
    detail = SeriesDetail(
        shoko_id=row.shoko_id or int(shoko_id),
        name=row.name,
        anidb_id=row.anidb_id,
        mal_ids=row.mal_ids,
        local_episodes=row.local_episodes,
        total_episodes=row.total_episodes,
    )
    for page in range(1, 6):
        try:
            payload = client._get(
                f"/api/v3/Series/{int(shoko_id)}/Episode",
                params={
                    "includeMissing": "true",
                    "includeFiles": "true",
                    "includeMediaInfo": "true",
                    "includeDataFrom": "AniDB",
                    "pageSize": _MAX_EPISODES,
                    "page": page,
                },
            )
        except ShokoError as exc:
            detail.error = str(exc)
            break
        listing = [item for item in payload.get("List") or [] if isinstance(item, dict)]
        for episode in listing:
            anidb = episode.get("AniDB") if isinstance(episode.get("AniDB"), dict) else {}
            kind = anidb.get("Type")
            if kind and str(kind) != "Episode":
                continue
            number = anidb.get("EpisodeNumber")
            files: list[DetailFile] = []
            for entry_file in episode.get("Files") or []:
                if not isinstance(entry_file, dict):
                    continue
                path = _file_path(entry_file)
                if not path:
                    continue
                size = entry_file.get("Size")
                files.append(
                    DetailFile(
                        name=PurePosixPath(str(path).replace(chr(92), "/")).name,
                        size=size if isinstance(size, int) else None,
                        height=_video_height(entry_file),
                    )
                )
            detail.episodes.append(
                DetailEpisode(
                    number=number if isinstance(number, int) else None,
                    title=str(episode.get("Name") or "") or None,
                    air_date=str(anidb.get("AirDate") or "")[:10] or None,
                    files=files,
                )
            )
        if len(listing) < _MAX_EPISODES:
            break
    detail.episodes.sort(key=lambda item: (item.number is None, item.number or 0))
    if not detail.error:
        with _lock:
            _detail_cache[shoko_id] = (time.monotonic(), detail)
    return detail


__all__ = [
    "DetailEpisode",
    "DetailFile",
    "SeriesDetail",
    "cached_overview",
    "forget_cache",
    "series_detail",
    "MissingEpisode",
    "EpisodeFile",
    "SeriesFacts",
    "episode_files_for",
    "holdings",
    "ShokoOverview",
    "ShokoSeries",
    "file_names_for",
    "overview",
    "poster_bytes_for",
    "poster_bytes_for_series",
    "shoko_id_for",
]
