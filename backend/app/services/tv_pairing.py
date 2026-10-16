"""Pairing a television without typing its token (build 187).

The TV asks to be paired and gets two different things back:

* a **code** (``K7QM-X2PD``) that it shows, and the phone's approval page
  asks for - short, because a person reads it;
* a **ticket**, 32 random bytes that never leave the TV, which it polls with.

They are separate on purpose. Anybody who can see the screen can read the code;
only the TV can collect the token. So approving a code never hands the token to
whoever approved it, and a stranger who guesses a code cannot collect anything.

The user approves from a browser that is already logged in, so approval is the
one step that needs a session, and it is the only step that makes a token.

**Held in memory, not in the database.** A pairing lives for minutes, and the
token it carries between approval and collection would otherwise sit in the
database in the clear - the one thing ``tv_tokens`` was built never to hold. A
restart in those minutes costs the user one more scan. A token approved but
never collected is revoked when its pairing runs out, so an abandoned pairing
leaves no working credential behind.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.services import tv_tokens

#: How long a code can be approved after it was shown.
LIFETIME_SECONDS = 300
#: How many pairings may wait at once. Anyone on the network can start one, so
#: the number that can pile up is bounded rather than trusted.
MAX_PENDING = 20
#: How often the TV should ask. A hint, not enforced.
POLL_SECONDS = 2

#: No 0/O, 1/I/L: a code read off a TV screen across a room.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class TooManyPairings(Exception):
    """Too many pairings are waiting; try again in a few minutes."""


@dataclass
class _Pairing:
    code: str
    name: str
    expires_at: float
    #: Set on approval, handed over once, then dropped.
    token: str | None = None
    token_id: int | None = None
    approved: bool = False
    #: The id the TV sent (build 263), if it sent one.
    device_id: str | None = None
    #: The entries the person approving said this TV replaces. Taken out of use when the TV *collects* its new
    #: token - not at approval, so a pairing that is approved and never collected costs the old entry nothing.
    replace_ids: set[int] = field(default_factory=set)


@dataclass
class Replaceable:
    """An existing entry this pairing could replace, as the approval page shows it."""

    id: int
    name: str
    created_at: datetime | None
    last_used_at: datetime | None
    #: True when the TV's own id matches (replaced automatically); False when only the name does (offered, and the
    #: person decides - two TVs of one make are both called "Samsung TV").
    same_device: bool


_lock = threading.Lock()
#: ticket -> pairing
_pairings: dict[str, _Pairing] = {}


def _now() -> float:
    return time.monotonic()


def format_code(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:]}"


def normalise(code: str | None) -> str:
    """What a person typed, as the stored form: no dash or space, upper case."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _sweep(db: Session | None) -> None:
    """Drop what has run out - and cut off a token nobody collected."""
    now = _now()
    for ticket in [key for key, p in _pairings.items() if p.expires_at <= now]:
        pairing = _pairings.pop(ticket)
        if pairing.token_id is not None and pairing.token is not None and db is not None:
            tv_tokens.revoke(db, pairing.token_id)


def start(db: Session | None, name: str, device_id: str | None = None) -> tuple[str, str, int]:
    """A new pairing. Returns ``(ticket, code, seconds_valid)``."""
    with _lock:
        _sweep(db)
        if len(_pairings) >= MAX_PENDING:
            raise TooManyPairings()
        raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        while any(normalise(p.code) == raw for p in _pairings.values()):
            raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        ticket = secrets.token_urlsafe(32)
        _pairings[ticket] = _Pairing(
            code=format_code(raw),
            name=(name or "").strip()[:80] or "TV",
            expires_at=_now() + LIFETIME_SECONDS,
            device_id=device_id or None,
        )
        return ticket, format_code(raw), LIFETIME_SECONDS


def lookup(db: Session | None, code: str | None) -> _Pairing | None:
    """The live, not yet approved pairing for this code, for the approval page."""
    wanted = normalise(code)
    with _lock:
        _sweep(db)
        for pairing in _pairings.values():
            if not pairing.approved and normalise(pairing.code) == wanted and wanted:
                return pairing
    return None


def replaceable(db: Session, pairing: _Pairing) -> list[Replaceable]:
    """The live entries this pairing could replace, for the approval page (build 263).

    Entries carrying the TV's own id come first and are replaced without being asked. Entries with *no* id of their
    own - made before TVs sent one - and the same name are offered: they are probably the same TV, and only the
    person can say. An entry with a *different* id is another TV however it is named, and is never offered.
    """
    found: list[Replaceable] = []
    same = tv_tokens.active_for_device(db, pairing.device_id)
    taken = {row.id for row in same}
    for row in same:
        found.append(Replaceable(row.id, row.name, row.created_at, row.last_used_at, True))
    wanted = pairing.name.strip().casefold()
    for row in tv_tokens.active(db):
        if row.id in taken or row.device_id is not None or row.name.strip().casefold() != wanted:
            continue
        found.append(Replaceable(row.id, row.name, row.created_at, row.last_used_at, False))
    return found


def approve(
    db: Session, code: str | None, name: str | None, replace_ids: list[int] | None = None
) -> tuple[_Pairing, int] | None:
    """Make the token for this code's TV. None when the code is not live.

    ``replace_ids`` are the offered entries the person ticked; only ones ``replaceable`` lists are honoured, so a
    request cannot take an unrelated TV out of use. Returns the pairing and the new token's row id.
    """
    wanted = normalise(code)
    with _lock:
        _sweep(db)
        pairing = next(
            (
                p
                for p in _pairings.values()
                if not p.approved and wanted and normalise(p.code) == wanted
            ),
            None,
        )
        if pairing is None:
            return None
        options = replaceable(db, pairing)
        same_device = [item for item in options if item.same_device]
        if name and name.strip():
            pairing.name = name.strip()[:80]
        elif same_device:
            # Nothing typed: the TV keeps the name its entry already has, not the generic one it reports.
            pairing.name = same_device[0].name
        offered = {item.id for item in options}
        pairing.replace_ids = {item.id for item in same_device} | (set(replace_ids or []) & offered)
        row, token = tv_tokens.create(db, pairing.name, pairing.device_id)
        pairing.token = token
        pairing.token_id = row.id
        pairing.approved = True
        return pairing, row.id


def poll(db: Session | None, ticket: str) -> tuple[str, str | None, int]:
    """What the TV is told: ``(status, token, seconds_left)``.

    ``pending`` until approved; then ``approved`` **once**, carrying the token,
    after which the pairing is gone; ``expired`` for anything else - an unknown
    ticket looks exactly like a run-out one.
    """
    with _lock:
        _sweep(db)
        pairing = _pairings.get(ticket)
        if pairing is None:
            return "expired", None, 0
        if not pairing.approved:
            return "pending", None, max(0, int(pairing.expires_at - _now()))
        token = pairing.token
        del _pairings[ticket]
        # The TV has its new token now: the entry it already had takes it over, so the list keeps one entry for it
        # (and the old token stops working).
        if db is not None and pairing.replace_ids and pairing.token_id is not None:
            tv_tokens.merge_replaced(db, pairing.token_id, pairing.replace_ids)
        return "approved", token, 0


def reset() -> None:
    """For tests."""
    with _lock:
        _pairings.clear()


__all__ = [
    "ALPHABET",
    "LIFETIME_SECONDS",
    "MAX_PENDING",
    "POLL_SECONDS",
    "Replaceable",
    "TooManyPairings",
    "approve",
    "replaceable",
    "lookup",
    "normalise",
    "poll",
    "reset",
    "start",
]
