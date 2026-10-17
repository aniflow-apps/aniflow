"""What counts as adult, decided the same way for every source (build 86).

Reported against build 85: hentai appeared in the calendar and *Hide adult
content* did not hide it. The data said why - zero of 358 stored entries were
flagged, so there was nothing for the filter to act on. Every source got it
wrong differently:

* **Shikimori** was being asked for hentai on purpose. Its ``censored``
  parameter excludes it by default and AniFlow was passing ``false``, with a
  comment claiming that was the safe direction. It was not.
* **Kitsu** publishes ``ageRating: "R18"``, which was read correctly - but
  only for the handful of entries that carry it.
* Nothing looked at the genres, which is where every one of these databases
  files hentai unambiguously.

So the rule is now: a source's own flag, **or** an adult genre. Genres are the
reliable half - "Hentai" is a category on Kitsu, a genre on MyAnimeList and an
explicit genre on Jikan - and a show carrying one is exactly what the filter
exists to keep off the screen.

Deliberately narrow. Yaoi, yuri, ecchi and "Erotica" are not on this list:
they describe content that is suggestive rather than explicit, and a filter
that hides more than it says it hides is its own kind of broken.
"""
from __future__ import annotations

#: Genres that mean explicit content, however a source spells them.
ADULT_GENRES = frozenset({"hentai"})


def genres_are_adult(genres) -> bool:
    """Whether any of these genre names means explicit content."""
    return any(str(name).strip().casefold() in ADULT_GENRES for name in genres or ())


__all__ = ["ADULT_GENRES", "genres_are_adult"]
