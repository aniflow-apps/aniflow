"""Which storage pipeline AniFlow runs, and why it cannot change.

Two modes, chosen once per deployment in ``docker-compose.yml``:

``standalone``
    AniFlow owns the library. A finished download goes straight into
    ``<media root>/<anime folder>/`` under a name AniFlow builds, and AniFlow
    is the only thing that writes there.

``shokoserver``
    Shoko Server owns the library. A finished download goes into the *import*
    root; Shoko picks it up, identifies it, renames it and moves it into the
    media root under a folder and filename of its own choosing. AniFlow never
    writes into the media root and never invents a name for anything there -
    it hands the file over and then finds out where it went.

The mode is chosen **once**, and from build 137 it is chosen in the install
wizard - the one screen that explains what the choice means - rather than in
``docker-compose.yml``. ``ANIFLOW_MODE`` is the default for an install that
never runs a wizard.

It is not an editable setting afterwards. Switching it on a library that
already has files would leave one anime split across two folders: the episodes
AniFlow placed stay under AniFlow's name, while everything new goes wherever
Shoko puts it, and neither side would know about the other.

So the recorded mode wins. It describes what is already on disk, which no
environment variable can contradict from the outside: if the two disagree, the
variable is stale, and that is worth a line in the log rather than a container
that refuses to start. Until something records a mode, nothing is written down
and the configured value is used - that is what lets the wizard ask before
anything has been decided.
"""
import enum

from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import for_component
from backend.app.models.setting import Setting

logger = for_component("Mode")


class AppMode(str, enum.Enum):
    STANDALONE = "standalone"
    SHOKO_SERVER = "shokoserver"


#: Where the mode of record lives. In the database rather than the config file
#: because it describes what is already on disk, not what the user wants.
MODE_KEY = "storage_mode"

#: The recorded mode, once something has read or written it (build 137).
#:
#: Kept in the module because the callers cannot ask the database: the
#: importer, the serializers, the worker and the health icons all want to know
#: whether this is Shoko mode, and none of them has a session to hand. It is
#: filled by :func:`verify_mode` at startup and by :func:`record_mode` when the
#: wizard writes, which covers every way the value can come to exist in a
#: process that runs the API and the worker together.
_recorded: AppMode | None = None


def current_mode(settings: Settings | None = None) -> AppMode:
    """The mode AniFlow is actually running: recorded first, configured second.

    Passing ``settings`` asks a different question - *what is configured* -
    and is answered literally. That is the startup comparison and the tests;
    everything else wants the effective mode and passes nothing.
    """
    if settings is not None:
        return AppMode(settings.mode)
    if _recorded is not None:
        return _recorded
    return AppMode(get_settings().mode)


def record_mode(session: Session, mode: AppMode, *, replace: bool = False) -> AppMode:
    """Write down the mode this library is built under (build 137).

    An already-recorded mode is *not* replaced by default: it describes files
    that already exist, and changing it would split the library in two.

    ``replace`` is for the install wizard, and only while setup is unfinished.
    Nothing has been placed on disk yet at that point, so the answer is still
    a question - and without it, stepping past the library screen once would
    lock the other mode out for ever on an install with nothing in it.
    """
    existing = recorded_mode(session)
    if existing is not None and not replace:
        _remember(existing)
        return existing
    session.merge(Setting(key=MODE_KEY, value=mode.value))
    session.commit()
    _remember(mode)
    logger.info("storage mode recorded as %s", mode.value)
    return mode


def _remember(mode: AppMode | None) -> None:
    global _recorded
    _recorded = mode


def reset_mode_cache() -> None:
    """Forget the recorded mode. For tests, which build a database per test."""
    _remember(None)


def is_shoko_mode(settings: Settings | None = None) -> bool:
    return current_mode(settings) is AppMode.SHOKO_SERVER


def recorded_mode(session: Session) -> AppMode | None:
    """The mode this database was created under, or None on a fresh install."""
    row = session.get(Setting, MODE_KEY)
    if row is None or not isinstance(row.value, str):
        return None
    try:
        return AppMode(row.value)
    except ValueError:
        return None


def verify_mode(session: Session, settings: Settings | None = None) -> AppMode:
    """Adopt the recorded mode for this process, and report a stale variable.

    Called once at startup. It **reads**; the deciding is the wizard's job
    (build 137), and a fresh database is left unrecorded so the wizard still
    has something to ask about.

    A recorded mode that disagrees with ``ANIFLOW_MODE`` is not an error to
    stop on. The recorded one describes the files that are already on disk, so
    it wins, and the variable is simply out of date - said plainly in the log,
    because somebody edited it expecting something to happen.
    """
    configured = AppMode((settings or get_settings()).mode)
    existing = recorded_mode(session)
    if existing is None:
        # Nothing has chosen yet. Not written down here: doing so would settle
        # the question before the wizard could put it to anyone.
        _remember(None)
        return configured
    _remember(existing)
    if existing is not configured:
        logger.warning(
            "ANIFLOW_MODE is %s but this library was set up in %s mode, so %s is"
            " being used - the files already in it were placed that way."
            " Change ANIFLOW_MODE to %s to stop this warning; switching the"
            " library itself would split every anime across two folders.",
            configured.value,
            existing.value,
            existing.value,
            existing.value,
        )
    return existing


__all__ = [
    "AppMode",
    "MODE_KEY",
    "current_mode",
    "is_shoko_mode",
    "record_mode",
    "recorded_mode",
    "reset_mode_cache",
    "verify_mode",
]
