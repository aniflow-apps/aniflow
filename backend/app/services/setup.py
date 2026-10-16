"""What the install wizard needs to know, and the three things only it does.

A fresh AniFlow has to be told four things before it can do anything: who you
are, who organises your library, whether it should download at all, and how to
reach the programs it works with. Until build 137 those lived in four
different places - an account page, an environment variable, a folder you had
to know to put a file in, and the settings page - and nothing said in what
order or that you were finished.

This module is the wizard's side of it. Deliberately thin: the account is
created by ``services/auth`` exactly as before, the connections are saved
through the normal settings route, feeds through the normal RSS route. What is
genuinely new is only what nothing else could do:

* recording the storage mode, which used to come from ``ANIFLOW_MODE`` (see
  core/mode.py - the wizard decides it now);
* accepting an indexer file, because Advanced is unreachable without one and
  "put a YAML file in a folder you cannot see from here" is not a step a
  person can take from a browser;
* remembering that setup is over, so the wizard stops appearing.

**An install that predates the wizard is already set up.** It has an account
and a recorded mode and possibly a library, and showing it a first-run wizard
would be both wrong and alarming, so :func:`adopt_existing_install` stamps it
as finished at startup.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.core.mode import AppMode, current_mode, recorded_mode
from backend.app.models.setting import Setting

logger = for_component("Setup")

#: Set when the wizard finishes, and the only thing that makes it stop asking.
DONE_KEY = "setup_completed_at"

#: What a file has to have before AniFlow will accept it as an indexer.
#:
#: Checked by shape rather than by a version marker: these are the fields
#: services/indexer_definitions.py actually needs, so a file that has them
#: works and a file that does not could never have worked. A marker is the
#: next job after this one and can be added on top without changing this.
REQUIRED_KEYS = ("name", "url", "search_url")


class SetupError(ValueError):
    """Something the user can fix, phrased for them rather than for a log."""


@dataclass(frozen=True)
class SetupState:
    """Where a fresh install has got to. Every field answers one screen."""

    completed: bool
    needs_account: bool
    #: The mode in force, and whether anybody has chosen it yet.
    mode: str
    mode_recorded: bool
    #: Advanced is unreachable without one of these.
    has_indexer: bool
    indexer_names: list[str]
    #: Whether the Shoko step has been answered (only asked in Shoko mode).
    shoko_configured: bool
    #: How many feeds exist, so the feeds step can say what it found.
    feeds: int
    #: Whether the first-run notice (services/terms.py) has been accepted.
    terms_accepted: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "completed": self.completed,
            "needs_account": self.needs_account,
            "mode": self.mode,
            "mode_recorded": self.mode_recorded,
            "has_indexer": self.has_indexer,
            "indexer_names": list(self.indexer_names),
            "shoko_configured": self.shoko_configured,
            "feeds": self.feeds,
            "terms_accepted": self.terms_accepted,
        }


def is_complete(session: Session) -> bool:
    row = session.get(Setting, DONE_KEY)
    return bool(row and row.value)


def state(session: Session) -> SetupState:
    """Everything the wizard needs, in one answer.

    One call rather than six, because the wizard decides which steps *exist*
    from this - and asking six endpoints to lay out one screen is how a
    half-answered wizard ends up showing a step it should have skipped.
    """
    from backend.app.models.auth import User
    from backend.app.models.indexer import Indexer
    from backend.app.models.rss import FeedSource
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services import terms
    from backend.app.services.config_sync import sync_from_files

    # A file dropped into the folder by hand counts, and nothing else would
    # notice it until somebody opened the indexers page. A file that cannot be applied must not take the wizard
    # with it (build 269): this answer is what lets the wizard move on after the account is made, and a failure
    # here left a person on step 1 with an account that already existed - and, after a reload, skipped the wizard.
    try:
        sync_from_files(session)
    except Exception as exc:  # noqa: BLE001 - the folders are a convenience here, not a precondition
        session.rollback()
        logger.warning("the configuration folders could not be applied while reading the setup state: %s", exc)

    settings = load_app_settings(session)
    indexers = list(session.scalars(select(Indexer)))
    return SetupState(
        completed=is_complete(session),
        needs_account=session.scalar(select(User.id).limit(1)) is None,
        mode=current_mode().value,
        mode_recorded=recorded_mode(session) is not None,
        has_indexer=bool(indexers),
        indexer_names=[row.name for row in indexers],
        shoko_configured=bool(settings.shoko_url and settings.shoko_api_key),
        feeds=len(list(session.scalars(select(FeedSource)))),
        terms_accepted=terms.is_accepted(session),
    )


def choose_mode(session: Session, value: str) -> AppMode:
    """Record the storage mode the wizard was given, replacing any earlier one.

    Refuses an unknown value rather than falling back to a default: this is
    the one decision that cannot be revisited afterwards, so a typo must not
    quietly become standalone.

    It *replaces* because the wizard is still open: this is reachable only
    while setup is unfinished (see the route's guard), nothing has been placed
    on disk yet, and a person who steps forward and back must not find the
    other mode locked out. Once setup completes the route closes, and with it
    the only way to change a mode that now describes real files.
    """
    from backend.app.core.mode import record_mode

    try:
        wanted = AppMode(value)
    except ValueError as exc:
        raise SetupError(f"{value!r} is not a storage mode") from exc
    return record_mode(session, wanted, replace=True)


def _slug(name: str) -> str:
    """A filename from whatever the browser sent, and nothing else.

    The name arrives from a file picker, so it is the user's and not to be
    trusted as a path: everything but letters, digits and dashes goes, which
    leaves no way to write outside the folder.
    """
    stem = re.sub(r"\.(ya?ml)$", "", (name or "").strip(), flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug[:60] or "indexer"


def add_indexer(session: Session, *, filename: str, content: str) -> str:
    """Save an indexer file, or say exactly what is wrong with it.

    The content comes as text rather than as an upload because reading the
    file in the browser needs nothing installed, and a multipart endpoint
    would need a new dependency for the one place AniFlow accepts a file.

    Validated by shape before it is written: a file that lands in the folder
    becomes an indexer AniFlow searches with, and a broken one would fail far
    from here, in the middle of a scan, as a warning nobody is reading.
    """
    from backend.app.services.config_store import INDEXERS
    from backend.app.services.config_sync import sync_from_files

    try:
        data = yaml.safe_load(content or "")
    except yaml.YAMLError as exc:
        raise SetupError(f"That file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SetupError("An indexer file has to be a mapping of settings")

    missing = [key for key in REQUIRED_KEYS if not str(data.get(key) or "").strip()]
    if missing:
        raise SetupError(
            "That file is missing " + ", ".join(missing) + ", so AniFlow could not"
            " search with it"
        )
    if "{query}" not in str(data.get("search_url")):
        # The failure the file exists to prevent: without the placeholder every
        # search returns the site's front page.
        raise SetupError("search_url needs a {query} placeholder for the search term")

    folder = INDEXERS.path
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{_slug(filename)}.yaml"
    target.write_text(content, encoding="utf-8")
    logger.info("indexer file accepted as %s", target.name)

    # Straight into the database, so the wizard's next answer already knows.
    sync_from_files(session)
    return str(data.get("name") or target.stem)


def complete(session: Session) -> str:
    """Mark setup finished. Idempotent: the first time is the one recorded.

    Stored as an ISO string because ``Setting.value`` is a JSON column, and a
    timestamp rather than a flag so the log of what happened when survives.
    """
    row = session.get(Setting, DONE_KEY)
    if row is not None and row.value:
        return str(row.value)
    when = datetime.now(UTC).isoformat()
    session.merge(Setting(key=DONE_KEY, value=when))
    session.commit()
    logger.info("setup finished")
    return when


def adopt_existing_install(session: Session) -> bool:
    """An AniFlow that already has an account has already been set up (137).

    Runs at startup. Without it, every existing deployment would be shown a
    first-run wizard by this build - asked to create an account it has, and to
    choose a storage mode its library was already built under.

    An account is the signal because it is the one thing the old flow always
    produced: you could not reach any page without creating one.
    """
    from backend.app.models.auth import User

    if is_complete(session):
        return False
    if session.scalar(select(User.id).limit(1)) is None:
        return False
    complete(session)
    logger.info("existing install adopted as already set up")
    return True


__all__ = [
    "DONE_KEY",
    "REQUIRED_KEYS",
    "SetupError",
    "SetupState",
    "add_indexer",
    "adopt_existing_install",
    "choose_mode",
    "complete",
    "is_complete",
    "state",
]
