"""What the television API answers with (build 181, 0.8.1).

The contract is written down in docs/tv-api-plan.md. Everything here is small
on purpose: a TV renders a card and moves a focus ring, so it is given finished
answers - titles already in the user's language, the week already grouped,
the calendar already filtered - rather than the raw material.
"""
import re
from datetime import date, datetime

from pydantic import BaseModel, field_validator


class TvPing(BaseModel):
    #: The API generation. Additive changes stay in 1.
    api: int = 1
    app_version: str
    build: int
    #: The server's display timezone. Weekdays and ``airs_time`` are in it.
    timezone: str
    now: datetime
    #: 0 = Monday.
    today_weekday: int
    #: ``basic`` | ``advanced``.
    app_mode: str
    #: Whether an empty calendar will fill itself. Off by default, and the
    #: reason a fresh install shows nothing until Update is pressed.
    calendar_auto_update: bool


class TvCard(BaseModel):
    """One watchlist entry, as a basic card."""

    id: str
    title: str
    #: An image URL: absolute-path (``/api/tv/v1/art/...``, resolve against the
    #: server) or an absolute provider URL. Null when there is none.
    cover: str | None = None
    #: ``UPCOMING`` | ``ACTIVE`` | ``COMPLETE``.
    lifecycle: str
    #: Added without release matching, so the subgroup pass is still to do in
    #: AniFlow. Only ever true in Advanced mode - in Basic mode a basic entry
    #: is simply what an entry is.
    needs_setup: bool = False
    #: 0 = Monday, server timezone. Null when nothing says when it airs.
    airs_weekday: int | None = None
    #: ``HH:MM``, server timezone.
    airs_time: str | None = None
    owned_episodes: int = 0
    episode_count: int | None = None
    #: Something is wrong with this show; the detail is on ``/progress``.
    problem: bool = False
    #: The calendar's popularity number for this show (build 186), the same one
    #: that orders a season there - for sorting only, not a quantity to show.
    #: Null when the calendar does not know the show.
    popularity: int | None = None
    #: Kind as the source spells it (``TV``, ``Movie``, ``ONA``...); null when unknown (build 254).
    format: str | None = None
    #: Premiere date, for the card's date pill (build 254).
    air_start_date: date | None = None
    #: The library's name and its place in the watchlist's own library order (build 254), for
    #: "by library". Null when the show has no known library.
    library: str | None = None
    library_rank: int | None = None


class TvWatchlist(BaseModel):
    items: list[TvCard]


class TvScheduleDay(BaseModel):
    weekday: int
    #: The date of this weekday in the current Monday-to-Sunday week.
    date: date
    is_today: bool
    items: list[TvCard]


class TvSchedule(BaseModel):
    days: list[TvScheduleDay]
    #: Entries nothing says a day for.
    unscheduled: list[TvCard]


class TvCalendarItem(BaseModel):
    calendar_id: str
    title: str
    cover: str | None = None
    format: str | None = None
    episode_count: int | None = None
    #: ``NOT_YET_AIRED`` | ``AIRING`` | ``FINISHED`` | ``CANCELLED`` | ``UNKNOWN``.
    airing_status: str = "UNKNOWN"
    air_start_date: date | None = None
    air_slot_at: datetime | None = None
    genres: list[str] = []
    synopsis: str | None = None
    studios: list[str] = []
    #: Whether it can be added at all. A show only some sources describe is
    #: real and worth showing, and cannot be added.
    addable: bool = True
    #: A hint, not a promise: matched on names as well as ids.
    in_library: bool = False
    #: The other names the calendar knows (English, romaji, native), without the title itself (build 254).
    alt_titles: list[str] = []
    #: AniDB's id when the calendar has one.
    anidb_id: int | None = None
    #: Popularity as one to five stars, relative to the whole season (not the filtered page);
    #: 0 when unknown (build 254).
    stars: int = 0
    #: How it relates to seasons already tracked: ``sequel`` | ``gap`` | ``prequel`` | null (build 255).
    related: str | None = None
    #: The calendar reads it as adult (the flag or an adult genre), for the 18+ tag (build 255).
    is_adult: bool = False


class TvCalendarPage(BaseModel):
    season: str
    year: int
    #: ``ready`` | ``loading`` | ``updating`` | ``empty`` | ``error``.
    #: Anything but ``ready`` with no items means "not loaded", never "no shows".
    status: str
    stale: bool = False
    loaded_at: datetime | None = None
    unavailable_reason: str | None = None
    page: int
    page_size: int
    #: After the user's calendar filters, before paging.
    total: int
    #: How many of the season's shows the filters left out, for "N hidden" (build 255).
    hidden: int = 0
    items: list[TvCalendarItem]


class TvAddRequest(BaseModel):
    calendar_id: str


class TvAddResponse(BaseModel):
    #: False when it was already on the watchlist.
    added: bool
    #: Null only when the show is on the watchlist under a different id and the
    #: match was by name.
    item: TvCard | None = None


class TvProgressItem(BaseModel):
    anime_id: str
    title: str
    episode_number: int | None = None
    #: One of ``episode_view.STATES``.
    state: str
    progress: float | None = None
    since: datetime | None = None
    detail: str | None = None
    #: Whether this is a problem rather than simply work in flight.
    problem: bool = False


class TvAttentionItem(BaseModel):
    id: str
    #: ``AttentionKind``. The TV words each one itself.
    kind: str
    anime_id: str | None = None
    title: str | None = None
    episode_number: int | None = None
    detail: str | None = None
    created_at: datetime


class TvProgress(BaseModel):
    in_progress: int
    attention: int
    items: list[TvProgressItem]
    issues: list[TvAttentionItem]


class TvDevice(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class TvDeviceCreate(BaseModel):
    name: str


class TvDeviceCreated(TvDevice):
    #: Shown once. Only its hash is kept.
    token: str


_DEVICE_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


class TvPairStart(BaseModel):
    #: What the TV calls itself; the person approving may change it.
    name: str = ""
    #: An id the TV keeps for itself (build 263), so a TV that pairs again is recognised and its old entry replaced.
    #: Optional: a TV that cannot make one pairs as before. One that is not 8-64 plain characters is dropped, not
    #: refused - a malformed id must never stop a TV pairing.
    device_id: str | None = None

    @field_validator("device_id", mode="before")
    @classmethod
    def _plain_id_or_none(cls, value: object) -> str | None:
        text = value.strip() if isinstance(value, str) else ""
        return text if _DEVICE_ID.fullmatch(text) else None


class TvPairStarted(BaseModel):
    #: Secret, for polling. Stays on the TV.
    ticket: str
    #: What the TV shows and the approval page asks for.
    code: str
    #: Where the QR code sends the phone.
    approve_url: str
    expires_in: int
    poll_seconds: int


class TvPairStatus(BaseModel):
    status: str  # pending | approved | expired
    #: Present once, on the poll that finds the pairing approved.
    token: str | None = None
    expires_in: int = 0


class TvReplaceable(BaseModel):
    """An existing entry the pairing in front of the person could replace (build 263)."""

    id: int
    name: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    #: True: the TV's own id matches, so the entry is replaced when the TV collects its new token. False: only the
    #: name matches and the entry has no id (it is older than TV ids) - offered, and the person decides.
    same_device: bool


class TvPairLookup(BaseModel):
    name: str
    code: str
    #: The name to start the approval page's name field with: the existing entry's, when the TV is recognised,
    #: otherwise null (the field starts with ``name``).
    suggested_name: str | None = None
    replaces: list[TvReplaceable] = []


class TvPairApprove(BaseModel):
    code: str
    name: str | None = None
    #: The offered entries (``same_device`` false) the person ticked. Ones the pairing does not offer are ignored.
    replace_ids: list[int] = []
