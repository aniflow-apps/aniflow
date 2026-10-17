"""What Shoko Server knows about a show, for the gaps nothing else could close (build 263).

*"Fill in missing should also check Shoko Server (if connected), and get pictures and infos from Shoko if they are
only available there."*

"Fill in missing" asked the calendar, then the metadata source chosen in Settings, then the other sources by id.
A show that none of them knows - an old OVA, a title the sources spell differently - stayed a row of gaps while
Shoko, which keeps AniDB's own record of everything it has ever seen, could have answered. This is that last step.

**It only reads.** Every call here is a GET on Shoko's own cache of AniDB and of its series; nothing changes in Shoko
and nothing reaches AniDB (``fetch_anidb_anime``, the one call that does, is not used - a show Shoko has never seen is
simply not answered). It is called only for an anime that still has a gap, and only fills what is *missing*
(``metadata_fill.close_gaps_from``): a value any other source or the user gave is never replaced.

What Shoko answers with, and where it goes:

* the AniDB record (``/Series/AniDB/{aid}``): the description, the titles, the kind, the episode count, the premiere
  date, the 18+ flag, and a poster reference - for any show Shoko has seen, in the library or not;
* when Shoko *holds* the series: its tags (the ones that are genres), the studios from its cast list, its MyAnimeList
  id, and its own poster.

The tags and the cast list are read tolerantly - Shoko's shapes have moved between versions and these two have not
been seen against the user's Shoko - and a shape that is not understood fills nothing rather than something wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from backend.app.core.logging import for_component
from backend.app.integrations.metadata.base import AnimeCandidate
from backend.app.integrations.shoko.client import ShokoError, client_from_settings
from backend.app.models.anime import Anime
from backend.app.services import shoko_status

logger = for_component("Shoko")

#: AniDB's kinds as AniFlow names them (the calendar's own list, ``calendar_metadata.KNOWN_KINDS``). A kind with no
#: clear counterpart ("Other", "Unknown") is left out: an empty format is honest, a wrong one is not.
FORMATS = {
    "tv": "TV", "tvseries": "TV", "movie": "MOVIE", "ova": "OVA", "web": "ONA", "ona": "ONA",
    "tvspecial": "TV_SPECIAL", "musicvideo": "MUSIC",
}

#: The tags that are genres in the sense every other source uses the word, by the spelling AniFlow shows. AniDB has
#: hundreds of tags (themes, settings, content); copying them all in would bury the genre chips and say little.
GENRES = {
    "action": "Action", "adventure": "Adventure", "comedy": "Comedy", "drama": "Drama", "ecchi": "Ecchi",
    "fantasy": "Fantasy", "horror": "Horror", "magical girl": "Mahou Shoujo", "mahou shoujo": "Mahou Shoujo",
    "mecha": "Mecha", "music": "Music", "mystery": "Mystery", "psychological": "Psychological",
    "romance": "Romance", "science fiction": "Sci-Fi", "sci-fi": "Sci-Fi", "slice of life": "Slice of Life",
    "sports": "Sports", "sport": "Sports", "supernatural": "Supernatural", "thriller": "Thriller",
}

#: The labels a cast row carries when it is a studio. Anything else in the list - voice actors, staff - is not.
STUDIO_ROLES = {"studio", "animation work", "animation production", "animation"}


@dataclass
class ShokoFacts:
    """What Shoko said about one anime."""

    candidate: AnimeCandidate
    #: Shoko's own id for the series, when it holds it.
    shoko_id: int | None
    #: The poster to fetch for a show Shoko does not hold: ``(source, id)`` from the AniDB record.
    poster_ref: tuple[str, str] | None = None


_BBCODE_URL = re.compile(r"\[url=[^\]]*\](.*?)\[/url\]", re.I | re.S)
_ANIDB_LINK = re.compile(r"https?://anidb\.net/\S+\s*\[(.*?)\]", re.I)
_BBCODE_TAG = re.compile(r"\[/?(?:i|b|u|s|spoiler|quote|code|color|size)(?:=[^\]]*)?\]", re.I)
#: "Source: ANN", "Note: ...", "* Based on ..." - AniDB's footnotes on a description.
_SOURCE_LINE = re.compile(r"^\s*(?:source|note)\s*:.*$", re.I | re.M)


def clean_description(text: object) -> str | None:
    """AniDB's description as plain text: its link and formatting markup removed, its footnotes dropped."""
    if not isinstance(text, str):
        return None
    cleaned = _BBCODE_URL.sub(r"\1", text)
    cleaned = _ANIDB_LINK.sub(r"\1", cleaned)
    cleaned = _BBCODE_TAG.sub("", cleaned)
    cleaned = _SOURCE_LINE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or None


def _titles(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(romaji, English, Japanese) from the titles Shoko lists, each only when it is stated as such."""
    romaji = english = native = None
    rows = payload.get("Titles")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, language = row.get("Name"), str(row.get("Language") or "").lower()
        kind = str(row.get("Type") or "").lower()
        if not name:
            continue
        if kind == "main" and romaji is None:
            romaji = str(name)
        elif kind == "official" and language.startswith("en") and english is None:
            english = str(name)
        elif kind == "official" and language.startswith("ja") and native is None:
            native = str(name)
    return romaji or (str(payload["Title"]) if payload.get("Title") else None), english, native


def _rows(answer: object) -> list[dict[str, Any]]:
    """The rows of an answer that is a list, or a ``{"List": [...]}`` / ``{"data": [...]}`` around one."""
    if isinstance(answer, dict):
        answer = answer.get("List") or answer.get("data") or []
    return [row for row in answer if isinstance(row, dict)] if isinstance(answer, list) else []


def genres_from(tags: object) -> tuple[str, ...]:
    """The tags that are genres, in Shoko's order, once each."""
    found: dict[str, None] = {}
    for row in _rows(tags):
        name = str(row.get("Name") or row.get("name") or "").strip().casefold()
        if name in GENRES:
            found[GENRES[name]] = None
    return tuple(found)


def studios_from(cast: object) -> tuple[str, ...]:
    """The studios of a cast list: only the rows that say they are one."""
    found: dict[str, None] = {}
    for row in _rows(cast):
        role = str(row.get("RoleName") or row.get("Role") or row.get("RoleType") or "").strip().casefold()
        staff = row.get("Staff") if isinstance(row.get("Staff"), dict) else {}
        name = str(staff.get("Name") or row.get("Name") or "").strip()
        if name and role in STUDIO_ROLES:
            found[name] = None
    return tuple(found)


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _poster_ref(payload: dict[str, Any]) -> tuple[str, str] | None:
    poster = payload.get("Poster")
    if isinstance(poster, dict):
        source, image_id = poster.get("Source") or poster.get("source"), poster.get("ID") or poster.get("id")
        if source and image_id is not None:
            return str(source), str(image_id)
    return None


def facts_for(anime: Anime) -> ShokoFacts | None:
    """What Shoko knows about this anime, or None. Never raises: a Shoko that is down is no news here."""
    from backend.app.services import shoko_library

    if not shoko_status.configured():
        return None
    aid = anime.anidb_id or (anime.provider_ids or {}).get("anidb")
    if not str(aid or "").isdigit():
        return None  # by id only: a name is how the wrong show gets described
    aid = int(aid)
    try:
        client = client_from_settings()
        payload = client.anidb_anime(aid)
    except ShokoError as exc:
        logger.debug("Shoko has nothing on anidb %s: %s", aid, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - this is a helper to a refill and must never break it
        logger.debug("Shoko could not be asked about anidb %s: %s", aid, exc)
        return None
    if not isinstance(payload, dict):
        return None
    shoko_id = shoko_library.shoko_id_in(payload)

    genres: tuple[str, ...] = ()
    studios: tuple[str, ...] = ()
    ids: dict[str, str] = {"anidb": str(aid)}
    if shoko_id is not None:
        # What only a series Shoko *holds* has. Each is its own question and its own failure.
        genres = _ask(lambda: genres_from(client._get(f"/api/v3/Series/{shoko_id}/Tags")), ())
        studios = _ask(
            lambda: studios_from(client._get(f"/api/v3/Series/{shoko_id}/Cast", params={"roleType": "Studio"})), ()
        )
        mal = _ask(lambda: _mal_id(client._get(f"/api/v3/Series/{shoko_id}")), None)
        if mal:
            ids["myanimelist"] = mal

    romaji, english, native = _titles(payload)
    kind = str(payload.get("Type") or "").replace(" ", "").casefold()
    count = payload.get("EpisodeCount")
    candidate = AnimeCandidate(
        provider="shoko",
        provider_id=str(shoko_id or aid),
        display_name=english or romaji or anime.display_name,
        title_romaji=romaji,
        title_english=english,
        title_native=native,
        genres=genres,
        studios=studios,
        synopsis=clean_description(payload.get("Description")),
        format=FORMATS.get(kind),
        episode_count=count if isinstance(count, int) and count > 0 else None,
        is_adult=bool(payload.get("Restricted")),
        air_start_date=_date(payload.get("AirDate")),
        provider_ids=ids,
    )
    return ShokoFacts(candidate=candidate, shoko_id=shoko_id, poster_ref=_poster_ref(payload))


def _mal_id(series: object) -> str | None:
    """Shoko's MyAnimeList id for a series - only when there is exactly one, since it names this entry."""
    ids = series.get("IDs") if isinstance(series, dict) and isinstance(series.get("IDs"), dict) else {}
    mal = [value for value in ids.get("MAL") or [] if isinstance(value, int)]
    return str(mal[0]) if len(mal) == 1 else None


def _ask(question, fallback):
    try:
        return question()
    except Exception as exc:  # noqa: BLE001 - one unanswered question must not lose the others
        logger.debug("Shoko did not answer: %s", exc)
        return fallback


def poster_bytes(facts: ShokoFacts) -> bytes | None:
    """The poster, fetched with Shoko's key and handed back as bytes (the browser never holds the key)."""
    from backend.app.services import shoko_library

    if facts.shoko_id is not None:
        data = shoko_library.poster_bytes_for_series(facts.shoko_id)
        if data:
            return data
    if facts.poster_ref is not None:
        source, image_id = facts.poster_ref
        try:
            return client_from_settings().image_bytes(f"/api/v3/Image/{source}/Poster/{image_id}") or None
        except Exception as exc:  # noqa: BLE001 - a picture is never worth an error
            logger.debug("Shoko poster %s/%s could not be read: %s", source, image_id, exc)
    return None


__all__ = [
    "FORMATS", "GENRES", "ShokoFacts", "clean_description", "facts_for", "genres_from", "poster_bytes", "studios_from",
]
