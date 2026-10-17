"""What Shoko Server says about the library (build 75).

One endpoint, because the page it serves asks one question: what does Shoko
know that AniFlow does not? Everything is read-only - Shoko owns the library,
and two programs renaming the same files is how a library gets ruined.
"""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.session import get_db

router = APIRouter()


class ShokoMissingEpisode(BaseModel):
    number: int | None = None
    title: str | None = None
    air_date: str | None = None
    kind: str | None = None


class ShokoSeriesView(BaseModel):
    shoko_id: int
    name: str
    local_episodes: int
    total_episodes: int
    missing_count: int = 0
    #: Shoko's own ids. The MyAnimeList one matters most: AniFlow stores it for
    #: anything added through AniList, so the two libraries can be matched
    #: without comparing titles.
    anidb_id: int | None = None
    mal_ids: list[int] = []
    tmdb_ids: list[int] = []
    missing: list[ShokoMissingEpisode] = []
    episode_error: str | None = None
    #: AniFlow's own picture for this series, for when Shoko has none: the
    #: watchlist entry's cover, or the downloaded calendar's (build 180).
    cover_url: str | None = None
    #: The watchlist entry this series is, when there is one.
    watchlist_id: str | None = None


class ShokoAddResponse(BaseModel):
    added: bool
    anime_id: str | None = None
    #: Why not, in words.
    detail: str | None = None


class ShokoOverviewResponse(BaseModel):
    """Shoko's own answer, translated but not interpreted."""

    reachable: bool
    detail: str | None = None
    state: str | None = None
    #: Shoko's dashboard figures as it reports them, passed through verbatim -
    #: it is Shoko's page, and renaming its fields would only hide what it
    #: actually said.
    stats: dict[str, object] = {}
    import_folders: list[dict[str, object]] = []
    series: list[ShokoSeriesView] = []
    #: The per-series gap added up. Deliberately reported next to Shoko's own
    #: MissingEpisodes rather than instead of it: Shoko counts what it is
    #: collecting, this counts everything AniDB lists, and presenting one
    #: number would be a guess about which the reader meant.
    missing_total: int = 0
    #: When Shoko was actually asked. The page remembers its answer for ten
    #: minutes (build 180), so this is what tells you how old it is.
    fetched_at: str | None = None


class ShokoUnlinkedSeries(BaseModel):
    """One series Shoko holds that the watchlist has no entry for."""

    shoko_id: int
    name: str
    local_episodes: int
    total_episodes: int
    missing_count: int
    anidb_id: int | None = None
    mal_id: int | None = None
    #: A poster from the downloaded data, when there is one (build 94).
    cover_url: str | None = None


class ShokoUnlinkedResponse(BaseModel):
    #: Empty with a ``detail`` when Shoko could not be read. The watchlist
    #: works without this, so an outage must not fail the page.
    series: list[ShokoUnlinkedSeries] = []
    detail: str | None = None


@router.get("/unlinked", response_model=ShokoUnlinkedResponse)
def shoko_unlinked(db: Session = Depends(get_db)) -> ShokoUnlinkedResponse:
    """Shoko's series with no watchlist entry - the gap between two libraries."""
    from backend.app.services.shoko_watchlist import unlinked

    rows, detail = unlinked(db)
    return ShokoUnlinkedResponse(
        series=[ShokoUnlinkedSeries(**asdict(row)) for row in rows], detail=detail
    )


class ShokoAdoptResponse(BaseModel):
    """What one run of "take everything Shoko has" did (build 117)."""

    considered: int = 0
    adopted: int = 0
    enriched: int = 0
    skipped: int = 0
    #: Why it could do nothing, when that is the answer - an unreachable
    #: Shoko, or no API key. Empty on an ordinary run.
    detail: str | None = None
    #: What was taken on, for a page that wants to say more than a number.
    names: list[str] = []
    #: What could not be filled in, one line each (build 194): a cover Shoko
    #: has none of, a file list it would not give.
    problems: list[str] = []


class ShokoCheckResponse(BaseModel):
    """The answer to "is Shoko there", asked as cheaply as possible (118)."""

    state: str
    detail: str | None = None


@router.post("/check", response_model=ShokoCheckResponse)
def shoko_check() -> ShokoCheckResponse:
    """One request to Shoko, so the status line stops saying "not checked".

    Its own endpoint rather than part of /system/health: that one is what
    Docker polls, it already makes two live calls, and a container gets
    restarted when it answers slowly. This is a page asking a question about
    somebody else's server, and the page can wait two seconds for it.
    """
    from backend.app.services import shoko_status

    if not shoko_status.configured():
        return ShokoCheckResponse(state="disabled")
    state = shoko_status.check_now()
    return ShokoCheckResponse(state=state, detail=shoko_status.status().detail)


class ShokoRunImportResponse(BaseModel):
    started: bool
    detail: str | None = None


@router.post("/run-import", response_model=ShokoRunImportResponse)
def shoko_run_import() -> ShokoRunImportResponse:
    """Have Shoko run its import now (build 178).

    The fix for a file that was handed over and is waiting: Shoko's own
    schedule for this can be very long, and pressing the button in its
    interface is what usually clears it. Nothing is read back - Shoko queues
    the work, and the In Progress page shows what came of it.
    """
    from backend.app.integrations.shoko.client import ShokoError, client_from_settings

    try:
        client_from_settings().run_import()
    except ShokoError as exc:
        return ShokoRunImportResponse(started=False, detail=str(exc))
    return ShokoRunImportResponse(started=True)


@router.post("/adopt-all", response_model=ShokoAdoptResponse)
def shoko_adopt_all(db: Session = Depends(get_db)) -> ShokoAdoptResponse:
    """Put every Shoko series on the watchlist, and fill in what is missing.

    The same work the worker does every half hour, without its limit: this is
    somebody waiting for an answer, so it finishes the job rather than taking
    the first fifty.

    Nothing is downloaded and no tracker is asked - adoption is about what
    already exists on disk. Releases are found afterwards, the ordinary way.
    """
    from datetime import UTC, datetime

    from backend.app.services import schedule_state
    from backend.app.services.shoko_adopt import adopt_all
    from backend.app.worker.runtime import worker_state

    summary = adopt_all(db, limit=None)
    result = {
        "considered": summary.considered,
        "adopted": summary.adopted,
        "enriched": summary.enriched,
        "skipped": summary.skipped,
        "detail": summary.detail,
        "problems": len(summary.problems),
    }
    # This button and the Schedule page's "Run" on shoko_adoption do the same
    # work (build 160); this is what makes the Schedule page agree with
    # reality regardless of which one somebody actually pressed (build 161).
    now = datetime.now(UTC)
    state = worker_state.tasks.get("shoko_adoption")
    if state is not None:
        state.last_run_at = now
        state.last_result = result
        state.last_error = None
    schedule_state.save_with_session(db, "shoko_adoption", last_run_at=now, last_result=result, last_error=None)
    db.commit()
    return ShokoAdoptResponse(
        considered=summary.considered,
        adopted=summary.adopted,
        enriched=summary.enriched,
        skipped=summary.skipped,
        detail=summary.detail,
        names=summary.names[:50],
        problems=summary.problems[:40],
    )


def _agreed_total(shoko_total: int, local: int, watchlist_count: int | None) -> int:
    """How many episodes the series has: the watchlist's count when it has one, else Shoko's.

    Never below what is held - a file for episode 13 of a "12 episode" show makes it 13.
    """
    if not watchlist_count:
        return shoko_total
    return max(int(watchlist_count), local)


@router.post("/series/{shoko_id}/add-to-watchlist", response_model=ShokoAddResponse)
def shoko_add_to_watchlist(shoko_id: int, db: Session = Depends(get_db)) -> ShokoAddResponse:
    """The button on a Shoko series that the watchlist does not have (build 243)."""
    from backend.app.services.shoko_adopt import adopt_series
    from backend.app.worker.runtime import worker_state

    anime, reason = adopt_series(db, shoko_id)
    if anime is None:
        return ShokoAddResponse(added=False, detail=reason)
    # An anime with episodes still to get is a job for the ordinary workflow: look for them now.
    worker_state.request_run("queue_downloads")
    return ShokoAddResponse(added=True, anime_id=anime.id)


@router.get("", response_model=ShokoOverviewResponse)
def shoko_overview(refresh: bool = False, db: Session = Depends(get_db)) -> ShokoOverviewResponse:
    """The Shoko page's data - remembered for ten minutes, ``refresh`` re-asks."""
    from sqlalchemy import select

    from backend.app.models.anime import Anime
    from backend.app.services.shoko_library import cached_overview
    from backend.app.services.shoko_watchlist import _cover_for

    result, asked = cached_overview(force=refresh)
    watching: dict[int, tuple[str, str | None, int | None]] = {}
    for anime in db.scalars(select(Anime).where(Anime.anidb_id.is_not(None))):
        watching.setdefault(int(anime.anidb_id), (anime.id, anime.cover_url, anime.effective_episode_count))
    series = []
    for row in result.series:
        known = watching.get(int(row.anidb_id)) if row.anidb_id else None
        cover = (known[1] if known else None) or _cover_for(row.anidb_id)
        fields = {**asdict(row)}
        total = _agreed_total(row.total_episodes, row.local_episodes, known[2] if known else None)
        fields["total_episodes"] = total
        # The watchlist's own count wins where there is one (build 243): a missing "episode 13" of a
        # twelve-episode show is not a gap, whatever Shoko's cache of AniDB says.
        fields["missing"] = [item for item in fields["missing"] if item["number"] is None or item["number"] <= total]
        series.append(
            ShokoSeriesView(
                **{**fields, "missing_count": max(0, total - row.local_episodes)},
                cover_url=cover,
                watchlist_id=known[0] if known else None,
            )
        )
    return ShokoOverviewResponse(
        reachable=result.reachable,
        detail=result.detail,
        state=result.state,
        stats=result.stats,
        import_folders=result.import_folders,
        series=series,
        missing_total=sum(row.missing_count for row in series),
        fetched_at=asked.isoformat(),
    )


class MylistRow(BaseModel):
    aid: int
    title: str
    english: str | None = None
    year: str = ""
    type: str
    held: int
    total: int
    specials_total: int = 0
    held_specials: int = 0
    #: finished | airing | unaired - estimated, the export has no end date.
    airing: str
    #: The watchlist entry for it, when there already is one.
    watchlist_id: str | None = None


class MylistResponse(BaseModel):
    items: list[MylistRow] = []
    #: False until the user has uploaded an export.
    has_export: bool = False
    uploaded_at: str | None = None
    filename: str | None = None
    #: Anime in the export before any filter.
    considered: int = 0
    #: Whether the stored export carries the files section (build 258); false means upload it again for the
    #: AniDB page's Not watched, State unknown and Several groups tabs.
    files_read: bool = False


@router.get("/anidb-mylist", response_model=MylistResponse)
def shoko_anidb_mylist(
    adult: str = "hide", airing: str = "finished", status: str = "incomplete", types: str = "",
    db: Session = Depends(get_db),
) -> MylistResponse:
    """AniDB's My List filters over the export the user uploaded (build 244).

    The defaults are the filter set the first reference list was made with. ``types`` is comma-separated.
    """
    from datetime import date

    from backend.app.services import anidb_mylist

    stored = anidb_mylist.load()
    if stored is None:
        return MylistResponse()
    wanted = anidb_mylist.Filters(
        adult=adult, airing=airing, status=status,
        types=tuple(part.strip() for part in types.split(",") if part.strip()),
    )
    today = date.today()
    rows = anidb_mylist.select(stored.entries, wanted, today=today)
    from sqlalchemy import select as sql_select

    from backend.app.models.anime import Anime

    watching = {
        int(anidb_id): anime_id
        for anime_id, anidb_id in db.execute(sql_select(Anime.id, Anime.anidb_id).where(Anime.anidb_id.is_not(None)))
    }
    return MylistResponse(
        items=[
            MylistRow(**asdict(row), airing=anidb_mylist.airing_state(row, today), watchlist_id=watching.get(row.aid))
            for row in rows
        ],
        has_export=True,
        uploaded_at=stored.uploaded_at,
        filename=stored.filename,
        considered=len(stored.entries),
        files_read=stored.files_read,
    )


@router.post("/anidb-export", response_model=MylistResponse)
async def shoko_anidb_export(request: Request, db: Session = Depends(get_db)) -> MylistResponse:
    """Take the My List export the user got from AniDB (build 244).

    The raw ``.tgz`` as the request body, its name in ``X-Filename``: multipart would need a package this
    install does not carry, and a file is all there is to send. Nothing is kept unless it parses.
    """
    from backend.app.services import anidb_mylist

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > anidb_mylist.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is too large to be a My List export")
    body = await request.body()
    try:
        entries = anidb_mylist.parse(body)
    except anidb_mylist.ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from urllib.parse import unquote

    anidb_mylist.save(entries, unquote(request.headers.get("x-filename") or "export.tgz"), archive=body)
    return shoko_anidb_mylist(db=db)


@router.post("/anidb-mylist/{aid}/add-to-watchlist", response_model=ShokoAddResponse)
def shoko_add_mylist_entry(aid: int, db: Session = Depends(get_db)) -> ShokoAddResponse:
    """Put one anime of the AniDB My List on the watchlist, so AniFlow can complete it (build 245)."""
    from types import SimpleNamespace

    from backend.app.services import anidb_mylist
    from backend.app.services.shoko_adopt import adopt_entry
    from backend.app.services.shoko_library import cached_overview
    from backend.app.worker.runtime import worker_state

    stored = anidb_mylist.load()
    found = next((row for row in (stored.entries if stored else []) if row.aid == aid), None)
    if found is None:
        return ShokoAddResponse(added=False, detail="This anime is not in the uploaded export")
    # Shoko's series for it, when it holds one: its files and folder are then adopted with the entry.
    held, _ = cached_overview()
    series = next((row for row in held.series if row.anidb_id == aid), None) if held.reachable else None
    entry = SimpleNamespace(
        name=found.title, anidb_id=aid, total_episodes=found.total, shoko_id=series.shoko_id if series else None,
    )
    from sqlalchemy import select as sa_select

    from backend.app.models.anime import Anime

    already_there = bool(db.scalar(sa_select(Anime.id).where(Anime.anidb_id == aid)))
    anime, reason = adopt_entry(db, entry)
    if anime is None:
        return ShokoAddResponse(added=False, detail=reason)
    detail = None
    if not already_there:
        # What the list says the user has is not fetched (item 36b); Shoko's own files, adopted above, already
        # count as owned and are left as they are.
        from backend.app.services.episode_skip import skip_episodes

        if found.held_numbers:
            skip_episodes(anime, found.held_numbers)
            db.commit()
        elif found.held:
            detail = "This export was uploaded before AniFlow read which episodes are in the list - upload it again to skip the ones you have"
    worker_state.request_run("queue_downloads")
    return ShokoAddResponse(added=True, anime_id=anime.id, detail=detail)


@router.get("/series/{shoko_id}/poster", include_in_schema=False)
def shoko_series_poster(shoko_id: int) -> Response:
    """Shoko's own poster for one series, fetched with the API key.

    Served from here because Shoko's image endpoints want the key and the
    browser has no business holding it. Cached hard: a poster does not change.
    """
    from backend.app.services.shoko_library import poster_bytes_for_series

    data = poster_bytes_for_series(shoko_id)
    if not data:
        raise HTTPException(status_code=404, detail="Shoko has no poster for this series")
    kind = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
    return Response(content=data, media_type=kind, headers={"Cache-Control": "public, max-age=86400"})


class ShokoFileView(BaseModel):
    name: str
    size: int | None = None
    height: int | None = None


class ShokoEpisodeView(BaseModel):
    number: int | None = None
    title: str | None = None
    air_date: str | None = None
    missing: bool = False
    files: list[ShokoFileView] = []


class ShokoSeriesDetailResponse(BaseModel):
    shoko_id: int
    name: str
    anidb_id: int | None = None
    mal_ids: list[int] = []
    local_episodes: int = 0
    total_episodes: int = 0
    episodes: list[ShokoEpisodeView] = []
    error: str | None = None
    cover_url: str | None = None
    watchlist_id: str | None = None


@router.get("/series/{shoko_id}", response_model=ShokoSeriesDetailResponse)
def shoko_series_detail(
    shoko_id: int, refresh: bool = False, db: Session = Depends(get_db)
) -> ShokoSeriesDetailResponse:
    """One series with every episode and the files behind it."""
    from sqlalchemy import select

    from backend.app.models.anime import Anime
    from backend.app.services.shoko_library import series_detail
    from backend.app.services.shoko_watchlist import _cover_for

    detail = series_detail(shoko_id, force=refresh)
    if detail is None:
        raise HTTPException(status_code=404, detail="Shoko Server does not know this series")
    known = None
    count = None
    if detail.anidb_id:
        anime = db.scalar(select(Anime).where(Anime.anidb_id == detail.anidb_id))
        if anime is not None:
            known = (anime.id, anime.cover_url)
            count = anime.effective_episode_count
    total = _agreed_total(detail.total_episodes, detail.local_episodes, count)
    # Episodes past the watchlist's count that Shoko holds nothing for are not gaps (build 243).
    shown = [
        episode for episode in detail.episodes
        if not (episode.missing and episode.number is not None and episode.number > total)
    ]
    return ShokoSeriesDetailResponse(
        shoko_id=detail.shoko_id,
        name=detail.name,
        anidb_id=detail.anidb_id,
        mal_ids=detail.mal_ids,
        local_episodes=detail.local_episodes,
        total_episodes=total,
        episodes=[
            ShokoEpisodeView(
                number=episode.number,
                title=episode.title,
                air_date=episode.air_date,
                missing=episode.missing,
                files=[ShokoFileView(name=f.name, size=f.size, height=f.height) for f in episode.files],
            )
            for episode in shown
        ],
        error=detail.error,
        cover_url=(known[1] if known else None) or _cover_for(detail.anidb_id),
        watchlist_id=known[0] if known else None,
    )


class AnidbTitlesResponse(BaseModel):
    """What AniFlow knows about AniDB's title dump.

    Shown on this page because this is where the dump earns its place: it is
    what stops "Only in Shoko" offering a series the watchlist already holds
    under a different name.
    """

    #: Whether the file is on disk at all. Everything works without it, more
    #: crudely, so this is a state and not an error.
    present: bool
    downloaded_at: str | None = None
    parsed_at: str | None = None
    anime_count: int = 0
    title_count: int = 0
    bytes_downloaded: int = 0
    next_refresh_at: str | None = None
    last_error: str | None = None
    #: How many watchlist anime have been given an AniDB id, out of how many
    #: there are. The dump is only useful to the extent this is close to full.
    linked: int = 0
    total: int = 0


def _titles_response(db: Session) -> AnidbTitlesResponse:
    from sqlalchemy import func, select as sa_select

    from backend.app.models.anime import Anime
    from backend.app.services import anidb_titles

    status = anidb_titles.current_status()
    total = db.scalar(sa_select(func.count()).select_from(Anime)) or 0
    linked = sum(
        1
        for provider_ids in db.scalars(sa_select(Anime.provider_ids))
        if (provider_ids or {}).get("anidb")
    )
    return AnidbTitlesResponse(
        present=status.present,
        downloaded_at=status.downloaded_at.isoformat() if status.downloaded_at else None,
        parsed_at=status.parsed_at.isoformat() if status.parsed_at else None,
        anime_count=status.anime_count,
        title_count=status.title_count,
        bytes_downloaded=status.bytes_downloaded,
        next_refresh_at=status.next_refresh_at.isoformat() if status.next_refresh_at else None,
        last_error=status.last_error,
        linked=linked,
        total=total,
    )


@router.get("/titles", response_model=AnidbTitlesResponse)
def anidb_titles_status(db: Session = Depends(get_db)) -> AnidbTitlesResponse:
    """Touches no network - just what the last download left behind."""
    return _titles_response(db)


@router.post("/titles/refresh", response_model=AnidbTitlesResponse)
def anidb_titles_refresh(db: Session = Depends(get_db)) -> AnidbTitlesResponse:
    """Fetch the dump now, whatever its age, then re-link the watchlist.

    ``force`` on purpose: the only reason to press a button that the worker
    presses weekly by itself is impatience with the weekly part - a show was
    just added to AniDB and its id is wanted now.

    Synchronous deliberately (build 92). Both halves of this block: the
    download fetches two megabytes, and the backfill reads every anime and
    queries the title index once each, on a database that lives on a network
    share. As an ``async def`` the second half ran on the event loop and held
    up every other request - the shape of the build-84 crash. FastAPI runs a
    sync endpoint in its threadpool, so declaring it that way is the whole
    fix; nothing here starts a task that would need a loop of its own.
    """
    from backend.app.services import anidb_identity, anidb_titles

    anidb_titles.download(force=True)
    anidb_identity.backfill(db)
    db.commit()
    return _titles_response(db)
