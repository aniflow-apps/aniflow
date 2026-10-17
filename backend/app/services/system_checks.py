"""The checklist: what has to be true for AniFlow to do its job, tested one thing at a time (build 232).

Found when an anime's import failed on a ``PermissionError`` for a library folder that was read-only - and the
Libraries page had said "read-only" all along. The facts were scattered: the Settings paths check, an indexer's last
error, a library card's writable flag, the calendar's status. Nothing said *here is what still needs doing*.

Each check answers one question and returns a result with a **code**, not a sentence: the page turns
``("torrent_client", "unreachable")`` into words in the person's language, with the values it was given. A result is

``ok``       fine
``warning``  works, but something is likely to go wrong
``error``    something that has to be fixed for AniFlow to do its job
``skipped``  does not apply here (no downloading in Basic mode, no Shoko in standalone mode)

Every check is isolated: one that raises becomes a warning about *itself* and never takes the page down.
"""
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Checks")

OK, WARNING, ERROR, SKIPPED = "ok", "warning", "error", "skipped"

#: Less free space than this in a library folder is worth saying (bytes).
LOW_SPACE = 5 * 1024**3

#: A smaller floor for AniFlow's own folders: a database needs room to grow, not a season of video.
LOW_SPACE_OWN = 1 * 1024**3

#: The current season counts as stale after this many days without an update.
CALENDAR_STALE_DAYS = 10

#: Live indexer answers are reused for this long, and one request is made per indexer, spaced - the rule for the
#: free services AniFlow asks: sequential, spaced, never a burst.
LIVE_REUSE_SECONDS = 120.0
LIVE_SPACING_SECONDS = 2.0
_live_indexers: tuple[float, list["CheckResult"]] | None = None


@dataclass
class CheckResult:
    #: Which question: ``torrent_client``, ``library`` ... The page's wording is keyed by it.
    id: str
    #: Which block of the page it belongs to.
    group: str
    state: str
    #: Which answer to that question: ``unreachable``, ``not_writable`` ...
    code: str
    #: What the sentence needs: a name, a path, a number.
    values: dict[str, str] = field(default_factory=dict)
    #: Where to go to fix it, as a page address the app understands; None when there is nothing to open.
    fix: str | None = None
    #: A stable key when one question has several answers (one row per library).
    subject: str | None = None
    #: Asks somebody else over the network (an indexer, AniList). Run when a person presses the button, never by
    #: the background refresh of the sidebar number: a free service is not asked every few minutes for ever.
    live: bool = False


def _size(value: int) -> str:
    return f"{value / 1024**3:.1f} GB"


def can_write(folder: str | Path) -> bool:
    """Whether a file can really be created in this folder.

    A real test, not ``os.access``: on a network share or an ACL-mounted dataset the permission bits answer
    "yes" for a folder the container's user cannot write to, which is the failure this exists for. The file is
    created and removed at once and is never named like anything AniFlow keeps.
    """
    try:
        with tempfile.NamedTemporaryFile(dir=str(folder), prefix=".aniflow-check-"):
            pass
        return True
    except OSError:
        return False


# -- the checks ------------------------------------------------------------------------------------------------


def _torrent_client(db: Session, settings, basic: bool) -> list[CheckResult]:
    from backend.app.integrations.torrent_client import TorrentClientError, client_for, client_name

    if basic:
        return [CheckResult("torrent_client", "setup", SKIPPED, "basic")]
    if not settings.qb_url:
        return [CheckResult("torrent_client", "setup", ERROR, "not_configured", fix="#/settings/downloads")]
    name = client_name(settings.torrent_client)
    results: list[CheckResult] = []
    client = None
    try:
        client = client_for(settings.torrent_client, settings.qb_url, settings.qb_username, settings.qb_password, timeout=5)
        version = client.version()
        results.append(CheckResult("torrent_client", "setup", OK, "reachable", {"name": name, "version": str(version)}))
        ready = getattr(client, "check_ready", None)
        if ready is not None:
            ready(settings.qb_category)
        results.extend(_client_paths(client, settings))
    except TorrentClientError as exc:
        text = str(exc)[:200]
        if results:  # reachable, but not usable (Deluge without its Label plugin)
            results.append(CheckResult("torrent_client_ready", "setup", ERROR, "not_ready", {"name": name, "reason": text}, fix="#/settings/downloads"))
        else:
            results.append(CheckResult("torrent_client", "setup", ERROR, "unreachable", {"name": name, "reason": text}, fix="#/settings/downloads"))
    finally:
        if client is not None:
            client.close()
    return results


def _client_paths(client, settings) -> list[CheckResult]:
    """Whether the client saves where AniFlow looks: the same folder at the same path, in both containers."""
    try:
        saved = str(client.default_save_path() or "").rstrip("/")
    except Exception:  # noqa: BLE001 - a client that cannot say is not a finding
        return []
    ours = str(get_settings().download_root).rstrip("/")
    if not saved:
        return []
    if saved == ours or saved.startswith(ours + "/"):
        # The same folder, or one inside it (Transmission's image saves into /downloads/complete): AniFlow finds it.
        return [CheckResult("client_paths", "setup", OK, "same", {"path": saved})]
    return [
        CheckResult(
            "client_paths", "setup", WARNING, "differ", {"client": saved, "aniflow": ours}, fix="#/settings/downloads"
        )
    ]


def _download_folder(basic: bool) -> list[CheckResult]:
    if basic:
        return [CheckResult("download_folder", "folders", SKIPPED, "basic")]
    folder = Path(get_settings().download_root)
    if not folder.is_dir():
        return [CheckResult("download_folder", "folders", ERROR, "missing", {"path": str(folder)})]
    if not os.access(folder, os.R_OK):
        return [CheckResult("download_folder", "folders", ERROR, "unreadable", {"path": str(folder)})]
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = None
    if free is not None and free < LOW_SPACE:
        return [CheckResult("download_folder", "folders", WARNING, "low_space", {"path": str(folder), "free": _size(free)})]
    return [
        CheckResult(
            "download_folder", "folders", OK, "readable",
            {"path": str(folder), "free": _size(free) if free is not None else "?"},
        )
    ]


def _own_folders() -> list[CheckResult]:
    """AniFlow's own folders: the data folder holds the database, the config folder the settings file and the log.

    If the database cannot be written AniFlow does not run at all, so the finding is for the half-broken case:
    a folder that was fine when it was created and is full, or has changed owner, since.
    """
    env = get_settings()
    results: list[CheckResult] = []
    for id_, folder in (("data_folder", Path(env.data_dir)), ("config_folder", Path(env.config_dir or "/config"))):
        base = {"path": str(folder)}
        if not folder.is_dir():
            results.append(CheckResult(id_, "folders", ERROR, "missing", base))
        elif not can_write(folder):
            results.append(CheckResult(id_, "folders", ERROR, "not_writable", base))
        else:
            try:
                free = shutil.disk_usage(folder).free
            except OSError:
                free = None
            if free is not None and free < LOW_SPACE_OWN:
                results.append(CheckResult(id_, "folders", WARNING, "low_space", {**base, "free": _size(free)}))
            else:
                results.append(CheckResult(id_, "folders", OK, "ready", {**base, "free": _size(free) if free is not None else "?"}))
    return results


def _libraries(db: Session, shoko_mode: bool) -> list[CheckResult]:
    from backend.app.models.library import Library

    results: list[CheckResult] = []
    for library in db.scalars(select(Library).order_by(Library.sort_order, Library.id)):
        folder = (library.folder or "").strip()
        if not folder:
            continue  # Upcoming: no folder by design
        base = {"name": library.name, "path": folder}
        subject = str(library.id)
        if not Path(folder).is_dir():
            results.append(CheckResult("library", "folders", ERROR, "missing", base, fix="#/libraries", subject=subject))
            continue
        # In Shoko Server mode AniFlow writes to the hand-over folder, and into a library only when that
        # library is linked to a Shoko import folder; an unlinked one is only read.
        needs_write = (not shoko_mode) or bool(library.shoko_folder_id)
        if needs_write and not can_write(folder):
            results.append(CheckResult("library", "folders", ERROR, "not_writable", base, fix="#/libraries", subject=subject))
            continue
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            free = None
        if free is not None and free < LOW_SPACE:
            results.append(
                CheckResult("library", "folders", WARNING, "low_space", {**base, "free": _size(free)}, fix="#/libraries", subject=subject)
            )
            continue
        code = "ready" if needs_write else "readable"
        results.append(
            CheckResult("library", "folders", OK, code, {**base, "free": _size(free) if free is not None else "?"}, subject=subject)
        )
    return results


def _import_folder(db: Session, shoko_mode: bool) -> list[CheckResult]:
    if not shoko_mode:
        return []
    from backend.app.models.library import Library

    root = get_settings().import_root
    unlinked = db.scalar(select(Library.id).where(Library.folder != "", Library.shoko_folder_id.is_(None)).limit(1))
    if root is None or not Path(root).is_dir():
        if unlinked is None:
            return [CheckResult("import_folder", "folders", SKIPPED, "not_needed")]
        return [CheckResult("import_folder", "folders", ERROR, "missing", {"path": str(root or "/import")})]
    if not can_write(root):
        return [CheckResult("import_folder", "folders", ERROR, "not_writable", {"path": str(root)})]
    return [CheckResult("import_folder", "folders", OK, "ready", {"path": str(root)})]


def _try_hardlink(source_dir: Path, target_dir: Path) -> tuple[str, str]:
    """Make a throwaway file in one folder and link it into the other. Returns ``(code, detail)``.

    Both names are removed again at once; neither is named like anything AniFlow keeps. The file is made in the
    download folder, because that is where a real link comes *from* - the answer has to be about that pair.
    """
    from backend.app.services.importer import hardlink_reason

    handle, name = tempfile.mkstemp(dir=str(source_dir), prefix=".aniflow-hardlink-")
    os.close(handle)
    origin = Path(name)
    link = target_dir / f"{origin.name}.link"
    try:
        try:
            os.link(str(origin), str(link))
        except OSError as exc:
            reason = hardlink_reason(exc)
            if reason == "hardlink_cross_device":
                return "cross_device", ""
            if reason == "hardlink_unsupported":
                return "unsupported", ""
            if isinstance(exc, PermissionError):
                return "not_writable", ""
            return "failed", f"{type(exc).__name__}: {exc}"[:160]
        return ("works", "") if os.stat(origin).st_nlink >= 2 else ("failed", "the link was made but shares no data")
    finally:
        for leftover in (link, origin):
            try:
                leftover.unlink()
            except OSError:
                pass


def _hardlink(db: Session, settings, basic: bool, shoko_mode: bool) -> list[CheckResult]:
    """Whether the download folder can be linked into every folder AniFlow places files in (build 272).

    Asked only when Settings > Downloads says to place by hardlink. A link needs the two folders on one filesystem
    *and* one mount inside the container - two bind mounts of one disk fail with "Invalid cross-device link" - so
    the only honest test is to make one from where the download is into where the episode goes.
    """
    from backend.app.models.library import Library

    if basic or settings.file_operation != "hardlink":
        return [CheckResult("hardlink", "folders", SKIPPED, "not_used")]
    source = Path(get_settings().download_root)
    if not source.is_dir():
        return [CheckResult("hardlink", "folders", ERROR, "no_download_folder", {"path": str(source)}, fix="#/settings/downloads")]

    targets: dict[str, str] = {}
    if shoko_mode and get_settings().import_root is not None:
        targets[str(get_settings().import_root)] = "import"
    for library in db.scalars(select(Library).order_by(Library.sort_order, Library.id)):
        folder = (library.folder or "").strip()
        # In Shoko Server mode a library is only written to when it is linked to one of Shoko's import folders.
        if folder and ((not shoko_mode) or library.shoko_folder_id):
            targets.setdefault(folder, library.name)

    results: list[CheckResult] = []
    for folder, name in targets.items():
        base = {"path": folder, "source": str(source), "name": name}
        if not Path(folder).is_dir():
            continue  # the library check already says so
        try:
            code, detail = _try_hardlink(source, Path(folder))
        except OSError:
            # No throwaway file could be made in the download folder: AniFlow only reads it. That is a fine setup
            # for copying; a link made from it is not something this check can try.
            return [CheckResult("hardlink", "folders", WARNING, "cannot_test", {"path": str(source)}, fix="#/settings/downloads")]
        state = OK if code == "works" else ERROR
        values = {**base, "reason": detail} if detail else base
        results.append(
            CheckResult(
                "hardlink", "folders", state, code, values,
                fix=None if state == OK else "#/settings/downloads", subject=folder,
            )
        )
    return results or [CheckResult("hardlink", "folders", SKIPPED, "no_target")]


def _indexers(db: Session, basic: bool) -> list[CheckResult]:
    from backend.app.models.indexer import Indexer
    from backend.app.models.rss import FeedSource

    if basic:
        return [CheckResult("indexer", "setup", SKIPPED, "basic"), CheckResult("feed", "setup", SKIPPED, "basic")]
    enabled = list(db.scalars(select(Indexer).where(Indexer.enabled.is_(True))))
    results: list[CheckResult] = []
    if not enabled:
        results.append(CheckResult("indexer", "setup", ERROR, "none", fix="#/indexers"))
    else:
        failing = [item.name for item in enabled if item.last_error]
        if failing:
            results.append(CheckResult("indexer", "setup", WARNING, "last_error", {"names": ", ".join(failing)}, fix="#/indexers"))
        else:
            results.append(CheckResult("indexer", "setup", OK, "ready", {"names": ", ".join(item.name for item in enabled)}))
    feeds = db.scalar(select(FeedSource.id).where(FeedSource.enabled.is_(True)).limit(1))
    if feeds is None:
        # New episodes of an airing show arrive through a feed; without one only a search finds them.
        results.append(CheckResult("feed", "setup", WARNING, "none", fix="#/rssSources"))
    else:
        results.append(CheckResult("feed", "setup", OK, "ready"))
    return results


def _metadata() -> list[CheckResult]:
    from backend.app.db import calendar_store
    from backend.app.models.calendar import CalendarSeason
    from backend.app.services import anidb_titles, id_mapping
    from backend.app.services.calendar import resolve_season

    results: list[CheckResult] = []
    blocked = calendar_store.blocked_reason()
    if blocked:
        results.append(CheckResult("calendar", "data", ERROR, "blocked", {"reason": blocked[:200]}, fix="#/calendar"))
    elif not calendar_store.exists():
        results.append(CheckResult("calendar", "data", WARNING, "not_loaded", fix="#/calendar"))
    else:
        year, season = resolve_season(None, None)
        with calendar_store.sessions()() as session:
            row = session.scalar(select(CalendarSeason).where(CalendarSeason.year == year, CalendarSeason.season == season))
            loaded = row.last_success_at if row is not None else None
        if loaded is None:
            results.append(CheckResult("calendar", "data", WARNING, "not_loaded", {"season": f"{season.title()} {year}"}, fix="#/calendar"))
        else:
            results.append(CheckResult("calendar", "data", OK, "loaded", {"season": f"{season.title()} {year}"}))

    dump = anidb_titles.current_status()
    if not dump.present:
        results.append(CheckResult("anidb_titles", "data", WARNING, "missing", fix="#/schedule"))
    elif dump.last_error:
        results.append(CheckResult("anidb_titles", "data", WARNING, "last_error", {"reason": str(dump.last_error)[:200]}, fix="#/schedule"))
    else:
        results.append(CheckResult("anidb_titles", "data", OK, "loaded", {"count": str(dump.anime_count)}))

    mapping = id_mapping.status()
    if not mapping.rows:
        results.append(CheckResult("id_list", "data", WARNING, "missing", fix="#/schedule"))
    else:
        results.append(CheckResult("id_list", "data", OK, "loaded", {"count": str(mapping.rows)}))
    return results


def _calendar_age(settings) -> list[CheckResult]:
    """The current season, updated lately? Local: the calendar's own record, no request."""
    from backend.app.db import calendar_store
    from backend.app.models.calendar import CalendarSeason
    from backend.app.services.calendar import resolve_season

    if not calendar_store.exists() or calendar_store.blocked_reason():
        return []
    year, season = resolve_season(None, None)
    with calendar_store.sessions()() as session:
        row = session.scalar(select(CalendarSeason).where(CalendarSeason.year == year, CalendarSeason.season == season))
        loaded = row.last_success_at if row is not None else None
    if loaded is None:
        return []  # "not loaded" is the calendar row's own finding
    if loaded.tzinfo is None:
        loaded = loaded.replace(tzinfo=UTC)
    days = (datetime.now(UTC) - loaded).days
    if days < CALENDAR_STALE_DAYS:
        return [CheckResult("calendar_age", "data", OK, "fresh", {"days": str(days)})]
    code = "stale" if settings.calendar_auto_update else "stale_manual"
    return [CheckResult("calendar_age", "data", WARNING, code, {"days": str(days)}, fix="#/schedule")]


def _database() -> list[CheckResult]:
    """Is the database at the schema this version of AniFlow expects?

    Every start runs the migrations before the app comes up, so a database that is behind means one of them
    failed (and AniFlow carried on regardless, which it is built to do). Cheap to ask, awkward to find out later.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from backend.app.db.session import SessionLocal

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    if not ini.is_file():
        return []
    head = ScriptDirectory.from_config(Config(str(ini))).get_current_head()
    try:
        with SessionLocal() as session:
            current = session.execute(text("select version_num from alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - no version table (a test database, a hand-made one): nothing to compare
        return []
    if current == head:
        return [CheckResult("database", "data", OK, "current", {"version": str(current)})]
    return [CheckResult("database", "data", WARNING, "behind", {"current": str(current), "head": str(head)})]


def _jobs() -> list[CheckResult]:
    """The background worker, and the scheduled jobs that failed the last time they ran.

    The Schedule page has always shown each job's last error; nothing said that
    any of them had one. A calendar that stops updating or an identity job that
    keeps failing looks, from everywhere else, like nothing at all.
    """
    from backend.app.worker.runtime import TASKS, worker_state

    results: list[CheckResult] = []
    if not worker_state.running:
        results.append(CheckResult("worker", "activity", ERROR, "stopped", fix="#/schedule"))
    failing = [
        (task.id, worker_state.tasks[task.id].last_error)
        for task in TASKS
        if task.id in worker_state.tasks and worker_state.tasks[task.id].last_error
    ]
    if failing:
        first_id, first_error = failing[0]
        results.append(
            CheckResult(
                "jobs", "activity", WARNING, "failing",
                {"n": str(len(failing)), "tasks": ",".join(task for task, _ in failing), "reason": f"{str(first_error)[:120]}"},
                fix="#/schedule",
            )
        )
    else:
        results.append(CheckResult("jobs", "activity", OK, "fine"))
    return results


def _shoko(settings, shoko_mode: bool) -> list[CheckResult]:
    if not shoko_mode and not settings.shoko_configured:
        return [CheckResult("shoko", "services", SKIPPED, "not_used")]
    if not settings.shoko_configured:
        return [CheckResult("shoko", "services", ERROR, "not_configured", fix="#/shoko")]
    from backend.app.services import shoko_status
    from backend.app.services.integration_state import remembered_shoko

    # The sidebar's "Shoko Server connected" is the worker's own periodic
    # check, and it is the better witness (build 237): the library list below
    # is only filled by a slower pass, so after a restart it was empty and this
    # read "did not answer" while Shoko was plainly connected - and stayed that
    # way however often the Shoko page loaded.
    _holdings, _by_title, answered = remembered_shoko()
    if answered or shoko_status.status().state == "ok":
        return [CheckResult("shoko", "services", OK, "answered")]
    return [CheckResult("shoko", "services", WARNING, "not_answered", fix="#/shoko")]


def _stuck_downloads(db: Session, basic: bool) -> list[CheckResult]:
    """Blocked or failed downloads - the ones the In Progress page shows (build 237).

    This used to count every download row ever marked ATTENTION or FAILED, so
    a failed attempt that was later replaced, or one whose episode had been
    archived, kept the warning up with nothing on the page to open: *"15
    downloads are blocked ... See In Progress"* - and In Progress was empty.
    It now asks the same list the page is built from, and names the first few.
    """
    if basic:
        return []
    from backend.app.core.mode import is_shoko_mode
    from backend.app.models.download import DownloadStatus
    from backend.app.services.progress import list_in_progress

    blocked = []
    for item in list_in_progress(db, shoko_mode=is_shoko_mode()):
        download = item.episode.download
        if download is not None and download.status in (DownloadStatus.ATTENTION, DownloadStatus.FAILED):
            reason = download.attention_reason or download.error or download.status.value.lower()
            blocked.append(f"{item.anime.display_name} {item.episode.number} ({reason})")
    if blocked:
        shown = "; ".join(blocked[:5])
        if len(blocked) > 5:
            shown += f"; +{len(blocked) - 5}"
        return [CheckResult("downloads", "activity", WARNING, "stuck", {"n": str(len(blocked)), "list": shown}, fix="#/progress")]
    return [CheckResult("downloads", "activity", OK, "none")]


# -- the live ones: they ask somebody else, so a person has to press the button -----------------------------------


def _metadata_source(settings) -> list[CheckResult]:
    """Does the metadata provider answer? One small request."""
    import httpx

    from backend.app.core.version import user_agent
    from backend.app.services.outbound import client_kwargs

    provider = (getattr(settings, "metadata_provider", "") or "anilist").lower()
    if provider == "kitsu":
        name, url, request = "Kitsu", "https://kitsu.io/api/edge/anime?page%5Blimit%5D=1", ("GET", None)
    else:
        name, url = "AniList", "https://graphql.anilist.co"
        request = ("POST", {"query": "{ Media(id: 1) { id } }"})
    try:
        with httpx.Client(timeout=6, headers={"User-Agent": user_agent()}, **client_kwargs(url)) as client:
            response = client.request(request[0], url, json=request[1])
        if response.status_code == 429:
            return [CheckResult("metadata_source", "data", WARNING, "busy", {"name": name}, live=True)]
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - the answer is the finding
        return [CheckResult("metadata_source", "data", WARNING, "unreachable", {"name": name, "reason": str(exc)[:120]}, live=True)]
    return [CheckResult("metadata_source", "data", OK, "reachable", {"name": name}, live=True)]


def _indexers_live(db: Session, basic: bool) -> list[CheckResult]:
    """Ask each enabled indexer's feed once, in turn, spaced - and reuse the answer for two minutes."""
    global _live_indexers
    if basic:
        return []
    if _live_indexers is not None and time.monotonic() - _live_indexers[0] < LIVE_REUSE_SECONDS:
        return list(_live_indexers[1])

    from backend.app.integrations.rss.base import FeedError, fetch_feed
    from backend.app.models.indexer import Indexer
    from backend.app.services.indexer_definitions import load_definitions

    definitions = {definition.source_file.rsplit(".", 1)[0]: definition for definition in load_definitions()}
    results: list[CheckResult] = []
    for position, indexer in enumerate(db.scalars(select(Indexer).where(Indexer.enabled.is_(True)).order_by(Indexer.id))):
        definition = definitions.get(indexer.config_slug or "")
        feed = definition.resolve_rss(indexer.url) if definition is not None else None
        base = {"name": indexer.name}
        subject = str(indexer.id)
        if feed is None:
            results.append(CheckResult("indexer_live", "setup", SKIPPED, "no_feed", base, subject=subject, live=True))
            continue
        if position:
            time.sleep(LIVE_SPACING_SECONDS)
        try:
            items = fetch_feed(feed, timeout=8)
        except FeedError as exc:
            results.append(
                CheckResult("indexer_live", "setup", WARNING, "unreachable", {**base, "reason": str(exc)[:120]}, fix="#/indexers", subject=subject, live=True)
            )
            continue
        results.append(CheckResult("indexer_live", "setup", OK, "answers", {**base, "n": str(len(items))}, subject=subject, live=True))
    _live_indexers = (time.monotonic(), results)
    return list(results)


# -- running them ------------------------------------------------------------------------------------------------


def run_checks(db: Session, *, live: bool = False) -> list[CheckResult]:
    """Every check, in the order the page shows them. Never raises.

    ``live`` adds the checks that ask somebody else over the network. The background refresh of the sidebar
    number leaves them out; the page's own button asks for them.
    """
    from backend.app.core.mode import AppMode, current_mode
    from backend.app.services.app_mode import AppMode as AppScope
    from backend.app.services.app_mode import current_mode as app_scope
    from backend.app.services.app_settings import load_app_settings

    settings = load_app_settings(db)
    shoko_mode = current_mode() is AppMode.SHOKO_SERVER
    basic = app_scope(db) is AppScope.BASIC

    steps = [
        ("torrent_client", lambda: _torrent_client(db, settings, basic)),
        ("indexers", lambda: _indexers(db, basic)),
        ("download_folder", lambda: _download_folder(basic)),
        ("libraries", lambda: _libraries(db, shoko_mode)),
        ("import_folder", lambda: _import_folder(db, shoko_mode)),
        ("hardlink", lambda: _hardlink(db, settings, basic, shoko_mode)),
        ("own_folders", _own_folders),
        ("metadata", _metadata),
        ("calendar_age", lambda: _calendar_age(settings)),
        ("database", _database),
        ("shoko", lambda: _shoko(settings, shoko_mode)),
        ("downloads", lambda: _stuck_downloads(db, basic)),
        ("jobs", _jobs),
    ]
    if live:
        steps[1:1] = [("indexers_live", lambda: _indexers_live(db, basic))]
        steps.append(("metadata_source", lambda: _metadata_source(settings)))
    results: list[CheckResult] = []
    for name, step in steps:
        try:
            results.extend(step())
        except Exception as exc:  # noqa: BLE001 - one broken check must not take the page down
            logger.warning("the %s check could not run: %s", name, exc, exc_info=True)
            results.append(CheckResult(name, "setup", WARNING, "could_not_run", {"reason": f"{type(exc).__name__}: {exc}"[:200]}))
    return results


def summarize(results: list[CheckResult]) -> dict[str, object]:
    return {
        "errors": sum(1 for item in results if item.state == ERROR),
        "warnings": sum(1 for item in results if item.state == WARNING),
        "ran_at": datetime.now(UTC).isoformat(),
    }
