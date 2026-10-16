from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db.session import get_db
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.services.metadata import PROVIDERS, NoProviderAnswered, by_id
from backend.app.models.anime import Anime
from backend.app.schemas.anime import AddAnimeRequest, AliasRequest, AnimeDetail, AnimeSearchResult, AnimeSummary, ConnectReleaseRequest, FeedSearchRequest, InspectedReleaseResult, InspectNameResult, InspectNamesRequest, InspectNamesResponse, InspectQueryResult, InspectSearchRequest, InspectSearchResponse, NumberingRequest, NumberingResponse, NumberingSaveRequest, NumberingSeasonResult, SearchProgressResponse, LinkFolderRequest, QueryOutcomeResult, ScannedReleaseResult, SelectionScanRequest, SelectionScanResponse, QueueReleaseRequest, ConvertAnimeRequest, SetupSearchRequest, SetupSearchResponse, TitleGroupResult, UpdateAnimeRequest
from backend.app.worker.runtime import worker_state
from backend.app.services.anime import add_anime, apply_indexer, forget_release_warnings, update_anime
from backend.app.services.metadata_fill import manual_fields
from backend.app.services.library_flags import mark_in_library
from backend.app.services.app_settings import load_app_settings
from backend.app.services.clock import now_local
from backend.app.core.version import build_string

logger = for_component("Anime")

router = APIRouter()
#: Kept as a name for the modules that already import it. The registry and
#: the fallback order live in services/metadata.py now, because "who answers
#: when AniList is down" is a policy and not a detail of one route module.
providers = PROVIDERS


@router.get("", response_model=list[AnimeSummary])
def list_anime(db: Session = Depends(get_db)) -> list[dict]:
    """Dashboard cards: one row per anime with owned/available episode counts.

    Both counts come straight from the episode status now - there is no
    cross-table number matching left to get wrong.
    """
    from sqlalchemy import func, select

    from backend.app.models.anime import Episode, EpisodeStatus

    anime_list = list(db.scalars(select(Anime).order_by(Anime.created_at.desc())))
    if not anime_list:
        return []

    anime_ids = [anime.id for anime in anime_list]
    counts_by_status: dict[tuple[str, EpisodeStatus], int] = {
        (anime_id, status): count
        for anime_id, status, count in db.execute(
            select(Episode.anime_id, Episode.status, func.count(Episode.id))
            .where(Episode.anime_id.in_(anime_ids))
            .group_by(Episode.anime_id, Episode.status)
        ).all()
    }

    new_by_anime = _new_episodes(db, anime_ids)

    from datetime import UTC, datetime

    from backend.app.services.season_state import State, classify, episode_facts, facts_of

    arrivals = episode_facts(db, anime_ids)
    now = datetime.now(UTC)

    # What to call each anime on screen, per the Preferred titles setting.
    # Read once for the whole list, not once per card.
    from backend.app.services import title_preference

    naming = title_preference.display_order()

    # And how complete each one is, in four groups (build 120). The Shoko
    # answer is read once for the whole list from what the worker last
    # learned - a page load never waits on somebody else's server.
    from backend.app.core.mode import AppMode, current_mode
    from backend.app.services.anime_health import health_for
    from backend.app.services.app_mode import has_indexer
    from backend.app.services.integration_state import remembered_shoko

    holdings, by_title, answered = remembered_shoko()
    indexer = has_indexer(db)
    shoko_mode = current_mode() is AppMode.SHOKO_SERVER

    results = []
    for anime in anime_list:
        payload = AnimeSummary.model_validate(anime)
        payload.health = health_for(
            db,
            anime,
            holdings=holdings,
            by_title=by_title,
            answered=answered,
            has_indexer=indexer,
            shoko_mode=shoko_mode,
        ).as_dict()
        payload.display_name = title_preference.display_name(anime, naming)
        payload.search_names = _search_names(anime)
        payload.genres = [str(g) for g in ((anime.metadata_json or {}).get("genres") or []) if g]
        payload.owned_episodes = counts_by_status.get((anime.id, EpisodeStatus.COMPLETE), 0)
        # "+N available" means: something is downloadable right now.
        payload.available_episodes = counts_by_status.get(
            (anime.id, EpisodeStatus.AVAILABLE), 0
        ) + counts_by_status.get((anime.id, EpisodeStatus.MISSING), 0)
        owned_here, last_here = arrivals.get(anime.id, (0, None))
        payload.waiting_for_next_part = classify(facts_of(anime, owned_here, last_here), now) is State.WAITING
        count, newest = new_by_anime.get(anime.id, (0, None))
        payload.new_episode_count = count
        payload.newest_episode_at = newest
        results.append(payload.model_dump())
    return results


def _search_names(anime: Anime) -> list[str]:
    """Every name the watchlist's search box should find this anime by, each once."""
    names = [
        anime.local_name,
        anime.title_english,
        anime.title_romaji,
        anime.title_native,
        *(anime.aliases or []),
        *(anime.anidb_titles.values() if isinstance(getattr(anime, "anidb_titles", None), dict) else []),
    ]
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        text = str(name).strip() if name else ""
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


#: How long a new episode keeps its blue corner on the watchlist card (build 210). It used to stay until
#: the watchlist was left, which was usually seconds after arriving; now it stays a day, and opening
#: that anime's page clears it sooner.
NEW_EPISODE_WINDOW = timedelta(days=1)

#: Which anime the user has opened, and when: ``{anime id: iso time}``. One setting row rather than a
#: column, so it needs no migration. An entry older than the window changes nothing and is dropped
#: whenever one is written.
ANIME_SEEN_KEY = "anime_seen_at"


def _seen_map(db: Session) -> dict[str, datetime]:
    """When each anime was last opened, as far as its new episodes go."""
    from backend.app.models.setting import Setting

    row = db.get(Setting, ANIME_SEEN_KEY)
    found: dict[str, datetime] = {}
    if row is None or not isinstance(row.value, dict):
        return found
    for anime_id, value in row.value.items():
        try:
            seen = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        found[str(anime_id)] = seen if seen.tzinfo else seen.replace(tzinfo=UTC)
    return found


#: The visits the blue corners are measured from: ``{"previous": iso, "current": iso}``. ``current`` is
#: when the user last opened AniFlow after a gap of at least a day, ``previous`` the visit before it.
#: Everything that arrived since ``previous`` is new, and stays so for a day after ``current`` - so a
#: week away shows the week, not just its last 24 hours (build 239).
VISITS_KEY = "watchlist_visits"


def _visits(db: Session) -> tuple[datetime | None, datetime | None]:
    from backend.app.models.setting import Setting

    row = db.get(Setting, VISITS_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    found: list[datetime | None] = []
    for key in ("previous", "current"):
        try:
            when = datetime.fromisoformat(str(value[key]))
        except (KeyError, ValueError):
            found.append(None)
            continue
        found.append(when if when.tzinfo else when.replace(tzinfo=UTC))
    return found[0], found[1]


@router.post("/visit")
def watchlist_visit(db: Session = Depends(get_db)) -> dict:
    """AniFlow was opened. A visit a day or more after the last one starts a new round of blue corners.

    Opening it again the same day changes nothing, so a reload never shortens the day.
    """
    from backend.app.models.setting import Setting

    now = datetime.now(UTC)
    previous, current = _visits(db)
    if current is None:
        previous, current = now - NEW_EPISODE_WINDOW, now
    elif now - current >= NEW_EPISODE_WINDOW:
        previous, current = current, now
    else:
        return {"previous": previous.isoformat() if previous else None, "current": current.isoformat()}
    db.merge(Setting(key=VISITS_KEY, value={"previous": previous.isoformat(), "current": current.isoformat()}))
    db.commit()
    return {"previous": previous.isoformat(), "current": current.isoformat()}


def _new_episodes(
    db: Session, anime_ids: list[str], *, now: datetime | None = None
) -> dict[str, tuple[int, datetime | None]]:
    """How many episodes each anime gained since the user's last visit and has not been opened since.

    A count, not the episode numbers (build 151): *"if the user had 10 animes, and he did not open
    the aniflow for 2 weeks and 2 episodes where downloaded, it should show (2)."* Build 210 made
    it a day instead of "until the watchlist is left" (*"like it stays for 1 day, if user clicks on
    it and opens the detail page then it can also disappear"*), so it is also cleared per anime by
    opening that anime - see ``anime_seen``.

    Only episodes AniFlow *downloaded*: in the library, and with a download
    row behind them. ``media_imported_at`` alone is not enough - adopting a
    Shoko series or matching files already on disk stamps it too, and a show
    adopted this morning did not get twenty-four new episodes today. Regular
    episodes only.
    """
    from sqlalchemy import exists, select

    from backend.app.models.anime import Episode, EpisodeStatus, EpisodeType
    from backend.app.models.download import TorrentDownload

    now = now or datetime.now(UTC)
    previous, current = _visits(db)
    # No visit recorded yet: the old rule, a day back from now.
    window_start = previous or now - NEW_EPISODE_WINDOW
    # What was shown at the visit stays for a day from that visit; what arrived after it (the tab left
    # open) stays until the next one.
    expired_at = current + NEW_EPISODE_WINDOW if current else None
    seen = _seen_map(db)
    rows = db.execute(
        select(Episode.anime_id, Episode.media_imported_at)
        .where(
            Episode.anime_id.in_(anime_ids),
            Episode.status == EpisodeStatus.COMPLETE,
            Episode.episode_type == EpisodeType.REGULAR,
            Episode.media_imported_at > window_start,
            exists().where(TorrentDownload.episode_id == Episode.id),
        )
    ).all()
    found: dict[str, tuple[int, datetime | None]] = {}
    for anime_id, arrived in rows:
        if arrived <= seen.get(anime_id, window_start):
            continue
        if current is not None and arrived <= current and now >= expired_at:
            continue
        count, newest = found.get(anime_id, (0, None))
        found[anime_id] = (count + 1, arrived if newest is None or arrived > newest else newest)
    return found


@router.post("/{anime_id}/seen")
def anime_seen(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """The user opened this anime: its blue corner goes."""
    from backend.app.models.setting import Setting

    get_anime_or_404(anime_id, db)
    now = datetime.now(UTC)
    keep_from = min(now - NEW_EPISODE_WINDOW, _visits(db)[0] or now)
    seen = {key: value for key, value in _seen_map(db).items() if value > keep_from}
    seen[anime_id] = now
    db.merge(Setting(key=ANIME_SEEN_KEY, value={key: value.isoformat() for key, value in seen.items()}))
    db.commit()
    return {"seen_at": now.isoformat()}


def get_anime_or_404(anime_id: str, db: Session) -> Anime:
    anime = db.get(Anime, anime_id)
    if anime is None:
        raise HTTPException(status_code=404, detail="Anime not found")
    return anime


def _no_provider(exc: "NoProviderAnswered") -> HTTPException:
    """Every provider was asked and none answered.

    Each one's reason is carried through: "anilist: 503; kitsu: timed out"
    tells the user their network is the problem, while a single failure among
    two working providers never reaches them at all.
    """
    reason = "; ".join(exc.failures) or str(exc)
    logger.warning("no metadata provider answered: %s", reason)
    return HTTPException(status_code=503, detail=f"No metadata provider answered ({reason})")


def _provider_unavailable(exc: Exception) -> HTTPException:
    """One way of saying the metadata provider did not answer.

    Carrying the reason matters more here than anywhere: when AniList is down
    there is nothing the user can do about it, and a message that does not say
    so sends them looking through their own configuration instead.
    """
    reason = str(exc) or type(exc).__name__
    logger.warning("metadata provider did not answer: %s", reason)
    return HTTPException(status_code=503, detail=f"AniList did not answer: {reason}")


def _from_anidb_dump(query: str, anidb_titles) -> list:
    """Search results out of AniDB's title dump (build 120).

    The one source that has every season of every franchise, offline. It can
    answer with names and an id and nothing else - no cover, no episode count
    - so these come last and are only added where nothing better already
    covers the same show.
    """
    from backend.app.integrations.metadata.base import AnimeCandidate

    try:
        aids = anidb_titles.search(query, limit=20)
    except Exception:  # noqa: BLE001 - the dump is optional
        return []
    found = []
    for aid in aids:
        names = anidb_titles.names_for(int(aid))
        if not names:
            continue
        main = names.get("main")
        english = names.get("en")
        display = english or main
        if not display:
            continue
        found.append(
            AnimeCandidate(
                provider="anidb",
                provider_id=str(aid),
                display_name=display,
                title_romaji=main,
                title_english=english,
                title_native=names.get("ja"),
                aliases=tuple(
                    value
                    for language, value in names.items()
                    if language not in ("main", "en", "ja") and value
                ),
                provider_ids={"anidb": str(aid)},
            )
        )
    return found


def _already_named(item, results: list) -> bool:
    """Whether one of these results is already this show.

    Compared by name because that is all two different databases share -
    the same aggressive normalisation release matching uses. An AniDB entry
    is dropped in favour of a provider's, which has a cover and a count.
    """
    from backend.app.services.anidb_titles import normalize

    mine = {
        normalize(name)
        for name in (item.display_name, item.title_romaji, item.title_english)
        if name
    }
    for other in results:
        theirs = {
            normalize(name)
            for name in (other.display_name, other.title_romaji, other.title_english)
            if name
        }
        if mine & theirs:
            return True
    return False


@router.get("/search", response_model=list[AnimeSearchResult])
async def search_anime(
    query: str = Query(min_length=2, max_length=120),
    db: Session = Depends(get_db),
) -> list[AnimeSearchResult]:
    """Search everything, best source first (build 95).

    The downloaded line-up answers first: it needs no network, it covers every
    season that was fetched, and it is why adopting a Shoko series used to
    fail with "no metadata provider answered" while AniList was refusing - the
    answer was on disk all along.

    Then the sources, in order, until one has something - the selected one, the
    other selectable one, Jikan, Shikimori, MyAnimeList if it has a client id,
    and the AniDB title dump last. Build 86 had removed that chain on purpose
    and the reasoning was sound for *metadata*; it does not hold for a list
    somebody picks from, and AniList spent a day answering 403 to everything.
    Every result carries the source that issued its id, which is what keeps
    this honest - see services/metadata.py.
    """
    from backend.app.services import anidb_titles, calendar_lookup, metadata

    # What has been downloaded, first (build 88). It needs no network, it
    # covers every season that was fetched, and it is why adopting a Shoko
    # series used to fail with "no metadata provider answered" while AniList
    # was refusing - the answer was on disk all along.
    #
    # First, not *instead* (build 120). Reported: searching "ascendance of a"
    # offered one season of a show that has five. The line-up only holds the
    # seasons somebody fetched - this season, and whatever was browsed - so
    # the four older ones were not in it; and because it found one thing,
    # nothing else was ever asked. One hit was enough to hide the rest.
    results = list(calendar_lookup.search(query))
    seen = {(item.provider, item.provider_id) for item in results}

    try:
        from_providers = await metadata.search_everywhere(
            query, preferred=await metadata.selected(db)
        )
    except NoProviderAnswered as exc:
        # Only fatal when the local answer is empty too: a provider outage
        # must not take away the seasons that are on disk.
        if not results:
            raise _no_provider(exc) from exc
        from_providers = []

    for item in from_providers:
        key = (item.provider, item.provider_id)
        if key in seen:
            continue
        seen.add(key)
        results.append(item)

    # And AniDB's own titles, which is where a franchise is complete (120).
    # Asked for with the report: *"can we also add the anidb data in here so
    # when it searches it also searched the anidb data?"*  The dump knows
    # every season of everything, under every name, and needs no network.
    for item in _from_anidb_dump(query, anidb_titles):
        key = (item.provider, item.provider_id)
        if key in seen or _already_named(item, results):
            continue
        seen.add(key)
        results.append(item)
    items = [
        AnimeSearchResult(
            provider=item.provider,
            provider_id=item.provider_id,
            display_name=item.display_name,
            title_english=item.title_english,
            title_romaji=item.title_romaji,
            title_native=item.title_native,
            aliases=list(item.aliases),
            cover_url=item.cover_url,
            genres=list(item.genres),
            format=item.format,
            episode_count=item.episode_count,
            provider_ids=item.provider_ids,
        )
        for item in results
    ]
    mark_in_library(db, items)
    _fill_missing_covers(db, items)
    return items


def _fill_missing_covers(db: Session, items: list[AnimeSearchResult]) -> None:
    """A poster for a result that came without one (build 183).

    A hit from AniDB's title dump has a name and an id and nothing else, so it
    showed a blank square - even when AniFlow already held the picture, as the
    watchlist's cover or the downloaded calendar's. The AniDB id is what ties
    them; no request is made.
    """
    from sqlalchemy import select

    from backend.app.models.anime import Anime
    from backend.app.services.shoko_watchlist import _cover_for

    bare = [
        item for item in items
        if not item.cover_url and (item.provider_ids or {}).get("anidb")
    ]
    if not bare:
        return
    watched = {
        int(anidb_id): cover
        for anidb_id, cover in db.execute(
            select(Anime.anidb_id, Anime.cover_url).where(
                Anime.anidb_id.is_not(None), Anime.cover_url.is_not(None)
            )
        )
    }
    for item in bare:
        try:
            aid = int(item.provider_ids["anidb"])
        except (TypeError, ValueError):
            continue
        item.cover_url = watched.get(aid) or _cover_for(aid)


@router.get("/by-anidb/{anidb_id}", response_model=AnimeSearchResult)
def anime_by_anidb_id(anidb_id: int, db: Session = Depends(get_db)) -> AnimeSearchResult:
    """One anime, named by its AniDB id (build 89).

    This is how a Shoko Server series is adopted. Shoko states the AniDB id
    of everything it holds, so there is nothing to search for - and searching
    was the bug: "Mushoku Tensei: Isekai Ittara Honki Dasu" is season one, the
    season-alias rule matched it to season three's entry, and season three was
    already in the watchlist. An id cannot be confused with its own sequel.

    The downloaded line-up is asked first, because it has the cover, the
    episode count and the airing time. The title dump answers when nothing
    has been downloaded for that season - names only, which is honest about
    what a file of titles contains.
    """
    from backend.app.services import anidb_titles, calendar_lookup

    found = calendar_lookup.by_anidb_id(anidb_id)
    if found is None:
        titles = anidb_titles.titles_for(anidb_id)
        if titles is None:
            raise HTTPException(
                status_code=404,
                detail=f"AniDB {anidb_id} is not in the downloaded data or the title dump",
            )
        found = AnimeCandidate(
            provider="anidb",
            provider_id=str(anidb_id),
            display_name=titles.english or titles.primary,
            title_english=titles.english,
            title_romaji=titles.primary,
            title_native=titles.native,
            aliases=titles.synonyms,
            provider_ids={"anidb": str(anidb_id)},
        )
    item = AnimeSearchResult(
        provider=found.provider,
        provider_id=found.provider_id,
        display_name=found.display_name,
        title_english=found.title_english,
        title_romaji=found.title_romaji,
        title_native=found.title_native,
        aliases=list(found.aliases),
        cover_url=found.cover_url,
        genres=list(found.genres),
        format=found.format,
        episode_count=found.episode_count,
        provider_ids=found.provider_ids,
    )
    # Still reported, never hidden: if the watchlist turns out to have this
    # after all, the dialog should say so rather than offer a second copy.
    mark_in_library(db, [item])
    return item


@router.post("/search-preview", response_model=SetupSearchResponse)
def search_preview(request: SetupSearchRequest) -> SetupSearchResponse:
    """Add-anime setup step: search for this not-yet-added anime under its
    English/Romaji/Japanese names + chosen subgroup/quality, and return the
    distinct release titles found (episode numbers stripped), with a count
    per title. Nothing is persisted here.

    If `query` is set, it overrides the three title variants as the sole
    search term - for checking what the indexer answers for a given query.
    """
    from backend.app.services.discovery import preview_title_search

    outcome = preview_title_search(
        _variants(request), subgroup=request.subgroup, resolution=request.resolution
    )
    return SetupSearchResponse(
        groups=[
            TitleGroupResult(
                title=g.title,
                count=g.count,
                subgroup=g.subgroup,
                resolution=g.resolution,
            )
            for g in outcome.groups
        ],
        # Only when nothing could be searched. A partial failure that still
        # produced results is in the log; the dialog would just be noise.
        indexer_error=outcome.message if outcome.failed else None,
    )


def _variants(request: SetupSearchRequest) -> list[str | None]:
    """The names to search for. A typed query replaces the provider's three."""
    if request.query and request.query.strip():
        return [request.query.strip()]
    return [request.title_english, request.title_romaji, request.title_native]


@router.post("/scan-selection", response_model=SelectionScanResponse)
def scan_selection_route(
    request: SelectionScanRequest, db: Session = Depends(get_db)
) -> SelectionScanResponse:
    """What one group has for one show, judged exactly as adding would judge it.

    The same call the dialog makes when a row is selected and the same one
    behind the More info window - and behind adding the anime, which is the
    point: the number in the box is a promise, not an estimate. See
    services/selection_scan.py.
    """
    from backend.app.services import scan_store
    from backend.app.services.selection_scan import scan_selection, stored_rules

    anime = db.get(Anime, request.anime_id) if request.anime_id else None
    scan = scan_selection(
        queries=request.queries,
        reference=request.reference,
        subgroup=request.subgroup,
        resolution=request.resolution,
        edition=request.edition,
        edition_not=request.edition_not,
        session=db,
        anime=anime,
        indexer_id=request.indexer_id,
    )
    return SelectionScanResponse(
        # Kept for half an hour so adding can write these down rather than
        # asking the tracker for them a second time.
        scan_ids=[scan_store.remember(scan.hits)] if scan.hits else [],
        releases=[
            ScannedReleaseResult(
                id=release.id,
                batch=_is_batch(release),
                raw_name=release.raw_name,
                subgroup=release.subgroup,
                resolution=release.resolution,
                season=release.season,
                episode=release.episode,
                number=release.number,
                version=release.version,
                crc32=release.crc32,
                folder_name=release.folder_name,
                tags=list(release.tags),
                size=release.size,
                seeders=release.seeders,
                accepted=release.accepted,
                reason=release.reason,
                detail=list(release.detail),
                warnings=list(release.warnings),
            )
            # Accepted first, then the rest in the order they arrived: the
            # window is read top-down and the answer belongs at the top.
            for release in [*scan.accepted, *scan.rejected]
        ],
        asked=[
            QueryOutcomeResult(
                term=outcome.term, found=outcome.found, new=outcome.new, error=outcome.error
            )
            for outcome in scan.asked
        ],
        episodes=list(scan.episodes),
        pages=scan.pages,
        failures=list(scan.failures),
        reached_provider=scan.reached_provider,
        no_indexer=scan.no_indexer,
        stored_rules=stored_rules(anime, request.resolution, request.edition, request.edition_not),
    )


@router.post("/search-inspect", response_model=InspectSearchResponse)
def search_inspect(request: InspectSearchRequest) -> InspectSearchResponse:
    """Every hit for one name, with its full parse. The Release Inspector.

    Deliberately unfiltered and ungrouped: the page filters and groups in the
    browser so that changing a filter is instant, and so that "what does this
    filter exclude" can be answered by looking at the same data twice rather
    than by trusting two different searches.
    """
    from backend.app.services.discovery import inspect_title_search

    outcome = inspect_title_search(
        request.query,
        extra_terms=request.extra_terms,
        native=request.native,
        max_pages=request.pages,
        progress_token=request.progress_token,
        term_index=request.term_index,
        term_count=request.term_count,
        indexer_id=request.indexer_id,
    )
    return InspectSearchResponse(
        releases=[InspectedReleaseResult(**asdict(item)) for item in outcome.releases],
        queries=[InspectQueryResult(**asdict(item)) for item in outcome.queries],
        failures=outcome.failures,
        reached_provider=outcome.reached_provider,
        reads_listing=outcome.reads_listing,
        reports_total=outcome.reports_total,
        definition_file=outcome.definition_file,
    )


@router.get("/search-inspect/progress", response_model=SearchProgressResponse)
def search_progress(token: str = Query(default="", max_length=64)) -> SearchProgressResponse:
    """How far the search using this token has got.

    Answers with zeroes for a token nothing knows about - a search that has
    not started, one that has finished, or one from before a restart. The page
    asking is decoration; nothing it does depends on the answer.
    """
    from backend.app.services import search_progress as store

    found = store.read(token)
    if found is None:
        return SearchProgressResponse()
    return SearchProgressResponse(
        term=found.term,
        done=found.done,
        expected=found.expected,
        term_index=found.term_index,
        term_count=found.term_count,
    )


@router.post("/numbering", response_model=NumberingResponse)
def episode_numbering(request: NumberingRequest) -> NumberingResponse:
    """Where this season's episode 1 sits in its franchise's numbering.

    Answers from the pool when it can, from Shoko when it must, and has Shoko
    fetch the entry from AniDB when it has never seen it - in that order, and
    everything learned on the way is written down. With no Shoko configured
    the pool is the only source, which is the honest answer for a standalone
    deployment until somebody types the number in.
    """
    from backend.app.integrations.shoko.client import client_from_settings
    from backend.app.services import episode_numbering as numbering
    from backend.app.services.app_settings import load_app_settings

    # No Shoko configured is not a failure: the pool still answers, and a
    # standalone deployment has nothing else until somebody types a number.
    settings = load_app_settings()
    client = (
        client_from_settings(settings)
        if getattr(settings, "shoko_url", "") and getattr(settings, "shoko_api_key", "")
        else None
    )
    found = numbering.resolve(request.anidb_id, client=client, fetch=request.fetch)
    return _numbering_response(found)


@router.post("/numbering/save", response_model=NumberingResponse)
def save_episode_numbering(request: NumberingSaveRequest) -> NumberingResponse:
    """Write down a start number a person typed. Outranks every other source."""
    from backend.app.services import episode_numbering as numbering

    numbering.set_manually(
        request.anidb_id, request.absolute_start, episode_count=request.episode_count
    )
    return _numbering_response(numbering.resolve(request.anidb_id, client=None, fetch=False))


def _numbering_response(found) -> NumberingResponse:
    return NumberingResponse(
        anidb_id=found.aid,
        absolute_start=found.absolute_start,
        absolute_end=found.absolute_end,
        episode_count=found.episode_count,
        source=found.source,
        detail=found.detail,
        chain=[
            NumberingSeasonResult(
                aid=season.aid,
                title=season.title,
                kind=season.kind,
                episode_count=season.episode_count,
                absolute_start=season.absolute_start,
                asked=season.aid == found.aid,
            )
            for season in found.chain
        ],
    )


@router.post("/inspect-names", response_model=InspectNamesResponse)
def inspect_names(request: InspectNamesRequest) -> InspectNamesResponse:
    """Every name one show goes by, each one searchable on its own.

    Read-only and local: the AniDB title comes from the dump already on disk
    and the per-source titles from the downloaded calendar, so selecting a
    show on the inspector costs no request to anybody.
    """
    from backend.app.services.inspect_names import names_for

    found = names_for(
        title_english=request.title_english,
        title_romaji=request.title_romaji,
        title_native=request.title_native,
        aliases=request.aliases,
        provider_ids=request.provider_ids,
    )
    return InspectNamesResponse(
        names=[
            InspectNameResult(kind=name.kind, value=name.value, native=name.native)
            for name in found
        ]
    )


@router.post("/search-preview/debug")
def search_preview_debug(request: SetupSearchRequest) -> PlainTextResponse:
    """The same search, written out release by release, as a text file.

    A count you cannot check is a count you have to take on faith, and "that
    show has more episodes than this says" has no answer from the outside. Two
    very different faults look identical there - the indexer returned little,
    or it returned plenty and AniFlow dropped some - so this reports what was
    asked, what came back, and what became of every single release.

    Text, not JSON: it exists to be read by a person, once, while they are
    annoyed. Nothing is persisted and nothing is decided by it.
    """
    from backend.app.services.discovery import preview_title_search

    settings = load_app_settings()
    outcome = preview_title_search(
        _variants(request),
        subgroup=request.subgroup,
        resolution=request.resolution,
        trace=True,
    )
    counted = [entry for entry in outcome.trace if not entry.skipped]
    skipped = [entry for entry in outcome.trace if entry.skipped]

    lines = [
        f"AniFlow search debug - {build_string()}",
        f"generated {now_local():%d.%m.%Y %H:%M:%S %Z}",
        "",
        "WHAT WAS ASKED",
        f"  searched for      : {', '.join(outcome.queries) or '(nothing)'}",
        f"  subgroup filter   : {request.subgroup or '(none - filtered in the browser)'}",
        f"  quality filter    : {request.resolution or '(none - filtered in the browser)'}",
        f"  pages per name    : {settings.search_max_pages} (50 results each)",
        f"  results per name  : {settings.search_max_results} at most",
        "",
        "WHAT CAME BACK",
        f"  releases returned : {len(outcome.trace)}",
        f"  counted           : {len(counted)}",
        f"  not counted       : {len(skipped)}",
    ]
    if outcome.failures:
        lines += ["", "SEARCHES THAT FAILED"] + [f"  {line}" for line in outcome.failures]

    lines += ["", "ROWS THE DIALOG SHOWS"]
    lines += [
        f"  {group.count:>4} x  {group.subgroup or '(unknown group)'} "
        f"{group.resolution or '(unknown quality)'}  -  {group.title}"
        for group in outcome.groups
    ] or ["  (none)"]

    lines += ["", "EVERY RELEASE, AS RETURNED"]
    if outcome.trace:
        for entry in outcome.trace:
            verdict = f"SKIPPED ({entry.skipped})" if entry.skipped else (
                f"counted as {entry.subgroup or '?'} {entry.resolution or '?'}"
                f" ep {entry.episode or '?'} - {entry.title}"
            )
            lines += [
                f"  [{entry.query} p{entry.page}] {entry.raw_title}",
                f"      -> {verdict}",
            ]
    else:
        lines.append("  (the indexer returned nothing at all)")

    if skipped:
        lines += [
            "",
            "The skipped ones are the difference between what the site lists and",
            "what the counts say. An unreadable name is a parser gap worth",
            "reporting; 'already counted' is the same torrent under two names.",
        ]
    lines.append("")
    return PlainTextResponse("\n".join(lines))


@router.get("/{anime_id}", response_model=AnimeDetail)
def get_anime(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """One anime with its episodes, each carrying its own releases/download/media.

    The nesting comes from the relationships; nothing is stitched together by
    episode number.
    """
    from backend.app.services.serializers import serialize_episode, serialize_profile, sorted_episodes
    from backend.app.services.storage import storage_roots

    anime = get_anime_or_404(anime_id, db)
    payload = AnimeSummary.model_validate(anime).model_dump()
    payload.update(
        {
            "title_english": anime.title_english,
            "title_romaji": anime.title_romaji,
            # For the Edit dialog's fields (build 156): the normalised keys
            # every reader uses, not whatever shape the source sent.
            "genres": list((anime.metadata_json or {}).get("genres") or []),
            "studios": list((anime.metadata_json or {}).get("studios") or []),
            "synopsis": (anime.metadata_json or {}).get("synopsis"),
            "title_native": anime.title_native,
            "aliases": list(anime.aliases or []),
            "provider_ids": dict(anime.provider_ids or {}),
            "format": anime.format,
            "drop_mode": anime.drop_mode,
            # Whether the slot and the count on screen are the user's own, so
            # the page knows whether Reset has anything to drop.
            "schedule_is_manual": anime.schedule_is_manual,
            "episode_count_is_manual": anime.episode_count_is_manual,
            "season_is_manual": "season" in manual_fields(anime),
            "release_name": anime.release_name,
            "media_folder": anime.media_folder,
            "import_folder": anime.import_folder,
            # The name, so the page does not have to resolve an id itself.
            "feed_source_name": anime.feed_source.name if anime.feed_source is not None else None,
            "profile": serialize_profile(anime),
            "storage": storage_roots().as_dict(),
            "episodes": [serialize_episode(episode) for episode in sorted_episodes(anime)],
        }
    )
    payload["owned_episodes"] = sum(
        1 for episode in anime.episodes if episode.status.value == "COMPLETE"
    )
    payload["available_episodes"] = sum(
        1 for episode in anime.episodes if episode.status.value in ("AVAILABLE", "MISSING")
    )
    return payload


def _add_failed(db: Session, request: AddAnimeRequest, reason: str, name: str | None = None) -> None:
    """Write down an add that did not happen (build 140).

    Asked for after a slow add on a Raspberry Pi failed with a message that
    vanished before it could be read: *"the message disappears very fast, I
    could not see or copy it"*. A toast is a notification; this is the record.

    Best effort on purpose - a log entry must never be the reason an error
    turns into a second error - and committed on its own session state, since
    the request's own work is being rolled back around it.
    """
    from backend.app.services.attention import log_activity

    try:
        db.rollback()
        log_activity(
            db,
            kind="ADD_FAILED",
            message=f"{name or request.provider_id} could not be added: {reason}",
            details={
                "provider": request.provider,
                "provider_id": request.provider_id,
                "reason": reason,
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001 - never worse than the failure it reports
        db.rollback()
        logger.warning("could not record the failed add", exc_info=True)


@router.post("", response_model=AnimeSummary, status_code=status.HTTP_201_CREATED)
async def create_anime(request: AddAnimeRequest, db: Session = Depends(get_db)) -> Anime:
    # What has been downloaded, first (build 99). The search already looks
    # there before the network - which is how an entry can be *found* while
    # its provider is down - and re-fetching it to add it undid that: the
    # reported failure was "The anime could not be added. Please try again."
    # for a show whose record was on disk the whole time, because the id
    # belonged to AniList and AniList was answering 403.
    from backend.app.services import calendar_lookup

    candidate = calendar_lookup.by_provider_id(request.provider, request.provider_id)

    if candidate is None:
        # Nothing downloaded for it, so the network is genuinely the only
        # place to ask. Deliberately no fallback between providers: an id
        # belongs to the provider that issued it, and asking somebody else for
        # "52991" would attach a different show's episodes to this one for
        # good. by_id rather than providers[...]: an AniDB id has to be
        # resolvable and AniDB is not a source anybody may select.
        adapter = by_id(request.provider)
        if adapter is None:
            _add_failed(db, request, "unsupported metadata provider")
            raise HTTPException(status_code=422, detail="Unsupported metadata provider")
        try:
            candidate = await adapter.get_anime(request.provider_id)
        except httpx.HTTPError as exc:
            # Was unhandled, so a provider outage arrived as a 500 and the
            # page said "please try again" - the one thing that cannot help.
            failure = _provider_unavailable(exc)
            _add_failed(db, request, str(getattr(failure, "detail", exc)))
            raise failure from exc
        if candidate is None:
            _add_failed(db, request, "the provider does not know this id")
            raise HTTPException(status_code=404, detail="Anime not found by provider ID")
    try:
        created = add_anime(db, request, candidate)
        # Adding an anime is what finds its back catalogue, so ask for a queue
        # pass now rather than leaving it until the next feed scan an hour
        # later. Nothing is downloaded that the release profile and the
        # Automate switch do not both allow.
        worker_state.request_run("queue_downloads")
        return created
    except ValueError as exc:
        _add_failed(db, request, str(exc), candidate.display_name)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        _add_failed(db, request, "that name or folder is already taken", candidate.display_name)
        raise HTTPException(status_code=409, detail="Anime or local folder name already exists") from exc


@router.patch("/{anime_id}", response_model=AnimeSummary)
def patch_anime(anime_id: str, request: UpdateAnimeRequest, db: Session = Depends(get_db)) -> Anime:
    anime = get_anime_or_404(anime_id, db)
    payload = request.model_dump(exclude_unset=True)
    if "feed_source_id" in payload:
        feed_source_id = payload.pop("feed_source_id")
        if feed_source_id is not None:
            from backend.app.models.rss import FeedSource

            if db.get(FeedSource, feed_source_id) is None:
                raise HTTPException(status_code=404, detail="Feed source not found")
        anime.feed_source_id = feed_source_id
    if "release_name" in payload:
        anime.release_name = (payload.pop("release_name") or "").strip() or None
    folder_changed = False
    if "media_folder" in payload:
        folder = payload.pop("media_folder")
        if not folder:
            # Without it nothing can be placed, so this one cannot be cleared.
            raise HTTPException(status_code=422, detail="media_folder cannot be empty")
        folder_changed = folder != anime.media_folder
        anime.media_folder = folder
    if "import_folder" in payload:
        # The third stage is optional, so clearing it is meaningful.
        anime.import_folder = payload.pop("import_folder") or None
    if "custom_feed_url" in payload:
        url = payload.pop("custom_feed_url")
        if url and not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="custom_feed_url must be an http(s) URL")
        anime.custom_feed_url = url or None
    try:
        update_anime(anime, request)
        if request.releases_ignored:
            # Nothing is looking for this one's releases any more, so the
            # warnings about not finding any have stopped being true rather
            # than been solved (build 123).
            forget_release_warnings(db, anime)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Anything changed here can change what should be downloaded, so ask for a
    # queue pass (build 94). Automate did work before this - the feed scan runs
    # the queue pass after itself - but only on the scan's own hour, so turning
    # the switch on did nothing for up to sixty minutes and looked broken.
    #
    # Nothing is downloaded that the release profile and the Automate switch do
    # not both allow, which is why this is safe to ask for on every change.
    worker_state.request_run("queue_downloads")
    if request.episode_count is not None or request.reset_episode_count:
        # A count is a claim about how many episodes exist, so the rows have to
        # follow it - otherwise raising it changes a label and nothing else,
        # and the episodes the user just said were coming never appear.
        # Rows holding a file are never removed: lowering the count must not
        # delete an episode that has one - only the empty ones go (build 190).
        from backend.app.services.anime import ensure_episode_rows, trim_episode_rows

        ensure_episode_rows(db, anime, anime.effective_episode_count)
        # And the other way (build 190): lowering the count removes the empty
        # rows past it, so a corrected "13" stops listing an episode 13.
        trim_episode_rows(db, anime, anime.effective_episode_count)
    if folder_changed:
        # Files are never moved by a rename, so every episode that recorded one
        # is now pointing at a path that does not exist. Re-derived here rather
        # than waiting for the next reconcile pass: the page has to tell the
        # truth the moment it reloads, or the rename looks like it did nothing.
        from backend.app.services.episode_state import refresh_episode_status
        from backend.app.services.storage import roots_for, storage_roots

        roots = roots_for(anime, storage_roots())
        for episode in anime.episodes:
            refresh_episode_status(episode, roots=roots, check_disk=True)
    db.commit()
    db.refresh(anime)
    return anime


@router.post("/{anime_id}/convert", response_model=AnimeSummary)
def convert_anime(
    anime_id: str, request: ConvertAnimeRequest, db: Session = Depends(get_db)
) -> Anime:
    """Give a tracker entry the release name it needs to be downloadable.

    Refused in Basic mode: there would be no indexer to search and nothing to
    match, so the anime would gain a release name that had never been checked
    against a real release - which is the one thing this flow exists to avoid.
    """
    from backend.app.services.anime import convert_to_advanced
    from backend.app.services.app_mode import AppMode, current_mode

    anime = get_anime_or_404(anime_id, db)
    if current_mode(db) is not AppMode.ADVANCED:
        raise HTTPException(
            status_code=409,
            detail="Add an indexer first - there is nothing to search for a release name yet",
        )
    try:
        convert_to_advanced(
            db,
            anime,
            release_name=request.release_name,
            local_name=request.local_name,
            subgroup=request.subgroup,
            resolution=request.resolution,
            edition=request.edition,
            edition_not=request.edition_not,
            template_id=request.template_id,
            feed_source_id=request.feed_source_id,
            episode_count=request.episode_count,
            library_id=request.library_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    apply_indexer(db, anime, request.indexer_id)
    if request.episode_offset:
        anime.episode_offset = request.episode_offset
    db.commit()
    db.refresh(anime)
    logger.info("%r converted to an advanced entry", anime.display_name)
    _write_down_the_chosen_releases(db, anime, request)
    # What adding does as well (build 208): without it the chosen group was saved and nothing
    # looked for its episodes until the next scan, so the page said nothing had happened.
    worker_state.request_run("queue_downloads")
    return anime


def _write_down_the_chosen_releases(db: Session, anime: Anime, request: ConvertAnimeRequest) -> None:
    """Attach the releases the chosen group has to this anime's episodes, as an add does.

    Best effort: an indexer that is down must not undo the conversion, and the worker pass
    that follows is the second chance."""
    query = (request.search_query or "").strip() or anime.release_name or anime.display_name
    if not query:
        return
    try:
        from backend.app.services import scan_store
        from backend.app.services.discovery import persist_available_from_preview

        persist_available_from_preview(
            db,
            anime,
            search_query=query,
            selected_folder=anime.release_name or query,
            subgroup=request.subgroup,
            resolution=request.resolution,
            edition=request.edition,
            edition_not=request.edition_not,
            known=scan_store.recall_all(request.scan_ids),
        )
        db.refresh(anime)
    except Exception as exc:  # noqa: BLE001 - never undo a conversion
        from backend.app.services.anime import record_lookup_failure

        record_lookup_failure(db, anime, exc, when="after converting it")


@router.post("/{anime_id}/aliases")
def add_alias(anime_id: str, request: AliasRequest, db: Session = Depends(get_db)) -> dict:
    anime = get_anime_or_404(anime_id, db)
    alias = request.alias.strip()
    aliases = list(anime.aliases or [])
    if alias and alias not in aliases:
        aliases.append(alias)
        anime.aliases = aliases
        db.commit()
    return {"aliases": aliases}


@router.delete("/{anime_id}/aliases", response_model=dict)
def remove_alias(anime_id: str, alias: str = Query(min_length=1), db: Session = Depends(get_db)) -> dict:
    anime = get_anime_or_404(anime_id, db)
    aliases = [entry for entry in (anime.aliases or []) if entry != alias]
    anime.aliases = aliases
    db.commit()
    return {"aliases": aliases}


@router.post("/{anime_id}/connect-release")
def connect_release(anime_id: str, request: ConnectReleaseRequest, db: Session = Depends(get_db)) -> dict:
    """Manually link an RSS naming scheme to this anime.

    Parses the release name and stores its title as a permanent alias, then
    re-evaluates that single release so the caller sees the new verdict.
    """
    anime = get_anime_or_404(anime_id, db)
    from backend.app.services.release_parser import parse_release_name

    try:
        parsed = parse_release_name(request.raw_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not parsed.title:
        raise HTTPException(status_code=409, detail="The release name contains no recognizable title")

    aliases = list(anime.aliases or [])
    connected = parsed.title not in aliases
    if connected:
        aliases.append(parsed.title)
        anime.aliases = aliases
        db.commit()

    from backend.app.services.feed_search import evaluate_item_for_anime

    item = evaluate_item_for_anime(db, anime.id, request.raw_name)
    return {"connected": connected, "alias": parsed.title, **item}


@router.post("/{anime_id}/feed-search")
async def feed_search(
    anime_id: str,
    request: FeedSearchRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Debug helper: evaluate every item of one feed against this anime."""
    from backend.app.services.feed_search import search_feed_for_anime

    return search_feed_for_anime(db, anime_id, request.feed_source_id, feed_url_override=request.url)


@router.post("/{anime_id}/queue-release")
def queue_release(
    anime_id: str,
    request: QueueReleaseRequest,
    db: Session = Depends(get_db),
) -> dict:
    from backend.app.services.feed_search import queue_manual_release

    row = queue_manual_release(
        db,
        anime_id=anime_id,
        raw_name=request.raw_name,
        torrent_url=request.torrent_url,
        episode=request.episode,
        version=request.version,
    )
    return {
        "id": row.id,
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "attention_reason": row.attention_reason,
        "error": row.error,
        "label": row.label,
        "episode": row.episode_number,
        "version": row.version,
    }


@router.post("/{anime_id}/link-folder")
def link_folder(anime_id: str, request: LinkFolderRequest, db: Session = Depends(get_db)) -> dict:
    """Derive the library folder from one selected release, create it and
    link this anime to it. No download is started."""
    anime = get_anime_or_404(anime_id, db)
    from backend.app.services.library import link_release_folder

    try:
        return link_release_folder(db, anime, request.raw_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{anime_id}/scan-folder")
def scan_folder(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """Scan the linked library folder and mark episodes whose files exist."""
    anime = get_anime_or_404(anime_id, db)
    from backend.app.services.library import scan_anime_folder

    try:
        return scan_anime_folder(db, anime)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{anime_id}/scan-files")
def scan_files(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """Scan this anime's folder, link its files to the episodes, and find the
    subgroup and quality they say (build 198). Reads only; sets nothing the
    user chose."""
    from backend.app.services.folder_scan import scan_and_identify

    anime = get_anime_or_404(anime_id, db)
    return scan_and_identify(db, anime)


class FileAssignment(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    #: The episode this file is, or null for "no episode's".
    episode: int | None = Field(default=None, ge=1, le=10000)


class AssignFilesRequest(BaseModel):
    assignments: list[FileAssignment] = Field(max_length=2000)


@router.get("/{anime_id}/folder-files")
def folder_files(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """The video files in this anime's folder and which episode each one is recorded as (build 213)."""
    from backend.app.services.file_assign import list_folder_files

    anime = get_anime_or_404(anime_id, db)
    try:
        return list_folder_files(db, anime)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{anime_id}/assign-files")
def assign_files_route(anime_id: str, request: AssignFilesRequest, db: Session = Depends(get_db)) -> dict:
    """Record by hand which file is which episode - for names no scan can read (build 213).

    Records only: no file is renamed, moved or deleted.
    """
    from backend.app.services.file_assign import assign_files

    anime = get_anime_or_404(anime_id, db)
    try:
        return assign_files(db, anime, [(item.filename, item.episode) for item in request.assignments])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class QueueAvailableRequest(BaseModel):
    episode: int | None = Field(default=None, ge=0, le=9999)
    all_available: bool = False


@router.post("/{anime_id}/queue-available")
def queue_available(
    anime_id: str,
    request: QueueAvailableRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Queue the best known release for one episode, or for every episode.

    "Best" is the most seeded release the episode knows about. Rows are created
    as ATTENTION (qb_not_configured) when no client is configured, so the
    linkage stays visible in the Downloads overview either way.
    """
    from backend.app.services.downloads import queue_episode_releases

    anime = get_anime_or_404(anime_id, db)
    try:
        result = queue_episode_releases(
            db,
            anime_id=anime.id,
            episode=request.episode,
            all_available=request.all_available,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


def _is_batch(release) -> bool:
    """A release that is a range of episodes ("01-12") rather than one (build 213)."""
    import re as _re

    if release.number is not None:
        return False
    if getattr(release, "whole_season", False):
        return True
    return bool(release.episode) and bool(_re.search(r"\d\s*-\s*\d", str(release.episode)))


class BatchChoice(BaseModel):
    """A batch torrent to take single episodes out of (build 213, docs/batch-episodes-plan.md)."""

    #: The tickets of the scan that found it, and the release's id in it. The link to the .torrent file stays
    #: on the server - the browser never held it.
    scan_ids: list[str] = Field(min_length=1, max_length=8)
    release_id: str = Field(min_length=1, max_length=200)
    #: Which episodes to take; none means every one of this anime's missing episodes the batch has.
    episodes: list[int] | None = Field(default=None, max_length=500)
    #: How far this group numbers past episode 1, when the dialog decided it.
    episode_offset: int | None = Field(default=None, ge=0, le=10000)


class FixMissingRequest(BaseModel):
    """Find what an entry is missing, without changing what it has (117).

    The third thing the setup dialog can be asked to do, beside adding an
    anime and changing its release. Asked for after the "Only in Shoko"
    category went away: an adopted series goes straight to its detail page,
    and a series that is missing four of ten episodes needs a way to say so
    that is not "change the release" - that one clears the episodes it
    already has, which is the opposite of what is wanted here.

    Nothing is cleared, nothing is deleted, and no episode that already has
    its file is touched.
    """

    #: What to ask the indexer. The dialog fills this in from what the entry
    #: already knows, so the ordinary case is pressing the button.
    search_query: str | None = Field(default=None, max_length=300)
    #: The indexer chosen in the dialog; kept on the anime so its searches ask it (item 44).
    indexer_id: str | None = Field(default=None, max_length=36)
    subgroup: str | None = Field(default=None, max_length=100)
    resolution: str | None = Field(default=None, max_length=20)
    edition: list[str] = Field(default_factory=list, max_length=12)
    edition_not: list[str] = Field(default_factory=list, max_length=12)
    #: The tickets for a scan the dialog has already run, so the tracker is
    #: not asked twice for one answer (build 100).
    scan_ids: list[str] = Field(default_factory=list, max_length=8)
    #: The name to match releases by, when the dialog picked a new one.
    release_name: str | None = Field(default=None, max_length=300)
    #: Whether to hand what it finds to the torrent client. True by choice:
    #: the button says "fix", and a fix that stops one step short of the
    #: download is a half-fix.
    queue: bool = True
    #: A batch to take the missing episodes out of, when the group only has one (build 213).
    batch: BatchChoice | None = None


class FixMissingResponse(BaseModel):
    #: Finished torrents that were already in the client and unclaimed, taken
    #: charge of before anything was searched for (build 119).
    adopted: int = 0
    #: Release rows attached to episodes that had none.
    found: int = 0
    #: Episodes handed to the torrent client.
    queued: int = 0
    #: Episodes left alone, and why - the same codes the Download button
    #: reports, so "nothing happened" is always answerable.
    skipped: int = 0
    reasons: dict[str, int] = {}
    errors: list[str] = []
    #: Episodes taken out of a batch torrent, and which, or why not (build 213).
    batch_queued: int = 0
    batch_episodes: list[int] = []
    batch_error: str | None = None


def _batch_url(choice: "BatchChoice") -> str | None:
    """The .torrent link of the release the dialog named, from the scan it came from."""
    from backend.app.services import scan_store

    for result in scan_store.recall_all(choice.scan_ids) or []:
        if result.id == choice.release_id:
            return result.link
    return None


class BatchPreviewFile(BaseModel):
    index: int
    path: str
    name: str
    size: int
    episode: int | None = None
    #: wanted | have | not_needed | beyond_count | extra | unreadable | ambiguous | not_video
    status: str


class BatchPreviewResponse(BaseModel):
    name: str
    files: list[BatchPreviewFile]
    #: The episodes this batch would give, in order.
    wanted: list[int]


@router.post("/{anime_id}/batch-files", response_model=BatchPreviewResponse)
def batch_files(anime_id: str, choice: BatchChoice, db: Session = Depends(get_db)) -> BatchPreviewResponse:
    """What is inside a batch torrent, and which of its files this anime is missing (build 213)."""
    from backend.app.services.batch_episodes import BatchError, preview_batch

    anime = get_anime_or_404(anime_id, db)
    url = _batch_url(choice)
    if url is None:
        raise HTTPException(status_code=409, detail="That search is no longer held. Search again.")
    try:
        plan = preview_batch(anime, url, offset=choice.episode_offset)
    except BatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return BatchPreviewResponse(
        name=plan.name,
        files=[
            BatchPreviewFile(
                index=item.index, path=item.path, name=item.name, size=item.size,
                episode=item.episode, status=item.status,
            )
            for item in plan.files
        ],
        wanted=sorted(item.episode for item in plan.wanted if item.episode),
    )


@router.post("/{anime_id}/fix-missing", response_model=FixMissingResponse)
def fix_missing_episodes(
    anime_id: str,
    payload: FixMissingRequest,
    db: Session = Depends(get_db),
) -> FixMissingResponse:
    """Look for what is missing, attach it, and fetch it.

    Deliberately additive. An episode that is already in the library keeps its
    file, its release and its history; only the ones with nothing behind them
    are looked for. That is what makes this safe to press on a series somebody
    has been collecting for a year.
    """
    from backend.app.services import scan_store
    from backend.app.services.discovery import persist_available_from_preview
    from backend.app.services.downloads import adopt_orphan_torrents, queue_episode_releases

    anime = get_anime_or_404(anime_id, db)
    apply_indexer(db, anime, payload.indexer_id)
    db.flush()

    # What is already downloaded, before asking anybody for it again (119).
    # Reported: five episodes missing whose files were sitting in the torrent
    # client, finished, with nothing tracking them. Re-downloading those would
    # have worked and been wrong - the file was already there.
    adopted = 0
    try:
        # `include_cleared`: pressing Fix missing episodes is the user asking
        # for adoption in so many words, so it overrides an earlier Clear
        # (build 128). The background pass is the one that honours it.
        adopted = int(
            adopt_orphan_torrents(db, include_cleared=True).get("adopted") or 0
        )
    except Exception as exc:  # noqa: BLE001 - the client is not this route
        logger.info("stray torrents could not be looked at: %s", exc)
    if payload.release_name and payload.release_name.strip():
        anime.release_name = payload.release_name.strip()
        db.flush()

    query = (
        (payload.search_query or "").strip()
        or anime.release_name
        or anime.title_romaji
        or anime.display_name
    )
    found = 0
    errors: list[str] = []
    if query:
        try:
            found = persist_available_from_preview(
                db,
                anime,
                search_query=query,
                selected_folder=anime.release_name or query,
                subgroup=payload.subgroup,
                resolution=payload.resolution,
                edition=payload.edition,
                edition_not=payload.edition_not,
                known=scan_store.recall_all(payload.scan_ids),
            )
        except Exception as exc:  # noqa: BLE001 - an indexer is not this route
            logger.warning("could not look for the missing episodes: %s", exc)
            errors.append(str(exc))
    db.commit()

    queued = skipped = 0
    reasons: dict[str, int] = {}
    if payload.queue:
        # Every episode, and the queue decides: one that is already in the
        # library, already downloading or already importing is skipped by the
        # same rules the Download button follows, and says which.
        result = queue_episode_releases(db, anime_id=anime.id, all_available=True)
        queued = int(result.get("queued") or 0)
        skipped = int(result.get("skipped") or 0)
        reasons = dict(result.get("reasons") or {})
        errors.extend(list(result.get("errors") or []))

    # What the single episodes could not give, from the batch the person picked (build 213). After them,
    # so an episode a single file already covers is not asked of the batch as well.
    batch_queued = 0
    batch_episodes: list[int] = []
    batch_error: str | None = None
    if payload.batch is not None and payload.queue:
        batch_queued, batch_episodes, batch_error = _queue_chosen_batch(db, anime, payload.batch)

    return FixMissingResponse(
        found=found,
        queued=queued,
        skipped=skipped,
        reasons=reasons,
        errors=errors,
        adopted=adopted,
        batch_queued=batch_queued,
        batch_episodes=batch_episodes,
        batch_error=batch_error,
    )


def _queue_chosen_batch(db: Session, anime: Anime, choice: "BatchChoice") -> tuple[int, list[int], str | None]:
    """Hand the chosen batch's missing episodes to the client. Never raises: the answer says what happened."""
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services.batch_episodes import BatchError, queue_batch
    from backend.app.services.downloads import _client_from

    url = _batch_url(choice)
    if url is None:
        return 0, [], "That search is no longer held. Search again."
    settings = load_app_settings(db)
    client = _client_from(settings, None)
    if client is None:
        return 0, [], "qb_not_configured"
    try:
        result = queue_batch(
            db, anime, url, client=client, category=settings.qb_category,
            episodes=choice.episodes, offset=choice.episode_offset,
        )
    except BatchError as exc:
        return 0, [], str(exc)
    finally:
        client.close()
    return result.queued, result.episodes, result.note if result.queued == 0 else None


class MatchOnlyResponse(BaseModel):
    """What one match-only scan settled."""

    #: Which of the four values it wrote, so the page can say what changed.
    stored: list[str] = []
    #: How many releases it attached to episodes. This is the part that makes
    #: a later v2 recognisable: the episodes now know what they *are*.
    matched: int = 0
    errors: list[str] = []


@router.post("/{anime_id}/match-only", response_model=MatchOnlyResponse)
def match_only(
    anime_id: str,
    payload: FixMissingRequest,
    db: Session = Depends(get_db),
) -> MatchOnlyResponse:
    """Work out what this library was released as, and download nothing.

    *"So in theory we need to make a new scan and match the subgroup and
    quality to this files, but not download anything, just matching. This is
    important as later when there is a version 2 or 3 of this files it needs
    to be found."*

    The same search and the same pick as the setup dialog, and then a full
    stop. It shares ``FixMissingRequest`` because it is asking the dialog for
    the same four answers; what differs is everything that happens after them,
    which here is nothing.

    ``queue`` on the payload is ignored rather than honoured: this endpoint's
    whole promise is that it fetches nothing, and a flag that could break that
    promise should not be reachable from the thing that makes it.
    """
    from backend.app.services import scan_store
    from backend.app.services.anime import remember_release_identity
    from backend.app.services.discovery import persist_available_from_preview

    anime = get_anime_or_404(anime_id, db)
    apply_indexer(db, anime, payload.indexer_id)
    stored = remember_release_identity(
        db,
        anime,
        release_name=payload.release_name,
        subgroup=payload.subgroup,
        resolution=payload.resolution,
        edition=payload.edition,
        edition_not=payload.edition_not,
    )
    db.flush()

    query = (
        (payload.search_query or "").strip()
        or anime.release_name
        or anime.title_romaji
        or anime.display_name
    )
    matched = 0
    errors: list[str] = []
    if query:
        try:
            matched = persist_available_from_preview(
                db,
                anime,
                search_query=query,
                selected_folder=anime.release_name or query,
                subgroup=payload.subgroup,
                resolution=payload.resolution,
                edition=payload.edition,
                edition_not=payload.edition_not,
                known=scan_store.recall_all(payload.scan_ids),
            )
        except Exception as exc:  # noqa: BLE001 - an indexer is not this route
            logger.warning("could not match the library to releases: %s", exc)
            errors.append(str(exc))
    db.commit()
    return MatchOnlyResponse(stored=stored, matched=matched, errors=errors)


class LeftoverFile(BaseModel):
    """One file a swap left behind for the user (build 119)."""

    id: str
    filename: str
    size_bytes: int | None = None
    #: "renamed" for a .backup file left by a swap, "awaiting_replacement"
    #: for one being kept until its new copy is imported.
    kind: str
    episode_number: int | None = None


class LeftoverResponse(BaseModel):
    files: list[LeftoverFile] = []


@router.get("/{anime_id}/health")
def anime_health(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """Everything the four Debug windows show (build 120).

    The same four groups the card's icons are, with what is missing *and*
    what was found in each - because "metadata is yellow" is a colour, and
    *"so I can start verifying stuff easier"* needs the list.

    One endpoint for all four rather than one each: they are computed
    together, and a window that disagreed with the icon beside it would be
    worse than no window.
    """
    from backend.app.services.anime_health import health_for
    from backend.app.services.integration_state import remembered_shoko

    anime = get_anime_or_404(anime_id, db)
    holdings, by_title, answered = remembered_shoko()
    return health_for(
        db, anime, holdings=holdings, by_title=by_title, answered=answered, check_anidb=True
    ).as_dict()


class LookForReleasesResponse(BaseModel):
    """What asking for the releases again came to (build 231)."""

    #: Releases the indexer search found for this anime's release name and profile.
    found: int = 0
    #: Releases attached to its episodes now.
    attached: int = 0
    #: Why it did not finish, in the words of the error; null when it did.
    error: str | None = None


@router.post("/{anime_id}/unskip", response_model=dict)
def unskip_episodes(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """Fetch the episodes the user's AniDB list said they have too (item 36b).

    Clears the mark on every one and asks for a queue pass; whatever then has a release and is allowed by
    the profile is fetched like any other missing episode."""
    from backend.app.services.episode_skip import clear_skips

    anime = get_anime_or_404(anime_id, db)
    cleared = clear_skips(anime)
    db.commit()
    if cleared:
        worker_state.request_run("queue_downloads")
    return {"cleared": cleared}


@router.post("/{anime_id}/look-for-releases", response_model=LookForReleasesResponse)
def look_for_releases(anime_id: str, db: Session = Depends(get_db)) -> LookForReleasesResponse:
    """Search the indexer for this anime again and attach what it finds - the step an add does.

    Synchronous on purpose: the person pressed a button and wants the answer, or the error, on the page. A
    slow indexer makes it slow; it is never silent.
    """
    from backend.app.services.anime import look_for_releases_now

    anime = get_anime_or_404(anime_id, db)
    found, attached, error = look_for_releases_now(db, anime)
    if attached and not error:
        # Whatever is attachable can be fetched now rather than at the next scan.
        worker_state.request_run("queue_downloads")
    return LookForReleasesResponse(found=found, attached=attached, error=error)


class AnidbCandidateResponse(BaseModel):
    aid: int
    title: str
    names: list[str] = []
    #: The id list says this entry is one of this anime's own ids.
    agrees: bool = False
    #: The id list gives this entry to another show (kind -> id); empty when it does not.
    belongs_to: dict[str, str] = {}
    current: bool = False


class AnidbChoicesResponse(BaseModel):
    """Is the AniDB id certain, and which entries could it be (build 229)."""

    current: int | None = None
    #: confirmed | unconfirmed | contradicted | unknown
    state: str
    reason: str = ""
    #: "list" or "you" when confirmed.
    by: str | None = None
    candidates: list[AnidbCandidateResponse] = []


class AnidbChoiceRequest(BaseModel):
    anidb_id: int = Field(gt=0)


def _anidb_choices(anime) -> AnidbChoicesResponse:
    from backend.app.services import anidb_confirmation as confirmation

    answer = confirmation.verdict(anime)
    return AnidbChoicesResponse(
        current=confirmation.stored_id(anime),
        state=answer.state,
        reason=answer.reason,
        by=answer.by,
        candidates=[AnidbCandidateResponse(**vars(item)) for item in confirmation.candidates(anime)],
    )


@router.get("/{anime_id}/anidb", response_model=AnidbChoicesResponse)
def anidb_choices(anime_id: str, db: Session = Depends(get_db)) -> AnidbChoicesResponse:
    """How certain this anime's AniDB id is, and the entries it might be."""
    return _anidb_choices(get_anime_or_404(anime_id, db))


@router.post("/{anime_id}/anidb", response_model=AnidbChoicesResponse)
def choose_anidb(anime_id: str, request: AnidbChoiceRequest, db: Session = Depends(get_db)) -> AnidbChoicesResponse:
    """The user picked (or kept) an AniDB id: store it and record that a person vouched for it."""
    from backend.app.services import anidb_confirmation as confirmation

    anime = get_anime_or_404(anime_id, db)
    confirmation.confirm(anime, request.anidb_id)
    db.commit()
    db.refresh(anime)
    return _anidb_choices(anime)


class RefillResponse(BaseModel):
    """What one press of Refill managed."""

    #: Which facts it filled in or corrected, as the Debug window's own codes.
    filled: list[str] = []
    #: Gaps the provider does not know either. Named so the window can stop
    #: implying that pressing again will help.
    unanswered: list[str] = []
    #: Values the user overrode that the provider now disagrees with. Raised
    #: as attention records rather than settled here.
    conflicts: int = 0
    #: True when there is nothing to ask: an entry built from AniDB's title
    #: dump has no id the selected source would recognise.
    unanswerable: bool = False


@router.post("/{anime_id}/refill-metadata", response_model=RefillResponse)
async def refill_metadata(anime_id: str, db: Session = Depends(get_db)) -> RefillResponse:
    """Ask the metadata source about this one anime again (build 123).

    The button half of the metadata backfill: *"Maybe we can add a rescan /
    refill button in the Metadata window to fix this?"*  The worker does the
    same work a few anime at a time, which is what makes a new install
    complete without anybody knowing this button exists - but somebody
    looking straight at one yellow icon should not have to wait for a cycle.

    Ignores the backfill's own timers deliberately. They exist to keep a
    background chore quiet; a press is not a chore.
    """
    from backend.app.services.metadata_fill import refill_one

    anime = get_anime_or_404(anime_id, db)
    try:
        result = await refill_one(db, anime)
    except Exception as exc:  # noqa: BLE001 - the provider is somebody else's
        db.rollback()
        # 502 rather than 500: nothing here is broken, somebody else did not
        # answer, and the interface says so differently.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # A count that has just been learned is a list that should now exist (build 207): the rows are
    # additive, nothing already there is touched.
    from backend.app.services.anime import ensure_episode_rows

    if anime.entry_mode != "basic" and anime.effective_episode_count:
        ensure_episode_rows(db, anime, anime.effective_episode_count)
    db.commit()
    if result is None:
        return RefillResponse(unanswerable=True)
    return RefillResponse(
        filled=result.filled,
        unanswered=result.unanswered,
        conflicts=len(result.conflicts),
    )


@router.get("/{anime_id}/leftovers", response_model=LeftoverResponse)
def list_leftovers(anime_id: str, db: Session = Depends(get_db)) -> LeftoverResponse:
    """The old files still on disk for this anime.

    Renaming a file and telling nobody which file it is makes "deleting it is
    the user's job" and "nobody can do that job" the same sentence. This is
    the list that fixes it.
    """
    from backend.app.services.superseded import leftovers

    get_anime_or_404(anime_id, db)
    return LeftoverResponse(
        files=[
            LeftoverFile(
                id=row.id,
                filename=row.filename,
                size_bytes=row.size_bytes,
                kind=row.kind,
                episode_number=row.episode.number if row.episode is not None else None,
            )
            for row in leftovers(db, anime_id)
        ]
    )


class LeftoverDeleteResponse(BaseModel):
    deleted: int = 0
    failed: list[str] = []


@router.delete("/{anime_id}/episodes/{episode_id}/copies/{copy_id}", status_code=204)
def delete_episode_copy(
    anime_id: str, episode_id: str, copy_id: str, db: Session = Depends(get_db)
) -> Response:
    """Delete one file next to an episode's own: a backup, a kept original, an older version.

    From the episode file window (build 189). Never the episode's own file, and
    never a copy still waiting for its replacement - that one is the only file
    the episode has until the new release lands.
    """
    from backend.app.models.superseded import SupersededFile
    from backend.app.services.duplicate_files import review
    from backend.app.services.superseded import delete_copy

    get_anime_or_404(anime_id, db)
    row = db.get(SupersededFile, copy_id)
    if row is None or row.anime_id != anime_id or row.episode_id != episode_id or row.removed_at is not None:
        raise HTTPException(status_code=404, detail="No such file")
    if row.kind == "awaiting_replacement":
        raise HTTPException(
            status_code=409,
            detail="This file is the episode's only copy until its replacement arrives",
        )
    if not delete_copy(db, row):
        raise HTTPException(status_code=409, detail=row.error or "The file could not be deleted")
    # One file left closes the duplicate case without waiting for the next pass.
    review(db)
    db.commit()
    return Response(status_code=204)


@router.delete("/{anime_id}/leftovers", response_model=LeftoverDeleteResponse)
def delete_leftovers(anime_id: str, db: Session = Depends(get_db)) -> LeftoverDeleteResponse:
    """Delete the renamed copies this anime left behind.

    Only the ones a swap renamed and left - never a file being kept until its
    replacement arrives, which is not the user's to delete yet and would
    leave an episode with nothing.

    The emptied folder goes with them, and with it the stored folder name, so
    a later download learns where the files go from where they land.
    """
    from pathlib import Path as FsPath

    from datetime import UTC, datetime

    from backend.app.services.superseded import leftovers, prune_empty_folders
    from backend.app.services.storage import roots_for, storage_roots

    anime = get_anime_or_404(anime_id, db)
    deleted = 0
    failed: list[str] = []
    for row in leftovers(db, anime_id):
        if row.kind != "renamed":
            continue
        target = FsPath(row.path)
        try:
            if target.exists():
                target.unlink()
            row.removed_at = datetime.now(UTC)
            row.error = None
            deleted += 1
        except OSError as exc:
            row.error = f"{type(exc).__name__}: {exc}"[:300]
            failed.append(row.filename)
    if deleted:
        roots = roots_for(anime, storage_roots())
        prune_empty_folders(roots.media_root, anime.media_folder)
    db.commit()
    logger.info("%s: %s leftover file(s) deleted", anime.display_name, deleted)
    return LeftoverDeleteResponse(deleted=deleted, failed=failed)


@router.get("/{anime_id}/db-dump")
def dump_anime_db(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """Debug endpoint: every record belonging to this anime, episode-oriented.

    Same serializer as the detail endpoint, so what you debug here is exactly
    what the application sees. Episode 1's releases, download, import and
    media all sit inside episode 1 instead of in four parallel lists.
    """
    from backend.app.services.serializers import serialize_anime

    anime = get_anime_or_404(anime_id, db)
    payload = serialize_anime(anime)
    payload["episodes_count"] = len(payload["episodes"])
    payload["releases_count"] = sum(len(episode["releases"]) for episode in payload["episodes"])
    payload["downloads_count"] = sum(1 for episode in payload["episodes"] if episode["download"])
    payload["media_count"] = sum(
        1 for episode in payload["episodes"] if episode["media"]["filename"]
    )
    return payload


@router.get("/{anime_id}/torrents", response_model=dict)
def anime_torrents(anime_id: str, db: Session = Depends(get_db)) -> dict:
    """How many of this anime's torrents AniFlow thinks are still in the client.

    What the delete confirmation is built from: none means nothing to ask
    about. Reported after an anime that was stalling was deleted and its
    torrents stayed behind for ever, with nothing left that knew they were
    its.
    """
    from backend.app.services.downloads import live_torrents_for

    get_anime_or_404(anime_id, db)
    rows = live_torrents_for(db, anime_id)
    return {
        "count": len(rows),
        # A few names, so the confirmation can show what it means rather than
        # only a number.
        "examples": [row.label for row in rows[:5] if row.label],
    }


@router.delete("/{anime_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_anime(
    anime_id: str,
    confirm: bool = Query(default=False),
    remove_torrents: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> Response:
    if not confirm:
        raise HTTPException(status_code=400, detail="Deletion requires confirm=true")
    anime = get_anime_or_404(anime_id, db)
    if remove_torrents:
        # Before the rows go: they carry the info hashes, and without them
        # nothing would know which torrents were this anime's. Only this
        # anime's are touched, and no files are deleted.
        from backend.app.services.downloads import remove_torrents_for

        try:
            remove_torrents_for(db, anime_id)
        except Exception as exc:  # noqa: BLE001 - never block the deletion
            logger.warning("could not remove torrents for %s: %s", anime_id, exc)
    db.delete(anime)
    db.commit()
    # An uploaded cover belongs to the anime, so it goes with it. Media files
    # are a different matter and are never touched: this deletes AniFlow's own
    # state only.
    from backend.app.services.covers import remove_cover_files

    remove_cover_files(anime_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{anime_id}/cover", include_in_schema=False)
def get_cover(anime_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """Serve the cover the user uploaded for this anime."""
    from backend.app.services.covers import cover_path

    anime = get_anime_or_404(anime_id, db)
    target = cover_path(anime_id, anime.custom_cover)
    if target is None:
        raise HTTPException(status_code=404, detail="This anime has no uploaded cover")
    # The filename carries the anime id, so a new upload replaces the old URL's
    # content. A short cache keeps the watchlist from re-fetching every render
    # while still picking up a replacement quickly.
    return FileResponse(target, headers={"Cache-Control": "public, max-age=60"})


@router.put("/{anime_id}/cover", response_model=AnimeSummary)
async def put_cover(anime_id: str, request: Request, db: Session = Depends(get_db)) -> Anime:
    """Replace this anime's artwork with an uploaded image.

    The body is the image itself rather than a multipart form: one file needs no
    form parser, and this keeps the dependency list as it is. The bytes decide
    the format - a declared content type is only a hint.
    """
    from backend.app.services.covers import CoverError, MAX_BYTES, store_cover

    anime = get_anime_or_404(anime_id, db)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES:
        # Refused before reading it into memory.
        raise HTTPException(
            status_code=413,
            detail=f"Covers are limited to {MAX_BYTES // (1024 * 1024)} MB",
        )
    data = await request.body()
    try:
        anime.custom_cover = store_cover(anime_id, data)
    except CoverError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(anime)
    return anime


@router.delete("/{anime_id}/cover", response_model=AnimeSummary)
def delete_cover(anime_id: str, db: Session = Depends(get_db)) -> Anime:
    """Drop the uploaded cover and fall back to the provider's artwork."""
    from backend.app.services.covers import remove_cover_files

    anime = get_anime_or_404(anime_id, db)
    remove_cover_files(anime_id)
    anime.custom_cover = None
    db.commit()
    db.refresh(anime)
    return anime


# -- the media check, and getting an episode again from another release ------


class ReacquirePlanResponse(BaseModel):
    """What changing the release would do to the files already there.

    Shown before the button does anything: "delete eleven files" is not
    something to find out afterwards.
    """

    mode: str
    episodes: int = 0
    files: list[str] = []
    #: Recorded but not on disk, so "nothing to delete" reads as a fact.
    missing: int = 0


@router.get("/{anime_id}/reacquire", response_model=ReacquirePlanResponse)
def reacquire_plan(
    anime_id: str,
    mode: str = Query("backup", pattern="^(replace|backup|when_replaced|keep)$"),
    db: Session = Depends(get_db),
) -> ReacquirePlanResponse:
    """What would happen to the existing files. Changes nothing."""
    from backend.app.services import reacquire

    get_anime_or_404(anime_id, db)
    found = reacquire.plan(db, anime_id, mode)
    return ReacquirePlanResponse(
        mode=mode, episodes=found.episodes, files=found.files, missing=found.missing
    )


class ReacquireRequest(BaseModel):
    """Change which release AniFlow considers correct for this anime."""

    #: replace deletes the current files; backup renames them to .backup and
    #: leaves them for the user to deal with; when_replaced leaves them alone
    #: and deletes each one once its replacement has been imported (116).
    mode: str = Field(pattern="^(replace|backup|when_replaced|keep)$")
    #: The indexer chosen in the dialog; kept on the anime so its searches ask it (item 44).
    indexer_id: str | None = Field(default=None, max_length=36)
    #: The new profile. Both are optional so a change of subgroup can keep the
    #: resolution and the other way round.
    subgroups: list[str] | None = None
    resolution: str | None = Field(default=None, max_length=8)
    #: Which copy of each episode: ["uncensored", "dual-audio"] (build 97).
    #: None leaves the stored edition alone, so a change of quality does not
    #: silently widen which copy is acceptable.
    edition: list[str] | None = None
    #: What the other copies are recognised by (build 98). None leaves the
    #: stored value alone, as with the edition itself.
    edition_not: list[str] | None = None
    #: The release name the new group publishes under, when the in-between
    #: dialog has picked one. This is what availability is matched by, so a
    #: change of group has to change it or the search would keep finding the
    #: old group's files.
    release_name: str | None = Field(default=None, max_length=300)
    #: What to ask the indexer, so the new group's back catalogue is found
    #: immediately (build 91). The same field the add flow sends, because it
    #: now runs the same step: without it, changing the group left every
    #: episode with no releases and nothing to download.
    search_query: str | None = Field(default=None, max_length=300)
    #: What this anime is called now (build 116).
    #:
    #: Asked for after a walk through a real payload: *"when you swap the
    #: subgroup we always need to update this local_name, subgroup,
    #: resolution"*. Groups spell the same season differently - one publishes
    #: "Mushoku Tensei S3", the next "Mushoku Tensei III" - and an entry
    #: labelled with the name of the group it no longer follows is the same
    #: class of bug as a release name that was never changed.
    #:
    #: The storage folders are deliberately *not* here. ``media_folder`` and
    #: ``import_folder`` are where files already are - in Shoko Server mode
    #: they are Shoko's own naming - and renaming those would orphan
    #: everything filed under them. See the model.
    local_name: str | None = Field(default=None, max_length=180)


class ReacquireResponse(BaseModel):
    cleared: int = 0
    deleted: int = 0
    backed_up: int = 0
    missing: int = 0
    #: What the old release left behind, forgotten (build 90). Reported so
    #: "it still says SubsPlease" is answerable with a number.
    releases_forgotten: int = 0
    downloads_forgotten: int = 0
    #: Releases found for the *new* group and attached to the episodes, the
    #: same way adding an anime does it (build 91).
    releases_found: int = 0
    #: Torrents still running for the old release, removed with their files.
    torrents_abandoned: int = 0
    #: Feed matches set aside so the queue pass cannot re-download them.
    feed_matches_ignored: int = 0
    #: Whether the worker was asked to look for the new release now.
    searching: bool = False
    #: Files left on disk with a note against them, to be deleted when the
    #: new release has actually been imported (build 116).
    remembered: int = 0
    #: Files left in place for good, as kept originals (build 189).
    kept: int = 0
    #: Files that could not be deleted or renamed. Their episodes are left
    #: alone on purpose - a cleared row pointing at a file still in the
    #: library is worse than either outcome asked for.
    failed: list[str] = []


@router.post("/{anime_id}/reacquire", response_model=ReacquireResponse)
def reacquire_anime(
    anime_id: str,
    payload: ReacquireRequest,
    db: Session = Depends(get_db),
) -> ReacquireResponse:
    """Clear the episodes, deal with the files, and set the new profile.

    Downloads nothing itself. The episodes come back as MISSING and the normal
    search path picks them up, which is the same route a new anime takes -
    one pipeline, one set of rules.
    """
    from sqlalchemy import select

    from backend.app.models.release_profile import ReleaseProfile
    from backend.app.services import reacquire

    anime = get_anime_or_404(anime_id, db)

    # The new label, decided before anything is touched (build 116). Checked
    # here rather than where it is applied, because everything below this
    # deletes files and clears episodes: a name that cannot be used has to
    # stop the swap while stopping it still means nothing has happened.
    renamed: str | None = None
    if payload.local_name is not None and payload.local_name.strip():
        from backend.app.services.anime import normalize_local_name

        try:
            renamed = normalize_local_name(payload.local_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if renamed != anime.local_name:
            clash = db.scalar(
                select(Anime).where(Anime.local_name == renamed, Anime.id != anime.id)
            )
            if clash is not None:
                # local_name is unique. Left to the database this arrived as a
                # 500, which reads as "AniFlow is broken" rather than as "that
                # name belongs to another season".
                raise HTTPException(
                    status_code=409,
                    detail=f"Another anime is already called {renamed}",
                )

    # The client, so a download still running for the old release can be
    # stopped rather than left to arrive. Best effort: an unreachable client
    # must not stop the rest of the change, and the result says how many
    # torrents were actually dealt with.
    client = None
    try:
        from backend.app.services.app_settings import load_app_settings
        from backend.app.services.downloads import _client_from

        client = _client_from(load_app_settings(db), None)
    except Exception as exc:  # noqa: BLE001 - no client is a normal state
        logger.info("no torrent client for the release change: %s", exc)

    try:
        result = reacquire.run(db, anime_id, payload.mode, client=client)
    finally:
        if client is not None:
            client.close()

    profile = db.scalars(
        select(ReleaseProfile).where(ReleaseProfile.anime_id == anime_id)
    ).first()
    if profile is None:
        profile = ReleaseProfile(anime_id=anime_id)
        db.add(profile)
    if payload.subgroups is not None:
        profile.subgroups = payload.subgroups
    if payload.resolution is not None:
        profile.resolution = payload.resolution or None
    if payload.edition is not None:
        profile.edition = list(payload.edition)
    if payload.edition_not is not None:
        profile.edition_not = list(payload.edition_not)
    if payload.release_name is not None:
        anime.release_name = payload.release_name or None
    apply_indexer(db, anime, payload.indexer_id)
    if renamed is not None:
        # The label follows the new group - checked at the top, applied here.
        # The storage folders deliberately stay: they are where the files
        # already are, and in Shoko Server mode they are Shoko's own naming.
        anime.local_name = renamed
    db.commit()

    # The step that adding an anime does and changing its release did not
    # (build 91): re-run the dialog's query against the indexer for the new
    # group and quality, and attach what it finds. This is what fills the
    # episode list with magnets; without it every episode sat at "the media
    # file is gone" with nothing to offer, which is how this was reported.
    #
    # Best effort, exactly as it is on the add path: an indexer that is
    # unreachable must not undo a release change that has already happened,
    # and the worker pass below is the second chance.
    found = 0
    query = (payload.search_query or "").strip() or anime.release_name or anime.display_name
    if query:
        try:
            from backend.app.services.discovery import persist_available_from_preview

            found = persist_available_from_preview(
                db,
                anime,
                search_query=query,
                # Matched by release name, never by a folder the user renamed.
                selected_folder=anime.release_name or query,
                subgroup=(payload.subgroups or [None])[0],
                resolution=payload.resolution,
            )
        except Exception as exc:  # noqa: BLE001 - availability is best-effort
            logger.info("could not look for the new release yet: %s", exc)

    # And ask the worker as well, which covers the anime whose indexer was
    # unreachable a moment ago and hands over whatever is now available.
    if result.backed_up:
        # Named in the log, because a renamed file nobody is told about is
        # the manual step nobody knows they have (build 119).
        from backend.app.services.attention import log_activity
        from backend.app.services.superseded import leftovers

        kept = [row.filename for row in leftovers(db, anime.id) if row.kind == "renamed"]
        log_activity(
            db,
            kind="FILES_RENAMED",
            message=(
                f"{anime.display_name}: {result.backed_up} old file(s) renamed and"
                f" left for you - {', '.join(kept[:5])}"
                + (" and more" if len(kept) > 5 else "")
            ),
            details={"files": kept},
            anime_id=anime.id,
        )
        db.commit()

    searching = worker_state.request_run("anime_search")
    worker_state.request_run("queue_downloads")

    return ReacquireResponse(
        cleared=result.cleared,
        deleted=result.deleted,
        backed_up=result.backed_up,
        missing=result.missing,
        failed=result.failed,
        remembered=result.remembered,
        kept=result.kept,
        releases_forgotten=result.releases_forgotten,
        downloads_forgotten=result.downloads_forgotten,
        releases_found=found,
        torrents_abandoned=result.torrents_abandoned,
        feed_matches_ignored=result.feed_matches_ignored,
        searching=searching,
    )
