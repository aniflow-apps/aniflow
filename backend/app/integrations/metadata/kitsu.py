"""Kitsu, as the independent second opinion (build 75).

Jikan was the first attempt at a fallback and it failed on the first outage:
it is a proxy in front of MyAnimeList, so when MyAnimeList refused connections
Jikan answered 504 and AniFlow was no better off than with one provider. Two
sources are only worth having if they are two *independent* sources.

Kitsu runs its own database on its own infrastructure. Nothing it answers
depends on AniList or MyAnimeList being up, which is the entire point of it
being here.

Three things differ from AniList and are handled here rather than left to the
caller:

**JSON:API.** Everything real is under ``data[].attributes``, ids are strings,
and relationships are links rather than embedded objects. That last part is why
**genres are not fetched**: they live behind a second request per anime, which
would turn one search into twenty. An empty genre list is honest; twenty
requests to fill a field nothing filters on would not be.

**No broadcast time.** Kitsu has no weekly slot at all, so ``air_slot_at`` is
always None and the weekday falls back to the premiere date - which
``slot_weekday`` has always done for shows AniList had no slot for. A calendar
served from Kitsu therefore places shows on the right day and shows no time,
rather than inventing one.

**Adult content** is a rating (``R18``) plus an ``nsfw`` flag, and both are
read: the calendar's filter is only as good as the flag behind it.
"""
from datetime import date

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.version import user_agent
from backend.app.integrations.metadata.adult import genres_are_adult
from backend.app.integrations.metadata.airing import airing_status
from backend.app.integrations.metadata.base import AnimeCandidate, as_plain_text

logger = for_component("Kitsu")


def _count(value: object) -> int | None:
    """A popularity count, when the source published one."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

#: Kitsu's own season names, which happen to match AniList's lowercased.
_SEASONS = ("winter", "spring", "summer", "fall")

#: The rating that means adult. R is violence and language; R18 is the one the
#: calendar's filter is about.
_ADULT_RATINGS = ("r18",)

#: How many categories to keep. Kitsu files a show under a dozen or more,
#: including several that are really content warnings, and a card shows three.
_MAX_CATEGORIES = 8

#: Kitsu's names for the other databases, mapped to AniFlow's (build 84).
#:
#: Kitsu keeps a cross-reference for most of its catalogue and publishes it as
#: an includable relationship, so this costs one query parameter and no extra
#: requests. It is worth far more than it looks: an id from here needs no
#: title matching at all, and for a season AniDB has not filed yet - which is
#: most of a current season - Kitsu's mapping is sometimes the only AniDB id
#: available.
_MAPPED_SITES = {
    "myanimelist/anime": "myanimelist",
    "anidb": "anidb",
    "anidb/anime": "anidb",
    "thetvdb/series": "thetvdb",
    "themoviedb/movie": "themoviedb",
    "anilist/anime": "anilist",
}


class KitsuProvider:
    """The Kitsu catalogue, over its public JSON:API."""

    name = "kitsu"

    #: 20 an entry, and a season runs to a few hundred once shorts and ONAs are
    #: counted. The cap stops a broken `next` link from looping for ever.
    _PAGE_SIZE = 20
    _MAX_SEASON_PAGES = 10

    def __init__(self) -> None:
        settings = get_settings()
        self._url = settings.kitsu_api_url.rstrip("/")
        self._timeout = settings.external_request_timeout_seconds

    async def search_anime(self, query: str, limit: int = 20) -> list[AnimeCandidate]:
        payload = await self._get(
            "/anime", {"filter[text]": query, "page[limit]": min(limit, 20)}
        )
        return [self._to_candidate(item) for item in self._entries(payload)]

    async def get_anime(self, provider_id: str) -> AnimeCandidate | None:
        if not provider_id.isdigit():
            return None
        # With the mappings and categories, as the season listing asks for
        # them (build 154). Without them one show looked up by id came back
        # with no cross-reference ids and no genres - the half of Kitsu that
        # makes it worth asking.
        payload = await self._get(f"/anime/{provider_id}", {"include": "mappings,categories"})
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return self._to_candidate(data, self._mappings(payload), self._categories(payload))

    async def find_by_external_id(self, site: str, external_id: str) -> str | None:
        """Kitsu's own id for the entry another database knows by ``external_id`` (build 239).

        Kitsu keeps those cross-references itself, so this is an exact lookup by id - not a search by
        name. ``site`` is Kitsu's spelling (``myanimelist/anime``, ``anidb``).
        """
        if not str(external_id).isdigit():
            return None
        payload = await self._get(
            "/mappings",
            {
                "filter[externalSite]": site,
                "filter[externalId]": external_id,
                "include": "item",
                "page[limit]": 1,
            },
        )
        for entry in self._entries(payload):
            relationships = entry.get("relationships")
            item = relationships.get("item") if isinstance(relationships, dict) else None
            reference = item.get("data") if isinstance(item, dict) else None
            if isinstance(reference, dict) and reference.get("type") == "anime" and reference.get("id"):
                return str(reference["id"])
        return None

    async def get_season(self, season: str, year: int) -> list[AnimeCandidate]:
        """Every anime Kitsu lists for this season, across its pages."""
        name = season.strip().lower()
        if name not in _SEASONS:
            raise httpx.HTTPError(f"{season!r} is not a season Kitsu knows")

        candidates: list[AnimeCandidate] = []
        seen: set[str] = set()
        for page in range(self._MAX_SEASON_PAGES):
            payload = await self._get(
                "/anime",
                {
                    # The cross-reference ids and the genres, in the same
                    # response. Both are includable relationships, so this is
                    # one parameter and no extra requests - see _MAPPED_SITES,
                    # and _categories for what build 83 got wrong here.
                    "include": "mappings,categories",
                    "filter[season]": name,
                    "filter[seasonYear]": year,
                    "page[limit]": self._PAGE_SIZE,
                    "page[offset]": page * self._PAGE_SIZE,
                    # Popularity first, so a truncated list is still the part
                    # of the season anyone was looking for.
                    "sort": "-userCount",
                },
            )
            entries = self._entries(payload)
            mappings = self._mappings(payload)
            categories = self._categories(payload)
            for item in entries:
                candidate = self._to_candidate(item, mappings, categories)
                if candidate.provider_id in seen:
                    continue
                seen.add(candidate.provider_id)
                candidates.append(candidate)
            links = payload.get("links")
            has_next = bool(links.get("next")) if isinstance(links, dict) else False
            if not entries or not has_next:
                break
        return candidates

    # -- the wire ------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, object] | None) -> dict[str, object]:
        from backend.app.services.outbound import client_kwargs

        target = f"{self._url}{path}"
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "User-Agent": user_agent(),
                # JSON:API asks for its own content type; Kitsu answers 406
                # without it.
                "Accept": "application/vnd.api+json",
            },
            **client_kwargs(target),
        ) as client:
            response = await client.get(target, params=params)
            if response.is_error:
                raise httpx.HTTPStatusError(
                    self._explain(response), request=response.request, response=response
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise httpx.HTTPStatusError(
                    f"Kitsu returned something that is not JSON: {exc}",
                    request=response.request,
                    response=response,
                ) from exc
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _explain(response: httpx.Response) -> str:
        """The reason, from JSON:API's own errors array when there is one."""
        detail = None
        try:
            body = response.json()
            errors = body.get("errors") if isinstance(body, dict) else None
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                detail = errors[0].get("detail") or errors[0].get("title")
        except ValueError:
            detail = None
        return f"Kitsu answered {response.status_code}: {detail or response.reason_phrase}"

    @staticmethod
    def _entries(payload: dict[str, object]) -> list[dict[str, object]]:
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    @staticmethod
    def _mappings(payload: dict[str, object]) -> dict[str, tuple[str, str]]:
        """The included mappings, by their own id: ``{mapping id: (site, id)}``.

        JSON:API keeps included resources in a flat list and each anime refers
        to them by id, so this is built once per page and looked up per entry.
        """
        found: dict[str, tuple[str, str]] = {}
        included = payload.get("included")
        if not isinstance(included, list):
            return found
        for item in included:
            if not isinstance(item, dict) or item.get("type") != "mappings":
                continue
            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                continue
            site = attributes.get("externalSite")
            external = attributes.get("externalId")
            if isinstance(site, str) and external not in (None, ""):
                found[str(item.get("id"))] = (site, str(external))
        return found

    @staticmethod
    def _categories(payload: dict[str, object]) -> dict[str, str]:
        """The included categories, by their own id: ``{id: title}``.

        Kitsu's word for genres. Build 83 recorded that these "sit behind
        another request per anime" and returned none at all - which was wrong,
        and made the comparison page report Kitsu as having no genres when in
        fact it publishes several per show.
        """
        found: dict[str, str] = {}
        included = payload.get("included")
        if not isinstance(included, list):
            return found
        for item in included:
            if not isinstance(item, dict) or item.get("type") != "categories":
                continue
            attributes = item.get("attributes")
            title = attributes.get("title") if isinstance(attributes, dict) else None
            if isinstance(title, str) and title:
                found[str(item.get("id"))] = title
        return found

    @staticmethod
    def _genres_of(entry: dict[str, object], categories: dict[str, str]) -> tuple[str, ...]:
        """This anime's categories, in the order Kitsu lists them."""
        relationships = entry.get("relationships")
        if not isinstance(relationships, dict) or not categories:
            return ()
        block = relationships.get("categories")
        data = block.get("data") if isinstance(block, dict) else None
        if not isinstance(data, list):
            return ()
        names: list[str] = []
        for reference in data:
            if not isinstance(reference, dict):
                continue
            title = categories.get(str(reference.get("id")))
            if title and title not in names:
                names.append(title)
        return tuple(names[:_MAX_CATEGORIES])

    @staticmethod
    def _cross_ids(
        entry: dict[str, object], mappings: dict[str, tuple[str, str]]
    ) -> dict[str, str]:
        """This anime's ids in the other databases, as Kitsu records them."""
        relationships = entry.get("relationships")
        if not isinstance(relationships, dict) or not mappings:
            return {}
        block = relationships.get("mappings")
        data = block.get("data") if isinstance(block, dict) else None
        if not isinstance(data, list):
            return {}
        found: dict[str, str] = {}
        for reference in data:
            if not isinstance(reference, dict):
                continue
            pair = mappings.get(str(reference.get("id")))
            if pair is None:
                continue
            site, external = pair
            name = _MAPPED_SITES.get(site.lower())
            # Kitsu's own numeric ids for a site are not always digits (TVDB
            # uses "12345-1"), and everything downstream compares strings, so
            # they are stored as given.
            if name and name not in found:
                found[name] = external
        return found

    # -- mapping -------------------------------------------------------------

    @staticmethod
    def _start_date(attributes: dict[str, object]) -> date | None:
        raw = attributes.get("startDate")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    @classmethod
    def _is_adult(cls, attributes: dict[str, object], genres=()) -> bool:
        """Kitsu's own flag, or an adult category.

        The genres are the half that works: only some entries carry an
        ``ageRating`` at all, and build 85 stored 140 Kitsu rows with none of
        them flagged. See integrations/metadata/adult.py.
        """
        rating = str(attributes.get("ageRating") or "").strip().lower()
        if rating in _ADULT_RATINGS:
            return True
        if attributes.get("nsfw"):
            return True
        return genres_are_adult(genres)

    @staticmethod
    def _cover(attributes: dict[str, object]) -> str | None:
        """The largest poster Kitsu offers."""
        poster = attributes.get("posterImage")
        if not isinstance(poster, dict):
            return None
        for key in ("original", "large", "medium", "small"):
            value = poster.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @classmethod
    def _names(cls, attributes: dict[str, object]) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
        """English, romaji, native and the alternatives.

        Kitsu keys its titles by language: ``en`` is the English title,
        ``en_jp`` the romanised Japanese one - which is what AniFlow calls
        romaji - and ``ja_jp`` the native one.
        """
        titles = attributes.get("titles")
        titles = titles if isinstance(titles, dict) else {}
        english = titles.get("en") or titles.get("en_us")
        romaji = titles.get("en_jp") or attributes.get("canonicalTitle")
        native = titles.get("ja_jp")
        aliases = [
            value
            for value in attributes.get("abbreviatedTitles") or []
            if isinstance(value, str) and value
        ]
        canonical = attributes.get("canonicalTitle")
        if isinstance(canonical, str) and canonical and canonical not in (english, romaji, native):
            aliases.append(canonical)
        return (
            english if isinstance(english, str) else None,
            romaji if isinstance(romaji, str) else None,
            native if isinstance(native, str) else None,
            tuple(aliases),
        )

    def _to_candidate(
        self,
        entry: dict[str, object],
        mappings: dict[str, tuple[str, str]] | None = None,
        categories: dict[str, str] | None = None,
    ) -> AnimeCandidate:
        identifier = str(entry.get("id"))
        attributes = entry.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        english, romaji, native, aliases = self._names(attributes)
        genres = self._genres_of(entry, categories or {})
        episodes = attributes.get("episodeCount")
        # Kitsu says "TV", "movie", "ONA", "OVA", "special" in mixed case;
        # AniFlow shows the value as-is, so it is normalised here.
        show_type = attributes.get("showType")
        return AnimeCandidate(
            provider=self.name,
            provider_id=identifier,
            display_name=english or romaji or native or identifier,
            title_english=english,
            title_romaji=romaji,
            title_native=native,
            aliases=aliases,
            cover_url=self._cover(attributes),
            genres=genres,
            format=show_type.upper() if isinstance(show_type, str) else None,
            episode_count=episodes if isinstance(episodes, int) else None,
            is_adult=self._is_adult(attributes, genres),
            # Kitsu says current / finished / unreleased / upcoming / tba.
            airing_status=airing_status(attributes.get("status")),
            # Kitsu's synopsis comes with the listing; its studios sit behind
            # a second request per anime, the way its categories do - so the
            # merge takes them from a source that carries them (build 101).
            synopsis=as_plain_text(attributes.get("synopsis")),
            # How many Kitsu users have it on a list.
            popularity=_count(attributes.get("userCount")),
            air_start_date=self._start_date(attributes),
            # Kitsu has no weekly slot. The weekday still comes from the
            # premiere date downstream; a made-up time would be worse than none.
            air_slot_at=None,
            provider_ids={"kitsu": identifier, **self._cross_ids(entry, mappings or {})},
            raw_metadata=entry,
        )


__all__ = ["KitsuProvider"]
