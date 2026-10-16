"""The season rollover for the watchlist's banner and review window (build 205).

See docs/season-rollover-plan.md. Read-only except pinning: the moves
themselves go through ``POST /api/libraries/move``, which the review window
shares with the Libraries page.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.services import season_rollover

router = APIRouter()


class RolloverItem(BaseModel):
    anime_id: str
    name: str
    #: finished | waiting | running
    reason: str
    from_library_id: int


class RolloverGroup(BaseModel):
    #: The library they should go to; null when the user has to choose.
    target_id: int | None = None
    items: list[RolloverItem] = Field(default_factory=list)


class RolloverResponse(BaseModel):
    #: ask | auto | off. Only "ask" lists anything: automatic mode has already
    #: moved what it could, and off asks for nothing.
    mode: str
    earlier: RolloverGroup = Field(default_factory=RolloverGroup)
    airing: RolloverGroup = Field(default_factory=RolloverGroup)


class PinBody(BaseModel):
    anime_ids: list[str] = Field(min_length=1, max_length=500)
    pinned: bool = True


@router.get("", response_model=RolloverResponse)
def get_rollover(db: Session = Depends(get_db)) -> RolloverResponse:
    chosen = season_rollover.mode()
    answer = RolloverResponse(mode=chosen)
    if chosen != "ask":
        return answer
    for proposal in season_rollover.proposals(db):
        group = answer.airing if proposal.group == "airing" else answer.earlier
        group.target_id = proposal.target_id
        group.items.append(
            RolloverItem(
                anime_id=proposal.anime_id,
                name=proposal.name,
                reason=proposal.reason,
                from_library_id=proposal.from_library_id,
            )
        )
    return answer


@router.post("/pin")
def pin(body: PinBody, db: Session = Depends(get_db)) -> dict[str, int]:
    """"Keep here": the rollover leaves these anime where they are (or takes that back)."""
    return {"changed": season_rollover.pin(db, body.anime_ids, body.pinned)}
