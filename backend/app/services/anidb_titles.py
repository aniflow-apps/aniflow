"""AniDB's title dump: one id per show, and every name it goes by (build 83).

The problem this solves was reported against build 82. The watchlist's *Only
in Shoko* filter offered a series that was already in the watchlist, because
Shoko knows it by one name and AniList by another. Title matching gets most of
those, and "most" is the wrong standard for "should I add this show again".

AniDB publishes ``anime-titles.dat.gz``: about two megabytes, every anime it
knows, and for each one every spelling - the primary romaji title, the official
title per language, synonyms and short forms. Shoko already tells us the AniDB
id of everything it holds. So if a watchlist entry can be resolved to an AniDB
id by its titles, the two libraries can be compared on an id instead of on a
guess.

That makes AniDB the spine of AniFlow's identity, which is what it is good
for. It publishes no seasonal line-up, so it is not a calendar source - see
services/calendar_update.SEASON_PROVIDERS for the ones that are.

**Fetching politely.** AniDB asks for this file at most once a day; AniFlow
takes it weekly, because titles for a show that already exists do not change
and a new show's titles matter within days rather than minutes. The file is
kept on disk, so a failed download leaves the previous one in place and
nothing stops working.
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import httpx
from sqlalchemy import delete, or_, select

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.version import user_agent
from backend.app.db import calendar_store
from backend.app.models.calendar_sources import AnidbAnime, AnidbTitle, DumpState

logger = for_component("AniDB")

#: Where AniDB publishes it. Documented, and the only sanctioned bulk access
#: to their titles - the website itself must not be crawled.
DUMP_URL = "https://anidb.net/api/anime-titles.dat.gz"

#: How often to fetch it. AniDB's own limit is once a day; a week is well
#: inside that and still catches a new season's shows within days.
REFRESH_INTERVAL = timedelta(days=7)

#: How long to leave it alone after a failed attempt. The worker asks every
#: half hour, so without this a refused download would become forty-eight
#: requests a day to a service that permits one - which is how a polite client
#: becomes an impolite one by accident. The button ignores this.
RETRY_INTERVAL = timedelta(hours=6)

#: Titles this short match too much to be useful. "Air" and "Kimi" are real
#: primary titles, so this only drops the truly ambiguous.
_MIN_TITLE_LENGTH = 2

#: How many names to ask about in one statement. SQLite's default ceiling on
#: bound parameters is what this is under.
_LOOKUP_CHUNK = 400

#: Roman numerals as far as a sequel ever gets. AniDB numbers its sequels this
#: way ("Youjo Senki II"); every other source writes them out ("2nd Season").
_ROMAN = {
    "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

#: The ways a trailing season marker is written, once the title has been
#: normalised (lowercase, no punctuation). Each pattern must end the string -
#: "part 2" in the middle of a title is part of the title.
_SEASON_TAIL = (
    re.compile(r"^(?P<base>.+?)\s+(?P<n>\d{1,2})(?:st|nd|rd|th)?\s+seasons?$"),
    re.compile(r"^(?P<base>.+?)\s+seasons?\s+(?P<n>\d{1,2})$"),
    re.compile(r"^(?P<base>.+?)\s+(?:part|cour)\s+(?P<n>\d{1,2})$"),
    re.compile(r"^(?P<base>.+?)\s+s(?P<n>\d{1,2})$"),
    re.compile(r"^(?P<base>.+?)\s+(?P<roman>ii|iii|iv|v|vi|vii|viii|ix|x)$"),
)

#: Kinds AniDB uses. 1 primary, 2 synonym, 3 short, 4 official.
PRIMARY, SYNONYM, SHORT, OFFICIAL = 1, 2, 3, 4

#: Short forms are deliberately not indexed: "FMA" or "SAO" belong to several
#: shows between them, and a wrong id is worse here than no id at all.
INDEXED_KINDS = (PRIMARY, SYNONYM, OFFICIAL)

#: What each kind of title is worth when candidates disagree (build 84).
#:
#: Build 83 required unanimity - every title had to point at one id or the
#: answer was "no idea". On real data that threw away most of the matches: an
#: anime carries four or five names, and one synonym shared with a prequel was
#: enough to veto a primary title and two official ones. So the titles vote,
#: and a title AniDB calls official counts for more than a fan synonym.
KIND_WEIGHT = {PRIMARY: 4, OFFICIAL: 3, SYNONYM: 1}

#: How far ahead the winner has to be. A tie, or a near-tie, still means "no
#: idea" - which is the half of build 83's rule that was right.
WIN_MARGIN = 2


@dataclass(frozen=True)
class DumpStatus:
    """What the page shows about the dump."""

    present: bool = False
    downloaded_at: datetime | None = None
    parsed_at: datetime | None = None
    anime_count: int = 0
    title_count: int = 0
    bytes_downloaded: int = 0
    last_error: str | None = None
    #: The last attempt, successful or not. What the backoff is measured from.
    attempted_at: datetime | None = None
    #: When AniFlow will fetch it again, so "old" and "about to refresh" are
    #: different things on screen.
    next_refresh_at: datetime | None = None


def dump_path() -> Path:
    """Where the downloaded file is kept, beside the databases."""
    settings = get_settings()
    root = Path(str(settings.database_url).split("///")[-1]).parent
    return root / "anidb-titles.dat.gz"


def normalize(title: str) -> str:
    """A title reduced to what two sources can be expected to agree on.

    The same aggressive form release matching uses: case, punctuation and
    spacing removed. "Re:ZERO -Starting Life-" and "re zero starting life"
    have to be the same string for any of this to work.
    """
    lowered = title.casefold().replace("&", " and ")
    lowered = lowered.replace("'", "").replace("’", "")
    # Latin letters, digits, and the Japanese scripts. Everything else goes,
    # which is what makes the Japanese titles usable: the punctuation around
    # them differs per source - the ideographic space, the angle brackets in
    # 魔法少女まどか☆マギカ 〈ワルプギスの廻天〉 - while the characters themselves do not.
    kept = r"a-z0-9ぁ-ヿ一-鿿"
    return re.sub(r"\s+", " ", re.sub(rf"[^{kept}]+", " ", lowered)).strip()


#: Every mark a title may use for its apostrophe. AniDB writes it as a backtick ("I`ll"); a folder, a release name
#: and every other source write ' or a typographic one.
_APOSTROPHES = re.compile("[\u2018\u2019\u201b\u02bc'`\u00b4]")


def forms_of(title: str) -> set[str]:
    """Every normalised form one title can be looked up by (build 273).

    ``normalize`` drops ' and the typographic apostrophe ("I'll" -> "ill") but turns a backtick into a space
    ("I`ll" -> "i ll"), and AniDB writes its apostrophes as backticks: so the folder "...I'll Use My..." and AniDB's
    "...I`ll Use My..." never met, over one tiny mark. The title is therefore also read with every apostrophe as a
    space, and both forms (and their season spellings) look it up. Only ever adds a way in: the exact title still
    matches exactly, and nothing here is fuzzy - the same characters, differently spelled.
    """
    forms = {normalize(title), normalize(_APOSTROPHES.sub(" ", title))}
    forms |= {alias for alias in (season_alias(form) for form in forms) if alias}
    return {form for form in forms if form}


def season_alias(normalized: str) -> str | None:
    """One canonical spelling for a title that ends in a season marker.

    AniDB calls the second season "II"; Kitsu calls it "2nd Season"; Shikimori
    writes "2nd Season" too and MyAnimeList often "Season 2". Same show, four
    strings, no match - and sequels are most of what a current season *is*, so
    this was a large part of why half a line-up came back unidentified.

    Both spellings are indexed, so the exact title still matches exactly and
    this only ever adds a way in. Returns ``None`` when the title carries no
    season marker, which is most of them.

    Deliberately *not* returning the bare base title: "Show" and "Show II" are
    different anime with different episode lists, and merging them would be a
    worse bug than the one this fixes.
    """
    for pattern in _SEASON_TAIL:
        match = pattern.match(normalized)
        if match is None:
            continue
        found = match.groupdict()
        number = int(found["n"]) if found.get("n") else _ROMAN[found["roman"]]
        if number < 2:
            # "1st Season" is how a source writes a show that AniDB simply
            # calls by its name, so there is nothing to canonicalise.
            return None
        alias = f"{match.group('base')} {number}"
        return alias if alias != normalized else None
    return None


def parse(text: Iterable[str]) -> Iterator[tuple[int, int, str, str]]:
    """The dump's lines as ``(aid, kind, language, title)``.

    The format is ``<aid>|<kind>|<language>|<title>`` with ``#`` comments. A
    malformed line is skipped rather than fatal: this is somebody else's file,
    it is two megabytes of it, and one bad row is not a reason to have no
    titles at all.
    """
    for line in text:
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("|", 3)
        if len(parts) != 4:
            continue
        raw_aid, raw_kind, language, title = parts
        try:
            aid = int(raw_aid)
            kind = int(raw_kind)
        except ValueError:
            continue
        title = title.strip()
        if not title:
            continue
        yield aid, kind, language.strip(), title


def download(*, force: bool = False, client: httpx.Client | None = None) -> DumpStatus:
    """Fetch the dump unless a recent copy is already on disk.

    Returns the status either way, so the caller can report "not due yet"
    without having to work out what "due" means.
    """
    status = current_status()
    now = datetime.now(UTC)
    if not force and status.present and status.downloaded_at is None:
        # A file with nothing recorded about it. That is what an updated
        # deployment looks like: build 84 discards calendar.db when the schema
        # changes, and the record of the download goes with it while the two
        # megabytes on disk stay. The file's own timestamp says when it
        # arrived, so it is adopted rather than fetched again - AniDB permits
        # this once a day and there is no reason at all to spend one of those.
        target = dump_path()
        logger.info("adopting the AniDB dump already on disk and reading it again")
        _record(downloaded_at=datetime.fromtimestamp(target.stat().st_mtime, UTC), error=None)
        return rebuild()
    if not force and status.present and status.downloaded_at is not None:
        if now - _as_utc(status.downloaded_at) < REFRESH_INTERVAL:
            if status.title_count == 0:
                # The file is here and the index is not, which happens when
                # calendar.db has been started again after a schema change
                # (build 84). Reading the file we already have costs a second
                # and nothing on the network - waiting a week for the next
                # download would leave every match to titles alone.
                logger.info("the title index is empty; reading the dump already on disk")
                return rebuild()
            logger.debug("the AniDB title dump is recent enough; not fetching")
            return status
    if not force and status.last_error and status.attempted_at is not None:
        if now - _as_utc(status.attempted_at) < RETRY_INTERVAL:
            logger.debug("the last AniDB download failed recently; not retrying yet")
            return status

    target = dump_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # AniDB is on the internet, so it follows the proxy setting - the same
    # rule every other outgoing request obeys. See services/outbound.py.
    from backend.app.services.outbound import client_kwargs

    owns_client = client is None
    http = client or httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": user_agent()},
        **client_kwargs(DUMP_URL),
    )
    try:
        _record(attempted_at=datetime.now(UTC))
        response = http.get(DUMP_URL, headers={"User-Agent": user_agent()})
        response.raise_for_status()
        payload = response.content
    except httpx.HTTPError as exc:
        # The previous file stays where it is: an old title index is worth far
        # more than none, and this runs unattended.
        reason = str(exc) or type(exc).__name__
        logger.warning("could not fetch the AniDB title dump: %s", reason)
        _record(error=reason)
        return current_status()
    finally:
        if owns_client:
            http.close()

    # Written whole, then moved: a half-written file that happened to be
    # readable would index a fraction of the titles and look fine.
    staging = target.with_suffix(".part")
    staging.write_bytes(payload)
    staging.replace(target)
    logger.info("AniDB title dump fetched: %s bytes", len(payload))
    _record(downloaded_at=datetime.now(UTC), bytes_downloaded=len(payload), error=None)
    return rebuild()


def rebuild() -> DumpStatus:
    """Read the file on disk into the title index, replacing what was there.

    Replaced rather than merged: a title AniDB has removed - a wrong synonym,
    say - has to disappear here too, and merging would keep it for ever.
    """
    path = dump_path()
    if not path.exists():
        _record(error="the dump has not been downloaded yet")
        return current_status()

    anime: dict[int, str] = {}
    titles: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for aid, kind, language, title in parse(handle):
                if kind == PRIMARY:
                    anime[aid] = title
                elif aid not in anime:
                    anime.setdefault(aid, title)
                if kind not in INDEXED_KINDS:
                    continue
                normalized = normalize(title)
                if len(normalized) < _MIN_TITLE_LENGTH:
                    continue
                for spelling in (normalized, season_alias(normalized)):
                    if spelling is None or (spelling, aid) in seen:
                        continue
                    seen.add((spelling, aid))
                    titles.append(
                        {
                            "title_norm": spelling,
                            # The spelling as written, for the pool's own
                            # sake - the season alias is a derived form and
                            # keeps the title it was derived from.
                            "title": title[:400],
                            "aid": aid,
                            "kind": kind,
                            "language": language,
                        }
                    )
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        reason = f"the dump could not be read: {exc}"
        logger.warning("%s", reason)
        _record(error=reason)
        return current_status()

    with calendar_store.sessions()() as session:
        session.execute(delete(AnidbTitle))
        session.execute(delete(AnidbAnime))
        session.bulk_insert_mappings(
            AnidbAnime, [{"aid": aid, "primary_title": name[:400]} for aid, name in anime.items()]
        )
        # In batches: a quarter of a million rows in one statement is a large
        # allocation for no gain.
        for start in range(0, len(titles), 5000):
            session.bulk_insert_mappings(AnidbTitle, titles[start : start + 5000])
        session.commit()
    logger.info("AniDB title index rebuilt: %s anime, %s titles", len(anime), len(titles))
    _record(parsed_at=datetime.now(UTC), anime_count=len(anime), title_count=len(titles), error=None)
    return current_status()


def resolve(titles: Iterable[str | None]) -> int | None:
    """The AniDB id for a show known by any of these names.

    The names vote, weighted by how much AniDB stands behind each of them -
    see KIND_WEIGHT - and the winner has to be clearly ahead. So four titles
    agreeing outvote one synonym that a prequel also uses, while two shows
    that genuinely share a name still resolve to nothing.
    """
    return resolve_many([tuple(titles)])[0]


def resolve_many(groups: Sequence[Iterable[str | None]]) -> list[int | None]:
    """The same question for a whole season, in one pass over the index.

    A season is a few hundred shows and a query each was most of what made a
    refresh take a minute and a half - and every one of them was a blocking
    call on the event loop. All the names are looked up together and the votes
    are counted in memory.
    """
    if not groups:
        return []
    if not calendar_store.exists():
        return [None] * len(groups)

    # Per candidate, per title, the spellings that title can be written as.
    # Nested rather than flattened because a title votes once however many
    # spellings it has: "Youjo Senki II" and "youjo senki 2" are one name, and
    # counting both would let a sequel outvote its own first season.
    per_group: list[list[set[str]]] = []
    every: set[str] = set()
    for titles in groups:
        spellings: list[set[str]] = []
        for title in titles:
            if not title:
                continue
            names = {name for name in forms_of(title) if len(name) >= _MIN_TITLE_LENGTH}
            if names:
                spellings.append(names)
                every |= names
        per_group.append(spellings)
    if not every:
        return [None] * len(groups)

    # (title, aid, kind) for every name any of them used. Chunked because
    # SQLite has a ceiling on how many values one statement may carry, and a
    # season can mention two thousand names.
    index: dict[str, list[tuple[int, int]]] = {}
    ordered = list(every)
    with calendar_store.sessions()() as session:
        for start in range(0, len(ordered), _LOOKUP_CHUNK):
            chunk = ordered[start : start + _LOOKUP_CHUNK]
            rows = session.execute(
                select(AnidbTitle.title_norm, AnidbTitle.aid, AnidbTitle.kind).where(
                    AnidbTitle.title_norm.in_(chunk)
                )
            ).all()
            for title_norm, aid, kind in rows:
                index.setdefault(title_norm, []).append((aid, kind))

    return [_vote(spellings, index) for spellings in per_group]


def _vote(
    spellings: list[set[str]], index: dict[str, list[tuple[int, int]]]
) -> int | None:
    """Which AniDB id these names point at, or None if they cannot agree.

    ``spellings`` is one entry per title, holding the ways that title can be
    written - see resolve_many.
    """
    scores: dict[int, int] = {}
    for names in spellings:
        # The best weight this one title carries for each anime it matches.
        weights: dict[int, int] = {}
        for name in names:
            for aid, kind in index.get(name, ()):
                weights[aid] = max(weights.get(aid, 0), KIND_WEIGHT.get(kind, 1))
        if not weights:
            continue
        # A title that matches several anime says less about each of them, so
        # its vote is divided. Without this, a synonym shared by a franchise of
        # six would count as six separate endorsements.
        share = len(weights)
        for aid, weight in weights.items():
            scores[aid] = scores.get(aid, 0) + max(1, weight // share)
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    best, best_score = ranked[0]
    if len(ranked) == 1:
        # Nothing to disagree with. This is build 83's rule, and it was the
        # right rule for this case: one name matching one anime is an answer,
        # even when that name is only a synonym. Requiring a margin here is
        # what made the first attempt at this *worse* than what it replaced -
        # measured against the real dump, Kitsu went from 81 matches to 72.
        return best
    if best_score - ranked[1][1] >= WIN_MARGIN:
        return best
    logger.debug("titles %s cannot agree on an AniDB id: %s", sorted(names), ranked[:3])
    return None


@dataclass(frozen=True)
class DumpTitles:
    """Every name the dump has for one anime, in its own spelling.

    The stored index keeps titles *normalised*, because that is all matching
    needs and it makes the column indexable. Building a watchlist entry needs
    the spellings themselves, so this reads them from the file - one scan of
    1.4 MB, once, when somebody adopts a series.
    """

    aid: int
    #: AniDB's main title. Romaji, and the name Shoko shows.
    primary: str
    #: The official English title, when AniDB has one.
    english: str | None = None
    #: The Japanese title, in Japanese.
    native: str | None = None
    #: Everything else it is known by, official titles in other languages
    #: included. Deduplicated, order kept.
    synonyms: tuple[str, ...] = ()


def titles_for(aid: int) -> DumpTitles | None:
    """Every spelling the dump has for ``aid``, or None if it has none.

    Reads the downloaded file rather than the index. No network: this is the
    same file AniFlow already fetches weekly, and AniDB's website must never
    be scraped - see the module docstring.
    """
    path = dump_path()
    if not path.exists():
        return None
    primary: str | None = None
    english: str | None = None
    native: str | None = None
    synonyms: list[str] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line_aid, kind, language, title in parse(handle):
                if line_aid != aid:
                    continue
                if kind == PRIMARY:
                    primary = title
                elif kind == OFFICIAL and language == "en" and english is None:
                    english = title
                elif language == "ja" and native is None:
                    native = title
                elif kind in INDEXED_KINDS:
                    synonyms.append(title)
    except OSError as exc:
        logger.info("the AniDB dump could not be read for %s: %s", aid, exc)
        return None
    if primary is None:
        # No primary title means the id is not in this dump - a show newer
        # than the file, or a wrong id. Either way there is nothing to build.
        return None
    seen = {primary, english, native}
    unique = tuple(dict.fromkeys(s for s in synonyms if s not in seen))
    return DumpTitles(
        aid=aid, primary=primary, english=english, native=native, synonyms=unique
    )


def names_for(aid: int) -> dict[str, str]:
    """AniDB's names for ``aid`` by language, for storing on an anime.

    ``{"main": "Saga of Tanya the Evil II", "en": "Saga of Tanya the Evil
    Season 2", "ja": "\u5e7c\u5973\u6226\u8a18II", "de": "..."}``, and an empty map when
    the dump has nothing - a show newer than the file, or a wrong id.

    Different from :func:`titles_for`, which answers "every spelling, for
    matching" and throws the languages away. This keeps the language, because
    a watchlist entry wants to *show* these: the main title is what Shoko
    shows, the Japanese one is what nothing else in AniFlow has in Japanese,
    and which other languages exist is AniDB's business rather than a list
    hard-coded here.

    The titles AniDB calls its own - the main one and the official ones. The one
    exception (build 237) is the English slot: with no official English title,
    the first synonym AniDB itself files under English stands in, because the
    alternative is a show with no English name at all. Other fan synonyms are
    what :func:`titles_for` is for.
    """
    path = dump_path()
    if not path.exists():
        return {}
    names: dict[str, str] = {}
    # The first English fan synonym, kept aside (build 237): a show AniDB has no
    # official English title for - and many have none - is still known in
    # English by the name people use, and that beats showing nothing.
    english_synonym: str | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line_aid, kind, language, title in parse(handle):
                if line_aid != aid:
                    continue
                if kind == SYNONYM and language == "en" and english_synonym is None and title.isascii():
                    english_synonym = title
                if kind == PRIMARY:
                    names["main"] = title
                elif kind == OFFICIAL and language and language not in names:
                    # First official title per language wins. AniDB lists a
                    # second one now and again; they are alternate spellings
                    # of the same name rather than a better one.
                    names[language] = title
                elif language == "ja" and "ja" not in names:
                    # The Japanese title is not always filed as official, and
                    # it is the one name no other source in AniFlow carries.
                    names["ja"] = title
    except OSError as exc:
        logger.info("the AniDB dump could not be read for %s: %s", aid, exc)
        return {}
    if "main" not in names:
        # No main title means this id is not in this dump, and the languages
        # of a show that is not there are not worth keeping either.
        return {}
    if "en" not in names and english_synonym and english_synonym != names["main"]:
        names["en"] = english_synonym
    return names


def titles_for_many(aids: Iterable[int]) -> dict[int, DumpTitles]:
    """Every spelling for a handful of ids, in one pass over the file.

    One scan rather than one per id: the file is 1.4 MB of gzip and a search
    asks about twenty shows at once, which as twenty scans is twenty times the
    work for the same answer.
    """
    wanted = {int(aid) for aid in aids}
    if not wanted:
        return {}
    path = dump_path()
    if not path.exists():
        return {}
    primary: dict[int, str] = {}
    english: dict[int, str] = {}
    native: dict[int, str] = {}
    synonyms: dict[int, list[str]] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for aid, kind, language, title in parse(handle):
                if aid not in wanted:
                    continue
                if kind == PRIMARY:
                    # The first one wins. A dump holds one primary title per
                    # anime; keeping the last would mean a second such row -
                    # malformed, or a format change - silently replacing the
                    # romaji name with whatever came after it.
                    primary.setdefault(aid, title)
                elif kind == OFFICIAL and language == "en" and aid not in english:
                    english[aid] = title
                elif language == "ja" and aid not in native:
                    native[aid] = title
                elif kind in INDEXED_KINDS:
                    synonyms.setdefault(aid, []).append(title)
    except OSError as exc:
        logger.info("the AniDB dump could not be read: %s", exc)
        return {}
    found: dict[int, DumpTitles] = {}
    for aid, name in primary.items():
        seen = {name, english.get(aid), native.get(aid)}
        found[aid] = DumpTitles(
            aid=aid,
            primary=name,
            english=english.get(aid),
            native=native.get(aid),
            synonyms=tuple(
                dict.fromkeys(s for s in synonyms.get(aid, []) if s not in seen)
            ),
        )
    return found


def search(query: str, limit: int = 20) -> list[int]:
    """AniDB ids whose names contain ``query``, best match first.

    Ranked rather than returned in whatever order SQLite likes: an exact name
    beats a name that merely starts with it, which beats one that contains it,
    and a title AniDB calls primary or official beats a fan synonym. Without
    that, searching "Frieren" answers with a drama CD.

    Deliberately not the voting rule :func:`resolve` uses. That one answers
    "which single anime is this?" and refuses to guess between two; this one
    answers "which anime might you mean?", where several answers is the point
    and the person picks.
    """
    needles = {
        form for form in (normalize(query), normalize(_APOSTROPHES.sub(" ", query)))
        if len(form) >= _MIN_TITLE_LENGTH
    }
    if not needles or not calendar_store.exists():
        return []
    with calendar_store.sessions()() as session:
        rows = session.execute(
            select(AnidbTitle.aid, AnidbTitle.title_norm, AnidbTitle.kind).where(
                or_(*(AnidbTitle.title_norm.like(f"%{needle}%") for needle in sorted(needles)))
            )
            # A ceiling, not a page: a two-letter needle matches thousands and
            # ranking them all would cost more than the answer is worth.
            .limit(2000)
        ).all()
    best: dict[int, tuple[int, int, int]] = {}
    for aid, title_norm, kind in rows:
        if title_norm in needles:
            closeness = 0
        elif title_norm.startswith(tuple(needles)):
            closeness = 1
        else:
            closeness = 2
        # Shorter is better within the same closeness: "frieren" before
        # "frieren beyond journeys end special".
        score = (closeness, -KIND_WEIGHT.get(kind, 1), len(title_norm))
        if aid not in best or score < best[aid]:
            best[aid] = score
    ordered = sorted(best.items(), key=lambda pair: (pair[1], pair[0]))
    return [aid for aid, _score in ordered[:limit]]


def primary_title(aid: int) -> str | None:
    """AniDB's own main title for an id, for showing what was matched."""
    if not calendar_store.exists():
        return None
    with calendar_store.sessions()() as session:
        return session.scalar(select(AnidbAnime.primary_title).where(AnidbAnime.aid == aid))


def current_status() -> DumpStatus:
    """What is known about the dump right now. Touches no network."""
    path = dump_path()
    if not calendar_store.exists():
        return DumpStatus(present=path.exists())
    with calendar_store.sessions()() as session:
        state = session.get(DumpState, 1)
        if state is None:
            return DumpStatus(present=path.exists())
        downloaded = _as_utc(state.downloaded_at) if state.downloaded_at else None
        return DumpStatus(
            present=path.exists(),
            downloaded_at=downloaded,
            attempted_at=_as_utc(state.attempted_at) if state.attempted_at else None,
            parsed_at=_as_utc(state.parsed_at) if state.parsed_at else None,
            anime_count=state.anime_count,
            title_count=state.title_count,
            bytes_downloaded=state.bytes_downloaded,
            last_error=state.last_error,
            next_refresh_at=downloaded + REFRESH_INTERVAL if downloaded else None,
        )


def _record(
    *,
    downloaded_at: datetime | None = None,
    attempted_at: datetime | None = None,
    parsed_at: datetime | None = None,
    anime_count: int | None = None,
    title_count: int | None = None,
    bytes_downloaded: int | None = None,
    error: str | None | object = ...,
) -> None:
    """Update the one row of state, leaving anything not mentioned alone."""
    with calendar_store.sessions()() as session:
        state = session.get(DumpState, 1)
        if state is None:
            state = DumpState(id=1)
            session.add(state)
        if downloaded_at is not None:
            state.downloaded_at = downloaded_at
        if attempted_at is not None:
            state.attempted_at = attempted_at
        if parsed_at is not None:
            state.parsed_at = parsed_at
        if anime_count is not None:
            state.anime_count = anime_count
        if title_count is not None:
            state.title_count = title_count
        if bytes_downloaded is not None:
            state.bytes_downloaded = bytes_downloaded
        if error is not ...:
            state.last_error = str(error)[:400] if error else None
        session.commit()


def _as_utc(moment: datetime) -> datetime:
    """A stored timestamp made comparable. SQLite hands back naive ones."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


__all__ = [
    "names_for",
    "DUMP_URL",
    "search",
    "titles_for_many",
    "DumpStatus",
    "INDEXED_KINDS",
    "REFRESH_INTERVAL",
    "current_status",
    "download",
    "dump_path",
    "normalize",
    "parse",
    "primary_title",
    "rebuild",
    "resolve",
    "resolve_many",
    "season_alias",
]
