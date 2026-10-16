"""One television that may talk to AniFlow (build 181).

A TV is not a browser and must not be able to act like one: the threat model in
services/auth.py names "the smart TV" as the thing that cannot open AniFlow and
start deleting a library. So a TV does not get a session. It gets a token that
opens exactly one door - ``/api/tv/v1`` - and nothing behind it.

Only the SHA-256 of the token is stored, the same way sessions are, so a copy
of the database is not a set of working TVs. The token itself is shown once,
when it is made.
"""
from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc


class TvToken(Base):
    """A named, individually revocable credential for one TV."""

    __tablename__ = "tv_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: What the user calls this TV ("Living room Samsung").
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: The id the TV itself gave when it asked to be paired (build 263), so a TV that pairs again is recognised
    #: and its old entry replaced. Not a secret and not proof of anything: it only lets the person approving see
    #: "this TV was paired before". Null for a token made before this, or by a TV that sent none.
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    #: Pushed forward at most once a minute, so a TV refreshing a screen does
    #: not turn every read into a write.
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Revoked rather than deleted, so the list can still say "this one was
    #: switched off" instead of the TV simply vanishing from it.
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


__all__ = ["TvToken"]
