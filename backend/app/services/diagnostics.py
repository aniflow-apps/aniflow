"""What AniFlow can tell about itself, and the file that carries it (build 262).

Two things came out of the container log being useless for a bug report (it is at ``ERROR`` by default, and the
person reading it has never seen the code): an **About** answer - which build is this, how is it set up - and a
**diagnostics bundle**, one zip a person can attach to a report.

The bundle holds: the About answer, the settings (secrets removed), the Checks results, the libraries and
indexers, the scheduled tasks and how their last run ended, counts of what is in the database (never its content),
the last Activity entries, and AniFlow's own last few thousand log lines (``core/log_buffer.py``).

It does **not** hold: the database, the AniDB export, the calendar, any password, key or token
(``services/redaction.py``). It **does** hold anime titles and folder paths, because they are in the log and a
report that cannot say which show failed is of little use - the README in the zip and the page beside the button
say so, and the person can open the zip and read it before sending it.

Every section is built on its own and an error in one becomes a line in that section instead of an error for the
whole bundle: this is used when something is broken, which is exactly when a part of it is likely to fail.
"""
from __future__ import annotations

import io
import json
import platform
import shutil
import sys
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component, get_logging_system
from backend.app.core.mode import current_mode
from backend.app.core.version import app_version, build_number, build_string
from backend.app.services import redaction

logger = for_component("Diagnostics")

#: When this process started (the module is imported at startup): what "uptime" counts from.
STARTED_AT = datetime.now(UTC)

#: How many Activity entries the bundle carries.
ACTIVITY_LINES = 100


# ------------------------------------------------------------------------------------------------ About


def database_version() -> dict[str, Any]:
    """The migration the database is at, and the one this build expects."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from backend.app.db.session import SessionLocal

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    head = ScriptDirectory.from_config(Config(str(ini))).get_current_head() if ini.is_file() else None
    current = None
    try:
        with SessionLocal() as session:
            current = session.execute(text("select version_num from alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - no version table: a test database or a hand-made one
        pass
    return {"current": current, "head": head, "up_to_date": current is not None and current == head}


def about(db: Session) -> dict[str, Any]:
    """Which build this is and how it is set up: the About panel, and the first file of the bundle."""
    from backend.app.services.app_mode import current_mode as app_mode
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services.clock import now_local, timezone_name
    from backend.app.services.storage import path_status
    from backend.app.worker.runtime import worker_state

    env = get_settings()
    cfg = load_app_settings(db)
    now = datetime.now(UTC)
    folders = []
    for key, path in (
        ("data", env.data_dir),
        ("config", env.config_dir or "/config"),
        ("downloads", env.download_root),
        ("media", env.anime_library_root),
    ):
        status = path_status(path)
        free = None
        if status["exists"]:
            try:
                free = shutil.disk_usage(str(path)).free
            except OSError:
                pass
        # "name", not "key": a field called key is read as a secret by the redaction and would be masked.
        folders.append({"name": key, **status, "free_bytes": free})
    return {
        "version": app_version(),
        "build": build_number(),
        "build_string": build_string(),
        "environment": env.app_env,
        "storage_mode": current_mode().value,
        "app_mode": app_mode(db).value,
        "debug_mode": bool(cfg.debug_mode),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "docker": Path("/.dockerenv").exists(),
        "timezone": timezone_name(),
        "server_time": now_local().isoformat(timespec="seconds"),
        "started_at": STARTED_AT.isoformat(timespec="seconds"),
        "uptime_seconds": int((now - STARTED_AT).total_seconds()),
        "worker_running": bool(worker_state.running),
        "database": database_version(),
        "folders": folders,
    }


# ------------------------------------------------------------------------------------------------ the secrets


def url_secrets(value: object) -> list[str]:
    """The secrets inside an address: the password of ``http://user:password@host`` and any ``?apikey=…``.

    An indexer's key or a client's login lives in a URL AniFlow stores, and shows up on its own in an error
    message ("could not reach … 12ab34") that no pattern around it would catch - so the value is collected here and
    replaced wherever it appears.
    """
    from urllib.parse import parse_qsl, unquote, urlsplit

    if not isinstance(value, str) or "://" not in value:
        return []
    try:
        parts = urlsplit(value)
        found = [unquote(parts.password)] if parts.password else []
        found.extend(item for name, item in parse_qsl(parts.query) if redaction.is_secret_name(name))
    except ValueError:
        return []
    return found


def known_secrets(db: Session) -> list[str]:
    """Every secret value AniFlow holds, to be replaced wherever it appears (see redaction.py)."""
    from backend.app.models.indexer import Indexer
    from backend.app.models.rss import FeedSource
    from backend.app.services.app_settings import load_app_settings

    values: list[object] = []
    cfg = asdict(load_app_settings(db))
    values.extend(value for key, value in cfg.items() if redaction.is_secret_name(key))
    # An id or a key given to a provider is not worth the risk of explaining the difference.
    values.append(cfg.get("mal_client_id"))
    for value in cfg.values():
        values.extend(url_secrets(value))
    env = get_settings()
    values.extend(
        value for key, value in env.model_dump().items() if redaction.is_secret_name(key)
    )
    # The addresses of the indexers and feeds, where private trackers put the key.
    for address in (*db.scalars(select(Indexer.url)), *db.scalars(select(FeedSource.url))):
        values.extend(url_secrets(address))
    return redaction.secret_values(values)


# ------------------------------------------------------------------------------------------------ the sections


def _settings(db: Session, secrets: list[str]) -> dict[str, Any]:
    from backend.app.services.app_settings import load_app_settings

    return redaction.redact_value(asdict(load_app_settings(db)), secrets)


def _checks(db: Session) -> dict[str, Any]:
    from backend.app.services.system_checks import run_checks, summarize

    results = run_checks(db, live=False)
    return {**summarize(results), "checks": [asdict(item) for item in results]}


def _libraries(db: Session) -> list[dict[str, Any]]:
    from backend.app.services import libraries
    from backend.app.services.storage import path_status

    rows = []
    for library in libraries.list_libraries(db):
        entry = {
            "id": library.id, "built_in": library.key, "name": library.name, "folder": library.folder,
            "is_default": library.is_default, "fits_adult": library.fits_adult, "fits_season": library.fits_season,
            "shoko_folder_id": library.shoko_folder_id,
        }
        if library.has_folder:
            entry.update(path_status(library.folder))
        rows.append(entry)
    return rows


def _indexers(db: Session, secrets: list[str]) -> list[dict[str, Any]]:
    from backend.app.models.indexer import Indexer

    return [
        redaction.redact_value(
            {
                "name": row.name, "url": row.url, "enabled": row.enabled, "is_default": row.is_default,
                "config_slug": row.config_slug, "last_used_at": row.last_used_at, "last_error": row.last_error,
            },
            secrets,
        )
        for row in db.scalars(select(Indexer).order_by(Indexer.name))
    ]


def _schedule(db: Session) -> list[dict[str, Any]]:
    from backend.app.api.routes.schedule import list_tasks

    return [task.model_dump(mode="json") for task in list_tasks(db)]


def _counts(db: Session) -> dict[str, Any]:
    """How much is in the database - never what."""
    from backend.app.db.base import Base
    from backend.app.models.anime import Anime, Episode
    from backend.app.models.download import TorrentDownload as Download

    tables = {}
    for table in Base.metadata.sorted_tables:
        tables[table.name] = db.scalar(select(func.count()).select_from(table)) or 0
    grouped: dict[str, Any] = {}
    for label, column in (
        ("anime_by_lifecycle", Anime.lifecycle), ("anime_by_entry_mode", Anime.entry_mode),
        ("episodes_by_status", Episode.status), ("downloads_by_status", Download.status),
    ):
        grouped[label] = {
            str(getattr(key, "value", key)): count for key, count in db.execute(select(column, func.count()).group_by(column))
        }
    return {"tables": tables, **grouped}


def _activity(db: Session, secrets: list[str]) -> str:
    from backend.app.models.activity import Activity

    lines = []
    for row in db.scalars(select(Activity).order_by(Activity.created_at.desc()).limit(ACTIVITY_LINES)):
        stamp = row.created_at.isoformat(timespec="seconds") if row.created_at else "?"
        lines.append(f"{stamp} {row.kind} {row.message}")
    return redaction.redact_text("\n".join(lines), secrets)


def _log(secrets: list[str]) -> str:
    lines = get_logging_system().buffer.lines()
    return redaction.redact_text("\n".join(lines), secrets)


# ------------------------------------------------------------------------------------------------ the bundle

README = """AniFlow diagnostics
===================

Made {when} by AniFlow {version} (build {build}).

What is in this file
--------------------
about.json      Which build this is, how it is set up (mode, timezone, folders, database version, uptime).
settings.json   Every setting. Passwords, keys and tokens are replaced by ***.
checks.json     The Checks page: what was tested and what it found.
libraries.json  Your libraries: name, folder, whether the folder exists and can be written.
indexers.json   Your indexers (addresses with any key removed) and the last error of each.
schedule.json   The background tasks and how their last run ended.
counts.json     How many rows each table holds and how anime, episodes and downloads are distributed.
                Counts only - not the content of the database.
activity.txt    The last {activity} Activity entries.
log.txt         AniFlow's own last log lines (INFO and above), whatever the container log is set to.

What was left out
-----------------
The database, the AniDB export, the calendar, and every password, key and token.

What is in it that you may not want to send
-------------------------------------------
Anime titles and folder paths appear in log.txt and activity.txt, because that is what the log says. If you would
rather not share them, open the file in a text editor and remove those lines before you send it. Addresses of your
own machines (the torrent client, Shoko) are in settings.json.
"""

#: (file name, how to make it, kind) - ``kind`` says how the result is written.
Section = tuple[str, Callable[[], Any], str]


def _write(name: str, make: Callable[[], Any], kind: str) -> str:
    """One file's text. A section that fails says so in the file rather than stopping the bundle."""
    try:
        value = make()
        if kind == "json":
            return json.dumps(value, ensure_ascii=False, indent=2, default=str)
        return str(value)
    except Exception as exc:  # noqa: BLE001 - see the module note
        logger.warning("diagnostics: %s could not be made: %s", name, exc)
        if kind == "json":
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2)
        return f"(could not be made: {type(exc).__name__}: {exc})"


def build_bundle(db: Session) -> bytes:
    """The zip: every section made, every text passed through the redaction once more, then packed."""
    secrets = known_secrets(db)
    now = datetime.now(UTC)
    sections: list[Section] = [
        ("about.json", lambda: about(db), "json"),
        ("settings.json", lambda: _settings(db, secrets), "json"),
        ("checks.json", lambda: _checks(db), "json"),
        ("libraries.json", lambda: _libraries(db), "json"),
        ("indexers.json", lambda: _indexers(db, secrets), "json"),
        ("schedule.json", lambda: _schedule(db), "json"),
        ("counts.json", lambda: _counts(db), "json"),
        ("activity.txt", lambda: _activity(db, secrets), "text"),
        ("log.txt", lambda: _log(secrets), "text"),
    ]
    readme = README.format(
        when=now.strftime("%Y-%m-%d %H:%M UTC"), version=app_version(), build=build_number(), activity=ACTIVITY_LINES,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", readme)
        for name, make, kind in sections:
            # A second pass over the finished text: whatever a section forgot to scrub, this still catches.
            bundle.writestr(name, redaction.redact_text(_write(name, make, kind), secrets))
    return buffer.getvalue()


def bundle_name(now: datetime | None = None) -> str:
    """``aniflow-diagnostics-b262-20261014-1530.zip``"""
    moment = now or datetime.now(UTC)
    return f"aniflow-diagnostics-b{build_number()}-{moment:%Y%m%d-%H%M}.zip"


__all__ = ["ACTIVITY_LINES", "STARTED_AT", "about", "build_bundle", "bundle_name", "database_version", "known_secrets"]
