"""The one account, and what it takes to get in (build 72).

The threat this answers is a shared network, not a determined attacker: the
point is that the flatmate's laptop and the smart TV cannot open AniFlow and
start deleting a library. Everything here is chosen with that in mind, and
where a compromise was made it is written down rather than implied.

**Passwords** are stored as scrypt hashes with a per-account salt. Standard
library, so there is no wheel to build for ARM and no dependency to keep up
with; memory-hard, so a stolen database does not hand over the password.

**Sessions** live in a table. The cookie carries 32 random bytes and the table
stores only their SHA-256, so a copy of the database is not a set of working
logins. Ninety days, pushed forward every time the session is used - "not a
bank app" was the brief, and a login that survives a container restart is the
whole point of storing them at all.

**Tokens** are how you get in when there is no account yet or you have
forgotten the password. They are printed to the log, which means the real
credential is access to the container's log - and anyone who has that can
already read the database. One hour, single use, so a token left behind in a
log file stops being a key.

What this deliberately does not do: HTTPS. On a plain-HTTP LAN the password
crosses the network in the clear, and no amount of hashing here changes that.
The cookie is marked Secure only when the request already arrived over HTTPS,
so putting a reverse proxy in front upgrades it without touching this code.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.auth import AuthSession, AuthToken, User

logger = for_component("Auth")

#: The cookie the browser gets back. Named plainly; nothing about it is secret
#: except its value.
COOKIE_NAME = "aniflow_session"

#: How long a login lasts without being used. Refreshed on every request that
#: carries it, so an active browser is never asked again.
SESSION_DAYS = 90

#: How long a setup/reset code stays usable. Long enough to read a log and
#: type it in, short enough that an old log line is not a key.
TOKEN_MINUTES = 60

#: Wrong passwords in a row before logins are refused for a while, and for how
#: long. Guessing becomes impractical; being locked out of your own AniFlow
#: never lasts long enough to matter.
MAX_FAILURES = 10

#: How long a *successful* session check is trusted without asking the
#: database again (build 84).
#:
#: Every request under /api is gated, and a page of two hundred posters is two
#: hundred requests. Each one used to run two queries and hold a pooled
#: connection while it did, which is how build 83 managed to exhaust a pool of
#: fifteen and answer nothing for thirty seconds at a time.
#:
#: Only positive answers are remembered, and only for seconds. A logout still
#: takes effect within one tick of the clock, an unknown cookie is never
#: trusted for a moment, and the thing this exists for - the same browser
#: asking two hundred times in one second - costs one query.
GATE_SECONDS = 15.0
LOCKOUT_MINUTES = 5

#: A deliberate pause on every failed attempt. Costs a person nothing and costs
#: a script everything.
FAILURE_DELAY_SECONDS = 0.75

#: scrypt parameters. n=2^14 with r=8 is ~16 MB per hash: unnoticeable once per
#: login, expensive in bulk, and modest enough for a Raspberry Pi. Deliberately
#: under OpenSSL's own 32 MB ceiling, which n=2^15 exceeds - it raises
#: "memory limit exceeded" rather than being slow, so a bigger n is not a
#: harmless change.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
#: Stated rather than left to OpenSSL's default, so the parameters above are
#: the only thing that decides the cost.
_SCRYPT_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD_LENGTH = 8


class AuthError(RuntimeError):
    """Something the user needs told: wrong password, expired token, locked."""


@dataclass(frozen=True)
class AuthState:
    """What the login screen needs to know before anyone types anything."""

    #: No account exists, so the only thing on offer is creating one.
    needs_setup: bool
    authenticated: bool
    username: str | None = None


# -- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    """``scrypt$n$r$p$salt$hash``, with a fresh salt every time."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Whether the password matches, in constant time.

    The parameters come out of the stored string rather than the constants
    above, so raising the cost later does not lock anyone out of an account
    hashed under the old settings.
    """
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected) // 2,
        )
    except (ValueError, TypeError):
        # A hash we cannot read is not a hash that matches.
        return False
    return hmac.compare_digest(derived.hex(), expected)


def validate_password(password: str) -> None:
    """The one rule: long enough to not be guessed in ten tries.

    No character-class demands. They push people towards ``Passw0rd!`` and
    away from a long phrase, which is the wrong trade for a home server.
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"The password needs at least {MIN_PASSWORD_LENGTH} characters")


def _hash_token(token: str) -> str:
    """Tokens and session values are high-entropy already, so one SHA-256 pass
    is the right primitive - a slow KDF here would only slow down every
    request without adding anything a 256-bit random value does not have."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# -- the account -------------------------------------------------------------


def current_user(db: Session) -> User | None:
    """The single account, or None when AniFlow has not been set up yet."""
    return db.scalars(select(User).order_by(User.id)).first()


def state(db: Session, session_token: str | None) -> AuthState:
    """Everything the login screen decides its shape from."""
    user = current_user(db)
    if user is None:
        return AuthState(needs_setup=True, authenticated=False)
    holder = session_user(db, session_token) if session_token else None
    return AuthState(
        needs_setup=False,
        authenticated=holder is not None,
        username=holder.username if holder is not None else None,
    )


def create_account(db: Session, *, username: str, password: str, token: str) -> User:
    """Create the one account, against a token from the log.

    Refused once an account exists: this is the door into an empty AniFlow, and
    leaving it open would make "reset the password" the least of the problems.
    """
    if current_user(db) is not None:
        raise AuthError("An account already exists on this AniFlow")
    _consume_token(db, token, purpose="setup")
    name = _clean_username(username)
    validate_password(password)
    user = User(username=name, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    logger.warning("account %r created", name)
    return user


def reset_password(db: Session, *, password: str, token: str) -> User:
    """Set a new password against a token from the log.

    Every session is ended: a forgotten password is one of the ways an account
    gets taken over, and leaving the old browsers logged in would make the
    reset pointless in exactly that case.
    """
    user = current_user(db)
    if user is None:
        raise AuthError("There is no account to reset yet")
    _consume_token(db, token, purpose="reset")
    validate_password(password)
    user.password_hash = hash_password(password)
    user.failed_attempts = 0
    user.locked_until = None
    revoke_all_sessions(db, user)
    logger.warning("password reset for %r; every session was ended", user.username)
    return user


def change_credentials(
    db: Session,
    user: User,
    *,
    current_password: str,
    username: str | None = None,
    new_password: str | None = None,
) -> User:
    """Change the username and/or password from inside the app.

    The current password is required even though the caller is already logged
    in: without it, anyone who finds an unlocked browser can lock the owner out
    of their own AniFlow silently.
    """
    if not verify_password(current_password, user.password_hash):
        raise AuthError("The current password is not right")
    if username is not None:
        user.username = _clean_username(username)
    if new_password:
        validate_password(new_password)
        user.password_hash = hash_password(new_password)
        logger.warning("password changed for %r", user.username)
    return user


def _clean_username(username: str) -> str:
    name = (username or "").strip()
    if len(name) < 3:
        raise AuthError("The username needs at least 3 characters")
    if len(name) > 80:
        raise AuthError("That username is too long")
    return name


# -- logging in --------------------------------------------------------------


def login(db: Session, *, username: str, password: str) -> str:
    """Check the credentials and return a fresh session token.

    The same message answers a wrong username and a wrong password, so the
    login page cannot be used to find out which account exists.
    """
    user = current_user(db)
    now = datetime.now(UTC)
    if user is None:
        raise AuthError("Set up an account first")
    if user.locked_until is not None and user.locked_until > now:
        wait = int((user.locked_until - now).total_seconds() // 60) + 1
        raise AuthError(f"Too many failed attempts. Try again in {wait} minute(s)")

    if user.username != (username or "").strip() or not verify_password(password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILURES:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_attempts = 0
            # Warning, not info: someone is guessing at your AniFlow and that
            # is worth seeing at any log level short of ERROR.
            logger.warning(
                "login locked for %s minutes after %s failed attempts",
                LOCKOUT_MINUTES,
                MAX_FAILURES,
            )
        else:
            logger.warning("failed login attempt (%s in a row)", user.failed_attempts)
        raise AuthError("Wrong username or password")

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    return _start_session(db, user)


def _start_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        AuthSession(
            token_hash=_hash_token(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=SESSION_DAYS),
        )
    )
    _drop_expired_sessions(db, now)
    return token


def session_user(db: Session, token: str | None) -> User | None:
    """The account this cookie belongs to, refreshing its expiry.

    Returns None for anything that is not a live session - unknown, expired,
    or logged out - so a caller cannot accidentally treat "no session" as
    "some session".
    """
    if not token:
        return None
    now = datetime.now(UTC)
    row = db.get(AuthSession, _hash_token(token))
    if row is None:
        return None
    if row.expires_at <= now:
        db.delete(row)
        return None
    # Sliding, but not written on every single request: an update per request
    # would mean a database write for every poll the UI makes.
    if (now - row.last_seen_at) > timedelta(hours=6):
        row.last_seen_at = now
        row.expires_at = now + timedelta(days=SESSION_DAYS)
    return db.get(User, row.user_id)


#: token hash -> when it was last confirmed. Positive answers only.
_gate: dict[str, float] = {}
_gate_lock = threading.Lock()
#: Whether an account exists at all. True is remembered for good - an account
#: cannot un-exist - and False is never cached, so a fresh install starts
#: working the moment it is set up.
_account_exists = False


def gate_allows(token: str | None) -> bool:
    """Whether this cookie was confirmed a moment ago. Touches no database."""
    if not token or not _account_exists:
        return False
    key = _hash_token(token)
    with _gate_lock:
        seen = _gate.get(key)
    return seen is not None and (time.monotonic() - seen) < GATE_SECONDS


def gate_confirm(db: Session, token: str | None) -> str | None:
    """Check the cookie against the database, and remember a success.

    Returns ``None`` when the request may proceed, or the reason it may not,
    in the words the browser already knows: ``setup_required`` means there is
    no account, ``login_required`` means this is not a live session.
    """
    global _account_exists
    if current_user(db) is None:
        return "setup_required"
    _account_exists = True
    if session_user(db, token) is None:
        return "login_required"
    assert token is not None
    with _gate_lock:
        _gate[_hash_token(token)] = time.monotonic()
        # Bounded, because the keys are attacker-supplied: a browser sending a
        # new cookie every request must not be able to grow this for ever.
        if len(_gate) > 512:
            cutoff = time.monotonic() - GATE_SECONDS
            for stale in [key for key, seen in _gate.items() if seen < cutoff]:
                _gate.pop(stale, None)
    return None


def forget_gate(token: str | None = None) -> None:
    """Drop what the gate remembers - one cookie, or all of them.

    Called from every place that invalidates a session, so "log out" means
    logged out rather than "logged out in fifteen seconds".
    """
    with _gate_lock:
        if token is None:
            _gate.clear()
        else:
            _gate.pop(_hash_token(token), None)


def logout(db: Session, token: str | None) -> None:
    """End this one session. Other browsers stay logged in."""
    if not token:
        return
    forget_gate(token)
    row = db.get(AuthSession, _hash_token(token))
    if row is not None:
        db.delete(row)


def revoke_all_sessions(db: Session, user: User) -> None:
    forget_gate()
    for row in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        db.delete(row)


def _drop_expired_sessions(db: Session, now: datetime) -> None:
    for row in db.scalars(select(AuthSession).where(AuthSession.expires_at <= now)):
        db.delete(row)


# -- tokens ------------------------------------------------------------------


def issue_token(db: Session, purpose: str) -> str:
    """Mint a code and put it in the log where the deployment's owner can see it.

    Announced through :func:`log_token` so no log level and no component filter
    can hide it. A token nobody can read is the same as no way in at all.
    """
    token = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    db.add(
        AuthToken(
            token_hash=_hash_token(token),
            purpose=purpose,
            created_at=now,
            expires_at=now + timedelta(minutes=TOKEN_MINUTES),
        )
    )
    # Superseded tokens are useless the moment a new one is issued, so they go
    # rather than sitting around waiting to be found.
    for row in db.scalars(select(AuthToken).where(AuthToken.purpose == purpose)):
        if row.token_hash != _hash_token(token):
            db.delete(row)
    log_token(purpose, token)
    return token


def _consume_token(db: Session, token: str, *, purpose: str) -> None:
    """Spend a token, or say precisely why it cannot be spent."""
    row = db.get(AuthToken, _hash_token((token or "").strip()))
    if row is None or row.used:
        raise AuthError("That code is not valid. Ask for a new one and try again")
    if row.expires_at <= datetime.now(UTC):
        db.delete(row)
        raise AuthError("That code has expired. Ask for a new one and try again")
    if row.purpose != purpose:
        raise AuthError("That code was issued for something else")
    db.delete(row)


def log_token(purpose: str, token: str) -> None:
    """Print a token where nothing can filter it out.

    Every other line AniFlow writes can be silenced by the log level or by the
    component filter, and this one must not be: it is the only way into a
    locked AniFlow, and a user who has set the level to ERROR to chase
    something else has not asked to be locked out.
    """
    from backend.app.core.logging import announce

    what = "Set up your account" if purpose == "setup" else "Reset your password"
    announce(
        "Auth",
        "%s with this one-time code (valid for %s minutes): %s",
        what,
        TOKEN_MINUTES,
        token,
    )


__all__ = [
    "COOKIE_NAME",
    "AuthError",
    "AuthState",
    "MIN_PASSWORD_LENGTH",
    "SESSION_DAYS",
    "TOKEN_MINUTES",
    "GATE_SECONDS",
    "change_credentials",
    "create_account",
    "current_user",
    "forget_gate",
    "gate_allows",
    "gate_confirm",
    "hash_password",
    "issue_token",
    "login",
    "logout",
    "reset_password",
    "session_user",
    "state",
    "validate_password",
    "verify_password",
]
