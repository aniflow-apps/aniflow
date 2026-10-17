"""Making, checking and revoking the tokens a television uses (build 181).

Same shape as the session tokens in services/auth.py, for the same reasons:
32 random bytes, only the SHA-256 stored, so a copy of the database is not a
set of working TVs.

**Images.** An ``<img>`` cannot send an ``Authorization`` header, and the
poster routes under ``/api`` are cookie-gated. So the TV API hands out image
URLs carrying ``k``: an HMAC of the image's own path, keyed by the token's
hash. The token never appears in a URL, the key is useless for anything except
that one image, and revoking the token kills every key made from it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.tv_token import TvToken

#: How often ``last_used_at`` is written. A TV refreshing a screen is a burst
#: of reads, and none of them should be a write.
TOUCH_SECONDS = 60

_PREFIX = "aftv_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(db: Session, name: str, device_id: str | None = None) -> tuple[TvToken, str]:
    """A new TV. Returns the row and the token, which is never available again."""
    token = _PREFIX + secrets.token_urlsafe(32)
    row = TvToken(name=name.strip()[:80] or "TV", token_hash=_hash(token), device_id=device_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, token


def active(db: Session) -> list[TvToken]:
    """The TVs that can still sign in, newest first."""
    return [row for row in listing(db) if row.revoked_at is None]


def active_for_device(db: Session, device_id: str | None) -> list[TvToken]:
    """The live entries that carry this TV's id - normally one, or none."""
    if not device_id:
        return []
    return [row for row in active(db) if row.device_id == device_id]


def merge_replaced(db: Session, new_id: int, replaced_ids: set[int]) -> TvToken | None:
    """The entry a TV already had takes over its new token, and the new row goes (build 263).

    A TV that pairs again must not leave a second entry, and must not leave a "revoked" line behind either: one TV,
    one entry. The most recently used of the replaced entries survives - it keeps its place in the list - with the
    new token, name and id; the temporary row made at approval is dropped, and any other entry the person said was
    the same TV is dropped with it. Its old token stops working, because the hash is replaced.

    Returns the surviving row, or None when there was nothing live to replace.
    """
    replaced = [
        row for row in db.scalars(select(TvToken).where(TvToken.id.in_(replaced_ids))) if row.revoked_at is None
    ] if replaced_ids else []
    new = db.get(TvToken, new_id)
    if not replaced or new is None:
        return None
    survivor = max(replaced, key=lambda row: _aware(row.last_used_at or row.created_at))
    token_hash, name, device_id = new.token_hash, new.name, new.device_id
    db.delete(new)
    db.flush()  # the hash is unique: the new row has to be gone before the old one can have it
    survivor.token_hash = token_hash
    survivor.name = name
    survivor.device_id = device_id
    survivor.created_at = datetime.now(UTC)
    survivor.last_used_at = None
    for row in replaced:
        if row is not survivor:
            db.delete(row)
    db.commit()
    return survivor


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.min.replace(tzinfo=UTC)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def listing(db: Session) -> list[TvToken]:
    return list(db.scalars(select(TvToken).order_by(TvToken.created_at.desc())))


def revoke(db: Session, token_id: int) -> bool:
    row = db.get(TvToken, token_id)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
    return True


def rename(db: Session, token_id: int, name: str) -> TvToken | None:
    row = db.get(TvToken, token_id)
    if row is None:
        return None
    row.name = name.strip()[:80] or row.name
    db.commit()
    return row


def verify(db: Session, token: str | None) -> TvToken | None:
    """The live token row for this credential, or None."""
    if not token or not token.startswith(_PREFIX):
        return None
    row = db.scalar(select(TvToken).where(TvToken.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    seen = row.last_used_at
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    if seen is None or now - seen > timedelta(seconds=TOUCH_SECONDS):
        row.last_used_at = now
        db.commit()
    return row


def image_key(token_hash: str, kind: str, ident: str) -> str:
    """The ``k`` for one image, made from one token."""
    message = f"{kind}/{ident}".encode()
    return hmac.new(token_hash.encode(), message, hashlib.sha256).hexdigest()[:32]


def image_key_valid(db: Session, kind: str, ident: str, key: str | None) -> bool:
    """Whether ``key`` was made from any live token, for exactly this image."""
    if not key:
        return False
    for token_hash in db.scalars(
        select(TvToken.token_hash).where(TvToken.revoked_at.is_(None))
    ):
        if hmac.compare_digest(image_key(token_hash, kind, ident), key):
            return True
    return False


def bearer(header: str | None) -> str | None:
    """The token out of ``Authorization: Bearer <token>``."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def signing_hash(token: str) -> str:
    """What image keys are made from for this request's own token."""
    return _hash(token)
