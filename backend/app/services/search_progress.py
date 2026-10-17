"""How far a search has got, while it is still going (build 109).

A search used to be one request that answered when it was finished. That was
fine when it read one page; it reads every page now, and a six-name search on
a popular show is eighty requests and the better part of a minute. A page that
shows nothing for a minute looks broken whatever it is doing.

The search itself is unchanged - still one request per term, still answering
with everything it found. This is a counter beside it: the search writes its
position into a slot the browser named, and the browser reads that slot while
it waits. Nothing depends on it, so nothing fails when it is missing: a lost
token, a restarted server or a browser that never asks all read as "no
progress to report" rather than as an error.

Deliberately in memory, deliberately small, and deliberately expiring. This is
the state of something happening *now*; a minute after it stopped happening
there is nothing worth knowing.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

#: How long a finished slot is worth keeping. Long enough for the browser's
#: last poll to land, short enough that nothing accumulates.
TTL_SECONDS = 120.0

#: At most this many searches at once. A slot is a handful of integers, but an
#: unbounded dictionary written to by every request is a leak with a
#: respectable name.
MAX_SLOTS = 40


@dataclass
class Progress:
    """Where one search has got to."""

    #: The term being asked about now.
    term: str = ""
    #: Requests finished for this term.
    done: int = 0
    #: How many this term is expected to take, as far as anything knows. Zero
    #: while nothing knows - the caller then has a spinner rather than a bar,
    #: which is the honest picture of "one page in, no idea how many".
    expected: int = 0
    #: Which term this is, of how many the browser said it would run.
    term_index: int = 0
    term_count: int = 0
    updated_at: float = field(default_factory=time.monotonic)


_slots: dict[str, Progress] = {}
_lock = threading.Lock()


def start(token: str, *, term_index: int = 0, term_count: int = 0) -> None:
    """Open a slot, or reuse the one this token already has."""
    if not token:
        return
    with _lock:
        slot = _slots.get(token) or Progress()
        slot.term_index = term_index
        slot.term_count = term_count
        slot.done = 0
        slot.expected = 0
        slot.updated_at = time.monotonic()
        _slots[token] = slot
        # After the new slot is in, not before: trimming first would let this
        # one take the count one past the limit.
        _expire()


def asking(token: str, term: str, *, expected: int = 0) -> None:
    """Say which term is being asked, and how much of it is expected."""
    if not token:
        return
    with _lock:
        slot = _slots.get(token)
        if slot is None:
            return
        slot.term = term
        slot.expected = expected
        slot.updated_at = time.monotonic()


def page_done(token: str, *, expected: int | None = None) -> None:
    """One more request finished."""
    if not token:
        return
    with _lock:
        slot = _slots.get(token)
        if slot is None:
            return
        slot.done += 1
        if expected is not None:
            # Refined as the search learns: the site's own count divided by
            # its page size is a far better guess than anything set up front,
            # and it only becomes available after the first request.
            slot.expected = max(expected, slot.done)
        slot.updated_at = time.monotonic()


def read(token: str) -> Progress | None:
    """Where that search is, or None when nothing is known about it."""
    if not token:
        return None
    with _lock:
        _expire()
        return _slots.get(token)


def finish(token: str) -> None:
    """Forget a search that has finished."""
    if not token:
        return
    with _lock:
        _slots.pop(token, None)


def reset() -> None:
    """Forget everything. For tests."""
    with _lock:
        _slots.clear()


def _expire() -> None:
    """Drop what has gone quiet, and the oldest if there are too many."""
    now = time.monotonic()
    for token, slot in list(_slots.items()):
        if now - slot.updated_at > TTL_SECONDS:
            _slots.pop(token, None)
    while len(_slots) > MAX_SLOTS:
        oldest = min(_slots, key=lambda token: _slots[token].updated_at)
        _slots.pop(oldest, None)


__all__ = ["MAX_SLOTS", "Progress", "TTL_SECONDS", "asking", "finish", "page_done", "read", "reset", "start"]
