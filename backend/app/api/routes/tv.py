"""The television API (build 181, 0.8.1) - contract in docs/tv-api-plan.md.

Three routers, because the three have different doors:

``router``  (``/api/tv/v1``)
    What a TV calls. Reachable without a session - ``/api/tv/`` is in
    ``deps.OPEN_PREFIXES`` - and guarded instead by ``require_tv_token``. A TV
    token opens this router and nothing else in AniFlow.

``art_router``  (``/api/tv/v1/art``)
    Images. An ``<img>`` cannot send a header, so these are guarded by the
    ``k`` key the API put in the URL (services/tv_tokens.py), not by the token.

``pair_router``  (``/api/tv/v1/pair``)
    A TV with no token yet asks to be paired and polls for the answer. Open,
    because it has no credential to show; harmless, because the token only
    exists after a logged-in person approves the code (services/tv_pairing.py).

``devices_router``  (``/api/tv-devices``)
    Making and revoking tokens. Behind the ordinary session like the rest of
    AniFlow, and deliberately *not* under ``/api/tv/``: a TV must not be able
    to mint itself another token.

The server does the thinking here: the week is grouped, the calendar filtered
by the user's own settings, the titles already in the user's language. A TV app
that had to redo any of that would drift from the web page.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.core.version import app_version, build_number
from backend.app.db import calendar_store
from backend.app.db.session import get_db
from backend.app.models.anime import Anime, AnimeLifecycle
from backend.app.models.tv_token import TvToken
from backend.app.schemas.anime import AddAnimeRequest, AnimeSearchResult, AnimeSummary
from backend.app.schemas.tv import (
    TvAddRequest,
    TvAddResponse,
    TvAttentionItem,
    TvCalendarItem,
    TvCalendarPage,
    TvCard,
    TvDevice,
    TvDeviceCreate,
    TvDeviceCreated,
    TvPairApprove,
    TvPairLookup,
    TvPairStart,
    TvPairStarted,
    TvPairStatus,
    TvPing,
    TvProgress,
    TvProgressItem,
    TvReplaceable,
    TvSchedule,
    TvScheduleDay,
    TvWatchlist,
)
from backend.app.services import calendar_view, tv_pairing, tv_tokens
from backend.app.services.app_mode import AppMode, current_mode as current_app_mode
from backend.app.services.app_settings import load_app_settings
from backend.app.services.library_flags import mark_in_library

logger = for_component("TV")

#: Episode states that mean something is wrong rather than merely in flight.
PROBLEM_STATES = frozenset({"failed", "file_gone", "download_missing", "download_stalled"})

#: Genres "hide adult content" is read to cover beyond the provider's own flag
#: - the same list as the web page (frontend/src/calendarView.ts).
ADULT_GENRES = frozenset({"ecchi", "hentai", "erotica"})

CALENDAR_ART_PREFIX = "/api/calendar/art/"
#: The older home of posters the v1 downloader stored; watchlist rows written by
#: the "Repair watchlist covers" tool still point at it (``/{key}/art/{id}``).
SOURCE_ART_PREFIX = "/api/calendar-sources/"

_MAX_PAGE_SIZE = 200


def require_tv_token(request: Request, db: Session = Depends(get_db)) -> TvToken:
    """Refuse the request unless it carries a live TV token."""
    token = tv_tokens.bearer(request.headers.get("authorization"))
    row = tv_tokens.verify(db, token)
    if row is None:
        raise HTTPException(status_code=401, detail="tv_token_invalid")
    # What image keys for this request are made from - kept off the row's
    # public surface, on the request instead.
    request.state.tv_signing = row.token_hash
    return row


router = APIRouter(dependencies=[Depends(require_tv_token)])
art_router = APIRouter()
#: Open to everyone who can reach AniFlow, like the login page: a TV that has
#: no token yet has to be able to ask for one. What it can ask for is a code,
#: and a code is worth nothing until a logged-in person approves it.
pair_router = APIRouter()
devices_router = APIRouter()


# --------------------------------------------------------------- helpers


def _art(request: Request, kind: str, ident: str) -> str:
    key = tv_tokens.image_key(request.state.tv_signing, kind, ident)
    return f"/api/tv/v1/art/{kind}/{ident}?k={key}"


def _anime_cover(request: Request, summary: AnimeSummary) -> str | None:
    if summary.has_custom_cover:
        return _art(request, "anime", summary.id)
    # An anime added from the calendar keeps the calendar's own cover URL, which
    # is a session-gated path on AniFlow: the TV has no session, so it gets the
    # signed link instead (build 185 - these were the missing covers).
    return _calendar_cover(request, summary.cover_url)


def _calendar_cover(request: Request, cover_url: str | None) -> str | None:
    """A cover for the TV: AniFlow's own gated art becomes a signed link.

    The TV has no session, so a path on AniFlow that needs one would simply 401.
    """
    if cover_url and cover_url.startswith(CALENDAR_ART_PREFIX):
        return _art(request, "calendar", cover_url[len(CALENDAR_ART_PREFIX):])
    if cover_url and cover_url.startswith(SOURCE_ART_PREFIX):
        key, _, rest = cover_url[len(SOURCE_ART_PREFIX):].partition("/art/")
        if key and rest and "/" not in key and "/" not in rest:
            return _art(request, "source", f"{key}/{rest}")
    return cover_url


def _problem_anime_ids(db: Session) -> set[str]:
    """Which anime have something wrong: an open issue, or a stuck episode."""
    from backend.app.api.routes import activity, progress

    ids = {
        item.anime_id
        for item in activity.list_attention(status_filter="OPEN", limit=200, db=db)
        if item.anime_id
    }
    ids.update(
        item.anime_id
        for item in progress.get_progress(db)
        if item.state in PROBLEM_STATES
    )
    return ids


def _popularity(summary: AnimeSummary) -> int | None:
    """The calendar's popularity for a watchlist show, found by its AniDB id.

    Exact, never a guess: an id the calendar does not hold is simply unknown.
    """
    if not summary.anidb_id:
        return None
    from backend.app.services.calendar_lookup import by_anidb_id

    try:
        found = by_anidb_id(int(summary.anidb_id))
    except Exception:  # noqa: BLE001 - a sort key is never worth a 500
        return None
    return found.popularity if found is not None else None


def _library_places(db: Session) -> dict[int, tuple[str, int]]:
    """Each library's name and place in the watchlist's own order (build 254): the ones tagged
    Current season first, then Upcoming, then the rest as the Libraries page lists them."""
    from backend.app.services import libraries

    rows = libraries.list_libraries(db)

    def rank(lib) -> int:
        return 0 if lib.fits_season == "current" else 1 if lib.key == "upcoming" else 2

    ordered = sorted(enumerate(rows), key=lambda pair: (rank(pair[1]), pair[0]))
    return {lib.id: (lib.name, place) for place, (_, lib) in enumerate(ordered)}


def _card(
    request: Request,
    summary: AnimeSummary,
    *,
    advanced: bool,
    problems: set[str],
    popularity: int | None = None,
    places: dict[int, tuple[str, int]] | None = None,
) -> TvCard:
    place = (places or {}).get(summary.library_id) if summary.library_id is not None else None
    return TvCard(
        id=summary.id,
        title=summary.display_name,
        cover=_anime_cover(request, summary),
        lifecycle=summary.lifecycle.value,
        needs_setup=advanced and summary.entry_mode != "advanced",
        airs_weekday=summary.airs_weekday,
        airs_time=summary.airs_time,
        owned_episodes=summary.owned_episodes,
        episode_count=summary.episode_count,
        problem=summary.id in problems,
        popularity=popularity,
        format=summary.format,
        air_start_date=summary.air_start_date,
        library=place[0] if place else None,
        library_rank=place[1] if place else None,
    )


#: What is on a TV watchlist. Dropped and archived shows are not "watching".
_HIDDEN = (AnimeLifecycle.DROPPED, AnimeLifecycle.ARCHIVED)


def _adult_anime_ids(db: Session) -> set[str]:
    """Every anime that counts as adult: the flag, or a library marked Adult (build 200)."""
    from backend.app.models.anime import Anime

    return {row.id for row in db.scalars(select(Anime)) if row.effective_is_adult}


def _cards(request: Request, db: Session, *, hide_adult: bool = True) -> list[TvCard]:
    from backend.app.api.routes.anime import list_anime

    advanced = current_app_mode(db) is AppMode.ADVANCED
    problems = _problem_anime_ids(db)
    places = _library_places(db)
    return [
        _card(
            request, summary, advanced=advanced, problems=problems,
            popularity=_popularity(summary), places=places,
        )
        for summary in (AnimeSummary.model_validate(row) for row in list_anime(db))
        if summary.lifecycle not in _HIDDEN and not (hide_adult and summary.is_adult)
    ]


def _time_key(card: TvCard) -> tuple[int, str, str]:
    return (card.airs_time is None, card.airs_time or "", card.title.lower())


# ------------------------------------------------------------- endpoints


@router.get("/ping", response_model=TvPing)
def ping(db: Session = Depends(get_db)) -> TvPing:
    """Token check and handshake: "Test connection", and every launch."""
    from backend.app.services.clock import now_local, timezone_name

    local = now_local()
    return TvPing(
        app_version=app_version(),
        build=build_number(),
        timezone=timezone_name(),
        now=local,
        today_weekday=local.weekday(),
        app_mode=current_app_mode(db).value,
        calendar_auto_update=load_app_settings(db).calendar_auto_update,
    )


@router.get("/watchlist/{anime_id}", response_model=TvCalendarItem)
def watchlist_show(anime_id: str, request: Request, db: Session = Depends(get_db)) -> TvCalendarItem:
    """One watchlist show in the calendar detail's shape (build 254), so the TV draws the same
    page for a show it owns as for one it could add. The calendar's record is used when it knows
    the show (by AniDB id, exact); otherwise what AniFlow itself holds."""
    from backend.app.services import calendar_lookup

    anime = db.get(Anime, anime_id)
    if anime is None:
        raise HTTPException(status_code=404, detail="No such anime")
    meta = anime.metadata_json or {}
    found = calendar_lookup.entry_by_anidb_id(int(anime.anidb_id)) if anime.anidb_id else None
    stars = 0
    if found is not None and anime.season and anime.season_year:
        try:
            with calendar_store.sessions()() as handle:
                stars = _star_rater(calendar_view.line_up(handle, anime.season_year, anime.season.upper()))(found)
        except Exception:  # noqa: BLE001 - stars are decoration, never a 500
            stars = 0
    if found is not None:
        item = _calendar_item(request, found, stars)
        item.in_library = True
        item.cover = _anime_cover(request, AnimeSummary.model_validate(anime)) or item.cover
        return item
    own = {anime.display_name, ""}
    alt = [n for n in (anime.title_english, anime.title_romaji, anime.title_native) if n and n not in own]
    slot = anime.effective_air_slot_at
    return TvCalendarItem(
        calendar_id="",
        title=anime.display_name,
        cover=_anime_cover(request, AnimeSummary.model_validate(anime)),
        format=anime.format,
        episode_count=anime.effective_episode_count,
        airing_status="UNKNOWN",
        air_start_date=anime.air_start_date,
        air_slot_at=slot,
        genres=list(meta.get("genres") or []),
        synopsis=meta.get("synopsis"),
        studios=list(meta.get("studios") or []),
        addable=False,
        in_library=True,
        alt_titles=alt,
        anidb_id=int(anime.anidb_id) if anime.anidb_id else None,
    )


@router.get("/watchlist", response_model=TvWatchlist)
def watchlist(
    request: Request,
    hide_adult: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> TvWatchlist:
    """The watchlist. Adult anime - flagged, or in a library marked Adult - are left
    out unless the TV asks for them with ``hide_adult=false`` (build 200)."""
    return TvWatchlist(items=_cards(request, db, hide_adult=hide_adult))


@router.get("/schedule", response_model=TvSchedule)
def schedule(
    request: Request,
    hide_adult: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> TvSchedule:
    """The week, grouped and ordered by the server. Adult anime are left out unless
    the TV sends ``hide_adult=false`` (build 200)."""
    from backend.app.services.clock import now_local

    today = now_local().date()
    monday = today - timedelta(days=today.weekday())
    cards = _cards(request, db, hide_adult=hide_adult)
    days = [
        TvScheduleDay(
            weekday=weekday,
            date=monday + timedelta(days=weekday),
            is_today=weekday == today.weekday(),
            items=sorted((c for c in cards if c.airs_weekday == weekday), key=_time_key),
        )
        for weekday in range(7)
    ]
    unscheduled = sorted(
        (c for c in cards if c.airs_weekday is None), key=lambda c: c.title.lower()
    )
    return TvSchedule(days=days, unscheduled=unscheduled)


def settings_or(*pairs):
    """Each ``(asked, configured)`` pair resolved: what was asked for, else the setting.

    ``None`` means "not asked" - an empty string is an answer (``hidden_kinds=``
    means hide no kind at all).
    """
    return tuple(configured if asked is None else asked for asked, configured in pairs)


def _reads_as_adult(item: AnimeSearchResult) -> bool:
    if item.is_adult:
        return True
    return any(genre.strip().lower() in ADULT_GENRES for genre in item.genres)


def _hidden_kind(item: AnimeSearchResult, hidden: set[str]) -> bool:
    kind = (item.format or "").strip().upper()
    return kind != "" and kind in hidden


#: How much the genres count against popularity in "recommended" - the web calendar's TASTE_WEIGHT.
_TASTE_WEIGHT = 0.65
_GENRES_USED = 3


def _taste_profile(db: Session) -> dict[str, float]:
    """genre (lower case) -> share of the most-collected genre: what the watchlist says the user likes."""
    counts: dict[str, int] = {}
    for anime in db.scalars(select(Anime)):
        for genre in (anime.metadata_json or {}).get("genres") or []:
            key = str(genre).strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    top = max(counts.values(), default=0)
    return {genre: count / top for genre, count in counts.items()} if top else {}


def _recommended(kept: list[AnimeSearchResult], season: list[AnimeSearchResult], profile: dict[str, float]):
    """``sortByRecommendation`` of the web calendar: taste plus popularity, stable on ties."""
    counts = sorted((e.popularity for e in season if e.popularity and e.popularity > 0), reverse=True)

    def rank(entry: AnimeSearchResult) -> float:
        count = entry.popularity or 0
        if count <= 0 or not counts:
            return 0.0
        better = next(i for i, other in enumerate(counts) if other <= count)
        return 1 - better / len(counts)

    def affinity(entry: AnimeSearchResult) -> float:
        if not profile or not entry.genres:
            return 0.0
        weights = sorted((profile.get(g.strip().lower(), 0.0) for g in entry.genres), reverse=True)[:_GENRES_USED]
        return sum(weights) / len(weights)

    def score(entry: AnimeSearchResult) -> float:
        return _TASTE_WEIGHT * affinity(entry) + (1 - _TASTE_WEIGHT) * rank(entry)

    return sorted(kept, key=lambda e: -score(e))  # sorted() is stable


def _star_rater(season: list[AnimeSearchResult]):
    """One to five stars by rank within the season - the web calendar's ``starsIn``, ported."""
    counts = sorted((e.popularity for e in season if e.popularity and e.popularity > 0), reverse=True)
    if not counts:
        return lambda entry: 0

    def at(fraction: float) -> int:
        return counts[min(len(counts) - 1, int(len(counts) * fraction))]

    bands = [at(0.1), at(0.25), at(0.5), at(0.75)]

    def stars(entry: AnimeSearchResult) -> int:
        count = entry.popularity or 0
        if count <= 0:
            return 0
        for value, band in zip((5, 4, 3, 2), bands):
            if count >= band:
                return value
        return 1

    return stars


def _calendar_item(request: Request, entry: AnimeSearchResult, stars: int = 0) -> TvCalendarItem:
    own = {entry.display_name, ""}
    alt: list[str] = []
    for name in (entry.title_english, entry.title_romaji, entry.title_native):
        if name and name not in own and name not in alt:
            alt.append(name)
    anidb = (entry.provider_ids or {}).get("anidb")
    return TvCalendarItem(
        alt_titles=alt,
        anidb_id=int(anidb) if anidb and str(anidb).isdigit() else None,
        stars=stars,
        related=entry.related,
        is_adult=_reads_as_adult(entry),
        calendar_id=entry.calendar_id or "",
        title=entry.display_name,
        cover=_calendar_cover(request, entry.cover_url),
        format=entry.format,
        episode_count=entry.episode_count,
        airing_status=entry.airing_status,
        air_start_date=entry.air_start_date,
        air_slot_at=entry.air_slot_at,
        genres=entry.genres,
        synopsis=entry.synopsis,
        studios=entry.studios,
        addable=entry.addable,
        in_library=entry.in_library,
    )


@router.get("/calendar", response_model=TvCalendarPage)
async def calendar(
    request: Request,
    year: int | None = Query(default=None, ge=1960, le=2100),
    season: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=48, ge=1, le=_MAX_PAGE_SIZE),
    hide_in_library: bool | None = Query(default=None),
    hide_adult: bool | None = Query(default=None),
    hide_no_cover: bool | None = Query(default=None),
    hidden_kinds: str | None = Query(default=None, max_length=200),
    sort: str | None = Query(default=None, pattern="^(airing|name|popularity|recommended)$"),
    db: Session = Depends(get_db),
) -> TvCalendarPage:
    """One season with calendar filters applied on the server.

    The filters are the user's own from AniFlow's Settings - unless the TV
    sends its own (build 182): ``hide_in_library``, ``hide_adult``,
    ``hide_no_cover``, ``hidden_kinds`` (comma-separated, empty = hide none)
    and ``sort``. Each one that is present overrides that one setting; each
    that is absent falls back to AniFlow's, so an older TV app keeps working.

    Wraps the web page's endpoint, so the loading rules are the same and no
    network request happens here: a season nobody has loaded answers with a
    status, never with an empty list standing in for "I don't know".
    """
    from backend.app.api.routes.calendar import season_calendar

    result = await season_calendar(year=year, season=season, db=db)

    settings = load_app_settings(db)
    rules = settings_or(
        (hide_in_library, settings.calendar_hide_in_library),
        (hide_adult, settings.calendar_hide_adult),
        (hide_no_cover, settings.calendar_hide_no_cover),
        (sort, settings.calendar_sort),
        (hidden_kinds, settings.calendar_hidden_kinds),
    )
    hide_in_library, hide_adult, hide_no_cover, sort, kinds = rules
    wanted_hidden = {kind.strip().upper() for kind in (kinds or "").split(",") if kind.strip()}
    kept = [
        entry
        for entry in result.anime
        if entry.calendar_id
        and not (hide_in_library and entry.in_library)
        and not (hide_adult and _reads_as_adult(entry))
        and not (hide_no_cover and not entry.cover_url)
        and not _hidden_kind(entry, wanted_hidden)
    ]
    # The server hands a season over most-watched first, so "popularity" is
    # the order it arrives in.
    if sort == "name":
        kept.sort(key=lambda entry: entry.display_name.lower())
    elif sort == "airing":
        kept.sort(key=lambda entry: (entry.air_start_date is None, entry.air_start_date or ""))
    elif sort == "recommended":
        kept = _recommended(kept, list(result.anime), _taste_profile(db))
    # Seasons of shows you already follow first, as the web calendar does when it marks them.
    if settings.calendar_mark_sequels:
        boost = {"gap": 2, "sequel": 1}
        kept.sort(key=lambda entry: -boost.get(entry.related or "", 0))

    start = (page - 1) * page_size
    rate = _star_rater(list(result.anime))
    return TvCalendarPage(
        season=result.season,
        year=result.year,
        status=result.status,
        stale=result.stale,
        loaded_at=result.loaded_at,
        unavailable_reason=result.unavailable_reason,
        page=page,
        page_size=page_size,
        total=len(kept),
        hidden=len(result.anime) - len(kept),
        items=[_calendar_item(request, entry, rate(entry)) for entry in kept[start:start + page_size]],
    )


def _one_entry(db: Session, calendar_id: str) -> AnimeSearchResult:
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


@router.get("/calendar/shows/{calendar_id}", response_model=TvCalendarItem)
def calendar_show(
    calendar_id: str, request: Request, db: Session = Depends(get_db)
) -> TvCalendarItem:
    return _calendar_item(request, _one_entry(db, calendar_id))


@router.post("/watchlist", response_model=TvAddResponse)
async def add_to_watchlist(
    body: TvAddRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TvAddResponse:
    """Add a calendar show as a basic entry.

    The TV cannot choose a subgroup, a resolution or a folder, and does not
    pretend to: the entry is added as a *future release* - no release name,
    no profile, no automation - and AniFlow's own "needs setup" attention asks
    for the rest once the premiere has passed. In Basic mode it is simply a
    basic entry. Idempotent: adding what is already there answers 200.
    """
    from backend.app.api.routes.anime import create_anime
    from backend.app.services.title_preference import display_name, display_order

    entry = _one_entry(db, body.calendar_id)
    if not entry.addable or not entry.provider_id:
        raise HTTPException(status_code=422, detail="This show cannot be added")

    advanced = current_app_mode(db) is AppMode.ADVANCED
    naming = display_order()

    def card_for(anime: Anime) -> TvCard:
        summary = AnimeSummary.model_validate(anime)
        summary.display_name = display_name(anime, naming)
        return _card(request, summary, advanced=advanced, problems=set())

    existing = db.scalar(
        select(Anime).where(
            Anime.primary_provider == entry.provider,
            Anime.primary_provider_id == entry.provider_id,
        )
    )
    if existing is not None:
        return TvAddResponse(added=False, item=card_for(existing))
    if entry.in_library:
        # On the watchlist under another id, found by name. Not added twice.
        return TvAddResponse(added=False, item=None)

    created = await create_anime(
        AddAnimeRequest(
            provider=entry.provider,
            provider_id=entry.provider_id,
            local_name=entry.folder_hint or entry.display_name,
            display_name=entry.display_name,
            future=True,
            # A TV cannot choose a library either (build 192): the show goes into
            # the default one, and the user moves it from there.
        ),
        db,
    )
    response.status_code = 201
    return TvAddResponse(added=True, item=card_for(created))


@router.get("/progress", response_model=TvProgress)
def progress(
    hide_adult: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> TvProgress:
    """Read-only: enough to say "something is wrong", never enough to act.

    Names of adult anime are left out of it too, like the watchlist (build 200): a
    title in a download list is as visible on a television as one in a grid.
    """
    from backend.app.api.routes import activity, progress as progress_routes

    items = progress_routes.get_progress(db)
    issues = activity.list_attention(status_filter="OPEN", limit=50, db=db)
    if hide_adult:
        adult = _adult_anime_ids(db)
        items = [item for item in items if item.anime_id not in adult]
        issues = [issue for issue in issues if issue.anime_id not in adult]
    return TvProgress(
        in_progress=len(items),
        attention=len(issues),
        items=[
            TvProgressItem(
                anime_id=item.anime_id,
                title=item.anime_name,
                episode_number=item.episode_number,
                state=item.state,
                progress=item.progress,
                since=item.since,
                detail=item.detail,
                problem=item.state in PROBLEM_STATES,
            )
            for item in items
        ],
        issues=[
            TvAttentionItem(
                id=issue.id,
                kind=issue.kind,
                anime_id=issue.anime_id,
                title=issue.anime_name,
                episode_number=issue.episode_number,
                detail=issue.detail,
                created_at=issue.created_at,
            )
            for issue in issues
        ],
    )


# ---------------------------------------------------------------- images


@art_router.get("/anime/{anime_id}", include_in_schema=False)
def art_anime(anime_id: str, k: str | None = None, db: Session = Depends(get_db)) -> FileResponse:
    from backend.app.api.routes.anime import get_cover

    if not tv_tokens.image_key_valid(db, "anime", anime_id, k):
        raise HTTPException(status_code=401, detail="tv_token_invalid")
    return get_cover(anime_id, db)


@art_router.get("/source/{key}/{source_id}", include_in_schema=False)
def art_source(key: str, source_id: str, k: str | None = None, db: Session = Depends(get_db)) -> FileResponse:
    from backend.app.api.routes.calendar_sources import source_art

    if not tv_tokens.image_key_valid(db, "source", f"{key}/{source_id}", k):
        raise HTTPException(status_code=401, detail="tv_token_invalid")
    return source_art(key, source_id)


@art_router.get("/calendar/{work_id}", include_in_schema=False)
def art_calendar(work_id: str, k: str | None = None, db: Session = Depends(get_db)) -> FileResponse:
    from backend.app.api.routes.calendar import calendar_art

    if not tv_tokens.image_key_valid(db, "calendar", work_id, k):
        raise HTTPException(status_code=401, detail="tv_token_invalid")
    return calendar_art(work_id)


# ------------------------------------------------------- managing the TVs


def _device(row: TvToken) -> TvDevice:
    return TvDevice(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


@devices_router.get("", response_model=list[TvDevice])
def list_devices(db: Session = Depends(get_db)) -> list[TvDevice]:
    return [_device(row) for row in tv_tokens.listing(db)]


@devices_router.post("", response_model=TvDeviceCreated, status_code=201)
def create_device(body: TvDeviceCreate, db: Session = Depends(get_db)) -> TvDeviceCreated:
    """Make a token for one TV. The only time it is ever shown."""
    row, token = tv_tokens.create(db, body.name)
    logger.info("TV token created for %r", row.name)
    return TvDeviceCreated(**_device(row).model_dump(), token=token)


@devices_router.delete("/{token_id}", status_code=204)
def revoke_device(token_id: int, db: Session = Depends(get_db)) -> Response:
    if not tv_tokens.revoke(db, token_id):
        raise HTTPException(status_code=404, detail="No such TV")
    return Response(status_code=204)


# ------------------------------------------------------------- pairing


@pair_router.post("/start", response_model=TvPairStarted)
def pair_start(body: TvPairStart, request: Request, db: Session = Depends(get_db)) -> TvPairStarted:
    """A TV asks to be paired. It shows the code and polls with the ticket."""
    try:
        ticket, code, seconds = tv_pairing.start(db, body.name, body.device_id)
    except tv_pairing.TooManyPairings:
        raise HTTPException(status_code=429, detail="tv_pairing_busy") from None
    # The phone is sent to the address the TV itself used, which is one the
    # phone can very probably reach too. Behind a proxy that rewrites the
    # host, the code can still be typed in Settings.
    base = str(request.base_url).rstrip("/")
    return TvPairStarted(
        ticket=ticket,
        code=code,
        approve_url=f"{base}/?pair={code}#/settings/integrations",
        expires_in=seconds,
        poll_seconds=tv_pairing.POLL_SECONDS,
    )


@pair_router.get("/{ticket}", response_model=TvPairStatus)
def pair_status(ticket: str, response: Response, db: Session = Depends(get_db)) -> TvPairStatus:
    """``pending``, then ``approved`` once - with the token - or ``expired``."""
    response.headers["Cache-Control"] = "no-store"
    status, token, left = tv_pairing.poll(db, ticket)
    return TvPairStatus(status=status, token=token, expires_in=left)


@devices_router.get("/pair/{code}", response_model=TvPairLookup)
def pair_lookup(code: str, db: Session = Depends(get_db)) -> TvPairLookup:
    """What the approval page shows before the user says yes."""
    pairing = tv_pairing.lookup(db, code)
    if pairing is None:
        raise HTTPException(status_code=404, detail="tv_pairing_unknown")
    options = tv_pairing.replaceable(db, pairing)
    recognised = next((item for item in options if item.same_device), None)
    return TvPairLookup(
        name=pairing.name,
        code=pairing.code,
        suggested_name=recognised.name if recognised else None,
        replaces=[TvReplaceable(**vars(item)) for item in options],
    )


@devices_router.post("/pair", response_model=TvDevice, status_code=201)
def pair_approve(body: TvPairApprove, db: Session = Depends(get_db)) -> TvDevice:
    """The user said yes: make the TV's token. The TV collects it, not the user."""
    approved = tv_pairing.approve(db, body.code, body.name, body.replace_ids)
    if approved is None:
        raise HTTPException(status_code=404, detail="tv_pairing_unknown")
    pairing, token_id = approved
    logger.info(
        "TV paired: %r%s", pairing.name,
        f" (replaces {len(pairing.replace_ids)} earlier entr{'y' if len(pairing.replace_ids) == 1 else 'ies'})"
        if pairing.replace_ids else "",
    )
    row = db.get(TvToken, token_id)
    return _device(row)
