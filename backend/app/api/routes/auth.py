"""Getting in, and staying in (build 72).

Five endpoints, and all of them reachable without a session - they have to be,
or there would be no way to acquire one. Everything else in the API is closed
by the middleware in ``main.py``; the list of open paths lives there, in one
place, rather than being a decision each route makes for itself.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.services.auth import (
    COOKIE_NAME,
    gate_confirm,
    SESSION_DAYS,
    AuthError,
    change_credentials,
    create_account,
    issue_token,
    login,
    logout,
    reset_password,
    session_user,
    state,
)

router = APIRouter()


class AuthStateResponse(BaseModel):
    """What the login screen decides its shape from."""

    needs_setup: bool
    authenticated: bool
    username: str | None = None


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    #: The one-time code from the log.
    token: str = Field(min_length=8, max_length=100)
    #: The version of the first-run notice the person agreed to (services/terms.py).
    terms_version: str | None = Field(default=None, max_length=40)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class ResetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)
    token: str = Field(min_length=8, max_length=100)


class AccountRequest(BaseModel):
    """Changing your own details from inside the app."""

    current_password: str = Field(min_length=1, max_length=200)
    username: str | None = Field(default=None, min_length=3, max_length=80)
    new_password: str | None = Field(default=None, min_length=8, max_length=200)


def _set_cookie(response: Response, token: str, request: Request) -> None:
    """Hand the browser its session.

    ``secure`` follows the scheme the request actually arrived on: forcing it
    on would break the plain-HTTP LAN deployment this is written for, and
    hardcoding it off would keep it off behind a reverse proxy that does have
    TLS. ``lax`` lets a normal visit carry the cookie while a cross-site POST
    does not, which is the CSRF protection a cookie-only API needs.
    """
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@router.get("/diagnostics")
def diagnostics_for_the_wizard(request: Request, db: Session = Depends(get_db)) -> Response:
    """The diagnostics zip, for a setup that is failing (build 270).

    The wizard is the one place a person has no session yet, and it is exactly where someone who cannot get past it
    needs to send a report. So this is open while **no account exists** - there is nothing to protect yet, and the
    bundle holds no password, key or token - and an ordinary session is needed from the moment there is one. The
    account is made on the wizard's first step, so a failure on any later step still has a session to download with.
    """
    refusal = gate_confirm(db, request.cookies.get(COOKIE_NAME))
    if refusal is not None and refusal != "setup_required":
        raise HTTPException(status_code=401, detail=refusal)

    from backend.app.services import diagnostics

    return Response(
        content=diagnostics.build_bundle(db),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{diagnostics.bundle_name()}"'},
    )


@router.get("/state", response_model=AuthStateResponse)
def read_state(request: Request, db: Session = Depends(get_db)) -> AuthStateResponse:
    """Whether anyone is set up, and whether this browser is logged in."""
    return AuthStateResponse(**vars(state(db, request.cookies.get(COOKIE_NAME))))


@router.post("/setup", response_model=AuthStateResponse)
def setup(
    request: Request,
    response: Response,
    payload: SetupRequest,
    db: Session = Depends(get_db),
) -> AuthStateResponse:
    """Create the one account, and log this browser straight in."""
    from backend.app.services import terms

    if payload.terms_version != terms.TERMS_VERSION:
        # The notice is the first screen of the wizard; a call that did not come
        # through it has not agreed to anything.
        raise HTTPException(status_code=400, detail="Read and accept the notice before creating the account")
    try:
        user = create_account(
            db, username=payload.username, password=payload.password, token=payload.token
        )
        token = login(db, username=user.username, password=payload.password)
    except AuthError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    terms.record(db)
    db.commit()
    _set_cookie(response, token, request)
    return AuthStateResponse(needs_setup=False, authenticated=True, username=user.username)


@router.post("/login", response_model=AuthStateResponse)
def sign_in(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> AuthStateResponse:
    import time

    from backend.app.services.auth import FAILURE_DELAY_SECONDS

    try:
        token = login(db, username=payload.username, password=payload.password)
    except AuthError as exc:
        # The attempt counter has to survive the failure, so this commits.
        db.commit()
        # A pause costs a person nothing and costs a script everything.
        time.sleep(FAILURE_DELAY_SECONDS)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    db.commit()
    _set_cookie(response, token, request)
    return AuthStateResponse(needs_setup=False, authenticated=True, username=payload.username.strip())


@router.post("/logout", response_model=AuthStateResponse)
def sign_out(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> AuthStateResponse:
    """End this session only. Other browsers stay logged in."""
    logout(db, request.cookies.get(COOKIE_NAME))
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return AuthStateResponse(needs_setup=False, authenticated=False)


@router.post("/forgot")
def forgot(db: Session = Depends(get_db)) -> dict[str, str]:
    """Put a fresh one-time code in the log.

    Anyone who can reach the login page can make the log print a code, which
    is deliberate and harmless: the code is only useful to someone who can
    *read* the log, and that person can already read the database. What it is
    not is a way to find out whether an account exists - the answer is the
    same either way.
    """
    issue_token(db, "reset")
    db.commit()
    return {"detail": "A one-time code was written to the log"}


@router.post("/reset", response_model=AuthStateResponse)
def reset(
    request: Request,
    response: Response,
    payload: ResetRequest,
    db: Session = Depends(get_db),
) -> AuthStateResponse:
    """Set a new password, and log this browser in with it."""
    try:
        user = reset_password(db, password=payload.password, token=payload.token)
        username = user.username
        token = login(db, username=username, password=payload.password)
    except AuthError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    _set_cookie(response, token, request)
    return AuthStateResponse(needs_setup=False, authenticated=True, username=username)


@router.post("/account", response_model=AuthStateResponse)
def account(
    request: Request, payload: AccountRequest, db: Session = Depends(get_db)
) -> AuthStateResponse:
    """Change your username or password while logged in."""
    user = session_user(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Log in first")
    try:
        change_credentials(
            db,
            user,
            current_password=payload.current_password,
            username=payload.username,
            new_password=payload.new_password,
        )
    except AuthError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return AuthStateResponse(needs_setup=False, authenticated=True, username=user.username)
