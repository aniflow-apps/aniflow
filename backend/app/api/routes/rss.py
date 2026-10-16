"""RSS source management, scan triggering and release history."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.anime import Anime
from backend.app.models.indexer import Indexer
from backend.app.services.config_sync import FEEDS, forget_file, sync_from_files, write_back
from backend.app.models.rss import FeedSource, Release
from backend.app.schemas.rss import (
    FeedSourceCreate,
    FeedSourceResponse,
    FeedSourceUpdate,
    ReleaseResponse,
    ReleasesClearedResponse,
    RssStatusResponse,
    ScanTriggerResponse,
)
from backend.app.services.app_settings import load_app_settings
from backend.app.worker.runtime import worker_state

router = APIRouter()


def _get_source_or_404(source_id: str, db: Session) -> FeedSource:
    source = db.get(FeedSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Feed source not found")
    return source


def _refuse_if_managed(source: FeedSource) -> None:
    """A source an indexer owns is changed through that indexer.

    Editing it here would let the two descriptions of the same site drift
    apart, and deleting it would leave the indexer claiming a feed that no
    longer exists. Making it the default is still the user's call.
    """
    if source.indexer_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "This feed comes from an indexer. Change it on the Indexer Profiles "
                "page, or switch the indexer's RSS option off to remove it."
            ),
        )


def _ensure_single_default(db: Session) -> None:
    """With exactly one feed source it is always the default."""
    sources = list(db.scalars(select(FeedSource)))
    if len(sources) == 1:
        sources[0].is_default = True


@router.get("/sources", response_model=list[FeedSourceResponse])
def list_sources(db: Session = Depends(get_db)) -> list[FeedSourceResponse]:
    """Every configured feed, each with what it has actually produced so far.

    The counts are derived from the release history instead of being stored, so
    they cannot drift away from the releases themselves.
    """
    # A file dropped into the rss_source folder is meant to appear here.
    sync_from_files(db)
    order = (FeedSource.is_default.desc(), FeedSource.name.asc())
    sources = list(db.scalars(select(FeedSource).order_by(*order)))
    stats = {
        source_id: (count, last_at)
        for source_id, count, last_at in db.execute(
            select(Release.feed_source_id, func.count(Release.id), func.max(Release.created_at))
            .group_by(Release.feed_source_id)
        ).all()
    }
    # A feed that reports plenty and matches nothing is the case worth seeing,
    # so the matches are counted separately rather than inferred.
    matches = {
        source_id: (count, last_at)
        for source_id, count, last_at in db.execute(
            select(Release.feed_source_id, func.count(Release.id), func.max(Release.created_at))
            .where(Release.anime_id.is_not(None))
            .group_by(Release.feed_source_id)
        ).all()
    }
    indexer_names = {
        indexer_id: name
        for indexer_id, name in db.execute(select(Indexer.id, Indexer.name)).all()
    }
    payloads: list[FeedSourceResponse] = []
    for source in sources:
        count, last_at = stats.get(source.id, (0, None))
        matched, last_match = matches.get(source.id, (0, None))
        payload = FeedSourceResponse.model_validate(source)
        payload.indexer_name = indexer_names.get(source.indexer_id or "")
        payload.release_count = count
        payload.last_release_at = last_at
        payload.matched_count = matched
        payload.last_match_at = last_match
        payloads.append(payload)
    return payloads


@router.post("/sources", response_model=FeedSourceResponse, status_code=status.HTTP_201_CREATED)
def create_source(request: FeedSourceCreate, db: Session = Depends(get_db)) -> FeedSource:
    existing_defaults = db.scalars(select(FeedSource).where(FeedSource.is_default.is_(True))).all()
    is_default = request.is_default or not existing_defaults  # first source auto-default
    if is_default:
        for other in existing_defaults:
            other.is_default = False
    source = FeedSource(name=request.name, url=str(request.url), is_default=is_default)
    db.add(source)
    try:
        db.flush()
        write_back(db, FEEDS, FeedSource)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This feed URL is already configured") from exc
    db.refresh(source)
    return source


@router.patch("/sources/{source_id}", response_model=FeedSourceResponse)
def update_source(source_id: str, request: FeedSourceUpdate, db: Session = Depends(get_db)) -> FeedSource:
    source = _get_source_or_404(source_id, db)
    # Which feed is the default is the user's choice even for a managed one;
    # its name, URL and enabled state belong to the indexer that owns it.
    if any(value is not None for value in (request.name, request.url, request.enabled)):
        _refuse_if_managed(source)
    if request.name is not None:
        source.name = request.name
    if request.url is not None:
        source.url = str(request.url)
    if request.enabled is not None:
        source.enabled = request.enabled
    if request.is_default:
        for other in db.scalars(select(FeedSource).where(FeedSource.id != source_id)):
            other.is_default = False
        source.is_default = True
    try:
        db.flush()
        write_back(db, FEEDS, FeedSource)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This feed URL is already configured") from exc
    db.refresh(source)
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: str, db: Session = Depends(get_db)) -> Response:
    source = _get_source_or_404(source_id, db)
    _refuse_if_managed(source)
    forget_file(FEEDS, source)
    # Discovered releases keep their history through ON DELETE SET NULL.
    db.delete(source)
    _ensure_single_default(db)
    # Deleting the default promotes another one, and that promotion has to
    # reach its file too - otherwise the next read puts the old default back.
    db.flush()
    write_back(db, FEEDS, FeedSource)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/releases", response_model=list[ReleaseResponse])
def list_releases(
    limit: int = Query(default=100, ge=1, le=500),
    decision: str | None = Query(
        default=None,
        description="One or more ReleaseDecision values, comma-separated "
        "(e.g. MATCHED,REJECTED). Omitted: every decision.",
    ),
    tracked: bool | None = Query(
        default=None,
        description="true: only releases that landed on a watchlist anime; "
        "false: only the untracked remainder; omitted: everything.",
    ),
    include_cleared: bool = Query(
        default=False,
        description="Include rows the user cleared off the list. They are kept "
        "as the feed's de-duplication memory, not shown by default.",
    ),
    db: Session = Depends(get_db),
) -> list[ReleaseResponse]:
    """The discovery history, newest first - the debug view for a feed.

    A feed carries the whole season, so most rows are untracked noise. The
    ``tracked`` filter is what makes the list readable: it separates the
    releases that reached a watchlist anime from everything else, and
    ``decision`` narrows it further - the page's own Archive asks for
    ``MATCHED,REJECTED`` explicitly (169) rather than leaning on ``tracked``
    alone happening to mean the same two decisions today.
    """
    statement = (
        select(Release, Anime.display_name, FeedSource.name)
        .outerjoin(Anime, Release.anime_id == Anime.id)
        .outerjoin(FeedSource, Release.feed_source_id == FeedSource.id)
    )
    if not include_cleared:
        statement = statement.where(Release.cleared_at.is_(None))
    if decision is not None:
        wanted = [value.strip().upper() for value in decision.split(",") if value.strip()]
        if wanted:
            statement = statement.where(Release.decision.in_(wanted))
    if tracked is True:
        statement = statement.where(Release.anime_id.is_not(None))
    elif tracked is False:
        statement = statement.where(Release.anime_id.is_(None))
    statement = statement.order_by(Release.created_at.desc()).limit(limit)
    results: list[ReleaseResponse] = []
    for release, anime_name, source_name in db.execute(statement).all():
        payload = ReleaseResponse.model_validate(release)
        payload.anime_name = anime_name
        payload.source_name = source_name
        results.append(payload)
    return results


@router.delete("/releases", response_model=ReleasesClearedResponse)
def clear_releases(db: Session = Depends(get_db)) -> ReleasesClearedResponse:
    """Clear the discovery list.

    The rows stay in the database on purpose: a scan recognises an item it has
    already seen by its guid, so deleting them would make the whole feed window
    look like new finds on the next scan - and every already-imported episode
    among them would produce another "skipped: duplicate" download row. This
    marks them cleared instead, which is the same contract as Clear on the
    Downloads page: the list is tidied, nothing else changes. Releases still
    waiting to be queued keep their place in the queue.
    """
    cleared = db.execute(
        update(Release)
        .where(Release.cleared_at.is_(None))
        .values(cleared_at=datetime.now(UTC))
    ).rowcount
    db.commit()
    return ReleasesClearedResponse(cleared=int(cleared or 0))


@router.post("/scan", response_model=ScanTriggerResponse)
def trigger_scan() -> ScanTriggerResponse:
    """Queue a manual scan. The worker performs it; handlers never block."""
    started = worker_state.request_run("rss_scan")
    return ScanTriggerResponse(started=started)


@router.get("/status", response_model=RssStatusResponse)
def scan_status(db: Session = Depends(get_db)) -> RssStatusResponse:
    # The interval the worker actually honours is the stored one, not the
    # environment default it falls back to.
    settings = load_app_settings(db)
    state = worker_state.tasks["rss_scan"]
    return RssStatusResponse(
        worker_running=worker_state.running,
        scanning=state.running,
        scan_requested=state.requested,
        last_scan_at=state.last_run_at,
        next_scan_at=state.next_run_at,
        last_scan_error=state.last_error,
        last_scan_summary=state.last_result,
        interval_minutes=settings.rss_scan_interval_minutes,
        enabled=settings.rss_enabled,
    )


class FeedCoverageResponse(BaseModel):
    feed_id: str | None = None
    feed_name: str
    items: int
    last_seen: datetime | None = None


class SubgroupRowResponse(BaseModel):
    subgroup: str
    anime: int
    episodes: int
    wanted_by: int
    feeds: list[FeedCoverageResponse]
    seen_on_a_feed: bool


class SilentAnimeResponse(BaseModel):
    anime_id: str
    name: str
    last_seen: datetime | None = None


class SubgroupOverviewResponse(BaseModel):
    subgroups: list[SubgroupRowResponse]
    silent_anime: list[SilentAnimeResponse]
    days: int
    history_since: datetime | None = None
    feeds: int


@router.get("/subgroups", response_model=SubgroupOverviewResponse)
def subgroup_overview(
    days: int = Query(default=14, ge=1, le=365), db: Session = Depends(get_db)
) -> SubgroupOverviewResponse:
    """Which subgroups you use, which feeds have shown them, and which airing
    anime no feed has mentioned lately. Read from stored history; nothing is
    fetched. See services/subgroup_overview.py for what it can and cannot say."""
    from backend.app.services import subgroup_overview as overview

    result = overview.build(db, days=days)
    return SubgroupOverviewResponse(
        subgroups=[
            SubgroupRowResponse(
                subgroup=row.subgroup,
                anime=row.anime,
                episodes=row.episodes,
                wanted_by=row.wanted_by,
                feeds=[
                    FeedCoverageResponse(
                        feed_id=f.feed_id, feed_name=f.feed_name, items=f.items, last_seen=f.last_seen
                    )
                    for f in row.feeds
                ],
                seen_on_a_feed=row.seen_on_a_feed,
            )
            for row in result.subgroups
        ],
        silent_anime=[
            SilentAnimeResponse(anime_id=a.anime_id, name=a.name, last_seen=a.last_seen)
            for a in result.silent_anime
        ],
        days=result.days,
        history_since=result.history_since,
        feeds=result.feeds,
    )
