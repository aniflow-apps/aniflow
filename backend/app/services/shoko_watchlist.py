"""What Shoko already holds that AniFlow is not tracking (build 77).

Shoko has been watching a library for years; AniFlow started with an empty
watchlist. The two lists overlap, and the interesting part is the difference:
a series sitting in Shoko with no watchlist entry gets no new episodes, and
nothing on any screen said so.

"Is this already in the watchlist?" is the same question the calendar's
filter asks, so it is answered in the same place: services/library_flags.py.
Sharing it means the two screens cannot disagree.

Since build 83 the answer is usually exact. Shoko states the AniDB id of every
series it holds, and AniFlow resolves its own anime to AniDB ids from AniDB's
title dump, so the comparison is an id on both sides. That was reported as a
bug against build 82: a series already in the watchlist was offered again
because Shoko knew it by a different name. Titles are still compared, because
the dump may not have been downloaded and a brand-new show may not be in it
yet - but they are no longer the only thing standing between you and a
duplicate.

Read-only, like everything else pointed at Shoko: this reports a difference
and the user decides what to do about it.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.services.library_flags import library_index

logger = for_component("Shoko")


@dataclass
class UnlinkedSeries:
    """One series Shoko holds and the watchlist does not have."""

    shoko_id: int
    name: str
    local_episodes: int
    total_episodes: int
    missing_count: int
    anidb_id: int | None = None
    mal_id: int | None = None
    #: A poster, when the downloaded data has one for this AniDB id (build
    #: 94). AniFlow's own URL, so the browser needs nothing from Shoko.
    cover_url: str | None = None


def _cover_for(anidb_id: int | None) -> str | None:
    """A poster for this AniDB id, out of the downloaded calendar data.

    The same lookup adoption uses (build 89), for the same reason: Shoko
    states an id, and an id is the one thing two databases can agree on
    without either of them guessing.

    Returns None rather than raising for a season nobody has downloaded - and
    for a calendar database that is not there at all, which is a supported
    state.
    """
    if not anidb_id:
        return None
    try:
        from backend.app.services.calendar_lookup import by_anidb_id

        found = by_anidb_id(int(anidb_id))
    except Exception as exc:  # noqa: BLE001 - a picture is never worth a 500
        logger.debug("no cover for anidb %s: %s", anidb_id, exc)
        return None
    return found.cover_url if found is not None else None


def unlinked(session: Session) -> tuple[list[UnlinkedSeries], str | None]:
    """Shoko's series that no watchlist entry matches, and why not, if it failed.

    Returns an empty list with a reason rather than raising: this drives a
    filter on the watchlist, and a page that already works must not break
    because somebody else's server is down.
    """
    from backend.app.services.shoko_library import overview

    # No episode lists: this needs the counts and the ids, and the per-series
    # episode fetch is one request per series with a gap.
    library = overview(with_episodes=False)
    if not library.reachable:
        return [], library.detail or "Shoko Server could not be reached"

    # The same index the calendar's "already in your library" filter uses, so
    # the two screens cannot disagree about what the watchlist contains.
    known = library_index(session)
    rows: list[UnlinkedSeries] = []
    for series in library.series:
        # Shoko can hold several MyAnimeList ids for one series, and any of
        # them matching means AniFlow has it. The AniDB id is the one Shoko is
        # surest of, and since build 83 it is the one AniFlow can answer.
        if any(
            known.knows(
                "anidb",
                series.anidb_id or "",
                provider_ids={"myanimelist": str(mal_id)},
                titles=(series.name,),
                anidb_id=series.anidb_id,
            )
            for mal_id in (series.mal_ids or [""])
        ):
            continue
        rows.append(
            UnlinkedSeries(
                cover_url=_cover_for(series.anidb_id),
                shoko_id=series.shoko_id,
                name=series.name,
                local_episodes=series.local_episodes,
                total_episodes=series.total_episodes,
                missing_count=series.missing_count,
                anidb_id=series.anidb_id,
                # The first id, for looking the series up. Shoko allows several
                # and a series with two MyAnimeList entries is a merge nobody
                # here can resolve.
                mal_id=series.mal_ids[0] if series.mal_ids else None,
            )
        )
    rows.sort(key=lambda row: (-row.missing_count, row.name.lower()))
    logger.info(
        "%s of Shoko's %s series are not in the watchlist", len(rows), len(library.series)
    )
    return rows, None


__all__ = ["UnlinkedSeries", "unlinked"]
