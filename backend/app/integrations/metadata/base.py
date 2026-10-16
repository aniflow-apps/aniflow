from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from backend.app.integrations.metadata.airing import STATUS_UNKNOWN


import re as _re

#: Markup the sources send inside a synopsis. AniList writes HTML, Shikimori
#: BBCode, and a detail page showing `<br>` or `[character=123]` would be
#: worse than showing nothing at all.
_MARKUP = _re.compile(r"<[^>]+>|\[/?[a-zA-Z][^\]]*\]")
_SPACE = _re.compile(r"[ \t]*\n[ \t]*")


def as_plain_text(text: str | None) -> str | None:
    """One source's synopsis as something to put on a page.

    Tags removed, paragraph breaks kept: a synopsis is written in paragraphs
    and flattening it into one block is the other way to make it unreadable.
    """
    if not text:
        return None
    stripped = _MARKUP.sub("", str(text))
    stripped = stripped.replace("&nbsp;", " ").replace("&amp;", "&")
    stripped = stripped.replace("&quot;", '"').replace("&#039;", "'")
    stripped = _SPACE.sub("\n", stripped).strip()
    return stripped or None


@dataclass(frozen=True)
class AnimeCandidate:
    """Provider-neutral anime information used by search and import flows."""

    provider: str
    provider_id: str
    display_name: str
    title_english: str | None = None
    title_romaji: str | None = None
    title_native: str | None = None
    aliases: tuple[str, ...] = ()
    cover_url: str | None = None
    genres: tuple[str, ...] = ()
    format: str | None = None
    episode_count: int | None = None
    # Provider's own adult flag. Reported rather than acted on here: the
    # calendar filters on it, and the user decides.
    is_adult: bool = False
    # How many people have this on a list, in whatever unit the provider
    # counts in (build 86). Not comparable between providers - AniList's
    # number and MyAnimeList's are on different scales - and only ever used to
    # order one list, which is all a "sort by popularity" needs.
    popularity: int | None = None
    # When the show premiered.
    air_start_date: date | None = None
    # One instant a known episode airs, in UTC. Weekday and time of day are
    # derived from it for display; storing them directly would bake in the
    # timezone the value was fetched in.
    air_slot_at: datetime | None = None
    #: Which episode ``air_slot_at`` is the airing time *of* (build 146).
    #:
    #: Without it the instant is only good for a weekday. With it, every other
    #: episode is that instant plus seven days each way - which is what lets a
    #: watchlist say when episode 7 is due rather than only that the show airs
    #: on Fridays.
    air_slot_episode: int | None = None
    #: Where the show is: not started, running, over, abandoned, or nobody
    #: said (build 148). One of ``airing.AIRING_STATUSES``, and normalised by
    #: ``airing.airing_status`` rather than by each adapter - see that module
    #: for what the five sources spell it as.
    airing_status: str = STATUS_UNKNOWN
    #: Who made it. One or more studios, as each source names them - the
    #: one credit people actually choose a show by (build 101).
    #:
    #: Staff is deliberately absent: it is a list of people per role, shaped
    #: differently by all five sources, and none of them returns it with a
    #: season listing - it would be one request per anime.
    studios: tuple[str, ...] = ()
    #: What it is about, in the source's own words. Plain text; some sources
    #: send HTML or BBCode and the adapters strip it.
    synopsis: str | None = None
    provider_ids: dict[str, str] = field(default_factory=dict)
    raw_metadata: dict[str, object] = field(default_factory=dict)


class MetadataProvider(Protocol):
    name: str

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]: ...

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None: ...
