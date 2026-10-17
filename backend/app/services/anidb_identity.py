"""Giving every anime in the watchlist its AniDB id (build 83).

An anime is added through AniList or Kitsu, so it arrives with that source's
id and nothing else. Shoko Server, on the other side, states an AniDB id for
everything it holds. The two libraries therefore had no id in common and had
to be compared by name, which is how build 82 came to offer a series the
watchlist already had.

This closes the gap from AniFlow's side: every anime's titles are looked up in
AniDB's title dump and the id that comes back is stored in ``provider_ids``
under ``anidb``. No schema change - that column is a JSON map and has always
been the place for "every other id we know about this show".

Deliberately re-runnable and deliberately incomplete:

* an anime whose titles match no AniDB entry is left alone and tried again
  after the next dump, because a show airing this week may simply not be in
  the file yet;
* an anime whose titles match *several* AniDB ids is left alone for good
  reason - a sequel sharing a synonym with its first season would otherwise be
  given the wrong id, and a wrong id is worse than none;
* an id already stored is never overwritten, so a correction made by hand or
  by Shoko survives.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.models.anime import Anime
from backend.app.services import anidb_titles

logger = for_component("AniDB")


@dataclass
class BackfillSummary:
    considered: int = 0
    resolved: int = 0
    ambiguous_or_missing: int = 0
    #: True when there is no title index to ask, which is not a failure - the
    #: dump is optional and everything falls back to matching by name.
    skipped: bool = False


def titles_of(anime: Anime) -> tuple[str | None, ...]:
    """Every name AniFlow knows for this anime.

    All of them, because which spelling AniDB filed it under is not
    predictable: an anime added through AniList carries romaji, English and
    native titles, and the folder name is whatever the user typed.
    """
    return (
        anime.display_name,
        anime.local_name,
        anime.title_english,
        anime.title_romaji,
        anime.title_native,
        *(anime.aliases or []),
    )


def adopt(anime: Anime, aid: int | None = None) -> int | None:
    """Give one anime its AniDB id and AniDB's own names (build 116).

    Returns the id it settled on, or None when there is nothing to settle -
    no id was sent, none is stored, and the dump either has no match or has
    several. None is an ordinary answer: a show airing this week may not be in
    the file yet, and the backfill pass tries again after the next dump.

    ``aid`` is the setup dialog's answer when it has one. It usually does, and
    it is worth more than a fresh lookup here: the dialog had to decide *which*
    AniDB entry this season is in order to work out the group's numbering, and
    a second guess made from the titles alone could land on the prequel.

    Writes the id in both places on purpose - the column, which can be indexed
    and asked about, and ``provider_ids["anidb"]``, which everything written
    between builds 83 and 115 reads. Neither is overwritten with a guess: an
    id already stored wins over the dump, because it may have been corrected
    by hand or by Shoko.
    """
    stored = (anime.provider_ids or {}).get("anidb")
    settled = aid or anime.anidb_id or (int(stored) if str(stored or "").isdigit() else None)
    if settled is None:
        settled = anidb_titles.resolve(titles_of(anime))
    if settled is None:
        return None

    anime.anidb_id = int(settled)
    if str(stored or "") != str(settled):
        # Reassigned rather than mutated: SQLAlchemy does not see a change
        # inside a JSON dict, so an in-place write would never be persisted.
        anime.provider_ids = {**(anime.provider_ids or {}), "anidb": str(settled)}

    names = anidb_titles.names_for(int(settled))
    if names:
        anime.anidb_titles = names
    return int(settled)


def backfill(session: Session, *, limit: int | None = None) -> BackfillSummary:
    """Resolve and store the AniDB id of every anime that lacks one."""
    summary = BackfillSummary()
    if not calendar_store.exists():
        summary.skipped = True
        return summary

    statement = select(Anime)
    rows = list(session.scalars(statement))
    # Two kinds of work, both of them this pass's (build 116): an anime with
    # no id at all, and one that has had its id since build 83 but none of
    # AniDB's names, which is every anime added before this build.
    pending = [
        anime
        for anime in rows
        if not (anime.provider_ids or {}).get("anidb")
        or anime.anidb_id is None
        or not anime.anidb_titles
    ]
    if limit is not None:
        pending = pending[:limit]
    summary.considered = len(pending)

    for anime in pending:
        if adopt(anime) is None:
            summary.ambiguous_or_missing += 1
            continue
        summary.resolved += 1
    if summary.resolved:
        logger.info(
            "AniDB ids filled in for %s of %s anime", summary.resolved, summary.considered
        )
    return summary


__all__ = ["BackfillSummary", "adopt", "backfill", "titles_of"]
