"""A record of what the Calendar just did, for a person watching (build 144).

Asked for while Calendar v2 was being worked on:

    *"we need a way better debug method for the calendar, this can be temp,
    but while working on it, I would like to see what is happening"*

Right, and the container log was a poor answer: it needs shell access, it is
interleaved with everything else AniFlow does, and it is gone as soon as it
scrolls. This is the same information where the work is happening.

**Deliberately in memory and deliberately bounded.** It is a window onto a run
in progress, not a history: a restart clears it, and nothing else in AniFlow
reads it. If Calendar v2 settles and nobody misses it, this file is deleted
and nothing else changes.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.app.db.types import now_utc

#: How much to keep. One season across five sources is a handful of lines; a
#: whole update of three seasons is a few dozen. Two hundred is several runs,
#: which is as far back as anybody looks while watching something.
LIMIT = 200


@dataclass(frozen=True)
class Event:
    """One thing that happened, with the numbers that make it worth reading."""

    at: datetime
    #: ``info`` | ``warn`` | ``error``. What the page colours it by.
    level: str
    #: Which part said it: ``fetch``, ``snapshot``, ``artwork``, ``job``.
    stage: str
    message: str
    #: The numbers behind the sentence, so the page can show them as a row.
    detail: dict[str, Any] = field(default_factory=dict)


_events: deque[Event] = deque(maxlen=LIMIT)
#: The worker and a request thread both write here.
_lock = threading.Lock()


def record(stage: str, message: str, *, level: str = "info", **detail: Any) -> None:
    """Note something worth seeing on the debug page. Never raises.

    Called from the engine's own paths, so it must never be the reason an
    update fails - a trace that can break the thing it is tracing is worse
    than no trace.
    """
    try:
        with _lock:
            _events.append(
                Event(at=now_utc(), level=level, stage=stage, message=message, detail=detail)
            )
    except Exception:  # noqa: BLE001 - see above
        pass


def recent(limit: int = LIMIT) -> list[Event]:
    """Newest first, because that is where a person looks."""
    with _lock:
        return list(reversed(list(_events)))[:limit]


def clear() -> int:
    """Empty it, for a run somebody wants to watch from the beginning."""
    with _lock:
        count = len(_events)
        _events.clear()
    return count
