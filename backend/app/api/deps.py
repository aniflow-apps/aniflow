"""What every API request has to satisfy before it runs (build 72).

One dependency, attached to the API router itself rather than to each route,
so a route added next month is protected without anyone remembering to
protect it. That direction matters: the settings-payload bug in build 70 was
exactly what "remember to add it to the list" looks like when someone forgets,
and forgetting here would publish a page rather than fail to save a field.

**The database is only asked when the answer is not already known**
(build 84). A confirmed cookie is trusted for a few seconds - see
services/auth.py:GATE_SECONDS. That is not an optimisation for its own sake:
this dependency is attached to every route, a page of two hundred posters is
two hundred requests, and each one running two queries emptied a connection
pool of fifteen and left the whole application answering nothing for thirty
seconds at a time. The injected session is still declared, because on a miss
it is what does the checking - and creating a Session acquires no connection
until something queries it, so a cache hit costs nothing at all.

Two paths cannot require a session, because requiring one would make them
useless:

``/api/auth/*``
    There would be no way to acquire a session otherwise.

``/api/system/health``
    Docker's HEALTHCHECK calls it every thirty seconds with no cookie. It
    reports status, database and worker, and nothing about the library.
"""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.db.session import get_db

#: Exactly what works without logging in. Written as data so a test can read
#: it and check that nothing else does.
#: The active theme's stylesheet is open (build 90) because the login page
#: is shown before there is a session, and without it somebody running the
#: bright theme meets a dark login screen and concludes AniFlow is broken.
#: It answers with colour values and nothing else - no theme name, no author,
#: no path on the server.
OPEN_PATHS = ("/api/system/health", "/api/themes/active.css")
#: ``/api/tv/`` (build 181) is the television API. It is open to the *cookie*
#: gate and closed by its own: every route under it demands a TV token
#: (api/routes/tv.py:require_tv_token), and a TV token opens nothing outside
#: it. The token-management routes are ``/api/tv-devices`` - no trailing slash,
#: so they are *not* under this prefix and stay behind a session.
OPEN_PREFIXES = ("/api/auth/", "/api/tv/")


def is_open(path: str) -> bool:
    """Whether this path is reachable without a session."""
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def require_session(request: Request, db: Session = Depends(get_db)) -> None:
    """Refuse the request unless it carries a live session.

    The two failures are told apart on purpose. ``setup_required`` means there
    is no account at all, so the browser shows the setup page rather than a
    login form for an account nobody has created; ``login_required`` means
    there is one and this browser is not in it.
    """
    if is_open(request.url.path):
        return

    from backend.app.services import auth

    token = request.cookies.get(auth.COOKIE_NAME)
    if auth.gate_allows(token):
        return
    refusal = auth.gate_confirm(db, token)
    if refusal is not None:
        raise HTTPException(status_code=401, detail=refusal)


__all__ = ["OPEN_PATHS", "OPEN_PREFIXES", "is_open", "require_session"]
