import asyncio
from datetime import UTC, date, datetime

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.version import user_agent
from backend.app.integrations.metadata.airing import airing_status
from backend.app.integrations.metadata.base import AnimeCandidate, as_plain_text

logger = for_component("AniList")


class AniListProvider:
    """AniList public GraphQL adapter.

    AniList accepts POST requests to its GraphQL endpoint. This adapter intentionally
    returns provider-neutral data and does not persist or choose anime on its own.
    """

    name = "anilist"
    _search_query = """
    query ($search: String, $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
          id
          title { romaji english native }
          synonyms
          coverImage { extraLarge large }
          genres
          format
          episodes
          popularity
          idMal
          startDate { year month day }
          status
          nextAiringEpisode { airingAt episode }
          isAdult
          description(asHtml: false)
          studios(isMain: true) { nodes { name } }
        }
      }
    }
    """
    _detail_query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) {
        id
        title { romaji english native }
        synonyms
        coverImage { extraLarge large }
        genres
        format
        episodes
        popularity
        idMal
        startDate { year month day }
        status
        nextAiringEpisode { airingAt episode }
        isAdult
        description(asHtml: false)
        studios(isMain: true) { nodes { name } }
      }
    }
    """
    _season_query = """
    query ($season: MediaSeason, $seasonYear: Int, $page: Int) {
      Page(page: $page, perPage: 50) {
        pageInfo { currentPage hasNextPage }
        media(season: $season, seasonYear: $seasonYear, type: ANIME, sort: POPULARITY_DESC) {
          id
          title { romaji english native }
          synonyms
          coverImage { extraLarge large }
          genres
          format
          episodes
          popularity
          idMal
          startDate { year month day }
          status
          nextAiringEpisode { airingAt episode }
          isAdult
          description(asHtml: false)
          studios(isMain: true) { nodes { name } }
        }
      }
    }
    """

    # AniList serves 50 per page. A season has well over 50 entries once shorts,
    # ONAs and specials are counted, so one page silently truncated the calendar
    # to the 50 most popular. The cap keeps a broken hasNextPage from looping.
    _MAX_SEASON_PAGES = 6

    def __init__(self) -> None:
        settings = get_settings()
        self._url = settings.anilist_api_url
        self._timeout = settings.external_request_timeout_seconds

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]:
        response = await self._request(self._search_query, {"search": query, "perPage": min(limit, 50)})
        media = response.get("data", {}).get("Page", {}).get("media", [])
        return [self._to_candidate(item) for item in media]

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None:
        try:
            identifier = int(provider_id)
        except ValueError:
            return None
        response = await self._request(self._detail_query, {"id": identifier})
        media = response.get("data", {}).get("Media")
        return self._to_candidate(media) if media else None

    async def get_season(self, season: str, year: int) -> list[AnimeCandidate]:
        """Every anime AniList lists for this season, not just the first page.

        The season is upper-cased here rather than trusted from the caller.
        ``$season`` is a ``MediaSeason`` enum and GraphQL rejects the wrong
        case outright::

            Variable "$season" got invalid value "summer"; Expected type
            MediaSeason; did you mean SUMMER? (HTTP 400)

        Every other adapter already normalises what it is given - Kitsu,
        Jikan, Shikimori and MyAnimeList all lower-case it on the way in -
        and this one did not, so it depended on which caller reached it.
        ``metadata.season()`` passes the upper-case name and worked;
        ``calendar_sources`` lower-cases and has been failing since build 83,
        which Calendar v2 then inherited by copying the call.
        """
        name = season.strip().upper()
        candidates: list[AnimeCandidate] = []
        seen: set[str] = set()
        for page in range(1, self._MAX_SEASON_PAGES + 1):
            response = await self._request(
                self._season_query, {"season": name, "seasonYear": year, "page": page}
            )
            page_data = response.get("data", {}).get("Page", {})
            media = page_data.get("media") or []
            for item in media:
                candidate = self._to_candidate(item)
                if candidate.provider_id in seen:
                    continue
                seen.add(candidate.provider_id)
                candidates.append(candidate)
            if not (page_data.get("pageInfo") or {}).get("hasNextPage"):
                break
        return candidates

    async def _request(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        """One GraphQL call, with the reason kept when it fails.

        Rate limiting is retried once. AniList publishes a fairly small budget
        per minute and answers 429 with ``Retry-After``; a season is six calls,
        so a busy moment used to fail the whole page rather than take a few
        seconds longer.
        """
        # AniList is on the internet, so it follows the proxy setting. See
        # services/outbound.py for what is and is not proxied.
        from backend.app.services.outbound import client_kwargs

        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": user_agent()},
            **client_kwargs(self._url),
        ) as client:
            response = await self._post(client, query, variables)
            if response.status_code == 429:
                wait = self._retry_after(response)
                logger.warning("AniList rate-limited the request; waiting %ss", wait)
                await asyncio.sleep(wait)
                response = await self._post(client, query, variables)
            if response.is_error:
                # Read the body before the status: an error response usually
                # explains itself, and `raise_for_status` would replace that
                # explanation with "Client error '403 Forbidden' for url …".
                raise httpx.HTTPStatusError(
                    f"AniList returned an error: {self._explain(response)}",
                    request=response.request,
                    response=response,
                )
            payload = response.json()
        detail = self._graphql_errors(payload)
        if detail:
            raise httpx.HTTPStatusError(
                f"AniList returned an error: {detail}",
                request=response.request,
                response=response,
            )
        return payload

    @classmethod
    def _explain(cls, response) -> str:
        """The most useful sentence an error response has to offer."""
        try:
            detail = cls._graphql_errors(response.json())
        except ValueError:  # not JSON at all - a proxy page, say
            detail = None
        if detail:
            return f"{detail} (HTTP {response.status_code})"
        body = (response.text or "").strip().replace("\n", " ")
        return f"HTTP {response.status_code}{f': {body[:160]}' if body else ''}"

    @staticmethod
    def _graphql_errors(payload: object) -> str | None:
        """The ``errors`` messages GraphQL puts in the body, joined."""
        if not isinstance(payload, dict):
            return None
        errors = payload.get("errors")
        if not errors:
            return None
        items = errors if isinstance(errors, list) else [errors]
        joined = "; ".join(
            str(item.get("message") or item) if isinstance(item, dict) else str(item)
            for item in items
        ).strip()
        return joined[:200] or None

    async def _post(self, client, query: str, variables: dict[str, object]):
        return await client.post(self._url, json={"query": query, "variables": variables})

    @staticmethod
    def _retry_after(response) -> float:
        """How long AniList asked us to wait, bounded so nothing hangs."""
        try:
            return max(1.0, min(float(response.headers.get("Retry-After", 5)), 60.0))
        except (TypeError, ValueError):
            return 5.0

    @staticmethod
    def _first_url(cover: object, *keys: str) -> str | None:
        if not isinstance(cover, dict):
            return None
        for key in keys:
            value = cover.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _start_date(media: dict[str, object]) -> date | None:
        """AniList's startDate, which may be partial (a year and nothing else)."""
        raw = media.get("startDate")
        if not isinstance(raw, dict):
            return None
        year, month, day = raw.get("year"), raw.get("month"), raw.get("day")
        if not all(isinstance(value, int) for value in (year, month, day)):
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None  # a provider typo like month 13

    @staticmethod
    def _air_slot(media: dict[str, object]) -> datetime | None:
        """The next episode's airing instant, in UTC.

        AniList gives a unix timestamp. Only currently-airing shows have one;
        for everything else the weekday still comes from the premiere date.
        """
        raw = media.get("nextAiringEpisode")
        if not isinstance(raw, dict):
            return None
        airing_at = raw.get("airingAt")
        if not isinstance(airing_at, int):
            return None
        try:
            return datetime.fromtimestamp(airing_at, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _air_slot_episode(media: dict[str, object]) -> int | None:
        """Which episode ``nextAiringEpisode`` is about (build 146).

        Returned by AniList alongside the timestamp and discarded until now,
        which left the instant usable for a weekday and nothing else.
        """
        raw = media.get("nextAiringEpisode")
        if not isinstance(raw, dict):
            return None
        episode = raw.get("episode")
        return episode if isinstance(episode, int) and episode > 0 else None

    def _to_candidate(self, media: dict[str, object]) -> AnimeCandidate:
        title = media.get("title") if isinstance(media.get("title"), dict) else {}
        english = title.get("english") if isinstance(title, dict) else None
        romaji = title.get("romaji") if isinstance(title, dict) else None
        native = title.get("native") if isinstance(title, dict) else None
        cover = media.get("coverImage") if isinstance(media.get("coverImage"), dict) else {}
        aliases = tuple(value for value in media.get("synonyms", []) if isinstance(value, str))
        genres = tuple(value for value in media.get("genres", []) if isinstance(value, str))
        provider_ids = {"anilist": str(media["id"])}
        if media.get("idMal") is not None:
            provider_ids["myanimelist"] = str(media["idMal"])
        # How many AniList users have it on a list. Its own scale - see
        # AnimeCandidate.popularity - and only ever used to order one list.
        raw_popularity = media.get("popularity")
        popularity = (
            int(raw_popularity)
            if isinstance(raw_popularity, int) and raw_popularity > 0
            else None
        )
        return AnimeCandidate(
            provider=self.name,
            provider_id=str(media["id"]),
            display_name=english or romaji or native or str(media["id"]),
            title_english=english if isinstance(english, str) else None,
            title_romaji=romaji if isinstance(romaji, str) else None,
            title_native=native if isinstance(native, str) else None,
            aliases=aliases,
            # extraLarge first: "large" is ~230px wide, which looks poor the
            # moment a poster is opened at full size.
            cover_url=self._first_url(cover, "extraLarge", "large"),
            genres=genres,
            # isMain, so a co-producer's name does not crowd out the studio
            # that actually made it.
            studios=tuple(
                node.get("name")
                for node in ((media.get("studios") or {}).get("nodes") or [])
                if isinstance(node, dict) and isinstance(node.get("name"), str)
            ),
            synopsis=as_plain_text(media.get("description")),
            format=media.get("format") if isinstance(media.get("format"), str) else None,
            episode_count=media.get("episodes") if isinstance(media.get("episodes"), int) else None,
            is_adult=bool(media.get("isAdult")),
            # Where the show is: NOT_YET_RELEASED / RELEASING / FINISHED /
            # CANCELLED / HIATUS, normalised by `airing`.
            airing_status=airing_status(media.get("status")),
            air_start_date=self._start_date(media),
            air_slot_at=self._air_slot(media),
            air_slot_episode=self._air_slot_episode(media),
            popularity=popularity,
            provider_ids=provider_ids,
            raw_metadata=media,
        )
