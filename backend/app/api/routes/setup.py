"""The install wizard's endpoints (build 137).

Four calls, and only because nothing else could do these four things. The
account is still ``POST /api/auth/setup``, the Shoko connection is still
``PATCH /api/settings``, a feed is still ``POST /api/rss/sources`` - the
wizard is a path through what AniFlow already has, not a second way to
configure it.

**Reachability.** These sit behind the session like every other route, and
that is not an oversight: the account is created first, and creating it logs
the browser in, so by the time the wizard asks anything here it has a session.
The one question that has to be answerable without one - *is there an account
at all* - is already answered by the open ``GET /api/auth/state``.

**They close when setup is over.** Recording the storage mode and writing into
the configuration folder are first-run powers; leaving them open afterwards
would be a way to change the library's mode from a browser, which core/mode.py
spends its whole docstring explaining must not happen.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.services import setup as service

router = APIRouter()


class SetupStateResponse(BaseModel):
    completed: bool
    needs_account: bool
    mode: str
    mode_recorded: bool
    has_indexer: bool
    indexer_names: list[str]
    shoko_configured: bool
    feeds: int
    terms_accepted: bool


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(standalone|shokoserver)$")


class IndexerRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    #: The file's text, read in the browser. See services/setup.add_indexer.
    content: str = Field(min_length=1, max_length=200_000)


class CompletedResponse(BaseModel):
    completed: bool
    at: str


def _while_setting_up(db: Session = Depends(get_db)) -> Session:
    """Refuse once setup is finished, and say so plainly.

    409 rather than 404: the endpoint exists, the moment for it has passed.
    """
    if service.is_complete(db):
        raise HTTPException(status_code=409, detail="Setup is already finished")
    return db


@router.get("/state", response_model=SetupStateResponse)
def read_state(db: Session = Depends(get_db)) -> SetupStateResponse:
    """Where setup has got to. Readable after it is over, so the app can tell."""
    return SetupStateResponse(**service.state(db).as_dict())


@router.post("/mode", response_model=SetupStateResponse)
def choose_mode(
    payload: ModeRequest, db: Session = Depends(_while_setting_up)
) -> SetupStateResponse:
    """Record which program organises the library. Once, and never again."""
    try:
        service.choose_mode(db, payload.mode)
    except service.SetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SetupStateResponse(**service.state(db).as_dict())


@router.post("/indexer", response_model=SetupStateResponse)
def add_indexer(
    payload: IndexerRequest, db: Session = Depends(_while_setting_up)
) -> SetupStateResponse:
    """Accept an indexer file, which is what unlocks Automate."""
    try:
        service.add_indexer(db, filename=payload.filename, content=payload.content)
    except service.SetupError as exc:
        # 400 with the reason: every one of these is something the user can
        # fix in the file they just chose.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The configuration folder could not be written: {exc}",
        ) from exc
    return SetupStateResponse(**service.state(db).as_dict())


@router.post("/complete", response_model=CompletedResponse)
def complete(db: Session = Depends(get_db)) -> CompletedResponse:
    """Finish. Idempotent, so a double-click cannot fail."""
    from backend.app.models.auth import User
    from sqlalchemy import select

    if db.scalar(select(User.id).limit(1)) is None:
        # Without an account there is nothing to log in to afterwards, and the
        # wizard would have nothing to hand over to.
        raise HTTPException(status_code=400, detail="Create the account first")
    return CompletedResponse(completed=True, at=service.complete(db))
