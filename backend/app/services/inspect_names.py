"""Every name one show goes by, gathered for the Release Inspector (build 105).

Asked for: selecting a show on the inspector should list *all* of its names,
each labelled, and each usable as a search on its own - because which spelling
a release group used is the whole question the page exists to answer.

They come from four different places, which is why this is a service rather
than a field:

* the three the calendar already carries - English, Romaji, Japanese - and the
  first alias beside them;
* **AniDB's** main title, from the title dump on disk. Nothing is fetched:
  AniDB bans automated access to the site, and the weekly dump is the whole
  reason AniFlow can know this at all;
* **each source's own title**, read from the calendar's per-provider title
  rows. AniList and MyAnimeList disagree about a show more often than one
  would like, and the merged card shows only the winner. Build 149 moved this
  onto the v2 tables, which keep a row per provider per kind of title - a
  better fit than the v1 rows it used to read, and one that is still being
  written to;
* the **clear name** - what AniFlow itself would search with, which is the
  title with the season suffix taken off. It is the name the rest of AniFlow
  reasons about, so seeing it beside the others explains a good deal.

Only distinct names are returned, compared case-insensitively: a list where
five of the seven rows say the same thing teaches nothing, and a search per
row would then be five wasted requests to the tracker.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.core.logging import for_component

logger = for_component("Inspector")

#: The per-source titles worth offering, in the order the merge trusts them.
#: `jikan` is MyAnimeList one hop further away and carries the same title, so
#: it is not asked for separately.
SOURCES: tuple[tuple[str, str], ...] = (
    ("anilist", "anilist"),
    ("mal", "myanimelist"),
    ("kitsu", "kitsu"),
    ("shikimori", "shikimori"),
)


@dataclass
class InspectName:
    """One name, and what it is."""

    #: english | romaji | japanese | alternative | anidb | anilist | mal |
    #: kitsu | shikimori | clean
    kind: str
    value: str
    #: Whether this spelling lives in the tracker's non-English category. Only
    #: the Japanese title does: searching for it in the English-translated
    #: category finds nothing at all, which reads as "this show has no
    #: releases" rather than as "wrong category".
    native: bool = False


def names_for(
    *,
    title_english: str | None = None,
    title_romaji: str | None = None,
    title_native: str | None = None,
    aliases: list[str] | None = None,
    provider_ids: dict[str, str] | None = None,
) -> list[InspectName]:
    """Every distinct name for one show, in the order they should be offered."""
    ids = provider_ids or {}
    found: list[InspectName] = []
    taken: set[str] = set()

    def offer(kind: str, value: str | None, *, native: bool = False) -> None:
        text = (value or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in taken:
            return
        taken.add(key)
        found.append(InspectName(kind=kind, value=text, native=native))

    offer("english", title_english)
    offer("romaji", title_romaji)
    offer("japanese", title_native, native=True)
    for alias in aliases or []:
        offer("alternative", alias)
        break

    offer("anidb", _anidb_title(ids.get("anidb")))
    for source, provider_key in SOURCES:
        offer(source, _source_title(source, ids.get(provider_key)))

    # Last on purpose: it is derived from the others rather than published by
    # anybody, and it is only ever different when a season suffix came off.
    offer("clean", _clean_name(title_romaji or title_english or title_native))
    return found


def _anidb_title(anidb_id: str | None) -> str | None:
    """AniDB's main title, from the dump - never from the site."""
    if not anidb_id:
        return None
    try:
        aid = int(str(anidb_id).strip())
    except (TypeError, ValueError):
        return None
    from backend.app.services import anidb_titles

    try:
        return anidb_titles.primary_title(aid)
    except Exception as exc:  # noqa: BLE001 - a missing dump is not an error here
        logger.debug("no AniDB title for %s: %s", aid, exc)
        return None


def _source_title(source: str, source_id: str | None) -> str | None:
    """What one source calls this show, from the calendar (build 149).

    Two steps rather than one, because v2 files titles against the *Work*
    rather than against the source's row: the id names an identity, the
    identity names a Work, and the Work holds a title row per provider. That
    is the better shape for this - it answers "what does AniList call this"
    even when AniList is not the source the id came from.
    """
    if not source_id:
        return None
    from sqlalchemy import select

    from backend.app.db import calendar_store
    from backend.app.models.calendar import TITLE_MAIN, CalendarIdentity, CalendarTitle

    try:
        with calendar_store.sessions()() as session:
            work_id = session.scalar(
                select(CalendarIdentity.work_id).where(
                    CalendarIdentity.provider == source,
                    CalendarIdentity.provider_id == str(source_id),
                )
            )
            if work_id is None:
                return None
            return session.scalar(
                select(CalendarTitle.title)
                .where(
                    CalendarTitle.work_id == work_id,
                    CalendarTitle.provider == source,
                    CalendarTitle.kind == TITLE_MAIN,
                )
                .limit(1)
            )
    except Exception as exc:  # noqa: BLE001 - the calendar may not be loaded
        logger.debug("no %s title for %s: %s", source, source_id, exc)
        return None


def _clean_name(title: str | None) -> str | None:
    """What AniFlow itself would search with."""
    if not title:
        return None
    from backend.app.services.release_identity import strip_season

    return strip_season(title) or None


__all__ = ["InspectName", "SOURCES", "names_for"]
