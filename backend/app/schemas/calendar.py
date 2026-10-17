"""What the Calendar endpoints answer with (build 143, Calendar v2)."""
from datetime import datetime

from pydantic import BaseModel

from backend.app.schemas.anime import AnimeSearchResult


class CalendarSeasonResponse(BaseModel):
    """One season, as the web page and the television client read it."""

    #: Always ``calendar`` now. Kept because clients read it, and because the
    #: value is the point: the line-up comes from AniFlow's own calendar, not
    #: from whichever provider happened to answer.
    provider: str
    season: str
    year: int
    anime: list[AnimeSearchResult]

    #: ``ready`` | ``loading`` | ``updating`` | ``empty`` | ``error``.
    #:
    #: ``loading`` and ``updating`` are different on purpose: one says there
    #: is nothing yet, the other says what is on screen is about to get
    #: better. ``error`` never means the data has gone - the last good
    #: line-up stays, and ``unavailable_reason`` says what failed.
    status: str = "ready"
    #: When a complete snapshot for this season was last applied.
    loaded_at: datetime | None = None
    #: Whether an update is running right now, for this season or another.
    updating: bool = False

    #: True when this season has not had a successful load for a while. The
    #: data is still shown - this only decides whether the page offers to
    #: update it.
    stale: bool = False
    #: Why the calendar could not be updated, when something went wrong.
    #: Never a reason for the page to be empty.
    unavailable_reason: str | None = None

    #: Whether this calendar has had its one initial fill (build 148).
    #:
    #: A fact about the database rather than about this season, and carried
    #: here because the page reads this response on every navigation and the
    #: alternative was a second request that would answer the same thing.
    first_fill_done: bool = False

    #: Kept so existing clients keep working. The same value as ``loaded_at``.
    cached_at: datetime | None = None
    #: Always None. v2 has no cache to expire - it has a database, and a
    #: season is updated when somebody asks or when the window is refreshed.
    next_refresh_at: datetime | None = None
    #: Always 0. Staleness is a boolean now, taken from the last successful
    #: load rather than from the age of a cache entry.
    stale_hours: int = 0


class CalendarStatusResponse(BaseModel):
    """What the Calendar is doing, for a page that is watching it."""

    running: bool = False
    #: ``update`` when somebody pressed the button, ``season`` when a season
    #: is being loaded because somebody navigated to it.
    kind: str = ""
    year: int = 0
    season: str = ""
    done: int = 0
    total: int = 0
    #: What is happening inside the season being worked on: the source being
    #: asked, or ``artwork``. Seasons alone were not enough to watch.
    step: str = ""
    #: Posters fetched so far in this season, and how many there are. The
    #: total is zero until the season's snapshots are in, because a bar that
    #: guesses its own length is worse than no bar.
    pictures: int = 0
    pictures_total: int = 0
    #: Sources asked, of the sources to ask. Known before any of them answer.
    sources_done: int = 0
    sources_total: int = 0
    summary: str = ""
    error: str | None = None
    #: Set when the calendar database itself could not be opened. Shown beside
    #: the Rebuild and Reset buttons, because a recovery step nobody is told
    #: about is a bug.
    blocked_reason: str | None = None
