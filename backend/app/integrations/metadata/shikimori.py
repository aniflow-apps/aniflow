"""Shikimori's catalogue (build 83).

A Russian anime database with a public API that needs no key, included in the
metadata experiment for one reason: it is not derived from AniList or from
MyAnimeList, so when either of those has an outage Shikimori is unlikely to
share it. Independence is the property being tested here.

Its season list is honest about what it does not carry. Genres are only on the
per-anime endpoint, which would be one request per show and is not worth two
hundred requests for a page of tags, so entries come back without them and the
Calendar Sources page counts that as missing rather than pretending otherwise.
It publishes no broadcast time either. What it does have is ids, titles,
posters, kind and episode counts.
"""
from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import urljoin

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.version import user_agent
from backend.app.integrations.metadata.adult import genres_are_adult
from backend.app.integrations.metadata.airing import airing_status
from backend.app.integrations.metadata.base import AnimeCandidate, as_plain_text

logger = for_component("Shikimori")


def _count(value: object) -> int | None:
    """A popularity count, when the source published one."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

_SEASONS = ("winter", "spring", "summer", "fall")

#: Shikimori's own season spelling for the last one.
_SEASON_ALIASES = {"fall": "autumn"}

#: ``rx`` is hentai; ``r_plus`` is nudity, which the calendar's filter is not
#: about.
_ADULT_RATINGS = ("rx",)


class ShikimoriProvider:
    """Shikimori's season line-up, over its public API."""

    name = "shikimori"

    #: The documented maximum.
    _PAGE_SIZE = 50
    _MAX_SEASON_PAGES = 6

    #: Five a second and ninety a minute are allowed; this asks for one a
    #: second, which is well inside both.
    _PAUSE_SECONDS = 1.0

    def __init__(self) -> None:
        settings = get_settings()
        self._url = "https://shikimori.one"
        self._timeout = settings.external_request_timeout_seconds

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]:
        entries = await self._get("/api/animes", {"search": query, "limit": min(limit, 50)})
        return [self._to_candidate(item) for item in entries]

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None:
        if not str(provider_id).isdigit():
            return None
        payload = await self._get(f"/api/animes/{provider_id}", None)
        return self._to_candidate(payload) if isinstance(payload, dict) else None

    async def get_season(self, season: str, year: int) -> list[AnimeCandidate]:
        name = season.strip().lower()
        if name not in _SEASONS:
            raise httpx.HTTPError(f"{season!r} is not a season Shikimori knows")
        term = f"{_SEASON_ALIASES.get(name, name)}_{year}"

        candidates: list[AnimeCandidate] = []
        seen: set[str] = set()
        for page in range(1, self._MAX_SEASON_PAGES + 1):
            if page > 1:
                await asyncio.sleep(self._PAUSE_SECONDS)
            entries = await self._get(
                "/api/animes",
                {
                    "season": term,
                    "limit": self._PAGE_SIZE,
                    "page": page,
                    # Popularity first, so a truncated list is still the part
                    # of the season anyone came for.
                    "order": "popularity",
                    # Excluded, and this parameter reads backwards: on
                    # Shikimori ``censored=true`` means "leave the hentai
                    # out". Build 83 passed false with a comment claiming the
                    # opposite, which is how hentai reached the calendar - and
                    # its season list does not publish a rating, so nothing
                    # downstream could flag what arrived.
                    "censored": "true",
                },
            )
            if not isinstance(entries, list) or not entries:
                break
            for item in entries:
                if not isinstance(item, dict):
                    continue
                candidate = self._to_candidate(item)
                if candidate.provider_id in seen:
                    continue
                seen.add(candidate.provider_id)
                candidates.append(candidate)
            if len(entries) < self._PAGE_SIZE:
                break
        return candidates

    # -- the wire ------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, object] | None):
        from backend.app.services.outbound import client_kwargs

        target = f"{self._url}{path}"
        async with httpx.AsyncClient(
            timeout=self._timeout,
            # Shikimori asks clients to identify themselves by name and
            # answers 429 to those that will not. AniFlow says who it is
            # everywhere, so this needs nothing special - it just matters more
            # here.
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
            follow_redirects=True,
            **client_kwargs(target),
        ) as client:
            response = await client.get(target, params=params)
            if response.is_error:
                raise httpx.HTTPError(
                    f"Shikimori answered {response.status_code}: {response.reason_phrase}"
                )
            return response.json()

    # -- one entry -----------------------------------------------------------

    def _cover(self, entry: dict[str, object]) -> str | None:
        """Shikimori's posters are site-relative paths with a cache-busting
        query, so they are joined onto the host rather than used as given."""
        image = entry.get("image")
        if not isinstance(image, dict):
            return None
        for key in ("original", "preview", "x96"):
            value = image.get(key)
            if isinstance(value, str) and value:
                return urljoin(self._url, value)
        return None

    @staticmethod
    def _start_date(entry: dict[str, object]) -> date | None:
        raw = entry.get("aired_on")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    def _to_candidate(self, entry: dict[str, object]) -> AnimeCandidate:
        romaji = entry.get("name")
        english = entry.get("english")
        native = entry.get("japanese")
        # Both are lists on the per-anime endpoint and absent from the season
        # list, so the first usable entry is taken and nothing is assumed.
        if isinstance(english, list):
            english = next((item for item in english if item), None)
        if isinstance(native, list):
            native = next((item for item in native if item), None)
        episodes = entry.get("episodes")
        aired = entry.get("episodes_aired")
        rating = entry.get("rating")
        genres = entry.get("genres")
        return AnimeCandidate(
            provider="shikimori",
            provider_id=str(entry.get("id") or ""),
            display_name=str(english or romaji or native or "Unknown"),
            title_english=str(english) if english else None,
            title_romaji=str(romaji) if romaji else None,
            title_native=str(native) if native else None,
            cover_url=self._cover(entry),
            # Deliberately empty for a season list: see the note at the top.
            genres=(),
            # Shikimori's listing carries neither studios nor a description;
            # both sit behind a request per anime, the way its genres do. The
            # merge takes them from a source that has them.
            synopsis=as_plain_text(entry.get("description")),
            format=str(entry.get("kind")) if entry.get("kind") else None,
            # anons / ongoing / released.
            airing_status=airing_status(entry.get("status")),
            episode_count=(
                int(episodes) if isinstance(episodes, int) and episodes
                else int(aired) if isinstance(aired, int) and aired
                else None
            ),
            # Shikimori's season list publishes neither a rating nor
            # genres, so in practice this is always False - and that is now
            # honest, because the list it answers with no longer contains
            # anything to flag. See integrations/metadata/adult.py.
            # Shikimori's own anime id *is* the MyAnimeList id (build 88).
            #
            # Believed for years and deliberately not claimed until it could
            # be checked. Build 87's stored data settled it: of 980 Shikimori
            # entries whose id also existed as a MyAnimeList id in the same
            # season, 978 carried a title that matched, and the two that did
            # not were the same show under an English and a romaji name. The
            # bug report that led here was four calendar cards appearing twice
            # for exactly this reason.
            provider_ids=(
                {"shikimori": str(entry.get("id")), "myanimelist": str(entry.get("id"))}
                if entry.get("id")
                else {}
            ),
            is_adult=(
                (str(rating).casefold() in _ADULT_RATINGS if rating else False)
                or genres_are_adult(
                    [item.get("name") for item in genres if isinstance(item, dict)]
                    if isinstance(genres, list)
                    else ()
                )
            ),
            popularity=_count(entry.get("members")),
            air_start_date=self._start_date(entry),
            # No broadcast slot published. The calendar can still place the
            # show in its season, just not on a weekday.
            air_slot_at=None,
        )


__all__ = ["ShikimoriProvider"]
