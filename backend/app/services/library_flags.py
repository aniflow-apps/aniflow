"""Whether AniFlow already tracks a show somebody is being offered.

One question, asked by the calendar and by the add-anime search: is this
already in the watchlist? Getting it wrong offers a second copy of something
you have, or hides a show you dropped.

Matching used to be the provider's id pair alone, which is exact and not
enough. AniList spent a week answering HTTP 403, so everything added during
that week was added through Kitsu - and a Kitsu anime carries a Kitsu id, so
the AniList line-up the calendar shows matched none of it and the filter
looked broken. Three ways to recognise the same show, in descending order of
certainty:

**The primary id pair.** What the anime was added as. Exact.

**Any other id the provider gave us.** An anime added through AniList stores
its MyAnimeList id as well, so a candidate carrying either is recognised.

**The title, normalised.** The only thing two providers reliably share. Used
for equality only, with the same aggressive normalisation release matching
uses, and every title AniFlow knows is compared - which one a provider
returns is not predictable.

A title match can be wrong. The cost of a false positive here is a card
hidden behind a filter the user can switch off, which is the cheaper mistake:
the alternative is being offered a show that is already downloading.

**The AniDB id, when AniFlow has one** (build 83). Every anime is resolved to
an AniDB id from AniDB's own title dump - see services/anidb_titles.py - and
Shoko Server states the AniDB id of everything it holds. So the comparison
that used to be "do these two names look alike" is now an id on both sides,
which is what stopped Shoko offering a series the watchlist already had under
another name. The dump is optional: without it, matching falls back to
everything above, exactly as before.
"""
from dataclasses import dataclass, field

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session

from backend.app.models.anime import Anime
from backend.app.schemas.anime import AnimeSearchResult
from backend.app.services.calendar_identity import canonical_provider, names_one_show
from backend.app.services.release_parser import fold_letters, normalize_title_for_match


@dataclass(frozen=True)
class Match:
    """Why a candidate counts as already tracked: the rule, and whose it was.

    Added because a false "in your watchlist" was impossible to chase: the tag
    said yes and nothing said which anime, or which of four rules, had said it.
    """

    #: ``anidb`` | ``pair`` | ``id`` | ``title``
    rule: str
    #: What was equal, e.g. ``anilist 12345`` or the normalised title.
    value: str
    #: The watchlist anime it matched, when the index knows (an index built by
    #: hand in a test has no owners).
    owner: str | None = None


@dataclass(frozen=True)
class LibraryIndex:
    """Everything the watchlist can be recognised by. One query, read-only."""

    #: (provider, provider_id) of each anime, as it was added.
    pairs: set[tuple[str, str]] = field(default_factory=set)
    #: Every (provider, id) from the stored provider id maps - "myanimelist"
    #: included, which is what makes a Shoko series matchable.
    ids: set[tuple[str, str]] = field(default_factory=set)
    #: Every title AniFlow knows, normalised for comparison.
    titles: set[str] = field(default_factory=set)
    #: Every AniDB id AniFlow has resolved for its own anime. The exact half
    #: of the comparison with Shoko Server.
    anidb_ids: set[int] = field(default_factory=set)
    #: Which anime each key above came from, for ``explain``: keyed by
    #: (rule, a, b) - ("pair", provider, id), ("id", name, value),
    #: ("title", normalised, ""), ("anidb", id, "").
    owners: dict[tuple[str, str, str], str] = field(default_factory=dict)
    #: Which anime carry each title key (positions in ``anime_ids``), and every
    #: anime's own ids - so a name two shows share can be checked against what
    #: their ids say before it is believed.
    title_holders: dict[str, list[int]] = field(default_factory=dict)
    anime_ids: list[dict[str, str]] = field(default_factory=list)

    def knows(
        self,
        provider: str,
        provider_id: str,
        *,
        provider_ids: dict[str, str] | None = None,
        titles: tuple[str | None, ...] = (),
        anidb_id: int | None = None,
    ) -> bool:
        """Whether this candidate is a show AniFlow already tracks."""
        return (
            self.explain(
                provider,
                provider_id,
                provider_ids=provider_ids,
                titles=titles,
                anidb_id=anidb_id,
            )
            is not None
        )

    def explain(
        self,
        provider: str,
        provider_id: str,
        *,
        provider_ids: dict[str, str] | None = None,
        titles: tuple[str | None, ...] = (),
        anidb_id: int | None = None,
    ) -> Match | None:
        """The first rule that recognises this candidate, or None.

        The same rules in the same order ``knows`` always had; this only says
        which one fired and whose it was.
        """

        def owner(rule: str, first: str, second: str = "") -> str | None:
            return self.owners.get((rule, first, second))

        # The exact answer first, when both sides have one.
        if anidb_id is not None and anidb_id in self.anidb_ids:
            return Match("anidb", str(anidb_id), owner("anidb", str(anidb_id)))
        if (provider, str(provider_id)) in self.pairs:
            return Match("pair", f"{provider} {provider_id}", owner("pair", provider, str(provider_id)))
        if (provider, str(provider_id)) in self.ids:
            return Match("id", f"{provider} {provider_id}", owner("id", provider, str(provider_id)))
        for name, value in (provider_ids or {}).items():
            if not names_one_show(name, value):
                continue
            if (name, str(value)) in self.ids:
                return Match("id", f"{name} {value}", owner("id", name, str(value)))
            if (name, str(value)) in self.pairs:
                return Match("pair", f"{name} {value}", owner("pair", name, str(value)))
        # Asked with both spellings for the same reason the index holds both.
        #
        # A name is only the last resort: it is believed when ids cannot say.
        # When both sides carry ids and they disagree, the two are different
        # shows that share a name (a remake, a later season, Anemone and
        # Anemone (2027)), and the ids win.
        mine = _show_ids((provider, str(provider_id)), provider_ids, anidb_id)
        for title in titles:
            if not title:
                continue
            for spelling in (title, without_year(title)):
                normalized = title_key(spelling)
                if not normalized or normalized not in self.titles:
                    continue
                holders = self.title_holders.get(normalized)
                if holders and all(_disagree(mine, self.anime_ids[index]) for index in holders):
                    continue
                return Match("title", f"{title} -> {normalized}", owner("title", normalized))
        return None


def _show_ids(pair: tuple[str, str] | None, provider_ids: dict[str, str] | None, anidb_id: int | None = None) -> dict[str, str]:
    """The ids that name exactly one show, by canonical provider.

    ``mal`` and ``myanimelist`` are one provider spelled two ways (22 anime on
    the NAS carry only ``mal``), so both are keyed ``mal``.
    """
    found: dict[str, str] = {}
    candidates = [(pair[0], pair[1])] if pair else []
    candidates += list((provider_ids or {}).items())
    if anidb_id is not None:
        candidates.append(("anidb", str(anidb_id)))
    for name, value in candidates:
        if value is not None and str(value).strip() and names_one_show(name, value):
            found[canonical_provider(name)] = str(value)
    return found


def _disagree(mine: dict[str, str], theirs: dict[str, str]) -> bool:
    """Whether two shows' ids prove they are different: a kind both have, with different values."""
    return any(kind in theirs and theirs[kind] != value for kind, value in mine.items())


def title_key(title: str) -> str:
    """A title reduced to what identifies it, without losing other scripts.

    ``normalize_title_for_match`` keeps ``[a-z0-9]`` only, so a Japanese title
    shrinks to whatever Latin letters and digits sit beside it: Sasaki and
    Peeps Season 2 is ``佐々木とピーちゃん 第２期`` and reduces to ``2``, as does
    the native name of any other show ending in a 2 - and the Add anime search
    then said "in your watchlist" for a show that was not (build 224's tag
    named the culprit). A title that contains letters the plain normaliser
    would throw away is keyed by all of its letters instead; a Latin title is
    keyed exactly as before.
    """
    folded = fold_letters(title)
    if any(char.isalpha() and not char.isascii() for char in folded):
        text = unicodedata.normalize("NFKC", title).casefold()
        return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text)).strip()
    return normalize_title_for_match(title)


#: A year the way a catalogue disambiguates a remake: "Koukaku Kidoutai
#: (2026)". Not part of the name, and comparing with it attached is how one
#: show appeared twice in the watchlist - once as itself and once as a series
#: only Shoko had (build 100).
_YEAR_SUFFIX = re.compile(r"\s*[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]\s*$")


def without_year(title: str | None) -> str:
    """A title without a trailing year in brackets."""
    return _YEAR_SUFFIX.sub("", (title or "").strip())


def library_index(db: Session) -> LibraryIndex:
    """Build the index. Everything else here reads it."""
    pairs: set[tuple[str, str]] = set()
    ids: set[tuple[str, str]] = set()
    titles: set[str] = set()
    anidb_ids: set[int] = set()
    owners: dict[tuple[str, str, str], str] = {}
    title_holders: dict[str, list[int]] = {}
    anime_ids: list[dict[str, str]] = []
    columns = (
        Anime.primary_provider,
        Anime.primary_provider_id,
        Anime.display_name,
        Anime.local_name,
        Anime.title_english,
        Anime.title_romaji,
        Anime.title_native,
        Anime.aliases,
        Anime.provider_ids,
    )
    for (
        provider,
        provider_id,
        display,
        local,
        english,
        romaji,
        native,
        aliases,
        provider_ids,
    ) in db.execute(select(*columns)).all():
        pairs.add((provider, str(provider_id)))
        position = len(anime_ids)
        anime_ids.append(_show_ids((provider, str(provider_id)), provider_ids))
        # The name AniFlow shows for it, for "matched <name>". setdefault: the
        # first anime holding a key is the one reported.
        owner_name = display or local or english or romaji or native or str(provider_id)
        owners.setdefault(("pair", provider, str(provider_id)), owner_name)
        for name, value in (provider_ids or {}).items():
            # Only ids that name one show (build 155): a series-wide TVDB or
            # TMDB id on one entry marked half an upcoming season as tracked.
            if names_one_show(name, value):
                ids.add((name, str(value)))
                owners.setdefault(("id", name, str(value)), owner_name)
                if name == "anidb":
                    try:
                        anidb_ids.add(int(value))
                        owners.setdefault(("anidb", str(int(value)), ""), owner_name)
                    except (TypeError, ValueError):
                        pass
        for value in (display, local, english, romaji, native, *(aliases or [])):
            if not value:
                continue
            # Both spellings: a catalogue disambiguates a remake by appending
            # the year - "Koukaku Kidoutai (2026)" - and a show appeared twice
            # in the watchlist because one side carried it and the other did
            # not (build 100).
            for spelling in (str(value), without_year(str(value))):
                normalized = title_key(spelling)
                if normalized:
                    titles.add(normalized)
                    owners.setdefault(("title", normalized, ""), owner_name)
                    holders = title_holders.setdefault(normalized, [])
                    if position not in holders:
                        holders.append(position)
    return LibraryIndex(
        pairs=pairs, ids=ids, titles=titles, anidb_ids=anidb_ids, owners=owners,
        title_holders=title_holders, anime_ids=anime_ids,
    )


def _seasons_tracked(db: Session) -> dict[str, set[int]]:
    """Which seasons of each show are in the watchlist, by base title.

    The base title is the name with its season marker taken off and then
    normalised - the same pair of rules release matching uses, so "Black
    Clover S2", "… 2nd Season" and "… II" all reduce to one key.
    """
    from backend.app.models.anime import Anime
    from backend.app.services.release_identity import folder_season, strip_season

    found: dict[str, set[int]] = {}
    columns = (Anime.display_name, Anime.local_name, Anime.title_english, Anime.title_romaji)
    for row in db.execute(select(*columns)).all():
        # Every name the anime goes by, because which one the calendar shows
        # is a different setting from which one the watchlist stored.
        for value in row:
            if not value:
                continue
            base = normalize_title_for_match(strip_season(str(value)))
            if not base or not any(char.isalpha() for char in base):
                continue
            found.setdefault(base, set()).add(folder_season(str(value)) or 1)
    return found


def related_state(
    titles: tuple[str | None, ...], tracked: dict[str, set[int]]
) -> str | None:
    """How this entry relates to seasons already in the watchlist.

    ``sequel`` - a later season than anything tracked.
    ``gap``    - a season between two that are tracked, which nobody skips
                 on purpose.
    ``prequel`` - earlier than what is tracked. Marked quietly; people do
                 start a series in the middle.

    None when the show is not tracked at all, or when this very season is -
    that case is ``in_library`` and is a different mark.
    """
    from backend.app.services.release_identity import folder_season, strip_season

    for title in titles:
        if not title:
            continue
        base = normalize_title_for_match(strip_season(str(title)))
        seasons = tracked.get(base) if base and any(char.isalpha() for char in base) else None
        if not seasons:
            continue
        season = folder_season(str(title)) or 1
        if season in seasons:
            return None  # this season itself: in_library says so already
        if any(other < season for other in seasons) and any(
            other > season for other in seasons
        ):
            return "gap"
        if any(other < season for other in seasons):
            return "sequel"
        return "prequel"
    return None


def mark_in_library(db, results: list[AnimeSearchResult]) -> None:
    """Set ``in_library`` on every result AniFlow already tracks.

    Computed per request and never stored beside a cached line-up: the line-up
    is what a provider said and changes over weeks, while this changes the
    moment something is added or deleted. Keeping the two together is what left
    a deleted anime hidden behind the calendar's filter.
    """
    if not results:
        return
    index = library_index(db)
    # Which seasons of what are tracked, for the entries that are *not* in the
    # watchlist themselves (build 101).
    tracked = _seasons_tracked(db)
    for result in results:
        result.related = related_state(
            (
                result.display_name,
                result.title_english,
                result.title_romaji,
                result.title_native,
            ),
            tracked,
        )
        match = index.explain(
            result.provider,
            result.provider_id,
            provider_ids=result.provider_ids,
            titles=(
                result.display_name,
                result.title_english,
                result.title_romaji,
                result.title_native,
            ),
        )
        result.in_library = match is not None
        # Said out loud so a wrong "in your watchlist" can be traced to the
        # anime and the rule behind it.
        result.in_library_as = match.owner if match else None
        result.in_library_why = f"{match.rule}: {match.value}" if match else None
