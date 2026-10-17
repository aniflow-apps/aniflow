"""MyAnimeList's catalogue, through Jikan (build 83).

Jikan is an unofficial read-only mirror of MyAnimeList that needs no
credentials, which is what makes it worth trying: MyAnimeList's own API wants
a registered client id, and a self-hosted tool cannot ask its user to go and
register an application before the calendar works.

It is here as a candidate to replace AniList, which spent a fortnight
answering HTTP 403 and took the calendar with it. Whether it is good enough is
a question about *data*, not about code - Jikan has titles, episode counts,
genres and posters, and unlike Kitsu it publishes a broadcast slot - so it is
wired up the same way as the real providers and judged on the Calendar Sources
page beside them.

**Rate limits.** Three requests a second and sixty a minute, published. A
season is a handful of pages, taken one at a time with a pause between them,
which is nowhere near either limit and stays polite even when someone holds
the refresh button down.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.version import user_agent
from backend.app.integrations.metadata.adult import genres_are_adult
from backend.app.integrations.metadata.airing import airing_status
from backend.app.integrations.metadata.base import AnimeCandidate, as_plain_text

logger = for_component("Jikan")


def _count(value: object) -> int | None:
    """A popularity count, when the source published one."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

#: Jikan's season names, which happen to be AniList's lowercased.
_SEASONS = ("winter", "spring", "summer", "fall")

#: The ratings that mean adult. Jikan spells them "Rx - Hentai" and
#: "R+ - Mild Nudity"; only the first is what the calendar's filter is about.
_ADULT_RATINGS = ("rx",)


class JikanProvider:
    """MyAnimeList's season line-up, read through Jikan's public API."""

    name = "jikan"

    #: Jikan caps a page at 25. A season runs past two hundred entries once
    #: shorts and ONAs are counted, and the cap stops a broken pagination flag
    #: from looping for ever.
    _PAGE_SIZE = 25
    _MAX_SEASON_PAGES = 10

    #: Between pages. Jikan allows three a second; one a second is a tenth of
    #: what it permits and costs a season fetch a few seconds.
    _PAUSE_SECONDS = 1.0

    def __init__(self) -> None:
        settings = get_settings()
        self._url = "https://api.jikan.moe/v4"
        self._timeout = settings.external_request_timeout_seconds

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]:
        payload = await self._get("/anime", {"q": query, "limit": min(limit, 25)})
        return [self._to_candidate(item) for item in self._entries(payload)]

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None:
        if not str(provider_id).isdigit():
            return None
        payload = await self._get(f"/anime/{provider_id}", None)
        data = payload.get("data")
        return self._to_candidate(data) if isinstance(data, dict) else None

    async def get_season(self, season: str, year: int) -> list[AnimeCandidate]:
        """Every anime MyAnimeList lists for this season, across its pages."""
        name = season.strip().lower()
        if name not in _SEASONS:
            raise httpx.HTTPError(f"{season!r} is not a season Jikan knows")

        candidates: list[AnimeCandidate] = []
        seen: set[str] = set()
        for page in range(1, self._MAX_SEASON_PAGES + 1):
            if page > 1:
                await asyncio.sleep(self._PAUSE_SECONDS)
            payload = await self._get(
                f"/seasons/{year}/{name}", {"page": page, "limit": self._PAGE_SIZE}
            )
            entries = self._entries(payload)
            for item in entries:
                candidate = self._to_candidate(item)
                if candidate.provider_id in seen:
                    continue
                seen.add(candidate.provider_id)
                candidates.append(candidate)
            pagination = payload.get("pagination")
            has_next = (
                bool(pagination.get("has_next_page")) if isinstance(pagination, dict) else False
            )
            if not entries or not has_next:
                break
        return candidates

    # -- the wire ------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, object] | None) -> dict[str, object]:
        from backend.app.services.outbound import client_kwargs

        target = f"{self._url}{path}"
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
            **client_kwargs(target),
        ) as client:
            response = await client.get(target, params=params)
            if response.status_code == 429:
                # Published as three a second, and this asks for one - so a 429
                # means something else was talking to Jikan too. One retry
                # after a breath, then let the caller report it.
                logger.warning("Jikan rate-limited the request; waiting a moment")
                await asyncio.sleep(2.0)
                response = await client.get(target, params=params)
            if response.is_error:
                raise httpx.HTTPError(self._explain(response))
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _explain(response: httpx.Response) -> str:
        """Jikan's own words when it can manage them."""
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("message") if isinstance(body, dict) else None
        return f"Jikan answered {response.status_code}: {detail or response.reason_phrase}"

    @staticmethod
    def _entries(payload: dict[str, object]) -> list[dict[str, object]]:
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    # -- one entry -----------------------------------------------------------

    @staticmethod
    def _cover(entry: dict[str, object]) -> str | None:
        """The largest JPEG on offer. WebP is skipped on purpose: several of
        Jikan's WebP URLs 404, and a broken poster reads as a broken page."""
        images = entry.get("images")
        if not isinstance(images, dict):
            return None
        jpg = images.get("jpg")
        if not isinstance(jpg, dict):
            return None
        for key in ("large_image_url", "image_url", "small_image_url"):
            value = jpg.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _start_date(entry: dict[str, object]) -> date | None:
        aired = entry.get("aired")
        raw = aired.get("from") if isinstance(aired, dict) else None
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None

    @classmethod
    def _air_slot(cls, entry: dict[str, object]) -> datetime | None:
        """One instant an episode airs, in UTC.

        Built from the premiere date and the published broadcast time, because
        that is what Jikan gives: a weekday, a clock time and a timezone. The
        first episode's slot is a real instant, and the weekday and time of day
        the calendar draws come out of it correctly - which is the whole reason
        AniList was hard to replace.
        """
        broadcast = entry.get("broadcast")
        if not isinstance(broadcast, dict):
            return None
        raw_time = broadcast.get("time")
        zone_name = broadcast.get("timezone") or "Asia/Tokyo"
        start = cls._start_date(entry)
        if not isinstance(raw_time, str) or not raw_time or start is None:
            return None
        try:
            hour, minute = (int(part) for part in raw_time.split(":", 1))
            clock = time(hour=hour, minute=minute)
        except (TypeError, ValueError):
            return None
        try:
            zone = ZoneInfo(str(zone_name))
        except (ZoneInfoNotFoundError, ValueError):
            return None
        local = datetime.combine(start, clock, tzinfo=zone)
        # A broadcast after midnight is filed under the previous evening in
        # Japan, so the slot can land a day before the premiere date. Nudged
        # forward rather than accepted: an episode does not air before it airs.
        if local.date() < start:
            local += timedelta(days=1)
        return local.astimezone(ZoneInfo("UTC"))

    @staticmethod
    def _named(values: object) -> tuple[str, ...]:
        """A list of ``{"name": ...}`` objects as names. Jikan shapes its
        studios, producers and licensors this way."""
        if not isinstance(values, list):
            return ()
        names: list[str] = []
        for item in values:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        return tuple(names)

    @staticmethod
    def _genres(entry: dict[str, object]) -> tuple[str, ...]:
        names: list[str] = []
        for key in ("genres", "themes", "demographics"):
            values = entry.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name and name not in names:
                    names.append(name)
        return tuple(names)

    @classmethod
    def _is_adult(cls, entry: dict[str, object]) -> bool:
        rating = entry.get("rating")
        if isinstance(rating, str):
            code = rating.split("-", 1)[0].strip().casefold()
            if code in _ADULT_RATINGS:
                return True
        # Jikan files hentai as an explicit genre as well as a rating, and the
        # two do not always agree.
        explicit = entry.get("explicit_genres")
        if isinstance(explicit, list) and explicit:
            return True
        return genres_are_adult(cls._genres(entry))

    @classmethod
    def _to_candidate(cls, entry: dict[str, object]) -> AnimeCandidate:
        romaji = entry.get("title")
        english = entry.get("title_english")
        native = entry.get("title_japanese")
        aliases = entry.get("title_synonyms")
        episodes = entry.get("episodes")
        return AnimeCandidate(
            provider="jikan",
            provider_id=str(entry.get("mal_id") or ""),
            display_name=str(english or romaji or native or "Unknown"),
            title_english=str(english) if english else None,
            title_romaji=str(romaji) if romaji else None,
            title_native=str(native) if native else None,
            aliases=tuple(str(name) for name in aliases if name) if isinstance(aliases, list) else (),
            cover_url=cls._cover(entry),
            genres=cls._genres(entry),
            studios=cls._named(entry.get("studios")),
            synopsis=as_plain_text(entry.get("synopsis")),
            format=str(entry.get("type")) if entry.get("type") else None,
            episode_count=int(episodes) if isinstance(episodes, int) else None,
            is_adult=cls._is_adult(entry),
            # "Currently Airing", "Finished Airing", "Not yet aired".
            airing_status=airing_status(entry.get("status")),
            # How many MyAnimeList users have it on a list, as Jikan reports
            # it. The same measure MyAnimeList's own API calls
            # num_list_users, so the two sources are on one scale.
            popularity=_count(entry.get("members")),
            air_start_date=cls._start_date(entry),
            air_slot_at=cls._air_slot(entry),
            # The MyAnimeList id, under the name AniFlow already stores it by:
            # this is the one id shared with the AniList entries added before
            # build 83, so it is worth carrying through.
            provider_ids={"myanimelist": str(entry.get("mal_id"))} if entry.get("mal_id") else {},
        )


__all__ = ["JikanProvider"]
