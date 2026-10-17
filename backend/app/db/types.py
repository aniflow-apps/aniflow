"""Column types that behave the same on SQLite as everywhere else.

SQLite has no timezone storage. ``DateTime(timezone=True)`` is accepted and
then ignored: what comes back is a naive datetime, whatever was written. That
is a quiet trap, because a naive datetime is not wrong in any visible way - it
is simply missing the one fact that makes it interpretable.

It has cost twice. Comparing a stored instant with ``datetime.now(UTC)`` raised
"can't subtract offset-naive and offset-aware", which was noticed. And every
timestamp left the API as ``2026-09-06T10:10:21`` with no marker, which
JavaScript reads as *local* time - so a UTC instant was rendered two hours
early with nothing to suggest anything was wrong.

Both come from the same missing fact, so it is supplied here rather than at
each of the places that need it: everything AniFlow stores is UTC, and now
everything it reads back says so.
"""
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware in Python.

    On the way in, an aware value is converted to UTC and a naive one is taken
    to be UTC already - AniFlow writes nothing else. On the way out, UTC is
    attached, because that is what the stored value has always meant.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


#: Guards the clamp in :func:`now_utc`; two threads writing rows at once must
#: not be handed the same instant.
_clock_lock = Lock()

#: The last value handed out, so the next one can be made later than it.
_last_stamp: datetime | None = None


def now_utc() -> datetime:
    """The current instant, for use as a column default - always increasing.

    Written per row in Python rather than left to ``server_default=func.now()``
    alone, because SQLite's CURRENT_TIMESTAMP has whole-second precision *and*
    is constant within a statement: every row a pass wrote in the same second
    got an identical timestamp, and ``ORDER BY created_at DESC`` then cannot
    order them at all - SQLite falls back to insertion order, oldest first.
    That is why the newest download sat at the *bottom* of the Downloads list
    (build 135), and why the Activity log could show one pass's entries in the
    wrong order.

    Precision alone turned out not to be enough. ``datetime.now()`` advances
    in ~15ms steps on Windows, so a loop of inserts still shared one value;
    Linux would have separated them, and a list whose whole job is to be
    ordered should not depend on which host wrote it. So each value is made
    strictly later than the last one handed out.

    The clamp moves a timestamp by microseconds, which is immaterial for a
    value that exists to be displayed and sorted. It is per process: the API
    and the worker can still produce equal values in the same microsecond, and
    that is a tie nothing on screen can act on.

    ``server_default`` stays on the columns so a row written by a migration or
    by raw SQL is still stamped.
    """
    global _last_stamp

    with _clock_lock:
        value = datetime.now(UTC)
        if _last_stamp is not None and value <= _last_stamp:
            value = _last_stamp + timedelta(microseconds=1)
        _last_stamp = value
        return value


__all__ = ["UtcDateTime", "now_utc"]
