"""Configuration that lives in files, mirrored into the database.

Three things AniFlow needs are *configuration*, not data: which sites it may
search, which feeds it may scan, and the named release rules to apply. They
used to live only in the database, which made "wipe the database and start
fresh" also mean "set all of that up again" - the one thing a reset should not
cost you.

So the folder is the truth and the database is a mirror:

    <CONFIG_DIR>/indexers/*.yaml     sites AniFlow may search
    <CONFIG_DIR>/subgroup/*.yaml     subgroup profiles (named release rules)
    <CONFIG_DIR>/rss_source/*.yaml   feeds AniFlow may scan

Everything follows from that one decision:

* **A file dropped in is picked up.** No import step and no restart - the
  folders are re-read whenever anything lists them, and a changed file wins.
* **The UI writes files.** Saving a profile writes its YAML; deleting one
  removes it. There is never a state that exists only in the database.
* **A wipe costs nothing.** Delete ``data/``, start again, and the same
  indexers, feeds and profiles come back, because they never lived there.

Ids are derived from the filename rather than generated, so the same file
produces the same id on every machine and after every reset. That is what lets
an anime keep pointing at "the SubsPlease profile" across a rebuild.

Volatile state stays in the database and is expected to be lost: when a feed
was last read, whether an indexer errored. None of it is configuration.

A file that cannot be read is skipped with a warning and never stops the
application: these are hand-written, in a folder the user owns, and one typo
must not take AniFlow down.
"""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Config")

#: Files whose name says they are documentation, not configuration. Shipped so
#: the format is visible in the folder it applies to, and skipped so that
#: shipping it configures nothing.
IGNORED_STEMS = {"example", "readme", "template"}

#: One namespace, so an id depends only on the kind and the file name - the
#: same file yields the same id on any machine and after any reset.
_NAMESPACE = uuid.UUID("6f0a7d1e-3d4b-5a2c-9e8f-1b2c3d4e5f60")


@dataclass(frozen=True)
class ConfigFolder:
    """One kind of file-backed configuration."""

    #: Folder name under CONFIG_DIR, and the id prefix.
    kind: str
    #: What to call it in a log line.
    label: str

    @property
    def path(self) -> Path:
        configured = get_settings().config_dir
        root = Path(configured) if configured else Path("/config")
        return root / self.kind


INDEXERS = ConfigFolder("indexers", "indexer")
SUBGROUPS = ConfigFolder("subgroup", "subgroup profile")
FEEDS = ConfigFolder("rss_source", "RSS source")

ALL_FOLDERS = (INDEXERS, SUBGROUPS, FEEDS)


def slugify(name: str) -> str:
    """A file name for an object name.

    Deliberately lossy and stable: "SubsPlease 1080p" and "subsplease 1080p"
    are the same profile, and both give ``subsplease-1080p.yaml``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "unnamed"


def stable_id(folder: ConfigFolder, slug: str) -> str:
    """The id a file's object always has, derived rather than generated."""
    return str(uuid.uuid5(_NAMESPACE, f"{folder.kind}:{slug}"))


def ensure_folders() -> None:
    """Create the three folders so they are visible before anything is in them.

    An empty folder is a much better instruction than a missing one: it says
    where the files go.
    """
    for folder in ALL_FOLDERS:
        try:
            folder.path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # a read-only mount is the user's to fix
            logger.warning("could not create %s: %s", folder.path, exc)


def _signature(folder: ConfigFolder) -> tuple:
    """What changes when the folder's contents change.

    Name, size and mtime of every file - enough to notice an edit, a new file
    or a deletion without reading anything.
    """
    path = folder.path
    if not path.is_dir():
        return ()
    entries = []
    for file in sorted(path.glob("*.y*ml")):
        try:
            stat = file.stat()
        except OSError:
            continue
        entries.append((file.name, stat.st_size, stat.st_mtime))
    return tuple(entries)


_cache: dict[str, tuple[tuple, list[dict[str, Any]]]] = {}


def read_folder(folder: ConfigFolder) -> list[dict[str, Any]]:
    """Every usable file in one folder, as ``{"slug": …, "data": {…}}``.

    Re-read only when something in the folder changed, which is what makes
    "drop a file in and it appears" cost nothing on the paths that list these
    objects on every request.
    """
    signature = _signature(folder)
    cached = _cache.get(folder.kind)
    if cached is not None and cached[0] == signature:
        return cached[1]

    found: list[dict[str, Any]] = []
    path = folder.path
    if path.is_dir():
        for file in sorted(path.glob("*.y*ml")):
            if file.stem.lower() in IGNORED_STEMS:
                continue
            try:
                raw = yaml.safe_load(file.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("%s %s could not be read: %s", folder.label, file.name, exc)
                continue
            if raw is None:
                continue
            if not isinstance(raw, dict):
                logger.warning("%s %s is not a mapping", folder.label, file.name)
                continue
            found.append({"slug": file.stem, "path": file, "data": raw})

    _cache[folder.kind] = (signature, found)
    return found


def invalidate(folder: ConfigFolder | None = None) -> None:
    """Forget what was read, so the next read goes back to disk."""
    if folder is None:
        _cache.clear()
    else:
        _cache.pop(folder.kind, None)


def write_object(folder: ConfigFolder, slug: str, data: dict[str, Any]) -> Path:
    """Save one object as YAML, atomically.

    Atomic because the folder is watched: a half-written file read mid-save
    would be reported as broken configuration.
    """
    folder.path.mkdir(parents=True, exist_ok=True)
    target = folder.path / f"{slug}.yaml"
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    handle, temporary = tempfile.mkstemp(dir=str(folder.path), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        os.replace(temporary, target)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
    invalidate(folder)
    logger.info("wrote %s %s", folder.label, target.name)
    return target


def delete_object(folder: ConfigFolder, slug: str) -> bool:
    """Remove one object's file. True when there was one."""
    removed = False
    for suffix in (".yaml", ".yml"):
        candidate = folder.path / f"{slug}{suffix}"
        if candidate.exists():
            candidate.unlink()
            removed = True
    if removed:
        invalidate(folder)
        logger.info("removed %s %s", folder.label, slug)
    return removed


def rename_object(folder: ConfigFolder, old_slug: str, new_slug: str) -> None:
    """Move an object's file when its name changed."""
    if old_slug == new_slug:
        return
    delete_object(folder, old_slug)


def as_bool(value: Any, default: bool = False) -> bool:
    """YAML already gives real booleans; this is for the hand-written cases."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return default


def as_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def as_list(value: Any) -> list[str]:
    """A YAML list, or one string, or nothing."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


__all__ = [
    "ALL_FOLDERS",
    "FEEDS",
    "IGNORED_STEMS",
    "INDEXERS",
    "SUBGROUPS",
    "ConfigFolder",
    "as_bool",
    "as_list",
    "as_text",
    "delete_object",
    "ensure_folders",
    "invalidate",
    "read_folder",
    "rename_object",
    "slugify",
    "stable_id",
    "write_object",
]
