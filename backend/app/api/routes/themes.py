"""What the interface looks like (build 90).

Three endpoints, and the split between them is about who may ask.

``GET /api/themes``
    Every theme AniFlow can see, with its metadata and anything wrong with
    it. Needs a session: it names the folders on the server and reports the
    contents of files in them.

``GET /api/themes/{slug}.css``
    One theme as a stylesheet. Needs a session.

``GET /api/themes/active.css``
    The selected theme as a stylesheet, and the one endpoint here that is
    open. That is deliberate: without it the login page - the one page shown
    before there is a session - keeps the bundled dark palette, so somebody
    running the bright theme meets a dark screen and reasonably concludes
    something is broken. What it discloses is a list of colour values and
    nothing else; the theme's name, author and file path are not in it -
    which is what ``credits=False`` below is for, and what a test in
    test_auth.py holds this endpoint to.
"""
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from backend.app.core.logging import for_component
from backend.app.services import themes
from backend.app.services.app_settings import load_app_settings

logger = for_component("Themes")

router = APIRouter()


class ThemeResponse(BaseModel):
    """One theme, as the Settings page shows it."""

    slug: str
    name: str
    author: str
    version: str
    description: str
    #: Which theme format the file states. Compared exactly against the one
    #: this build understands.
    themes_api: int
    #: True for the themes that ship with AniFlow.
    builtin: bool
    #: How many tokens it actually sets. A theme may set as few as one.
    tokens: int
    #: Whether it can be selected at all.
    usable: bool
    #: Why not, when it cannot - and any values that were dropped. Shown
    #: rather than hidden: a theme that disappears from the list because of a
    #: typo is a theme nobody can fix.
    problems: list[str]


class ThemeListResponse(BaseModel):
    themes: list[ThemeResponse]
    #: What this build understands, so the page can say "written for v2, this
    #: is v1" without guessing.
    themes_api: int
    #: Where to put your own.
    folder: str


def _as_response(theme: themes.Theme) -> ThemeResponse:
    return ThemeResponse(
        slug=theme.slug,
        name=theme.name,
        author=theme.author,
        version=theme.version,
        description=theme.description,
        themes_api=theme.themes_api,
        builtin=theme.builtin,
        tokens=len(theme.colors) + len(theme.radius),
        usable=theme.usable,
        problems=theme.problems,
    )


@router.get("", response_model=ThemeListResponse)
def list_themes() -> ThemeListResponse:
    """Every theme, built-in and user-written."""
    return ThemeListResponse(
        themes=[_as_response(theme) for theme in themes.discover()],
        themes_api=themes.THEMES_API,
        folder=str(themes.user_dir()),
    )


def _css(theme: themes.Theme | None, *, credits: bool = True) -> Response:
    """A theme as a stylesheet, or an empty one.

    An unknown or unusable theme answers with an empty stylesheet rather than
    an error: the bundled palette is already in the page, so "no theme" and
    "the default theme" look the same, and a broken theme file leaves a
    working interface rather than an unstyled one.
    """
    body = (
        themes.stylesheet(theme, credits=credits)
        if theme is not None and theme.usable
        else ""
    )
    return Response(
        content=body,
        media_type="text/css",
        # Re-read on every navigation: the file on disk is the truth and
        # somebody editing a theme wants to see it by reloading, not by
        # working out which cache to clear.
        headers={"Cache-Control": "no-store"},
    )


@router.get("/active.css", include_in_schema=False)
def active_stylesheet() -> Response:
    """The selected theme. Open, so the login page is themed too."""
    try:
        slug = load_app_settings().theme or themes.DEFAULT_SLUG
    except Exception:  # noqa: BLE001 - a settings file that cannot be read
        slug = themes.DEFAULT_SLUG
    theme = themes.get(slug)
    if theme is None and slug != themes.DEFAULT_SLUG:
        # The setting names a theme that has been deleted. The interface says
        # so on the Settings page; here the default is better than nothing.
        logger.info("the selected theme %r is not installed", slug)
        theme = themes.get(themes.DEFAULT_SLUG)
    # No credits comment: this is the endpoint anyone can reach.
    return _css(theme, credits=False)


@router.get("/{slug}.css", include_in_schema=False)
def theme_stylesheet(slug: str) -> Response:
    """One theme by name, for previewing one that is not selected."""
    theme = themes.get(slug)
    if theme is None:
        raise HTTPException(status_code=404, detail=f"No theme called {slug!r}")
    return _css(theme)
