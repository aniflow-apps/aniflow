"""MyAnimeList's own API (build 84).

Jikan mirrors this data without a credential and was the obvious first
choice - until MyAnimeList itself went down and took Jikan with it
(``Jikan answered 504: Jikan failed to connect to MyAnimeList``). Going
straight to the source removes a hop, and removes a service whose outages are
somebody else's to fix.

**What it needs, and what it does not.** Only a client id, sent as
``X-MAL-CLIENT-ID``. That is enough for every read-only endpoint, this one
included. It does *not* need OAuth, a user login, or a redirect URL - those
are for acting on somebody's list, which AniFlow never does. The id lives in
``aniflow.yaml`` as ``mal_client_id``; with it empty this adapter reports
itself as unavailable and nothing else changes.

**Why it matters beyond being another calendar.** The MyAnimeList id is the
one identifier the other sources already publish: AniList hands over
``idMal`` and Kitsu keeps a mapping to it. So this is the source that ties the
others together - see services/calendar_identity.py, where that turns out to
matter more than AniDB does for a season that has not aired yet. (Shikimori's
own ids are widely said to be MyAnimeList's too. Unverified, so it is not
claimed as a cross-reference; with this source refreshed alongside it, the
comparison page can measure it.)
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

logger = for_component("MyAnimeList")


def _count(value: object) -> int | None:
    """A popularity count, when the source published one."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _configured_client_id() -> str:
    """The client id as the user set it, from the settings file."""
    try:
        from backend.app.services.app_settings import load_app_settings

        return str(getattr(load_app_settings(), "mal_client_id", "") or "").strip()
    except Exception:  # noqa: BLE001 - fall back to the environment
        return ""


_SEASONS = ("winter", "spring", "summer", "fall")

#: Everything worth having, named the way MyAnimeList names it. Asked for
#: explicitly because the API returns only id, title and picture otherwise.
_FIELDS = ",".join(
    (
        "id",
        "title",
        "alternative_titles{en,ja,synonyms}",
        "main_picture{medium,large}",
        "media_type",
        "num_episodes",
        "genres",
        "num_list_users",
        "start_date",
        "start_season",
        "broadcast{day_of_the_week,start_time}",
        "nsfw",
        "status",
        "synopsis",
        "studios",
    )
)


class MalProvider:
    """MyAnimeList's season line-up, with a client id and nothing else."""

    name = "mal"

    #: The documented maximum for this endpoint.
    _PAGE_SIZE = 100
    _MAX_SEASON_PAGES = 6
    _PAUSE_SECONDS = 1.0

    def __init__(self) -> None:
        settings = get_settings()
        self._url = "https://api.myanimelist.net/v2"
        self._timeout = settings.external_request_timeout_seconds
        # From aniflow.yaml, not from the environment - see
        # services/calendar_providers._with_credentials for why that distinction
        # cost a build.
        self._client_id = _configured_client_id() or (settings.mal_client_id or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self._client_id)

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]:
        payload = await self._get(
            "/anime", {"q": query, "limit": min(limit, 100), "fields": _FIELDS}
        )
        return [self._to_candidate(node) for node in self._nodes(payload)]

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None:
        if not str(provider_id).isdigit():
            return None
        payload = await self._get(f"/anime/{provider_id}", {"fields": _FIELDS})
        return self._to_candidate(payload) if isinstance(payload, dict) else None

    async def get_season(self, season: str, year: int) -> list[AnimeCandidate]:
        name = season.strip().lower()
        if name not in _SEASONS:
            raise httpx.HTTPError(f"{season!r} is not a season MyAnimeList knows")
        if not self.configured:
            raise httpx.HTTPError("MyAnimeList needs a client id; set mal_client_id")

        candidates: list[AnimeCandidate] = []
        seen: set[str] = set()
        for page in range(self._MAX_SEASON_PAGES):
            if page:
                await asyncio.sleep(self._PAUSE_SECONDS)
            payload = await self._get(
                f"/anime/season/{year}/{name}",
                {
                    "limit": self._PAGE_SIZE,
                    "offset": page * self._PAGE_SIZE,
                    "fields": _FIELDS,
                    # By how many people have it on a list, so a truncated
                    # season is still the part anyone came for.
                    "sort": "anime_num_list_users",
                    # MyAnimeList's own filter would hide the adult entries
                    # entirely; AniFlow reports the flag and lets the calendar
                    # decide, so it is not asked for.
                    "nsfw": "true",
                },
            )
            nodes = self._nodes(payload)
            for node in nodes:
                candidate = self._to_candidate(node)
                if not candidate.provider_id or candidate.provider_id in seen:
                    continue
                seen.add(candidate.provider_id)
                candidates.append(candidate)
            paging = payload.get("paging")
            has_next = bool(paging.get("next")) if isinstance(paging, dict) else False
            if not nodes or not has_next:
                break
        return candidates

    # -- the wire ------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, object]) -> dict[str, object]:
        from backend.app.services.outbound import client_kwargs

        target = f"{self._url}{path}"
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "User-Agent": user_agent(),
                # The whole credential. Never logged - see _explain, which
                # reports the status and MyAnimeList's message and nothing of
                # the request.
                "X-MAL-CLIENT-ID": self._client_id,
            },
            **client_kwargs(target),
        ) as client:
            response = await client.get(target, params=params)
            if response.is_error:
                raise httpx.HTTPError(self._explain(response))
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _explain(response: httpx.Response) -> str:
        """MyAnimeList's own words, plus the one hint worth adding.

        A 403 here almost always means the client id is wrong rather than that
        anything is forbidden, and the id is easy to confuse with the secret -
        so the message says so instead of leaving somebody to guess.
        """
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("message") or body.get("error") if isinstance(body, dict) else None
        text = f"MyAnimeList answered {response.status_code}"
        if detail:
            text += f": {detail}"
        if response.status_code in (401, 403):
            text += " - check that mal_client_id is the Client ID, not the Client Secret"
        return text

    @staticmethod
    def _nodes(payload: dict[str, object]) -> list[dict[str, object]]:
        """The season endpoint wraps each anime in ``{"node": {...}}``."""
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        nodes = []
        for item in data:
            node = item.get("node") if isinstance(item, dict) else None
            if isinstance(node, dict):
                nodes.append(node)
        return nodes

    # -- one entry -----------------------------------------------------------

    @staticmethod
    def _cover(node: dict[str, object]) -> str | None:
        picture = node.get("main_picture")
        if not isinstance(picture, dict):
            return None
        for key in ("large", "medium"):
            value = picture.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _start_date(node: dict[str, object]) -> date | None:
        raw = node.get("start_date")
        if not isinstance(raw, str) or not raw:
            return None
        # MyAnimeList publishes partial dates - "2026" or "2026-07" - for
        # anything not yet scheduled, and a partial date is not a date.
        try:
            return date.fromisoformat(raw) if len(raw) == 10 else None
        except ValueError:
            return None

    @classmethod
    def _air_slot(cls, node: dict[str, object]) -> datetime | None:
        """One instant an episode airs, in UTC.

        The broadcast block is a weekday and a clock time in Japan. Combined
        with the premiere date that is a real instant, which is what the
        calendar needs to place a show on a weekday rather than in a season.
        """
        broadcast = node.get("broadcast")
        start = cls._start_date(node)
        if not isinstance(broadcast, dict) or start is None:
            return None
        raw_time = broadcast.get("start_time")
        if not isinstance(raw_time, str) or not raw_time:
            return None
        try:
            hour, minute = (int(part) for part in raw_time.split(":", 1))
            clock = time(hour=hour, minute=minute)
        except (TypeError, ValueError):
            return None
        try:
            tokyo = ZoneInfo("Asia/Tokyo")
        except ZoneInfoNotFoundError:  # pragma: no cover - tzdata is a dependency
            return None
        local = datetime.combine(start, clock, tzinfo=tokyo)
        # A programme after midnight is filed under the previous evening in
        # Japan, so the slot can land before the premiere. An episode does not
        # air before it airs.
        if local.date() < start:
            local += timedelta(days=1)
        return local.astimezone(ZoneInfo("UTC"))

    @staticmethod
    def _genres(node: dict[str, object]) -> tuple[str, ...]:
        values = node.get("genres")
        if not isinstance(values, list):
            return ()
        names = [
            str(item.get("name"))
            for item in values
            if isinstance(item, dict) and item.get("name")
        ]
        return tuple(dict.fromkeys(names))

    @classmethod
    def _to_candidate(cls, node: dict[str, object]) -> AnimeCandidate:
        alternatives = node.get("alternative_titles")
        alternatives = alternatives if isinstance(alternatives, dict) else {}
        english = alternatives.get("en") or None
        native = alternatives.get("ja") or None
        synonyms = alternatives.get("synonyms")
        romaji = node.get("title")
        episodes = node.get("num_episodes")
        mal_id = node.get("id")
        return AnimeCandidate(
            provider="mal",
            provider_id=str(mal_id or ""),
            display_name=str(english or romaji or native or "Unknown"),
            title_english=str(english) if english else None,
            title_romaji=str(romaji) if romaji else None,
            title_native=str(native) if native else None,
            aliases=(
                tuple(str(name) for name in synonyms if name)
                if isinstance(synonyms, list)
                else ()
            ),
            cover_url=cls._cover(node),
            genres=cls._genres(node),
            studios=tuple(
                item.get("name")
                for item in (node.get("studios") or [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ),
            synopsis=as_plain_text(node.get("synopsis")),
            format=str(node.get("media_type")) if node.get("media_type") else None,
            # Zero means "not announced", which is not the same as zero
            # episodes and should not be shown as one.
            episode_count=int(episodes) if isinstance(episodes, int) and episodes else None,
            # white / gray / black. Only black is what the calendar's filter
            # is about; gray is suggestive, not adult. The genre is checked as
            # well because the two do not always agree, and the genre is the
            # one that is always present.
            is_adult=(
                str(node.get("nsfw") or "").lower() == "black"
                or genres_are_adult(cls._genres(node))
            ),
            # Already among the requested fields; nothing read it until
            # build 148. not_yet_aired / currently_airing / finished_airing.
            airing_status=airing_status(node.get("status")),
            popularity=_count(node.get("num_list_users")),
            air_start_date=cls._start_date(node),
            air_slot_at=cls._air_slot(node),
            # The id every other source cross-references. This is the reason
            # MyAnimeList is worth a credential at all.
            provider_ids={"myanimelist": str(mal_id)} if mal_id else {},
        )


__all__ = ["MalProvider"]
