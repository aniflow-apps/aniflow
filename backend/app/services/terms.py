"""The notice a new install is shown before anything else, and that it was accepted.

The text lives in the interface (``setup.terms.*`` in the locale files, and
``LEGAL.md`` / ``LEGAL.de.md`` for everywhere outside it). What is kept here is
only that somebody agreed to **this version** of it, and when - so a later
change to the wording can ask again instead of assuming.

Enforced here and not only by a disabled button: the account is created by
``POST /api/auth/setup``, and a call that skips the notice is refused.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.models.setting import Setting

#: Bump this whenever the wording changes in a way a person should see again.
TERMS_VERSION = "2026-10-15-2"

VERSION_KEY = "terms_accepted_version"
AT_KEY = "terms_accepted_at"


def accepted_version(session: Session) -> str | None:
    row = session.get(Setting, VERSION_KEY)
    return row.value if row is not None else None


def is_accepted(session: Session) -> bool:
    """Whether this version of the notice has been accepted on this install."""
    return accepted_version(session) == TERMS_VERSION


def record(session: Session) -> None:
    """Remember that the current version was accepted, now. Does not commit."""
    session.merge(Setting(key=VERSION_KEY, value=TERMS_VERSION))
    session.merge(Setting(key=AT_KEY, value=datetime.now(UTC).isoformat(timespec="seconds")))


__all__ = ["TERMS_VERSION", "accepted_version", "is_accepted", "record"]
