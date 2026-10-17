"""Backup and restore: one zip of what is yours, and a choice of what to take back (build 266).

Modelled on Pi-hole's Teleporter: **Export** makes one archive, **Import** reads what is in it and lets the person
tick the parts they want. The main use is moving an installation (the NAS's settings and watchlist onto the Pi to
test with, or onto new hardware) without touching the original.

The archive is a plain zip with a ``manifest.json`` (format, build, the migration the database was at, a SHA-256
for every file). It holds **secrets** - the torrent client's password, the Shoko key, the proxy password, the TV
pairing tokens - because a backup that cannot restore the keys is of little use; the page says so, and nothing here
redacts. (``diagnostics.py`` is the other file AniFlow makes; that one is meant for strangers and is redacted.)

Groups, in the order the page shows them:

``settings``      ``config/aniflow.yaml``
``definitions``   ``config/indexers``, ``rss_source``, ``subgroup``
``library``       ``aniflow.db`` - the watchlist, libraries, downloads, accounts
``calendar``      ``calendar.db`` - canonical ids and the evidence behind them
``covers``        ``data/covers`` - the covers the person uploaded; nothing brings these back
``anidb``         the AniDB My List export they uploaded and its parsed copy
``calendar_art``  ``data/calendar-art`` - **off unless asked for**: it is hundreds of megabytes and fetched again

Never in it: the AniDB title dump (published, fetched weekly), logs, the Activity log.

How it is kept safe:

* **Databases are snapshotted with SQLite's own backup API**, never copied as files. ``calendar.db`` runs in WAL
  mode, and a plain copy of it while the app is writing can be torn.
* **An import is staged.** The upload is stored and checked (every hash, every name, the migration version) before
  anything is touched, and the person chooses the parts afterwards.
* **Before an import changes anything, what is about to be replaced is saved** to ``data/backups`` first.
* **A database is swapped, not overwritten**: the old file is renamed aside, the new one moved in, opened and (for
  ``aniflow.db``) migrated; if any of that fails the old one is put back.
* **Files are only ever added or replaced, never deleted**: a cover, an indexer or a poster that exists here and is
  not in the archive stays.
* **A file name from the archive is never trusted**: it must match one of the shapes below, and is written by this
  module to a path this module builds. Nothing is ``extractall``-ed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Backup")

FORMAT = 1
MANIFEST = "manifest.json"

#: In the order the page shows them.
GROUPS: tuple[str, ...] = ("settings", "definitions", "library", "calendar", "covers", "anidb", "calendar_art")
#: What an export holds when nothing else is asked for: everything but the large, re-fetchable one.
DEFAULT_GROUPS: tuple[str, ...] = tuple(g for g in GROUPS if g != "calendar_art")
#: Groups whose restore replaces a database, so the app should be restarted afterwards.
DATABASE_GROUPS = frozenset({"library", "calendar"})

DEFINITION_FOLDERS = ("indexers", "rss_source", "subgroup")

#: Settings that describe *this machine* rather than the person's preferences. "Keep this machine's paths and URLs"
#: leaves these as they are here when the settings are restored (and, for the database, the libraries' folders).
KEEP_LOCAL_SETTINGS = (
    "torrent_client", "qb_url", "qb_username", "qb_password", "qb_category",
    "shoko_url", "shoko_api_key",
    "proxy_kind", "proxy_host", "proxy_port", "proxy_username", "proxy_password", "proxy_check_url",
    "timezone",
)

#: The newest backups kept in ``data/backups`` (the ones made before an import).
KEEP_SAFETY_BACKUPS = 5
#: A staged upload nobody finished with is removed after this long.
STAGING_MAX_AGE = timedelta(days=1)
#: The largest upload accepted. Far above a backup with art; below "something else was sent".
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024

_SEGMENT = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]+$")
_ID = re.compile(r"^[0-9a-f]{32}$")
_COPY_CHUNK = 1024 * 1024


class BackupError(ValueError):
    """The archive or the request was refused, with a reason meant for the person."""


# --------------------------------------------------------------------------------------------- where things live


def data_root() -> Path:
    """The folder ``aniflow.db`` is in: the data folder, as the rest of the app reads it."""
    return _main_db_path().parent


def _main_db_path() -> Path:
    return Path(str(get_settings().database_url).split("///")[-1])


def config_root() -> Path:
    configured = get_settings().config_dir
    return Path(configured) if configured else Path("/config")


def staging_dir() -> Path:
    return data_root() / "backup-staging"


def backups_dir() -> Path:
    return data_root() / "backups"


def _calendar_db_path() -> Path:
    return data_root() / "calendar.db"


# ----------------------------------------------------------------------------------------- what a group is made of


def _files_in(folder: Path, pattern: str = "*") -> list[Path]:
    try:
        return sorted(p for p in folder.glob(pattern) if p.is_file() and not p.name.startswith("."))
    except OSError:
        return []


def _group_of(arcname: str) -> str | None:
    """Which group an archive path belongs to, or None when it is not a path this module ever writes."""
    parts = arcname.split("/")
    if not parts or any(p in ("", ".", "..") or not _SEGMENT.match(p) for p in parts):
        return None
    if parts == ["config", "aniflow.yaml"]:
        return "settings"
    if len(parts) == 3 and parts[0] == "config" and parts[1] in DEFINITION_FOLDERS and re.search(r"\.ya?ml$", parts[2]):
        return "definitions"
    if parts == ["data", "aniflow.db"]:
        return "library"
    if parts == ["data", "calendar.db"]:
        return "calendar"
    if len(parts) == 3 and parts[:2] == ["data", "covers"]:
        return "covers"
    if parts in (["data", "anidb-mylist.json"], ["data", "anidb-export.tgz"]):
        return "anidb"
    if len(parts) >= 3 and parts[:2] == ["data", "calendar-art"]:
        return "calendar_art"
    return None


def _local_files(group: str) -> list[tuple[str, Path]]:
    """``(archive name, file on disk)`` for a group's plain files. The databases are snapshotted separately."""
    data = data_root()
    found: list[tuple[str, Path]] = []
    if group == "settings":
        path = config_root() / "aniflow.yaml"
        if path.is_file():
            found.append(("config/aniflow.yaml", path))
    elif group == "definitions":
        for kind in DEFINITION_FOLDERS:
            for path in _files_in(config_root() / kind, "*.y*ml"):
                if re.search(r"\.ya?ml$", path.name):
                    found.append((f"config/{kind}/{path.name}", path))
    elif group == "covers":
        for path in _files_in(data / "covers"):
            found.append((f"data/covers/{path.name}", path))
    elif group == "anidb":
        for name in ("anidb-mylist.json", "anidb-export.tgz"):
            if (data / name).is_file():
                found.append((f"data/{name}", data / name))
    elif group == "calendar_art":
        root = data / "calendar-art"
        try:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.name.startswith("."):
                    found.append(("data/calendar-art/" + path.relative_to(root).as_posix(), path))
        except OSError:
            pass
    return found


def describe() -> list[dict[str, Any]]:
    """What an export could hold right now: per group, how many files and how many bytes (for the page)."""
    answer: list[dict[str, Any]] = []
    for group in GROUPS:
        if group == "library":
            paths = [_main_db_path()] if _main_db_path().is_file() else []
        elif group == "calendar":
            paths = [_calendar_db_path()] if _calendar_db_path().is_file() else []
        else:
            paths = [p for _, p in _local_files(group)]
        size = 0
        for path in paths:
            try:
                size += path.stat().st_size
            except OSError:
                pass
        answer.append({"name": group, "files": len(paths), "bytes": size, "default": group in DEFAULT_GROUPS})
    return answer


# ------------------------------------------------------------------------------------------------- the databases


def _snapshot_database(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite file, by SQLite's own backup API."""
    reader = sqlite3.connect(str(source))
    writer = sqlite3.connect(str(target))
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()


def _read_scalar(path: Path, query: str) -> str | None:
    handle = None
    try:
        handle = sqlite3.connect(str(path))
        row = handle.execute(query).fetchone()
        return str(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        return None
    finally:
        if handle is not None:
            handle.close()


def _integrity_ok(path: Path) -> bool:
    return _read_scalar(path, "pragma integrity_check") == "ok"


def _alembic_config():
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    return config


def _library_state(revision: str | None) -> tuple[bool, str | None]:
    """``(usable, note)`` for a database written at this migration."""
    if not revision:
        return False, "this database has no migration version, so it cannot be told apart from a foreign file"
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    if revision == script.get_current_head():
        return True, None
    try:
        known = script.get_revision(revision)
    except Exception:  # noqa: BLE001 - an unknown revision raises, in more than one way
        known = None
    if known is None:
        return False, "it was written by a newer AniFlow than this one - update AniFlow first"
    return True, "it is from an older AniFlow and will be brought up to date"


def _calendar_state(version: int) -> tuple[bool, str | None]:
    from backend.app.db import calendar_store

    if version > calendar_store.SCHEMA_VERSION:
        return False, "it was written by a newer AniFlow than this one - update AniFlow first"
    if version < calendar_store.SCHEMA_VERSION:
        return True, "it is from an older AniFlow and will be brought up to date"
    return True, None


def _calendar_version(path: Path) -> int:
    from backend.app.db import calendar_store

    return calendar_store._stamped_version(path)  # noqa: SLF001 - the one reader of the stamp


# ------------------------------------------------------------------------------------------------------ export


def _copy_into_zip(archive: zipfile.ZipFile, arcname: str, source: Path, *, compress: bool) -> dict[str, Any]:
    info = zipfile.ZipInfo(arcname, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
        while chunk := reader.read(_COPY_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def build_backup(groups: list[str] | tuple[str, ...], target: Path | None = None) -> Path:
    """Write the archive for these groups and return its path. The caller removes it when it has been sent."""
    chosen = [g for g in GROUPS if g in set(groups)]
    if not chosen:
        raise BackupError("Nothing was chosen to back up")
    from backend.app.core.version import app_version, build_number

    if target is None:
        data_root().mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=str(data_root()), prefix=".backup-", suffix=".zip")
        os.close(handle)
        target = Path(name)
    work = Path(tempfile.mkdtemp(dir=str(target.parent), prefix=".backup-work-"))
    files: dict[str, dict[str, Any]] = {}
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "app": "AniFlow",
        "version": app_version(),
        "build": build_number(),
        "created_at": datetime.now(UTC).isoformat(),
        "groups": {},
        "files": files,
        "library_revision": None,
        "calendar_version": None,
    }
    try:
        with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
            for group in chosen:
                before = len(files)
                if group == "library":
                    source = _main_db_path()
                    if source.is_file():
                        snap = work / "aniflow.db"
                        _snapshot_database(source, snap)
                        manifest["library_revision"] = _read_scalar(snap, "select version_num from alembic_version")
                        files["data/aniflow.db"] = {
                            **_copy_into_zip(archive, "data/aniflow.db", snap, compress=True), "group": group,
                        }
                elif group == "calendar":
                    source = _calendar_db_path()
                    if source.is_file():
                        snap = work / "calendar.db"
                        _snapshot_database(source, snap)
                        manifest["calendar_version"] = _calendar_version(snap)
                        files["data/calendar.db"] = {
                            **_copy_into_zip(archive, "data/calendar.db", snap, compress=True), "group": group,
                        }
                else:
                    # Pictures and archives are already compressed; deflating them only costs time.
                    for arcname, path in _local_files(group):
                        compress = group not in ("covers", "calendar_art") and not arcname.endswith(".tgz")
                        files[arcname] = {**_copy_into_zip(archive, arcname, path, compress=compress), "group": group}
                manifest["groups"][group] = {
                    "files": len(files) - before,
                    "bytes": sum(f["size"] for f in list(files.values())[before:]),
                }
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    logger.info("backup written: %s (%s)", ", ".join(chosen), _human(target.stat().st_size))
    return target


def backup_name() -> str:
    return f"aniflow-backup-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.zip"


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


# ---------------------------------------------------------------------------------------------- staging an import


def _staged_path(staged_id: str) -> Path:
    if not _ID.match(staged_id or ""):
        raise BackupError("That upload is not known - choose the file again")
    path = staging_dir() / f"{staged_id}.zip"
    if not path.is_file():
        raise BackupError("That upload is no longer there - choose the file again")
    return path


def new_staging_file() -> tuple[str, Path]:
    """Where an upload is to be written, and the id the page uses afterwards. Old, unfinished ones are cleared."""
    staging_dir().mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - STAGING_MAX_AGE
    for old in staging_dir().glob("*.zip"):
        try:
            if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
                old.unlink()
        except OSError:
            pass
    staged_id = uuid.uuid4().hex
    return staged_id, staging_dir() / f"{staged_id}.zip"


def discard(staged_id: str) -> None:
    try:
        _staged_path(staged_id).unlink(missing_ok=True)
    except BackupError:
        pass


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise BackupError("This is not an AniFlow backup (it has no readable manifest)") from exc
    if not isinstance(manifest, dict) or manifest.get("app") != "AniFlow" or not isinstance(manifest.get("files"), dict):
        raise BackupError("This is not an AniFlow backup (the manifest is not one of ours)")
    if manifest.get("format") != FORMAT:
        raise BackupError(
            f"This backup is format {manifest.get('format')!r}; this AniFlow reads format {FORMAT}. "
            "Update AniFlow if the backup is newer."
        )
    return manifest


def inspect(staged_id: str) -> dict[str, Any]:
    """Check a staged upload and say what is in it. Nothing is changed; a bad file is removed."""
    path = _staged_path(staged_id)
    try:
        return _inspect(staged_id, path)
    except (BackupError, zipfile.BadZipFile):
        path.unlink(missing_ok=True)
        raise


def _inspect(staged_id: str, path: Path) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise BackupError("That file is not a zip archive") from exc
    with archive:
        manifest = _read_manifest(archive)
        per_group: dict[str, dict[str, int]] = {}
        for arcname, entry in manifest["files"].items():
            group = _group_of(arcname)
            if group is None:
                raise BackupError(f"The backup lists a file AniFlow would never write ({arcname!r}), so it was refused")
            try:
                archive.getinfo(arcname)
            except KeyError as exc:
                raise BackupError(f"The backup is incomplete: {arcname} is listed but missing") from exc
            digest = hashlib.sha256()
            with archive.open(arcname) as reader:
                while chunk := reader.read(_COPY_CHUNK):
                    digest.update(chunk)
            if not isinstance(entry, dict) or digest.hexdigest() != entry.get("sha256"):
                raise BackupError(f"The backup is damaged: {arcname} does not match its checksum")
            slot = per_group.setdefault(group, {"files": 0, "bytes": 0})
            slot["files"] += 1
            slot["bytes"] += int(entry.get("size") or 0)

    library_ok, library_note = (True, None)
    if "library" in per_group:
        library_ok, library_note = _library_state(manifest.get("library_revision"))
    calendar_ok, calendar_note = (True, None)
    if "calendar" in per_group:
        calendar_ok, calendar_note = _calendar_state(int(manifest.get("calendar_version") or 0))
    blocked = {"library": (library_ok, library_note), "calendar": (calendar_ok, calendar_note)}

    groups = []
    for name in GROUPS:
        if name not in per_group:
            continue
        usable, note = blocked.get(name, (True, None))
        groups.append({
            "name": name, **per_group[name], "usable": usable, "note": note,
        })
    if not groups:
        raise BackupError("The backup holds nothing AniFlow can restore")
    return {
        "id": staged_id,
        "version": manifest.get("version"),
        "build": manifest.get("build"),
        "created_at": manifest.get("created_at"),
        "groups": groups,
    }


# ------------------------------------------------------------------------------------------------------ restore


def _extract(archive: zipfile.ZipFile, arcname: str, target: Path) -> None:
    """One file out of the archive to a path this module built, via a temporary file beside it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=str(target.parent), prefix=".restoring-")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as writer, archive.open(arcname) as reader:
            shutil.copyfileobj(reader, writer, _COPY_CHUNK)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _target_of(arcname: str) -> Path:
    """Where an archive path goes on this machine. Only ever called with a path ``_group_of`` accepted."""
    parts = arcname.split("/")
    if parts[0] == "config":
        return config_root().joinpath(*parts[1:])
    if arcname == "data/aniflow.db":
        return _main_db_path()
    return data_root().joinpath(*parts[1:])


def _drop_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm"):
        database.with_name(database.name + suffix).unlink(missing_ok=True)


def _local_libraries(database: Path) -> list[dict[str, Any]]:
    handle = None
    try:
        handle = sqlite3.connect(str(database))
        handle.row_factory = sqlite3.Row
        return [dict(r) for r in handle.execute("select id, key, name, folder, shoko_folder_id from libraries")]
    except sqlite3.Error:
        return []
    finally:
        if handle is not None:
            handle.close()


def _keep_library_folders(database: Path, local: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Put this machine's folders back on the libraries just restored; return how many, and which found no match.

    A library is matched by its key (the system ones) and otherwise by name. One with no counterpart keeps the
    folder the backup had, which is a path from the other machine - the page tells the person which.
    """
    by_key = {row["key"]: row for row in local if row["key"]}
    by_name = {row["name"].casefold(): row for row in local if not row["key"]}
    handle = sqlite3.connect(str(database))
    handle.row_factory = sqlite3.Row
    kept, unmatched = 0, []
    try:
        for row in handle.execute("select id, key, name, folder from libraries").fetchall():
            match = by_key.get(row["key"]) if row["key"] else by_name.get((row["name"] or "").casefold())
            if match is None:
                if row["folder"]:
                    unmatched.append(row["name"])
                continue
            handle.execute(
                "update libraries set folder = ?, shoko_folder_id = ? where id = ?",
                (match["folder"], match["shoko_folder_id"], row["id"]),
            )
            kept += 1
        handle.commit()
    finally:
        handle.close()
    return kept, unmatched


def _swap_database(archive: zipfile.ZipFile, arcname: str, target: Path, *, check) -> None:
    """Replace a database with the archive's, or leave the old one exactly where it was.

    ``check`` opens the new file the way the app will (and migrates it) and raises if it cannot be used.
    """
    candidate = target.with_name(target.name + ".incoming")
    aside = target.with_name(target.name + ".before-restore")
    _extract(archive, arcname, candidate)
    if not _integrity_ok(candidate):
        candidate.unlink(missing_ok=True)
        raise BackupError(f"The database inside the backup ({arcname}) is damaged and was not restored")
    had_old = target.exists()
    try:
        if had_old:
            os.replace(target, aside)
        _drop_sidecars(target)
        os.replace(candidate, target)
        check(target)
    except BaseException as exc:
        # Put the old one back; whatever was written is not worth keeping.
        target.unlink(missing_ok=True)
        _drop_sidecars(target)
        if had_old and aside.exists():
            os.replace(aside, target)
        candidate.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"{arcname} could not be restored ({exc}); the current one was put back") from exc
    aside.unlink(missing_ok=True)
    _drop_sidecars(aside)


def _check_main_database(target: Path, *, revision: str | None) -> None:
    from alembic import command
    from alembic.script import ScriptDirectory

    from backend.app.db.session import engine

    engine.dispose()
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    if revision != head:
        command.upgrade(_alembic_config(), "head")
        engine.dispose()


def _check_calendar_database(_target: Path) -> None:
    from backend.app.db import calendar_store

    calendar_store.reset()
    try:
        calendar_store.engine()
    except calendar_store.CalendarStoreBlocked as exc:
        calendar_store.reset()
        raise BackupError(f"The calendar database in the backup cannot be used: {exc}") from exc


def _restore_settings(archive: zipfile.ZipFile, keep_local: bool) -> None:
    from backend.app.services import config_file

    try:
        incoming = yaml.safe_load(archive.read("config/aniflow.yaml").decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise BackupError("The settings in the backup could not be read") from exc
    if incoming is None:
        incoming = {}
    if not isinstance(incoming, dict):
        raise BackupError("The settings in the backup are not a settings file")
    if keep_local:
        here = config_file.read_config_file()
        for key in KEEP_LOCAL_SETTINGS:
            if key in here:
                incoming[key] = here[key]
            else:
                incoming.pop(key, None)
    config_file.write_config_file(incoming)
    config_file.invalidate_cache()


def _prune_safety_backups() -> None:
    kept = sorted(backups_dir().glob("before-restore-*.zip"))
    for old in kept[:-KEEP_SAFETY_BACKUPS]:
        old.unlink(missing_ok=True)


def restore(staged_id: str, groups: list[str], *, keep_local: bool = False) -> dict[str, Any]:
    """Restore the chosen groups from a staged upload. Returns what was done, for the page to say."""
    summary = inspect(staged_id)  # the file is checked again: the page may have waited an hour
    path = _staged_path(staged_id)
    usable = {g["name"]: g for g in summary["groups"]}
    wanted = [g for g in GROUPS if g in set(groups)]
    if not wanted:
        raise BackupError("Nothing was chosen to restore")
    for name in wanted:
        if name not in usable:
            raise BackupError(f"The backup has no {name} to restore")
        if not usable[name]["usable"]:
            raise BackupError(f"{name} cannot be restored: {usable[name]['note']}")

    # What is about to be replaced is saved first. The big re-fetchable group is left out of that copy.
    backups_dir().mkdir(parents=True, exist_ok=True)
    safety = backups_dir() / f"before-restore-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.zip"
    safe_groups = [g for g in wanted if g != "calendar_art"]
    if safe_groups:
        build_backup(safe_groups, target=safety)
    _prune_safety_backups()

    result: dict[str, Any] = {
        "restored": [], "files": {}, "safety_backup": safety.name if safe_groups else None,
        "libraries_kept": 0, "libraries_unmatched": [], "warnings": [],
        "restart_recommended": bool(set(wanted) & DATABASE_GROUPS),
    }
    with zipfile.ZipFile(path) as archive:
        manifest = _read_manifest(archive)
        names = [n for n in manifest["files"] if _group_of(n)]
        for group in wanted:
            members = [n for n in names if _group_of(n) == group]
            if group == "settings":
                _restore_settings(archive, keep_local)
            elif group == "library":
                local = _local_libraries(_main_db_path()) if keep_local else []
                revision = manifest.get("library_revision")
                _swap_database(archive, "data/aniflow.db", _main_db_path(),
                               check=lambda target, revision=revision: _check_main_database(target, revision=revision))
                if keep_local:
                    kept, unmatched = _keep_library_folders(_main_db_path(), local)
                    result["libraries_kept"], result["libraries_unmatched"] = kept, unmatched
                result["warnings"].append("accounts")
            elif group == "calendar":
                _swap_database(archive, "data/calendar.db", _calendar_db_path(), check=_check_calendar_database)
            else:
                for arcname in members:
                    _extract(archive, arcname, _target_of(arcname))
            result["restored"].append(group)
            result["files"][group] = len(members)
            logger.info("restored %s (%s file(s))", group, len(members))

    if {"library", "definitions"} & set(wanted):
        _sync_definitions(result)
    discard(staged_id)
    return result


def _sync_definitions(result: dict[str, Any]) -> None:
    """Let the database and the definition files agree again; a failure is a note, not a failed restore."""
    try:
        from backend.app.db.session import SessionLocal
        from backend.app.services import config_sync

        with SessionLocal() as session:
            config_sync.sync_from_files(session)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("definitions were restored but could not be synced yet: %s", exc)
        result["warnings"].append("sync")
