"""A database of its own, for the metadata experiment (build 83).

Everything the Calendar Sources page collects lives in ``data/calendar.db``
rather than in AniFlow's own database, and that is a deliberate trade.

**Why separate.** This is an experiment: several metadata sources are being
compared to find out which two or three are worth keeping, and most of what is
collected will be thrown away with the losers. A table in the main schema
costs a migration to add and another to remove, and a migration for a
temporary thing has a way of outliving it. A file can simply be deleted.

**What that costs.** A second engine, no Alembic (the schema is created on
demand), and it is not covered by whatever backs up ``aniflow.db``. Losing it
means re-downloading, which is the only real consequence.

**What the main app is allowed to know about it.** One thing: the AniDB title
index, read-only, so an anime can be matched to its AniDB id. That is what
fixes Shoko Server offering a series the watchlist already has under another
name. If this file is missing the main app carries on with title matching, as
it did before - see services/library_flags.py.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Calendar")


class CalendarStoreBlocked(RuntimeError):
    """The file on disk cannot be brought to this schema, and was left alone.

    Raised only by ``engine()``. Most code never sees it, because it asks
    ``exists()`` first and that reports False - which is the answer it already
    knows how to handle.
    """


class CalendarBase(DeclarativeBase):
    """Its own metadata, so ``create_all`` cannot touch AniFlow's tables."""


#: Bumped whenever the tables in here change shape (build 84).
#:
#: Up to version 5 a mismatch deleted the file. That was defensible while
#: everything in here could be downloaded again - it was the reason this was a
#: separate file at all - and build 141 ends it, because Calendar v2 stores
#: things no provider can give back: canonical ids, the evidence a link was
#: proven on, merge decisions, and the memory that a provider once placed a
#: show in a season. Deleting those to fix a schema mismatch trades something
#: irreplaceable for something cheap.
#:
#: The wider reason, in the user's words: *"with all the api bans / slowness,
#: we should really avoid deleting it and redownload stuff"* - and a deleted
#: calendar breaks the link to AniFlow TV, which holds canonical ids of its
#: own.
#:
#: So from 6 onward this is a migration chain (see ``_MIGRATIONS``), run in
#: order, each in its own transaction, stamped as it goes. A file this version
#: cannot migrate is refused rather than deleted: ``exists()`` reports False,
#: every caller degrades as it already does for a missing file, and
#: ``blocked_reason()`` says what a person can do about it.
#:
#: Version 6 adds the v2 tables *beside* the v1 ones rather than replacing
#: them. The calendar keeps running on v1 until build 143 switches the routes
#: over, and the v1 tables are dropped once nothing reads them.
#:
#: Version 7 adds ``calendar_work.air_slot_episode`` - which episode the known
#: airing instant belongs to - and is the first bump this file has migrated
#: rather than deleted itself over.
#:
#: Version 8 adds ``calendar_note``, where the calendar remembers things about
#: itself - today, whether the first fill has been done.
#:
#: Version 9 drops the v1 comparison tables. The first version to *remove*
#: anything, and the one the whole migrate-never-delete design was built to
#: make safe: everything it takes away is re-derivable and nothing it takes
#: away is still read.
SCHEMA_VERSION = 10


def _to_v6(handle: sqlite3.Connection) -> None:
    """Calendar v2's tables arrive (build 141).

    Nothing to move. The v2 tables are new and empty, ``create_all`` makes
    them a moment after this runs, and the v1 tables are left exactly as they
    are because the calendar still reads them until build 143.

    The one thing worth saying is what is *not* happening: the AniDB title
    index - seventy-five thousand rows, fetched at most weekly, and the most
    expensive thing in the file - is untouched and carries straight over. An
    earlier version of this plan reset the file here; migrating instead saves
    that download and every other one already made.
    """
    logger.info("calendar database: adding the v2 tables alongside the existing ones")


def _add_column(handle: sqlite3.Connection, table: str, column: str, declaration: str) -> bool:
    """Add a column unless it is already there. True when it was added.

    Idempotent because a step in this chain cannot assume it is the only thing
    that has touched the file. A database from before version 5 is brought
    forward by ``_widened``, which adds every nullable column the *current*
    models have - so by the time the chain runs, a column a later step means
    to add may already exist. Without this, an old file was refused outright
    with "duplicate column name", which is the one outcome the whole
    migrate-never-delete design exists to avoid.
    """
    present = {
        row[1] for row in handle.execute(f'pragma table_info("{table}")')
    }
    if not present:
        # The table does not exist yet. A step runs *before* ``create_all``,
        # so a file old enough to predate the table has nothing to migrate -
        # create_all builds it in a moment, with this column already on it.
        # Getting this wrong refused every version-5 file with "no such table:
        # calendar_work", which is exactly the outcome this design exists to
        # avoid.
        return False
    if column in present:
        return False
    handle.execute(f'alter table "{table}" add column "{column}" {declaration}')
    return True


def _to_v7(handle: sqlite3.Connection) -> None:
    """An airing instant learns which episode it belongs to (build 146).

    One nullable column. Everything already stored keeps its instant and gains
    no anchor, which is the honest state: the episode number was being
    discarded by the adapter, so nothing downloaded before now knows it. The
    next update fills it in.
    """
    if _add_column(handle, "calendar_work", "air_slot_episode", "integer"):
        logger.info("calendar database: airing instants can now name their episode")


def _to_v8(handle: sqlite3.Connection) -> None:
    """The calendar gains a memory of its own (build 148).

    Nothing to move, and nothing to create here: ``calendar_note`` is a new
    table and ``create_all`` builds it a moment after this runs, which is the
    same shape ``_to_v6`` has. The step exists so the chain has a stamp to
    write and so an older file is migrated rather than refused.
    """
    logger.info("calendar database: adding the table it remembers itself in")


#: The v1 comparison tables, dropped at version 9.
#:
#: Order matters only in that nothing here references anything else here -
#: they were their own island, which is what made them safe to drop.
_V1_TABLES = ("calendar_entry", "source_refresh", "work_title", "work_id", "work")


def _to_v9(handle: sqlite3.Connection) -> None:
    """The build-83 experiment's tables come out (build 149).

    They stored one row per source per show per season so that the sources
    could be compared, which is the question builds 83 to 87 existed to ask.
    It was answered - merge all of them, which is what Calendar v2 does - and
    by build 143 nothing wrote to them any more. Anime search, adopt-by-AniDB
    -id and Shoko adoption went on *reading* them for five builds after that,
    which is the bug build 149 closes rather than the reason for this step.

    Everything dropped here is re-derivable from a provider. The identity
    spine, the proven links, the remembered memberships and the AniDB title
    index - the four things no download brings back - are in other tables and
    are not touched.

    ``drop table if exists``, because a file that has been through
    ``_widened`` may not have all of these, and a migration that cannot be run
    twice is a migration that will be run twice.
    """
    for table in _V1_TABLES:
        handle.execute(f'drop table if exists "{table}"')
    logger.info("calendar database: the v1 comparison tables have been dropped")


def _to_v10(handle: sqlite3.Connection) -> None:
    """The calendar becomes the metadata store (build 154).

    Two new tables - the offline id-mapping list and when it was fetched - and
    ``create_all`` builds both a moment after this runs, the same shape as
    ``_to_v8``. Nothing existing is touched: the identity spine gains ids
    through the ordinary attach path afterwards, never by rewriting rows here.
    """
    logger.info("calendar database: adding the id-mapping list")


#: Each step from one version to the next, run in order.
#:
#: A step receives a plain sqlite3 connection and runs before ``create_all``,
#: so it may not assume a table added in this version exists yet. It must be
#: safe to run on a file that has had every earlier step applied and nothing
#: since - which is the only file it will ever see.
_MIGRATIONS: dict[int, tuple[str, object]] = {
    6: ("calendar v2 tables", _to_v6),
    7: ("episode anchor for airing times", _to_v7),
    8: ("the calendar's own notes", _to_v8),
    9: ("dropping the v1 comparison tables", _to_v9),
    10: ("the offline id-mapping list", _to_v10),
}


_engine = None
_sessions: sessionmaker[Session] | None = None

#: Set when the file on disk cannot be brought to this schema.
#:
#: Not an exception thrown at whoever happened to ask first: the calendar is
#: optional by design and every reader already copes with there being nothing
#: there. So a blocked file reads as "nothing there", and this carries the
#: sentence a person needs in order to fix it.
_blocked: str | None = None

#: Held while the database is being opened for the first time.
#:
#: The worker's slow tick and a web request run in different threads and both
#: reach for this database. Without this, both could pass the "not opened yet"
#: check and both run create_all - which is where the log line ``table
#: anidb_anime already exists`` came from, once.
_opening = threading.Lock()


def database_path() -> Path:
    """``<data>/calendar.db``, beside the main database."""
    settings = get_settings()
    root = Path(str(settings.database_url).split("///")[-1]).parent
    return root / "calendar.db"


def exists() -> bool:
    """Whether anything usable has been collected yet.

    Asked before reading: the main app treats a missing file as "no answer"
    rather than an error, because this database is optional by design.

    A file that cannot be migrated reads as False for the same reason. It is
    present and unusable, and every caller already has a correct path for "no
    answer" - what none of them has is a path for "raises on open". See
    ``blocked_reason()`` for the half a person needs.
    """
    if _blocked is not None:
        return False
    try:
        return database_path().exists()
    except (OSError, ValueError, IndexError):
        return False


def blocked_reason() -> str | None:
    """Why the calendar database could not be opened, in one sentence.

    None when it opened, or when it has not been tried yet. Shown on the page
    beside the Reset button: a recovery step nobody is told about is a bug.
    """
    return _blocked


def engine():
    """The engine, created on first use along with the schema.

    No migrations: the schema is whatever the models say, and a file that
    disagrees with them is deleted and rebuilt from the sources rather than
    migrated. That is only acceptable *because* everything in here can be
    downloaded again.
    """
    global _engine, _sessions
    # Read outside the lock, which is the whole point of taking it rarely:
    # every call after the first is one attribute read.
    if _engine is not None:
        return _engine
    with _opening:
        # Asked again inside: another thread may have finished while this one
        # waited, and building a second engine would leave whichever lost
        # handing out sessions bound to an engine nobody else uses.
        if _engine is not None:
            return _engine
        target = database_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        refusal = _bring_up_to_date(target)
        if refusal is not None:
            # Left exactly as it was found. A person decides what happens to
            # it, from the Calendar page - see services/calendar_lifecycle.py.
            global _blocked
            _blocked = refusal
            logger.error("calendar database not opened: %s", refusal)
            raise CalendarStoreBlocked(refusal)
        opening = create_engine(f"sqlite:///{target}", future=True)
        # Both schemas register here while they coexist: v2's tables are built
        # from build 141 and read from 143, and v1 keeps the calendar running
        # in between.
        from backend.app.models import calendar as calendar_v2  # noqa: F401
        from backend.app.models import calendar_sources  # noqa: F401

        CalendarBase.metadata.create_all(opening)
        _write_ahead(opening)
        _stamp(opening)
        # Published only once it is usable. A half-built engine visible to
        # another thread is the failure this function had, one layer down.
        _sessions = sessionmaker(bind=opening, expire_on_commit=False, future=True)
        _engine = opening
        logger.debug("calendar database ready at %s", target)
        return _engine


def _bring_up_to_date(target: Path) -> str | None:
    """Migrate the file to this schema. Never delete it.

    Returns None when the file is usable, or one sentence saying why it is
    not - in which case the file has not been touched at all.

    Read with plain sqlite3 rather than SQLAlchemy: this has to happen before
    an engine exists, and the whole question is whether the file is one this
    version can use.
    """
    if not target.exists():
        return None  # nothing to migrate; create_all builds it at this version
    found = _stamped_version(target)
    if found == SCHEMA_VERSION:
        return None
    if found > SCHEMA_VERSION:
        # Written by a newer AniFlow. Migrating backwards is guesswork and
        # deleting is the thing this function no longer does.
        return (
            f"the calendar database is version {found} and this AniFlow uses "
            f"{SCHEMA_VERSION}; it was written by a newer version"
        )

    # Everything below version 6 predates the migration chain. Those files are
    # brought to 5 the way they always were - by adding columns that are only
    # missing and nullable - and then migrated properly from there.
    if found < 5:
        if not _widened(target):
            return (
                f"the calendar database is version {found or 'unstamped'} and cannot be "
                f"migrated to {SCHEMA_VERSION}"
            )
        found = 5

    for version in range(found + 1, SCHEMA_VERSION + 1):
        step = _MIGRATIONS.get(version)
        if step is None:
            return (
                f"the calendar database is version {found} and there is no way to "
                f"migrate it to {version}"
            )
        label, run = step
        handle = None
        try:
            handle = sqlite3.connect(target)
            # One transaction per step: a step that fails leaves the file at
            # the version before it, which is a file this code has run on.
            run(handle)  # type: ignore[operator]
            handle.execute(
                "create table if not exists schema_state (key text primary key, value text)"
            )
            handle.execute(
                "insert into schema_state (key, value) values ('version', ?)"
                " on conflict(key) do update set value = excluded.value",
                (str(version),),
            )
            handle.commit()
        except sqlite3.Error as exc:
            if handle is not None:
                handle.rollback()
            return f"the calendar database could not be migrated to {version} ({label}): {exc}"
        finally:
            if handle is not None:
                handle.close()
        logger.info("calendar database migrated to version %s (%s)", version, label)
    return None


def delete_database() -> None:
    """Delete the file, with its write-ahead companions.

    Never called by a version check. This is what the Reset button does, after
    a person has confirmed it and been told what it destroys - the canonical
    ids AniFlow TV holds, the evidence behind every proven link, and the
    record of seasons providers no longer report.
    """
    reset()  # let go of the engine first; an open handle makes unlink fail on Windows
    target = database_path()
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    global _blocked
    _blocked = None
    logger.warning("the calendar database was deleted on request")


def _stamped_version(target: Path) -> int:
    """The version written into the file, or 0 for a file with no stamp."""
    handle = None
    try:
        # Closed explicitly: sqlite3's context manager commits a transaction,
        # it does not close the connection, and an open handle makes the
        # unlink in the caller fail outright on Windows.
        handle = sqlite3.connect(target)
        row = handle.execute("select value from schema_state where key = 'version'").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, TypeError, ValueError):
        # No stamp at all means build 83, which had no stamp.
        return 0
    finally:
        if handle is not None:
            handle.close()


def _widened(target: Path) -> bool:
    """Add the columns this schema has and the file does not.

    True when the file now matches - either it already did, or every
    difference was a nullable column that has just been added. False when the
    difference is anything else: a column that has gone, one whose type or
    nullability changed, a table shaped differently. Those are real schema
    changes and the file is deleted, which is what this guard has always done.

    Whole tables that are missing are not this function's business -
    ``create_all`` makes those, and it makes their indexes with them.
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    from backend.app.models import calendar_sources  # noqa: F401 - registers the tables

    handle = None
    try:
        handle = sqlite3.connect(target)
        present = {
            row[0]
            for row in handle.execute("select name from sqlite_master where type = 'table'")
        }
        additions: list[str] = []
        for table in CalendarBase.metadata.sorted_tables:
            if table.name not in present:
                continue
            columns = {
                row[1]: row for row in handle.execute(f'pragma table_info("{table.name}")')
            }
            # A column the file has and the models do not: this file was
            # written by a schema that is not simply narrower than this one.
            if set(columns) - {column.name for column in table.columns}:
                return False
            for column in table.columns:
                if column.name in columns:
                    continue
                if not column.nullable or column.primary_key:
                    # Nothing to put in the existing rows.
                    return False
                rendered = column.type.compile(dialect=sqlite_dialect.dialect())
                additions.append(
                    f'alter table "{table.name}" add column "{column.name}" {rendered}'
                )
        # Every check first, then every change: a file that cannot be widened
        # must be left exactly as it was for the delete below to be the only
        # thing that touched it.
        for statement in additions:
            handle.execute(statement)
        handle.commit()
    except sqlite3.Error:
        return False
    finally:
        if handle is not None:
            handle.close()
    if additions:
        logger.info(
            "the calendar database was widened by %s column(s) rather than started again",
            len(additions),
        )
    return True


def _write_ahead(target_engine) -> None:
    """Let this database be read while it is being written (build 85).

    In SQLite's default journal mode a writer blocks every reader, and the one
    long write in here is rebuilding the AniDB title index - seventy-five
    thousand rows, on a file that lives on a network share. Opening the
    Calendar sources page during that meant waiting for it to finish before
    the Refresh button could even be enabled.

    Write-ahead logging costs two extra files next to the database, which for
    a file that is deleted and rebuilt on a schema change is no loss at all.
    The busy timeout is the other half: a writer that finds the database busy
    should wait a few seconds rather than raise.
    """
    from sqlalchemy import event

    @event.listens_for(target_engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    # The listener only fires for connections opened from now on, so the first
    # one is nudged by hand.
    with target_engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")


def _stamp(target_engine) -> None:
    """Write this version into the file, so the next start can check it."""
    with target_engine.begin() as connection:
        connection.execute(
            text("create table if not exists schema_state (key text primary key, value text)")
        )
        connection.execute(
            text(
                "insert into schema_state (key, value) values ('version', :version)"
                " on conflict(key) do update set value = excluded.value"
            ),
            {"version": str(SCHEMA_VERSION)},
        )


def sessions() -> sessionmaker[Session]:
    """The session factory. Creates the database if it is not there yet."""
    engine()
    assert _sessions is not None
    return _sessions


def reset() -> None:
    """Forget the engine, for tests that point the data folder somewhere else.

    Clears a recorded refusal with it: the refusal is about one file, and the
    point of calling this is that the next call will look at a different one.
    """
    global _engine, _sessions, _blocked
    with _opening:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _sessions = None
        _blocked = None


__all__ = [
    "CalendarBase",
    "SCHEMA_VERSION",
    "database_path",
    "engine",
    "exists",
    "reset",
    "sessions",
]
