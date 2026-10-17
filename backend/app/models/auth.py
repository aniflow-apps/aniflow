"""Who is allowed in (build 72).

Three tables, each with one job:

``User``
    The single account. One row, enforced by the service rather than by the
    schema, so growing to several accounts later is a migration and not a
    rewrite. The password is only ever here as a hash.

``AuthSession``
    One logged-in browser. Server-side rather than a self-contained signed
    cookie, for two reasons: logging out can actually end a session, and a
    sliding 90-day expiry has somewhere to record "last used". The cookie
    carries a random token; only its hash is stored, so a copy of the database
    is not a set of working sessions.

``AuthToken``
    A one-hour, single-use code for creating the account or resetting the
    password. Printed to the log, because whoever can read the container's log
    is already trusted with the deployment - and hashed here for the same
    reason sessions are.
"""
from datetime import datetime

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc


class User(Base):
    """The one account that may use this AniFlow."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    #: ``scrypt$n$r$p$salt$hash`` - see services/auth.py. Never a password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Consecutive failures, and until when logins are refused. Reset by any
    #: success, so a wrong password yesterday does not count against you today.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False
    )


class AuthSession(Base):
    """One browser that has logged in."""

    __tablename__ = "auth_sessions"

    #: The SHA-256 of the cookie's token. The token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    #: Pushed forward as the session is used, which is what makes the 90 days
    #: slide rather than expire from the moment you first logged in.
    last_seen_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class AuthToken(Base):
    """A single-use code for creating the account or resetting the password."""

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: ``setup`` or ``reset``. Recorded for the log, not for permission: both
    #: prove the same thing, which is that you can read this deployment's log.
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


__all__ = ["AuthSession", "AuthToken", "User"]
