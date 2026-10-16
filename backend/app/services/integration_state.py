"""What the worker last learned about Shoko's library (build 117).

One walk of Shoko's series list, remembered, and read by everything that wants
to know what the other library holds - the watchlist's icons, the Debug
windows, the adoption pass.

**Nothing here is called from a page load.** Asking Shoko is a request per
page of series plus one per series with a gap, so :func:`warm_shoko` runs on
the worker and :func:`remembered_shoko` reads what it found. Before the first
pass the answer is "nobody has asked", which shows as a grey Shoko icon - and
grey means exactly that. A watchlist that hangs because somebody else's server
is slow is worse than an icon admitting it does not know.

The colours themselves moved out in build 120. ``IntegrationState`` and
``states_for`` lived here and were the two-colour ``aniflow | shoko`` line;
services/anime_health.py replaced them with four groups. They were removed
rather than left alongside: two functions answering "how complete is this
anime" would drift apart, and the icons and the Debug windows have to agree.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime

logger = for_component("Shoko")

#: How long Shoko's library list is reused. Long enough that opening the
#: watchlist twice does not ask twice; short enough that a series added to
#: Shoko shows up while you are still looking at the screen.
SHOKO_CACHE_SECONDS = 300

_shoko_cache: tuple[datetime, dict[int, object], dict[str, int], bool] | None = None


def forget_shoko() -> None:
    """Drop what was remembered, so the next warm pass asks again."""
    global _shoko_cache
    _shoko_cache = None


def warm_shoko() -> tuple[dict[int, object], bool]:
    """Ask Shoko now and remember the answer. For the worker to call."""
    holdings, _by_title, answered = _shoko_library(force=True)
    return holdings, answered


def remembered_shoko() -> tuple[dict[int, object], dict[str, int], bool]:
    """What the worker last learned: holdings by AniDB id, the same by name,
    and whether Shoko answered at all.

    Reads only - see the module docstring for why.
    """
    return _shoko_library()


def _shoko_library(
    *, force: bool = False
) -> tuple[dict[int, object], dict[str, int], bool]:
    """The cache itself.

    The name index exists because an id is not always available on this side.
    Reported in build 118: *"why is the ghost in the shell grey?"* - Shoko had
    it, AniFlow had it, and the two could not be compared because AniFlow had
    no AniDB id for it, a show that new not being in the title dump yet.
    Comparing names is less certain than an id and far better than nothing.

    Never raises. An unreachable Shoko is remembered as "did not answer",
    which is not the same as "holds nothing" - the second would have every
    card claim Shoko does not have it.
    """
    global _shoko_cache
    now = datetime.now(UTC)
    if _shoko_cache is not None:
        cached_at, holdings, by_title, answered = _shoko_cache
        if force or now - cached_at < timedelta(seconds=SHOKO_CACHE_SECONDS):
            if not force:
                return holdings, by_title, answered
    elif not force:
        return {}, {}, False

    holdings: dict[int, object] = {}
    by_title: dict[str, int] = {}
    answered = False
    try:
        from backend.app.services.shoko_library import holdings as ask_shoko

        facts, answered = ask_shoko()
        holdings = dict(facts)
        for aid, fact in facts.items():
            by_title[_comparable(fact.name)] = aid
    except Exception as exc:  # noqa: BLE001 - a card colour is never a 500
        logger.debug("Shoko could not be asked: %s", exc)
        answered = False

    _shoko_cache = (now, holdings, by_title, answered)
    return holdings, by_title, answered


def _comparable(title: str | None) -> str:
    """One spelling of a name, for comparing two libraries' titles."""
    from backend.app.services.anidb_titles import normalize

    return normalize(title or "")


def stored_anidb_id(anime: Anime) -> int | None:
    """The AniDB id out of ``provider_ids``, for anime predating the column."""
    raw = (anime.provider_ids or {}).get("anidb")
    return int(raw) if str(raw or "").isdigit() else None


__all__ = [
    "SHOKO_CACHE_SECONDS",
    "forget_shoko",
    "remembered_shoko",
    "stored_anidb_id",
    "warm_shoko",
]
