"""What counts as adult on the watchlist (build 198).

Reported: Hide adult did nothing although adult shows were on the list. The
switch filters on ``anime.is_adult``, and that flag only began to be set at the
moment an anime was added in build 192 - everything added before, and
everything adopted from Shoko without a source flag, carried ``false``.

Two ways something is adult, and the user can always override the first:

* **The flag**, set from what is known: a provider's own adult flag, or a
  stored genre of ecchi, hentai or erotica (wider than the calendar's
  explicit-only filter, because here "adult" is the user's own word). Set
  *upward only* by data - never switched off by it - and never at all once the
  user has ticked or unticked it themselves (``metadata_fill.mark_manual``).
* **The library.** Everything in a library marked Adult counts, so a folder
  of ecchi needs one setting, not one tick box per show.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.anime import Anime

#: What reads as adult here: ecchi included, since it is the user's own word.
ADULT_GENRES = frozenset({"ecchi", "hentai", "erotica"})


def genres_read_as_adult(genres: Iterable[object] | None) -> bool:
    return any(str(name).strip().casefold() in ADULT_GENRES for name in genres or ())


def apply(anime: Anime, *, provider_flag: bool = False) -> bool:
    """Set the flag from the data the anime already has. True when it changed.

    Upward only, and never over a value the user chose.
    """
    from backend.app.services.metadata_fill import manual_fields

    if anime.is_adult or "is_adult" in manual_fields(anime):
        return False
    raw = anime.metadata_json if isinstance(anime.metadata_json, dict) else {}
    if provider_flag or genres_read_as_adult(raw.get("genres")):
        anime.is_adult = True
        return True
    return False


def backfill(db: Session) -> int:
    """Flag every anime whose stored genres say adult. Local; asks nobody.

    Run at start-up, so a watchlist that predates the flag is right the first
    time it is opened. Idempotent.
    """
    changed = 0
    for anime in db.scalars(select(Anime).where(Anime.is_adult.is_(False))):
        if apply(anime):
            changed += 1
    if changed:
        db.commit()
    return changed
