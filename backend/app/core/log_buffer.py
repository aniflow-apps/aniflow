"""AniFlow's own memory of its last log lines (build 262).

The container log is the only place the log goes by default (``LOG_FILE`` is off), at whatever level the settings
say - ``ERROR`` out of the box - and a person asked "what did AniFlow say around then?" has nothing to look at. This
keeps the last few thousand lines of INFO and above in memory, whatever the console level is, so the diagnostics
bundle always has something to hold.

What it keeps is the log *as written*: anime names and folder paths appear in it. Secrets are taken out when the
bundle is made (``services/redaction.py``), not here - the buffer never leaves the process on its own.

Never kept: the one-time account code, which is announced precisely so that it is read by the person running the
deployment and by no one else.
"""
from __future__ import annotations

import logging
from collections import deque

#: Lines kept. A few hundred bytes each, so well under a megabyte.
CAPACITY = 4000


class RingBufferHandler(logging.Handler):
    """Keeps the last ``capacity`` formatted records."""

    def __init__(self, capacity: int = CAPACITY, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._lines: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        # Announced lines exist to be read once by the person at the console (the account's one-time code).
        if getattr(record, "aniflow_announce", False):
            return
        if getattr(record, "component", None) is None:
            record.component = record.name
        try:
            self._lines.append(self.format(record))
        except Exception:  # noqa: BLE001 - a log line that cannot be formatted must never break the caller
            self.handleError(record)

    def lines(self) -> list[str]:
        """The kept lines, oldest first."""
        return list(self._lines)

    def clear(self) -> None:
        self._lines.clear()


__all__ = ["CAPACITY", "RingBufferHandler"]
