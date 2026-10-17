"""Search API endpoints for release discovery through configured indexers."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field

from backend.app.db.session import get_db
from backend.app.models.anime import Anime
from backend.app.models.search_result import SearchResult as SearchResultModel
from backend.app.worker.runtime import worker_state

router = APIRouter()


class SearchTriggerRequest(BaseModel):
    anime_id: str | None = None
    query: str | None = Field(default=None, max_length=500)
    max_pages: int | None = Field(default=None, ge=1, le=10)
    max_results: int | None = Field(default=None, ge=1, le=1000)


class SearchTriggerResponse(BaseModel):
    started: bool


class SearchResultResponse(BaseModel):
    id: str
    provider: str
    provider_id: str
    query_used: str
    page: int
    raw_name: str
    torrent_url: str | None
    magnet_url: str | None
    size: int | None
    seeders: int | None
    leechers: int | None
    published_at: datetime | None
    category: str | None
    parsed_title: str | None
    subgroup: str | None
    episode: str | None
    version: int
    resolution: str | None
    media_source: str | None
    decision: str
    anime_id: str | None
    failure_reasons: list[str]
    ambiguous_anime_ids: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SearchStatusResponse(BaseModel):
    search_enabled: bool
    searching: bool
    search_requested: bool
    last_search_summary: dict[str, object] | None
    last_search_error: str | None


@router.post("/trigger", response_model=SearchTriggerResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_search(request: SearchTriggerRequest, db: Session = Depends(get_db)) -> SearchTriggerResponse:
    """Trigger a manual search.

    - If anime_id is provided, search only for that anime.
    - If query is provided, use it as raw search query (overrides template).
    - Otherwise searches all active/upcoming anime with profiles.
    """
    if request.anime_id is not None:
        anime = db.get(Anime, request.anime_id)
        if anime is None:
            raise HTTPException(status_code=404, detail="Anime not found")
        if anime.profile is None:
            raise HTTPException(status_code=409, detail="Anime has no release profile")
    if not worker_state.request_run("anime_search"):
        raise HTTPException(status_code=409, detail="A search is already in progress")
    return SearchTriggerResponse(started=True)


@router.get("/status", response_model=SearchStatusResponse)
def search_status(db: Session = Depends(get_db)) -> SearchStatusResponse:
    from backend.app.services.app_settings import load_app_settings
    cfg = load_app_settings(db)
    state = worker_state.tasks["anime_search"]
    return SearchStatusResponse(
        search_enabled=cfg.search_enabled,
        searching=state.running,
        search_requested=state.requested,
        last_search_summary=state.last_result,
        last_search_error=state.last_error,
    )


@router.get("/results", response_model=list[SearchResultResponse])
def list_results(
    limit: int = Query(default=100, ge=1, le=200),
    decision: str | None = None,
    anime_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[SearchResultResponse]:
    from sqlalchemy import select
    from backend.app.models.anime import Anime

    statement = select(SearchResultModel, Anime.display_name).outerjoin(Anime, SearchResultModel.anime_id == Anime.id)
    if decision is not None:
        statement = statement.where(SearchResultModel.decision == decision.upper())
    if anime_id is not None:
        statement = statement.where(SearchResultModel.anime_id == anime_id)
    statement = statement.order_by(SearchResultModel.created_at.desc()).limit(limit)
    rows = db.execute(statement).all()
    results: list[SearchResultResponse] = []
    for result, anime_name in rows:
        payload = SearchResultResponse.model_validate(result)
        payload.anime_name = anime_name
        results.append(payload)
    return results


