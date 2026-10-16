"""What a selection scan found, kept briefly so adding need not ask again.

Build 100, and the reason is a question that should have been asked earlier:
*"when I select a subgroup and it fills the list and I press Add to watchlist,
does it do another search? This should not happen - the list is already there
and the files exist."*

It did. Build 95 made both searches ask the same question with the same
filters so the answers would be identical - and "should be identical" is what
this dialog keeps being fixed for. A tracker is not a function: a page times
out, a rate limit arrives, results shift between two requests a minute apart.
Each of those turned a box that said twelve into a watchlist entry with none.

**Why a store rather than sending the files back.** The scan deliberately does
not hand the browser magnet links for an anime nobody has added yet. So the
answer stays on the server and the browser carries only a ticket to it.

**Why forgetting is safe.** A miss falls back to searching again, which is
exactly what every build up to 99 did. So this can be small, in memory, and
short-lived without anything depending on it - which is also why it is not a
database table.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from backend.app.core.logging import for_component
from backend.app.integrations.search.base import SearchResult

logger = for_component("Search")

#: How long a ticket is worth anything. Long enough to read the file list and
#: think about it; short enough that what gets stored is what was on screen.
TTL = timedelta(minutes=30)

#: How many scans to remember. Each is a few hundred names; a person compares
#: a handful of rows before choosing, and the oldest goes when the box is
#: full.
MAX_ENTRIES = 40

_LOCK = Lock()
_ENTRIES: "OrderedDict[str, _Entry]" = OrderedDict()


@dataclass(frozen=True)
class _Entry:
    results: tuple[SearchResult, ...]
    stored_at: datetime


def remember(results: list[SearchResult]) -> str:
    """Keep one scan's hits and return the ticket for them."""
    ticket = uuid.uuid4().hex
    with _LOCK:
        _prune()
        _ENTRIES[ticket] = _Entry(results=tuple(results), stored_at=datetime.now(UTC))
        while len(_ENTRIES) > MAX_ENTRIES:
            _ENTRIES.popitem(last=False)
    return ticket


def recall(ticket: str | None) -> list[SearchResult] | None:
    """The hits behind a ticket, or None - which means "search again".

    Deliberately not removed on read: the dialog may scan once and then add,
    convert or change a release from the same answer, and making the first of
    those consume it would send the others back to the tracker.
    """
    if not ticket:
        return None
    with _LOCK:
        _prune()
        entry = _ENTRIES.get(ticket)
    if entry is None:
        logger.debug("scan ticket %s is not held; the search will be repeated", ticket)
        return None
    return list(entry.results)


def recall_all(tickets: list[str] | None) -> list[SearchResult] | None:
    """Every hit behind these tickets, deduplicated, or None for "ask again".

    None only when *nothing* is held: a partial answer is worse than a fresh
    search, because the missing name is exactly the one that found the season
    (build 96). So one expired ticket sends the whole thing back to the
    tracker.
    """
    if not tickets:
        return None
    found: list[SearchResult] = []
    seen: set[str] = set()
    for ticket in tickets:
        held = recall(ticket)
        if held is None:
            return None
        for result in held:
            if result.id in seen:
                continue
            seen.add(result.id)
            found.append(result)
    return found or None


def _prune(now: datetime | None = None) -> None:
    """Drop what has expired. Called under the lock."""
    moment = now or datetime.now(UTC)
    stale = [
        ticket for ticket, entry in _ENTRIES.items() if moment - entry.stored_at > TTL
    ]
    for ticket in stale:
        _ENTRIES.pop(ticket, None)


def forget_all() -> None:
    """Empty the store. For tests, and for a restart to mean something."""
    with _LOCK:
        _ENTRIES.clear()


__all__ = ["MAX_ENTRIES", "TTL", "forget_all", "recall", "recall_all", "remember"]
