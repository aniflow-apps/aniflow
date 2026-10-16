"""RSS Analyze API."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.analysis import AliasSource, AnalysisRun

router = APIRouter()


class AnalyzeRequest(BaseModel):
    # Empty means every enabled feed. The per-anime feed check sends nothing,
    # because which feed to read is not a question the user should answer.
    source_ids: list[str] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=1000, pattern="^https?://")


class AnalyzeRunResponse(BaseModel):
    __test__ = False
    run_id: str
    found: int
    exact: int
    high: int
    review: int
    unknown: int
    duration_ms: int
    items: list[dict]


class LearnAliasRequest(BaseModel):
    anime_id: str = Field(min_length=1)
    raw_name: str = Field(min_length=1, max_length=500)


class LearnAliasResponse(BaseModel):
    alias: str


class AnalysisRunHeader(BaseModel):
    id: str
    started_at: datetime
    duration_ms: int
    sources: list[str]
    found: int
    exact: int
    high: int
    review: int
    unknown: int

    model_config = ConfigDict(from_attributes=True)


@router.post("/run", response_model=AnalyzeRunResponse)
def run_analyze(request: AnalyzeRequest) -> AnalyzeRunResponse:
    """Analyze the selected feeds against all active anime. Read-only."""
    from backend.app.db.session import SessionLocal
    from backend.app.services.analyzer import run_analysis

    return run_analysis(SessionLocal, request.source_ids, extra_url=request.url)


@router.get("/runs", response_model=list[AnalysisRunHeader])
def recent_runs(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return list(db.scalars(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(limit)))


@router.post("/learn", response_model=LearnAliasResponse)
def learn_alias(request: LearnAliasRequest, db: Session = Depends(get_db)):
    """Learn a normalized title alias from a manually assigned release."""
    from backend.app.services.analyzer import learn_alias as _learn

    alias = _learn(db, anime_id=request.anime_id, raw_name=request.raw_name, source=AliasSource.LEARNED)
    return {"alias": alias}
