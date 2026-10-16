"""Deciding whether a release belongs to a tracked anime.

One rule, used everywhere a release has to be recognised - the add-anime
search, the RSS scan and the per-anime feed search - so an episode discovered
through a feed is matched exactly like one discovered through the indexer.

The comparison is against ``anime.release_name``: the name *providers* use for
the show, captured from a real release when the anime was added. Metadata
titles are unusable for this, because the parser moves the season marker into
its own field:

    "[Group] Show Name S2 - 01 (720p).mkv"  ->  title "Show Name", season 2

so comparing that title against "Youjo Senki S2", "Youjo Senki II" or
"Saga of Tanya the Evil Season 2" never matches. Matching the stored release
name instead makes the check both reliable and auditable - no fuzzy scoring.

Seasons are compared explicitly: a conflicting season is never a match, while
a season stated by only one side cannot conflict and is therefore allowed
(subgroups routinely omit the marker).
"""
import re
from typing import Iterable

from backend.app.models.anime import Anime
from backend.app.services.release_parser import ParsedRelease, normalize_title_for_match

#: Roman numerals as far as a sequel ever gets. Subgroups write "S2";
#: metadata and some groups write "II".
#:
#: "V" and "X" are left out on purpose: a single letter is a word at least as
#: often as it is a numeral, and reading "Show X" as season ten files a show
#: under a season it does not have. A fifth or tenth season written as a bare
#: numeral is rare enough to be worth losing for that.
_ROMAN = {
    "ii": 2, "iii": 3, "iv": 4, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9,
}

#: How a *trailing* season marker may be written (build 91).
#:
#: Trailing only, and only forms that cannot mean anything else. A number in
#: the middle of a title is not a season - "Gundam 00" and "Fate/Zero" say
#: what happens to code that guesses - and neither is a bare trailing number,
#: because "Mushoku Tensei 3" and "Bakemonogatari 2" are not the same kind of
#: string as "Nichijou 2" (an episode count, in some listings).
_TRAILING_SEASON = re.compile(
    r"""[\s._-]+(?:
        S(?P<s>\d{1,2})                              # Youjo Senki S2
      | (?:season\s*(?P<season>\d{1,2}))             # Youjo Senki Season 2
      | (?P<ordinal>\d{1,2})(?:st|nd|rd|th)\s+season  # Youjo Senki 2nd Season
      | (?:part|cour)\s*(?P<part>\d{1,2})            # Youjo Senki Part 2
      | (?P<roman>ii|iii|iv|vi|vii|viii|ix)         # Youjo Senki II
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def folder_season(name: str | None) -> int | None:
    """The season a name pins, e.g. "Mushoku Tensei S3" -> 3.

    Understands the spellings above; None when the name states no season,
    which everything here reads as season one.
    """
    match = _TRAILING_SEASON.search((name or "").strip())
    if match is None:
        return None
    roman = match.group("roman")
    if roman:
        return _ROMAN[roman.lower()]
    for group in ("s", "season", "ordinal", "part"):
        found = match.group(group)
        if found:
            return int(found)
    return None


def strip_season(name: str | None) -> str:
    """The name without its trailing season marker."""
    return _TRAILING_SEASON.sub("", (name or "").strip())


def release_matches_reference(
    parsed: ParsedRelease,
    reference: str | None,
    *,
    alternates: Iterable[str | None] = (),
) -> bool:
    """Whether a parsed release belongs to ``reference`` (a release name).

    ``reference`` is the benchmark: "Mushoku Tensei S3" accepts the S3 releases
    and nothing else. Base titles are compared after normalisation so
    separator styles ("Youjo_Senki_S2", "Youjo.Senki.S2") do not matter.

    ``alternates`` are the show's other names (build 96). One show has one
    release name per group that writes a different title: ToonsHub publishes
    "Makina-san's a Love Bot?!" where SubsPlease publishes
    "Kakushite! Makina-san!!", and with only the stored name to compare
    against, every file from the other group read as a different show.

    The season is *not* widened by this. It still comes from ``reference``
    alone, because that is the check build 91 was reported for - S2 releases
    landing in an S3 folder - and an alternate name says nothing about which
    season a release is. And names are compared for equality after
    normalisation rather than by substring, so a short or sloppy alternate
    ("Makina") matches nothing rather than everything.
    """
    if not reference or not parsed.title:
        return False

    # A missing season marker means season 1 on both sides, and the two must
    # agree. Treating "absent" as "cannot conflict" leaks in both directions:
    # a reference of "Youjo Senki S2" would accept "Youjo Senki - 05" (which is
    # season 1), and a reference of "Mushoku Tensei" would accept
    # "Mushoku Tensei S3 - 10".
    #
    # The symmetry is safe precisely because the reference is captured from a
    # real release of the wanted season: if the subgroup omits the marker, so
    # does the stored name, and both sides read as 1.
    if (parsed.season or 1) != (folder_season(reference) or 1):
        return False

    got = normalize_title_for_match(strip_season(parsed.title))
    if not got:
        return False
    wanted = {
        normalized
        for name in (reference, *alternates)
        if (normalized := normalize_title_for_match(strip_season(name)))
    }
    return got in wanted


def anime_reference_name(anime: Anime) -> str | None:
    """The release name to match this anime against.

    ``release_name`` is authoritative. The fallbacks keep rows created before
    the column existed working; both held the release-derived folder.
    """
    return anime.release_name or anime.media_folder or anime.local_name


def known_release_titles(anime: Anime) -> tuple[str | None, ...]:
    """Every name a release of this anime might carry.

    The provider's three spellings and the aliases the user added - not the
    folder name or the display name, which follow settings about what to read
    on screen and on disk and are not names any group publishes under.
    """
    return (
        anime.title_romaji,
        anime.title_english,
        anime.title_native,
        *(anime.aliases or []),
    )


def release_matches_anime(parsed: ParsedRelease, anime: Anime) -> bool:
    """Whether a parsed release belongs to this anime."""
    return release_matches_reference(
        parsed,
        anime_reference_name(anime),
        alternates=known_release_titles(anime),
    )


def matching_anime(parsed: ParsedRelease, tracked: list[Anime]) -> list[Anime]:
    """Every tracked anime this release could belong to.

    More than one result means the reference names are genuinely ambiguous and
    the caller must not guess.
    """
    return [anime for anime in tracked if release_matches_anime(parsed, anime)]


__all__ = [
    "anime_reference_name",
    "known_release_titles",
    "folder_season",
    "matching_anime",
    "release_matches_anime",
    "release_matches_reference",
    "strip_season",
]
