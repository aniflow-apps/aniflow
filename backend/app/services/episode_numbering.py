"""Where a season begins, when a group counts from the start of the franchise.

The question, in one line from the report that asked for this: *a file called
"39" - which episode is that?*

Some release groups number from the first episode of the franchise and never
restart. SubsPlease writes ``Re Zero kara Hajimeru Isekai Seikatsu - 39``;
Erai-raws writes ``Re:Zero kara Hajimeru Isekai Seikatsu 4th Season - 01`` for
a different episode of a different season, and the same show's fourth season
is "01" to one and "67" to the other. Nothing in either file name says which.

**AniDB has the pieces; nobody has them assembled.** Each season is its own
entry with its own episode count and a *Prequel* relation to the one before
it. Walk that chain, add the counts, and the answer falls out:

    Re:Zero      aid    kind  eps   franchise numbers
    season 1     11370  TV     25   1-25
    season 2 a   14792  TV     13   26-38
    season 2 b   15593  TV     12   39-50
    season 3     17947  TV     16   51-66
    season 4     19242  TV     19   67-85

which is why SubsPlease's "39" is the first episode of season 2's second half,
and why their row of 44 files spans three seasons at once.

**Where the counts come from**, in the order they are asked for:

1. **the pool** - this module's own table, which is written every time
   anything is learned. Free, and the only source that works with no network
   and no Shoko.
2. **Shoko**, which keeps AniDB's records for everything it has looked at -
   including seasons that are not in the library. Free, local, and complete
   for a show whose relations it has already seen.
3. **Shoko fetching it from AniDB**, for an entry it has never seen. This is
   the one step that reaches AniDB, and it goes as Shoko: its client
   registration, its rate limit, its ban status. Nothing is added to the
   library.
4. **a person**, typing the number in. Which outranks all of the above,
   because a chain can be wrong and a library renamed by a wrong chain is a
   bad afternoon.

The AniDB title dump cannot help here and never will: it carries ids and
titles, and not one episode count.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.models.calendar_sources import AnidbNumbering

logger = for_component("Numbering")

#: How deep a prequel chain is followed. Long-running franchises are the whole
#: point, but a chain this long is a loop or a mistake rather than a show.
MAX_CHAIN = 20

#: Between two fetches through Shoko. It is asking AniDB on our behalf and
#: AniDB bans for haste; Shoko queues its own requests, and this keeps the
#: queue from being handed a burst in the first place.
FETCH_PAUSE_SECONDS = 2.0

#: Between asking Shoko to fetch an entry and asking whether it has it yet.
FETCH_RETRY_SECONDS = 1.5

#: The kinds that carry a franchise's episode numbering. A film between two
#: seasons is a film: no group counts it as episode 26.
COUNTED_KINDS = frozenset({"TV", "Web", "Unknown"})


@dataclass
class Season:
    """One entry of a franchise, and where it sits in the numbering."""

    aid: int
    title: str | None = None
    kind: str | None = None
    episode_count: int | None = None
    prequel_aid: int | None = None
    absolute_start: int | None = None
    source: str | None = None
    #: True when this entry is in Shoko's library rather than only known to it.
    in_library: bool = False


@dataclass
class Numbering:
    """What was worked out about one entry, and how."""

    aid: int
    #: The chain, oldest first, ending with the entry that was asked about.
    chain: list[Season] = field(default_factory=list)
    #: The franchise number of this entry's first episode, when known.
    absolute_start: int | None = None
    episode_count: int | None = None
    #: pool | shoko | anidb | manual | none
    source: str = "none"
    #: Why there is no answer, when there is none.
    detail: str | None = None

    @property
    def absolute_end(self) -> int | None:
        if self.absolute_start is None or not self.episode_count:
            return None
        return self.absolute_start + self.episode_count - 1

    @property
    def complete(self) -> bool:
        """Whether every entry before this one was accounted for."""
        return self.absolute_start is not None


# --------------------------------------------------------------------------
# The pool
# --------------------------------------------------------------------------

def remembered(aid: int) -> Season | None:
    """What the pool holds for one entry, if anything."""
    with calendar_store.sessions()() as session:
        row = session.get(AnidbNumbering, int(aid))
        return _as_season(row) if row else None


def remember(season: Season) -> None:
    """Write one entry down, keeping a number a person typed.

    A manual number outranks a fetched one: somebody looked at the files and
    said so, and the next automatic answer must not quietly undo that.
    """
    now = datetime.now(UTC)
    with calendar_store.sessions()() as session:
        row = session.get(AnidbNumbering, int(season.aid))
        if row is None:
            row = AnidbNumbering(aid=int(season.aid))
            session.add(row)
        elif row.source == "manual" and season.source != "manual":
            # Keep what the person said, but let the rest be filled in.
            season = Season(
                aid=season.aid,
                title=season.title or row.title,
                kind=season.kind or row.kind,
                episode_count=season.episode_count or row.episode_count,
                prequel_aid=season.prequel_aid if season.prequel_aid is not None else row.prequel_aid,
                absolute_start=row.absolute_start,
                source="manual",
            )
        row.title = season.title or row.title
        row.kind = season.kind or row.kind
        row.episode_count = season.episode_count if season.episode_count is not None else row.episode_count
        row.prequel_aid = season.prequel_aid if season.prequel_aid is not None else row.prequel_aid
        row.absolute_start = (
            season.absolute_start if season.absolute_start is not None else row.absolute_start
        )
        row.source = season.source or row.source
        row.checked_at = now
        session.commit()


def set_manually(aid: int, absolute_start: int, *, episode_count: int | None = None) -> Season:
    """Record a start number a person typed in. Outranks everything."""
    season = Season(
        aid=int(aid),
        episode_count=episode_count,
        absolute_start=int(absolute_start),
        source="manual",
    )
    remember(season)
    logger.info("episode 1 of %s is franchise episode %s (typed in)", aid, absolute_start)
    return season


def _as_season(row: AnidbNumbering) -> Season:
    return Season(
        aid=row.aid,
        title=row.title,
        kind=row.kind,
        episode_count=row.episode_count,
        prequel_aid=row.prequel_aid,
        absolute_start=row.absolute_start,
        source=row.source,
    )


# --------------------------------------------------------------------------
# Working it out
# --------------------------------------------------------------------------

def resolve(aid: int, *, client=None, fetch: bool = True) -> Numbering:
    """Where this entry's episode 1 sits in its franchise's numbering.

    ``client`` is a Shoko client, or None for "there is no Shoko here" - in
    which case only the pool can answer, which is the standalone deployment's
    whole story until somebody types a number in.

    ``fetch`` allows the walk to ask Shoko to pull an entry from AniDB. Off,
    the answer is whatever can be had without reaching anybody.
    """
    found = Numbering(aid=int(aid))
    chain: list[Season] = []
    seen: set[int] = set()
    current: int | None = int(aid)
    reached_out = False

    while current and current not in seen and len(chain) < MAX_CHAIN:
        seen.add(current)
        season = remembered(current)
        if season is None or season.episode_count is None:
            if client is None:
                found.detail = "no Shoko Server is configured, so nothing can look this up"
                break
            if reached_out and fetch:
                # Only between two fetches, never before the first.
                time.sleep(FETCH_PAUSE_SECONDS)
            fetched, reached_out = _from_shoko(client, current, fetch=fetch)
            if fetched is None:
                found.detail = f"AniDB entry {current} could not be read"
                break
            season = fetched
            remember(season)
        chain.append(season)
        # A manual start ends the walk: somebody said where this begins, and
        # everything before it stops mattering.
        if season.absolute_start is not None and season.source == "manual":
            break
        current = season.prequel_aid

    if not chain:
        found.detail = found.detail or "nothing is known about this entry"
        return found

    chain.reverse()
    found.chain = chain
    here = chain[-1]
    found.episode_count = here.episode_count
    found.source = here.source or "pool"

    if here.absolute_start is not None and here.source == "manual":
        found.absolute_start = here.absolute_start
        found.source = "manual"
    elif chain[0].prequel_aid is None or (len(chain) > 1 and chain[0].source == "manual"):
        # The chain reached the beginning, so the sum is the whole truth.
        position = chain[0].absolute_start or 1
        for season in chain:
            season.absolute_start = position
            remember(season)
            if season.kind in COUNTED_KINDS:
                position += season.episode_count or 0
        found.absolute_start = here.absolute_start
    else:
        found.detail = (
            f"the chain stops at AniDB {chain[0].aid}: its prequel"
            f" {chain[0].prequel_aid} could not be read"
        )
    return found


def _from_shoko(client, aid: int, *, fetch: bool) -> tuple[Season | None, bool]:
    """One entry, from Shoko - fetching it through Shoko if it has none.

    Returns the entry and whether AniDB was reached for it, which is what the
    pause between fetches is counted from.
    """
    from backend.app.integrations.shoko.client import ShokoError

    reached_out = False
    try:
        record = client.anidb_anime(aid)
    except ShokoError:
        if not fetch:
            return None, reached_out
        try:
            client.fetch_anidb_anime(aid)
            reached_out = True
        except ShokoError as exc:
            logger.info("Shoko could not fetch AniDB %s: %s", aid, exc)
            return None, reached_out
        record = None
        # Shoko queues the fetch; it is usually there within a few seconds.
        for _attempt in range(8):
            time.sleep(FETCH_RETRY_SECONDS)
            try:
                record = client.anidb_anime(aid)
                break
            except ShokoError:
                continue
        if record is None:
            return None, reached_out

    season = Season(
        aid=int(aid),
        title=_text(record.get("Title")),
        kind=_text(record.get("Type")),
        episode_count=_number(record.get("EpisodeCount")),
        in_library=record.get("ShokoID") is not None,
        source="shoko",
    )
    try:
        for relation in client.anidb_relations(aid):
            if relation.get("Type") == "Prequel":
                season.prequel_aid = _number((relation.get("RelatedIDs") or {}).get("AniDB"))
                break
    except Exception as exc:  # noqa: BLE001 - a chain without relations is a chain of one
        logger.debug("no relations for AniDB %s: %s", aid, exc)
    return season, reached_out


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _number(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = [
    "COUNTED_KINDS",
    "MAX_CHAIN",
    "Numbering",
    "Season",
    "remember",
    "remembered",
    "resolve",
    "set_manually",
]
