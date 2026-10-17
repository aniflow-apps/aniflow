"""The Calendar's endpoints (build 143, Calendar v2).

Provider-agnostic by construction. Nothing here says AniList, Kitsu or
MyAnimeList: a season is read out of ``calendar.db``, every entry carries a
stable ``calendar_id``, and which source supplied the title or the poster is
not the caller's business. That is what lets a television client be written
against this without knowing anything about metadata providers.

Three behaviours are worth stating because they are the ones v1 got wrong:

* **What is on screen is never cleared to fetch something.** An update runs
  beside the data it is replacing, and replaces a season only when a whole
  snapshot has been applied.
* **A season nobody has loaded loads itself**, once, in the background. The
  page is told that it is happening rather than being given an empty grid.
* **A failure is a status.** An update that fails leaves the last good
  line-up exactly where it was and says so.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime

from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.db.session import get_db
from backend.app.schemas.anime import AnimeSearchResult
from backend.app.schemas.calendar import CalendarSeasonResponse, CalendarStatusResponse
from backend.app.services import calendar_jobs, calendar_notes, calendar_view
from backend.app.services.app_settings import load_app_settings
from backend.app.services.calendar import UPDATE_SCOPES, resolve_season, update_window
from backend.app.services.library_flags import mark_in_library

logger = for_component("Calendar")

router = APIRouter()



def _status_for(state, season_state, entries: int) -> str:
    """One word for what the page should show.

    ``loading`` and ``updating`` are different things and the page says so:
    one means there is nothing yet, the other means what you are looking at is
    about to get better.
    """
    if state.error and not entries:
        return "error"
    if season_state.loaded_at is None and not entries:
        return "loading" if state.running else "empty"
    if state.running:
        return "updating"
    return "ready"


def _unavailable(year: int, season: str, reason: str) -> CalendarSeasonResponse:
    return CalendarSeasonResponse(
        provider="calendar",
        season=season,
        year=year,
        anime=[],
        status="error",
        unavailable_reason=reason,
    )


@router.get("", response_model=CalendarSeasonResponse)
async def season_calendar(
    year: int | None = Query(default=None, ge=1960, le=2100),
    season: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> CalendarSeasonResponse:
    """The line-up for one season, from AniFlow's own calendar.

    No network request happens here, whatever the state of the world. If the
    season has never been loaded, a background load is started and the page is
    told to come back - which is a different thing from an empty season, and
    the response distinguishes them.
    """
    try:
        resolved_year, resolved_season = resolve_season(year, season)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked as exc:
        return _unavailable(resolved_year, resolved_season, str(exc))

    with sessions() as handle:
        state = calendar_view.season_state(handle, resolved_year, resolved_season)
        entries = calendar_view.line_up(handle, resolved_year, resolved_season)
        # On the session that is already open: this is read on every page
        # load, and a second connection for one boolean is not free.
        filled_once = calendar_notes.first_fill_done(handle)

    if (
        state.loaded_at is None
        and load_app_settings(db).calendar_auto_update
        and not calendar_jobs.running_for(resolved_year, resolved_season)
    ):
        # Loaded once, on demand, in the background. Navigating to 2019 should
        # fetch 2019 rather than leave a page saying "nothing here" about a
        # season that certainly had shows in it.
        #
        # Behind the setting since build 144: with automatic updates off, the
        # page says the season has not been loaded and offers the button,
        # which is the predictable half of the same behaviour. Opening a page
        # and having it start work is exactly what made "season 0 of 4"
        # appear out of nowhere.
        logger.info("%s %s has never been loaded; fetching it", resolved_season, resolved_year)
        calendar_jobs.start([(resolved_year, resolved_season)], kind="season")

    # Never stored beside a line-up: both change the moment somebody adds or
    # deletes something, and keeping them together is what once left a deleted
    # anime hidden behind the calendar's own filter.
    mark_in_library(db, entries)

    job = calendar_jobs.status()
    return CalendarSeasonResponse(
        provider="calendar",
        season=resolved_season,
        year=resolved_year,
        anime=entries,
        status=_status_for(job, state, len(entries)),
        loaded_at=state.loaded_at,
        cached_at=state.loaded_at,
        updating=job.running,
        stale=state.stale and state.loaded_at is not None,
        unavailable_reason=job.error or calendar_store.blocked_reason(),
        first_fill_done=filled_once,
    )


@router.post("/update", response_model=CalendarStatusResponse)
async def update_calendar(
    year: int | None = Query(default=None, ge=1960, le=2100),
    season: str | None = Query(default=None),
    scope: str = Query(default="season"),
) -> CalendarStatusResponse:
    """Update one season, the seasons in flight, or fill a fresh calendar.

    Three scopes and one endpoint (build 148). Which providers are asked,
    which answered and which refused still belongs in the log and on the debug
    page, not in a choice somebody has to make before they can press a button
    - the scope is a different kind of question, and it is the only one people
    kept having to work around.

    * ``season`` - the season named in the request, and nothing else. What the
      button has meant since build 144, and still the default.
    * ``upcoming`` - the season airing now and the two after it.
    * ``initial`` - six seasons on a fresh calendar, once. Refused with 409
      once it has been done, because "never again" has to be enforced
      somewhere and the page is not a place anything can be enforced.

    Returns immediately. Nothing on screen changes until a snapshot has been
    applied in full.
    """
    if scope not in UPDATE_SCOPES:
        raise HTTPException(
            status_code=422, detail=f"scope must be one of {', '.join(UPDATE_SCOPES)}"
        )
    try:
        resolved_year, resolved_season = resolve_season(year, season)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    after = None
    if scope == "initial":
        if calendar_notes.first_fill_done():
            raise HTTPException(
                status_code=409, detail="this calendar has already had its initial fill"
            )
        # Recorded when the run finishes, not now: a run that dies on its
        # first source has filled nothing, and burning the one offer on it
        # would leave an empty calendar with no way back short of a Reset.
        after = calendar_notes.mark_first_fill_done

    state = calendar_jobs.start(
        update_window(scope, resolved_year, resolved_season),
        kind=scope if scope != "season" else "update",
        on_success=after,
    )
    return _job_view(state)


@router.post("/refresh", response_model=CalendarStatusResponse)
async def refresh_calendar(
    year: int | None = Query(default=None, ge=1960, le=2100),
    season: str | None = Query(default=None),
) -> CalendarStatusResponse:
    """Update this one season now. The narrow half of ``/update``."""
    try:
        resolved_year, resolved_season = resolve_season(year, season)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state = calendar_jobs.start([(resolved_year, resolved_season)], kind="season")
    return _job_view(state)


@router.get("/status", response_model=CalendarStatusResponse)
def calendar_status() -> CalendarStatusResponse:
    """What the Calendar is doing, for a page that is watching."""
    return _job_view(calendar_jobs.status())


def _job_view(state) -> CalendarStatusResponse:
    return CalendarStatusResponse(
        running=state.running,
        kind=state.kind,
        year=state.year,
        season=state.season,
        done=state.done,
        total=state.total,
        step=state.step,
        pictures=state.pictures,
        pictures_total=state.pictures_total,
        sources_done=state.sources_done,
        sources_total=state.sources_total,
        summary=state.summary,
        error=state.error,
        blocked_reason=calendar_store.blocked_reason(),
    )


class CalendarResetPreview(BaseModel):
    """What pressing Reset would destroy, counted rather than described."""

    works: int = 0
    identities: int = 0
    aliases: int = 0
    remembered_memberships: int = 0
    summary: str = ""


class CalendarMaintenanceResponse(BaseModel):
    done: str
    removed: dict[str, int] = {}
    kept_ids: int = 0


@router.get("/reset-preview", response_model=CalendarResetPreview)
def reset_preview() -> CalendarResetPreview:
    """What Reset would take with it.

    Counted, and shown before the button does anything: "this deletes some
    things" is the kind of warning people learn to click through.
    """
    from backend.app.services import calendar_lifecycle

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        # The file cannot be opened, so nothing can be counted - and Reset is
        # exactly the thing somebody is here to press.
        return CalendarResetPreview(summary="the calendar database could not be read")
    with sessions() as handle:
        counts = calendar_lifecycle.what_reset_destroys(handle)
    return CalendarResetPreview(
        works=counts.works,
        identities=counts.identities,
        aliases=counts.aliases,
        remembered_memberships=counts.remembered_memberships,
        summary=counts.as_text(),
    )


@router.post("/rebuild", response_model=CalendarMaintenanceResponse)
def rebuild_calendar() -> CalendarMaintenanceResponse:
    """Throw away what providers will hand back. Keep the identity spine.

    The honest answer to "the metadata looks wrong": it costs a re-download
    and keeps every calendar id, so nothing that has stored one is broken.
    """
    from sqlalchemy import func, select

    from backend.app.models.calendar import CalendarWork
    from backend.app.services import calendar_lifecycle

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    with sessions() as handle:
        removed = calendar_lifecycle.rebuild(handle)
        kept = handle.scalar(select(func.count()).select_from(CalendarWork)) or 0
    return CalendarMaintenanceResponse(done="rebuild", removed=removed, kept_ids=kept)


@router.post("/reset", response_model=CalendarMaintenanceResponse)
def reset_calendar(
    confirm: bool = Query(default=False),
) -> CalendarMaintenanceResponse:
    """Delete the calendar database. Never automatic, never unconfirmed.

    This breaks every calendar id anything has stored - a television client
    included - which is why it takes an explicit confirmation and why the
    preview above exists.
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="Reset needs confirm=true")
    from backend.app.services import calendar_lifecycle

    calendar_lifecycle.reset()
    calendar_jobs.reset()
    return CalendarMaintenanceResponse(done="reset")


class TraceEventView(BaseModel):
    at: datetime
    level: str
    stage: str
    message: str
    detail: dict[str, object] = {}


class SeasonStateView(BaseModel):
    year: int
    season: str
    works: int
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None


class SourceStateView(BaseModel):
    provider: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    failures: int = 0
    last_error: str | None = None


class CalendarDebugResponse(BaseModel):
    """Everything a person needs to see what the calendar is doing.

    Asked for while v2 was being built: *"I would like to see what is
    happening"*. The container log was the only answer, which needs shell
    access and is interleaved with everything else AniFlow does.
    """

    status: CalendarStatusResponse
    auto_update: bool = False
    works: int = 0
    identities: int = 0
    seasons: list[SeasonStateView] = []
    sources: list[SourceStateView] = []
    events: list[TraceEventView] = []


@router.get("/debug", response_model=CalendarDebugResponse)
def calendar_debug(db: Session = Depends(get_db)) -> CalendarDebugResponse:
    """What the calendar holds, and what it has just been doing."""
    from sqlalchemy import func, select

    from backend.app.models.calendar import (
        CalendarIdentity,
        CalendarMembership,
        CalendarSeason,
        CalendarSourceState,
        CalendarWork,
    )
    from backend.app.services import calendar_trace

    events = [
        TraceEventView(
            at=event.at,
            level=event.level,
            stage=event.stage,
            message=event.message,
            detail={key: value for key, value in event.detail.items() if value is not None},
        )
        for event in calendar_trace.recent()
    ]
    auto = bool(load_app_settings(db).calendar_auto_update)

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked:
        # The events are the point when the database itself will not open.
        return CalendarDebugResponse(
            status=_job_view(calendar_jobs.status()), auto_update=auto, events=events
        )

    with sessions() as handle:
        seasons = []
        for row in handle.scalars(select(CalendarSeason).order_by(CalendarSeason.year)):
            works = (
                handle.scalar(
                    select(func.count(func.distinct(CalendarMembership.work_id))).where(
                        CalendarMembership.season_id == row.id,
                        CalendarMembership.active.is_(True),
                    )
                )
                or 0
            )
            seasons.append(
                SeasonStateView(
                    year=row.year,
                    season=row.season,
                    works=works,
                    last_attempt_at=row.last_attempt_at,
                    last_success_at=row.last_success_at,
                )
            )
        sources = [
            SourceStateView(
                provider=row.provider,
                last_attempt_at=row.last_attempt_at,
                last_success_at=row.last_success_at,
                failures=row.failures,
                last_error=row.last_error,
            )
            for row in handle.scalars(select(CalendarSourceState).order_by(CalendarSourceState.provider))
        ]
        return CalendarDebugResponse(
            status=_job_view(calendar_jobs.status()),
            auto_update=auto,
            works=handle.scalar(select(func.count()).select_from(CalendarWork)) or 0,
            identities=handle.scalar(select(func.count()).select_from(CalendarIdentity)) or 0,
            seasons=seasons,
            sources=sources,
            events=events,
        )


@router.post("/debug/clear", response_model=CalendarDebugResponse)
def clear_calendar_trace(db: Session = Depends(get_db)) -> CalendarDebugResponse:
    """Empty the trace, for a run somebody wants to watch from the start."""
    from backend.app.services import calendar_trace

    calendar_trace.clear()
    return calendar_debug(db)


@router.get("/shows/{calendar_id}", response_model=AnimeSearchResult)
def one_show(calendar_id: str, db: Session = Depends(get_db)) -> AnimeSearchResult:
    """One show by its calendar id, resolving a merged id to its Work.

    The promise to every client that ever stored an id: it keeps answering.
    """
    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    with sessions() as handle:
        entry = calendar_view.one(handle, calendar_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such calendar entry")
    mark_in_library(db, [entry])
    return entry


@router.get("/art/{work_id}", include_in_schema=False)
def calendar_art(work_id: str) -> FileResponse:
    """The one poster this show was given."""
    from backend.app.services.calendar_art import art_path

    # Not a path from the caller: the filename comes from disk by glob, so a
    # work id cannot address anything outside the folder.
    found = art_path(work_id.strip())
    if found is None or not found.exists():
        raise HTTPException(status_code=404, detail="No artwork stored")
    # Cached hard, because a poster for a given work does not change and a
    # calendar page is two hundred of them. Without this, every visit to the
    # page re-fetched the lot - from AniFlow rather than from the provider,
    # so it cost nobody an API call, but it was still two hundred requests
    # and two hundred log lines for a picture the browser already had.
    return FileResponse(found, headers={"Cache-Control": "public, max-age=86400"})
