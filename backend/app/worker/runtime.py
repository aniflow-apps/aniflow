"""Background worker: lifecycle plus every scheduled pass.

Jobs run here - never inside HTTP handlers. The API only flips a task's
``requested`` flag; the loop picks it up on its next tick. What runs, how
often, and under which setting is declared once in ``TASKS`` below; the loop
itself does not know what any individual job does.
"""
import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.db.session import SessionLocal
from backend.app.services import schedule_state
from backend.app.services.rss_scan import summarize
from backend.app.worker.tasks import TaskDefinition, TaskRuntimeState

logger = for_component("Worker")

_TICK_SECONDS = 5.0
# Delay the automatic post-boot scan slightly so slow storage/migrations
# never compete with application startup, and short-lived processes
# (tests) do not trigger network scans at all.
_STARTUP_DELAY_SECONDS = 30.0


@dataclass
class WorkerState:
    running: bool = False
    #: One entry per ``TaskDefinition.id``, populated once ``TASKS`` exists
    #: (see the bottom of this module).
    tasks: dict[str, TaskRuntimeState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def request_run(self, task_id: str) -> bool:
        """Queue a manual run. Returns False while one is already in flight."""
        with self._lock:
            state = self.tasks.get(task_id)
            if state is None or state.running or state.requested:
                return False
            state.requested = True
            return True


worker_state = WorkerState()
_task: asyncio.Task[None] | None = None


# --------------------------------------------------------------------------
# Job bodies. Each returns a plain dict describing what happened (or raises,
# which the generic wrapper below turns into ``{"error": ...}``) - none of
# them touch ``worker_state`` directly any more, so there is exactly one
# place that decides what "running" or "last result" means.
# --------------------------------------------------------------------------

def _run_scan_job():
    """Execute one full RSS scan synchronously (runs inside a worker thread)."""
    from backend.app.services.rss_scan import run_rss_scan

    return run_rss_scan(SessionLocal)


def _run_search_job() -> dict[str, object]:
    """Execute one search for all anime synchronously."""
    from dataclasses import asdict

    from backend.app.services.discovery import search_for_all_anime

    return asdict(search_for_all_anime(SessionLocal))


def _run_queue_job() -> dict[str, object]:
    """Queue MATCHED releases, then whatever automated anime have available.

    And first, hand over anything that was written down and never accepted by
    the client. That is a retry rather than a new attempt: an episode with a
    download row is skipped by the automated pass, so without this the four
    episodes the client refused on a bad afternoon stayed refused for ever.
    """
    from backend.app.services.downloads import (
        queue_automated_anime,
        queue_pending_downloads,
        retry_missing_downloads,
        retry_stranded_downloads,
        summarize_queue,
    )

    session = SessionLocal()
    try:
        stranded = retry_stranded_downloads(session)
        # A torrent AniFlow believes has vanished, tried again - the promise
        # its own record makes ("AniFlow will hand this to your download
        # client again"), which nothing was actually doing (166): stranded
        # only ever covered a row that never got a hash in the first place.
        missing = retry_missing_downloads(session)
    finally:
        session.close()

    summary = summarize_queue(queue_pending_downloads(SessionLocal))
    if stranded["handed"]:
        summary["recovered"] = stranded["handed"]
    if missing["handed"]:
        summary["recoveredMissing"] = missing["handed"]
    automated = queue_automated_anime(SessionLocal)
    if automated["queued"]:
        summary["automated"] = automated["queued"]
        # Named, so the log line says which anime rather than a bare count.
        summary["changes"] = list(summary.get("changes") or []) + list(automated["changes"])
    return summary


def _run_sync_job() -> dict[str, object]:
    """Refresh download states from the torrent client, and read the stalls.

    The stall clock is kept by the sync itself - it is the only pass that sees
    what the client reports - and the record is raised here, once the clock
    has run long enough. See services/downloads.report_stalled_downloads.
    """
    from backend.app.services.downloads import (
        report_stalled_downloads,
        summarize_sync,
        sync_download_states,
    )

    summary = sync_download_states(SessionLocal)
    session = SessionLocal()
    try:
        stalled = report_stalled_downloads(session)
    finally:
        session.close()
    reported = summarize_sync(summary)
    if stalled:
        reported["stalled"] = stalled
    return reported


def _run_import_job() -> dict[str, object]:
    """Import completed downloads, then reconcile the library with the disk.

    The reconcile pass is what turns a COMPLETE episode into MISSING after the
    user deletes a file outside AniFlow, so the episode can be offered again.
    It only stats episodes that actually recorded a media file.

    In Shoko Server mode a third step runs in between: files handed over on an
    earlier pass are looked for in the library, because that is the only way
    AniFlow learns where Shoko put them. It runs on the same task as the sync
    pass - Shoko usually takes seconds to minutes, so the next run is soon
    enough and nothing needs its own schedule.
    """
    from backend.app.core.mode import is_shoko_mode
    from backend.app.services.importer import reconcile_media, summarize_import

    summary = summarize_import(_import_summary())
    if is_shoko_mode():
        from backend.app.services.shoko import resolve_pending

        summary["shoko"] = resolve_pending(SessionLocal)
    summary["reconciled"] = reconcile_media(SessionLocal)
    # An import is how the last episode of a show arrives, so this is where
    # "12 / 12" becomes Complete (build 188).
    from backend.app.services.anime import trim_surplus_in
    from backend.app.services.anime_season import fill_missing_in
    from backend.app.services.lifecycle_auto import complete_finished_in, correct_upcoming_in

    # A future entry is Upcoming, not Active (build 205).
    summary["upcoming_corrected"] = correct_upcoming_in(SessionLocal)
    # Shows that have not aired may be moved by the providers: look again (build 205).
    from backend.app.services.anime_season import refresh_unaired_in
    from backend.app.services.season_rollover import run_in as rollover_in

    summary["seasons_refreshed"] = refresh_unaired_in(SessionLocal)
    # The season the calendar places each new anime in (build 190).
    summary["seasons_filled"] = fill_missing_in(SessionLocal)
    # Counts corrected by hand below the rows that exist (build 190).
    summary["trimmed_rows"] = trim_surplus_in(SessionLocal)
    summary["completed_shows"] = complete_finished_in(SessionLocal)
    # Upcoming -> the current season, and in automatic mode the rest (build 205).
    summary["rollover"] = rollover_in(SessionLocal)
    # And two files for one episode: a v2 next to its v1, say (build 189).
    from backend.app.services.duplicate_files import review_in

    summary["duplicates"] = review_in(SessionLocal)
    return summary


def _import_summary():
    from backend.app.services.importer import run_import

    return run_import(SessionLocal)


def _run_anidb_identity_job() -> dict[str, object]:
    """Keep the AniDB title dump current, and give new anime their AniDB id.

    Both halves decide for themselves whether there is anything to do: the
    download returns immediately unless the file on disk is a week old, and
    the backfill only looks at anime that have no AniDB id yet. That "at most
    weekly" cap lives inside the download itself and stays there even though
    this task now has its own configurable check cadence - a ban is
    installation-wide and permanent-feeling, so nothing this schedule does
    should be able to ask for the dump more often than that, however low
    somebody sets the interval.
    """
    from backend.app.services import anidb_identity, anidb_titles, id_mapping

    status = anidb_titles.download()
    # The offline id-mapping list, on the same weekly footing (build 154).
    # Returns at once unless the list is a week old.
    id_mapping.refresh()
    session = SessionLocal()
    try:
        # Once per installation (build 156): undo what series-wide ids linked
        # before build 155 stopped treating them as identities. After the
        # mapping refresh on purpose - the list is how the right id of two is
        # told from the wrong one.
        from backend.app.services import identity_repair

        try:
            identity_repair.run_once(session)
        except Exception:  # noqa: BLE001 - a repair must never stop the job
            session.rollback()
            logger.warning("identity repair failed", exc_info=True)
        summary = anidb_identity.backfill(session)
        session.commit()
    finally:
        session.close()
    result: dict[str, object] = {
        "titles": status.title_count,
        "resolved": summary.resolved,
        "unmatched": summary.ambiguous_or_missing,
    }
    # Only present on a real failure (build 160): this key used to be set
    # unconditionally to ``status.last_error``, which is ``None`` on a clean
    # run - but runtime.py's own bookkeeping below treats *any* dict with an
    # "error" key as a failure, then stringifies it. `str(None)` is the
    # literal text "None", which is truthy, so the Schedule page's dot read
    # this task as permanently failed even when nothing had ever gone wrong,
    # and no manual run could clear it because a clean run hit the exact same
    # path. Reported as "the AniDB dot stays yellow forever."
    if status.last_error:
        result["error"] = status.last_error
    return result


def _run_attention_review_job() -> dict[str, object]:
    """Close the warnings whose reason has gone away (build 94).

    Asked for: the attention list should check itself rather than keep
    reporting something the user has already dealt with by hand. See
    services/attention_review.py.
    """
    from dataclasses import asdict

    from backend.app.services.attention_review import review_open_attentions
    from backend.app.services.duplicate_files import review_in

    result = asdict(review_open_attentions(SessionLocal))
    # Two files for one episode, raised or closed (build 189): this is also
    # what catches a copy deleted by hand.
    result["duplicates"] = review_in(SessionLocal)
    return result


def _run_sweep_job() -> dict[str, object]:
    """Settle any torrent the import pass never got to (build 94).

    Reported as "there are still sometimes torrents which are not stopped".
    The after-import action is applied when the file is placed, and every step
    of that can be missed once - an unreachable client, a restart in between,
    or Shoko taking the file before AniFlow's importer ran at all. This asks
    the question again from the client's end. See services/torrent_sweep.py.

    Called two ways since build 160: inline, right after `torrent_monitor`
    imports something (unchanged - see `_run_torrent_monitor_task`), and as
    the manual-only half of `_run_housekeeping_job` below. Never on its own
    automatic timer any more - settling late is worth doing right after an
    import or when somebody asks, not worth polling qBittorrent for on a
    schedule of its own.
    """
    from dataclasses import asdict

    from backend.app.services.torrent_sweep import sweep_settled_torrents

    return asdict(sweep_settled_torrents(SessionLocal))


def _run_reconcile_job() -> dict[str, object]:
    """The "what actually happened" pass. Folded into Housekeeping (build 160)."""
    from backend.app.services.reconcile import reconcile_downloads
    from backend.app.services.reconcile import summarize as summarize_reconcile

    return summarize_reconcile(reconcile_downloads(SessionLocal))


def _run_orphan_job() -> dict:
    """Adopt finished torrents AniFlow has no download row for.

    Reported as five episodes that stayed missing with their files already
    downloaded: the import only ever places files it has a row for, and the
    rows were gone. Folded into Housekeeping (build 160).
    """
    from backend.app.services.downloads import adopt_orphan_torrents

    session = SessionLocal()
    try:
        found = adopt_orphan_torrents(session)
    finally:
        session.close()
    if found.get("adopted"):
        # Something new to place, so ask for the import rather than waiting
        # for its own next run.
        worker_state.request_run("torrent_monitor")
    return found


def _run_housekeeping_job() -> dict[str, object]:
    """Everything that tidies up downloads against the torrent client (build 160).

    Three passes that used to be three separate rows on the Schedule page,
    combined at the user's request: adopting torrents nothing knows about,
    reconciling what actually happened to an unsettled download, and settling
    one the import step never got to. The first two always run on this task's
    own timer. The third - sweep - does not: it already runs inline right
    after `torrent_monitor` imports something, and polling qBittorrent for
    leftover after-import actions on a *second* schedule besides was judged
    not worth it. So sweep only runs here when a person presses "Run" on this
    row by hand - `worker_state.tasks["torrent_housekeeping"].last_manual`,
    set by the tick loop right before this job is called.
    """
    result: dict[str, object] = {
        "orphan_adoption": _run_orphan_job(),
        "reconcile": _run_reconcile_job(),
    }
    if worker_state.tasks["torrent_housekeeping"].last_manual:
        result["sweep"] = _run_sweep_job()
    return result


def _has_active_downloads() -> bool:
    """Whether qBittorrent is worth asking about right now.

    Used only to decide how soon the *next* torrent-status sync should be -
    see `_torrent_monitor_interval` below. Never lets a bad query stop the
    loop: "yes" is the safe wrong answer, since it just means checking again
    sooner than strictly needed.
    """
    from sqlalchemy import select

    from backend.app.models.download import DownloadStatus, TorrentDownload

    session = SessionLocal()
    try:
        active = session.execute(
            select(TorrentDownload.id)
            .where(TorrentDownload.status.in_([DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING]))
            .limit(1)
        ).first()
        return active is not None
    except Exception:  # noqa: BLE001 - never let this stop the loop
        return True
    finally:
        session.close()


def _torrent_monitor_interval(cfg) -> float:
    """5 seconds while something is actually downloading, the configured
    interval otherwise (build 160, at the user's request: reacting fast
    matters while a torrent is moving, and does not while the client has
    nothing to report - the configured value is what an idle pass still
    costs to poll at, and is left in the user's hands)."""
    if _has_active_downloads():
        return 5.0
    return float(cfg.torrent_monitor_interval_seconds)


def _run_disk_recheck_job() -> dict[str, object]:
    """Check every linked media folder against the database (build 160).

    The same reconciliation `reconcile` does for an in-flight download, but
    for the whole library on a slow timer instead: a file touched by anything
    other than AniFlow's own download pipeline - moved, renamed, deleted by
    hand, replaced by an external tool - has no download row for `reconcile`
    to look at and would otherwise sit unnoticed until somebody opened that
    anime's page and pressed Rescan themselves. Reuses exactly what Rescan
    already does (`library.scan_anime_folder`), one anime at a time, so
    nothing about what "the folder is authoritative" means is duplicated.

    Every anime is given a folder name at creation (build 149's own name, if
    nothing has linked one explicitly), and most of those folders do not exist
    yet - a future show, one not downloaded yet. That is normal and not this
    job's business, so a folder that simply is not there yet is skipped
    quietly rather than counted as a problem. Only a name that resolves
    *outside* the configured media root - a real misconfiguration - is worth
    naming; a NAS path that lists but will not open would raise the same way
    `scan_anime_folder` always has, and must never stop every other anime from
    being checked alongside it.
    """
    from backend.app.db.session import SessionLocal as _SessionLocal
    from backend.app.models.anime import Anime
    from backend.app.services.library import scan_anime_folder
    from backend.app.services.storage import media_folder_path, roots_for, storage_roots

    session = _SessionLocal()
    completed = 0
    vanished = 0
    created = 0
    checked = 0
    unreadable: list[str] = []
    try:
        roots = storage_roots()
        for anime in session.query(Anime).all():
            name = anime.media_folder or anime.local_name
            folder = media_folder_path(roots_for(anime, roots), name)
            if folder is None:
                unreadable.append(anime.display_name or anime.local_name or anime.id)
                logger.warning(
                    "disk recheck: %s: folder name %r escapes the configured media root",
                    anime.id, name,
                )
                continue
            if not folder.is_dir():
                continue  # nothing downloaded yet - not a problem, nothing to check
            try:
                outcome = scan_anime_folder(session, anime)
            except FileNotFoundError as exc:
                unreadable.append(anime.display_name or anime.local_name or anime.id)
                logger.warning("disk recheck: %s: %s", anime.id, exc)
                continue
            checked += 1
            completed += int(outcome.get("completed") or 0)
            vanished += int(outcome.get("vanished") or 0)
            created += int(outcome.get("created") or 0)
    finally:
        session.close()
    from backend.app.services.lifecycle_auto import complete_finished_in

    result: dict[str, object] = {
        "checked": checked,
        "completed": completed,
        "vanished": vanished,
        "created": created,
        # A file added by hand can be the last one too (build 188).
        "completed_shows": complete_finished_in(_SessionLocal),
    }
    if unreadable:
        result["unreadable"] = unreadable
    return result


def _run_shoko_adopt_job() -> dict:
    """Adopt what Shoko holds and the watchlist does not, and fill entries in.

    Bounded per pass: a first run against a library of hundreds should not
    hold the worker while it walks them, and what is left is taken by the
    next pass. The button on the watchlist has no limit - someone waiting for
    an answer is a different situation from a background pass.
    """
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services.shoko_adopt import adopt_all

    session = SessionLocal()
    try:
        # Nothing to adopt from a Shoko that is not configured, and asking
        # costs a session, a commit and a failed connection every pass on
        # every deployment that does not use one. Checked here rather than
        # inside the service, which is also called from a button somebody
        # pressed - and there, "it is not configured" is worth saying.
        settings = load_app_settings(session)
        if not (settings.shoko_url and settings.shoko_api_key):
            return {"considered": 0, "adopted": 0, "enriched": 0, "skipped": 0,
                    "detail": "no Shoko Server configured"}
        summary = adopt_all(session)
    finally:
        session.close()
    return {
        "considered": summary.considered,
        "adopted": summary.adopted,
        "enriched": summary.enriched,
        "skipped": summary.skipped,
        "detail": summary.detail,
    }


async def _run_watchlist_repair_task() -> dict[str, object]:
    """Go through every anime and fill in whatever is missing (build 194)."""
    from backend.app.services.repair import repair_watchlist

    return await repair_watchlist(SessionLocal)


def _run_conversion_review() -> dict:
    """Flag anime that were added without an indexer and now need converting.

    A periodic pass rather than a one-off at the moment the mode changes: the
    mode follows the indexer *files*, which can appear by someone dropping a
    YAML into a folder, and nothing announces that.
    """
    from backend.app.db.session import SessionLocal as _SessionLocal
    from backend.app.services.app_mode import review_conversions

    session = _SessionLocal()
    try:
        waiting = review_conversions(session)
        session.commit()
    finally:
        session.close()
    return {"waiting": waiting}


# --------------------------------------------------------------------------
# Async task bodies - the ones that were previously "wrappers" mutating
# ``worker_state`` fields directly now just return what happened, same as
# every sync job above.
# --------------------------------------------------------------------------

async def _run_rss_scan_task() -> dict[str, object]:
    """Scan every feed, then queue whatever it matched.

    The queue step used to run inline here; it now runs as its own task
    (``queue_downloads``), asked for by id so the two stay one code path
    whether a scan triggers it or the Downloads page's own button does.
    """
    summary = await asyncio.to_thread(_run_scan_job)
    logger.info("rss scan finished: %s", summary)
    worker_state.request_run("queue_downloads")
    return summarize(summary)


async def _run_search_task() -> dict[str, object]:
    summary = await asyncio.to_thread(_run_search_job)
    logger.info("search finished: %s", summary)
    return summary


async def _run_torrent_monitor_task() -> dict[str, object]:
    """Sync qBittorrent, import whatever finished, settle it if so (94/115/117/119).

    Each step keeps its own "%s: %s" log line via ``_safe_job`` - unchanged
    from before this task had a registry entry. Sweep only runs inline here
    when something was actually imported; the ``torrent_sweep`` task is the
    net for everything this misses.
    """
    result: dict[str, object] = {}
    sync_summary = await asyncio.to_thread(_safe_job, "download sync", _run_sync_job)
    result["sync"] = sync_summary
    if isinstance(sync_summary, dict) and "error" in sync_summary:
        return result
    import_summary = await asyncio.to_thread(_safe_job, "library import", _run_import_job)
    result["import"] = import_summary
    if isinstance(import_summary, dict) and import_summary.get("imported"):
        # Straight after the import that may have needed it, rather than
        # waiting for the next torrent_sweep run (build 115). The per-row
        # settle runs inside the import; this is the second ask, from the
        # client's end, and the moment it is worth asking is the moment
        # something was just imported.
        result["sweep"] = await asyncio.to_thread(_safe_job, "torrent sweep", _run_sweep_job)
    return result


async def _run_metadata_backfill_task() -> dict[str, object]:
    """Fill in airing schedules, then metadata, a few anime at a time each.

    One task, not two (build 159, agreed with the user: two near-identical
    "Watchlist: filling in X" rows read as more confusing than useful, and
    they always ran back to back anyway). Both steps do nothing at all once
    every anime has what they ask for, and a handful of AniList/Kitsu lookups
    per pass converges within minutes of a first start - or of a library just
    adopted from Shoko, where there is the most to fill in - without ever
    looking like a burst of traffic.
    """
    from backend.app.services.metadata_fill import refill_missing
    from backend.app.services.schedule_backfill import refresh_schedules

    schedules = await refresh_schedules(SessionLocal)
    if schedules.get("updated"):
        logger.info("airing schedule filled in for %s anime", schedules["updated"])
    metadata = await refill_missing(SessionLocal)
    if metadata.get("filled"):
        logger.info("metadata filled in for %s anime", metadata["filled"])
    return {"schedules": schedules, "metadata": metadata}


async def _run_metadata_status_task() -> dict[str, object]:
    """Notice a metadata provider coming back, or going away.

    ``metadata.refresh()`` keeps its own internal rate limit besides this
    task's interval - a deliberate second gate, the same reasoning as the
    AniDB dump's: whatever this schedule is set to, the provider itself is
    never asked more often than that limit allows.
    """
    from backend.app.services import metadata

    before = metadata.status().state
    after = (await metadata.refresh()).state
    changed = after != before
    if changed:
        logger.info("metadata availability changed: %s -> %s", before, after)
    return {"state": after, "changed": changed}


async def _run_shoko_status_task() -> dict[str, object]:
    """Notice Shoko Server coming back, or going away."""
    from backend.app.services import shoko_status

    before = shoko_status.status().state
    after = (await asyncio.to_thread(shoko_status.check)).state
    changed = after != before and after != "disabled"
    if changed:
        logger.info("Shoko Server availability changed: %s -> %s", before, after)
    return {"state": after, "changed": changed}


async def _run_calendar_auto_update_task() -> dict[str, object]:
    """Keep the current, the next two and the previous season up to date, once a week.

    Whether this runs at all is the registry's job (``calendar_auto_update``
    is this task's enabled flag). Changed at build 160, at the user's request:
    this used to check hourly whether the current season alone had gone a day
    stale. Checking that often for one season read as solving a problem this
    task does not actually have - at a weekly cadence there is no "is it
    stale yet" question worth asking any more, so this now simply runs the
    full ``upcoming`` window (current season + the next two, the same as the
    button's "Update upcoming") every time it is due, unconditionally.
    """
    from backend.app.db import calendar_store
    from backend.app.services import calendar_jobs
    from backend.app.services.calendar import resolve_season, update_window

    if calendar_jobs.status().running or calendar_store.blocked_reason():
        return {"skipped": "already running or blocked"}
    year, season = resolve_season(None, None)
    # "automatic", not "update": a person watching the calendar should be
    # able to tell an update they started from one that started itself.
    calendar_jobs.start(update_window("upcoming", year, season), kind="automatic")
    return {"started": True, "year": year, "season": season}


# Counters that mean something actually happened. "checked" is deliberately
# absent: looking at rows is not work worth a log line.
_WORK_COUNTERS = frozenset(
    {
        "updated", "completed", "attention", "imported", "waiting",
        "queued", "failed", "skipped", "missing", "restored",
        "episodes_linked",
    }
)


def _did_something(result: object) -> bool:
    """Whether a job result describes real work rather than an idle pass.

    Every cycle used to log its full summary, so an idle instance produced two
    identical lines a minute forever. Idle passes are DEBUG now; anything that
    changed state still logs at INFO.
    """
    if not isinstance(result, dict):
        return True
    if "error" in result:
        return True
    total = 0
    for key, value in result.items():
        if isinstance(value, dict):  # nested summaries, e.g. "reconciled"
            total += sum(
                inner for name, inner in value.items()
                if name in _WORK_COUNTERS and isinstance(inner, int)
            )
        elif key in _WORK_COUNTERS and isinstance(value, int):
            total += value
    return total > 0


def _readable(result: object) -> str:
    """Prefer the named changes over bare counters, so a line is legible."""
    if isinstance(result, dict):
        changes = result.get("changes")
        if isinstance(changes, list) and changes:
            return "; ".join(str(change) for change in changes)
    return str(result)


def _safe_job(label: str, job) -> dict[str, object] | None:
    try:
        result = job()
        if _did_something(result):
            logger.info("%s: %s", label, _readable(result))
        else:
            logger.debug("%s: nothing to do (%s)", label, result)
        return result
    except Exception as exc:  # noqa: BLE001 - the worker must survive any job failure
        logger.exception("%s failed", label)
        return {"error": str(exc)}


async def _safe_job_async(label: str, job) -> dict[str, object] | None:
    try:
        result = await job()
        if _did_something(result):
            logger.info("%s: %s", label, _readable(result))
        else:
            logger.debug("%s: nothing to do (%s)", label, result)
        return result
    except Exception as exc:  # noqa: BLE001 - the worker must survive any job failure
        logger.exception("%s failed", label)
        return {"error": str(exc)}


def _read_settings():
    """The effective settings the loop should honour on this tick.

    Falls back to the environment layer alone if the file cannot be read, so
    a broken config never stops every scheduled task at once. Both objects
    declare every field a ``TaskDefinition`` might name, so ``getattr`` works
    against either.
    """
    from backend.app.services.app_settings import load_app_settings

    session = SessionLocal()
    try:
        return load_app_settings(session)
    except Exception:  # noqa: BLE001 - fall back to environment defaults
        return get_settings()
    finally:
        session.close()


def _read_has_indexer() -> bool:
    """Whether anything is configured that could actually find a release.

    The same test that decides Basic vs Advanced mode (see
    services/app_mode.py) - reused here so RSS/search/queue tasks stop
    reporting themselves as active every tick in an install that can never
    have anything for them to do.
    """
    from backend.app.services.app_mode import has_indexer

    session = SessionLocal()
    try:
        return has_indexer(session)
    except Exception:  # noqa: BLE001 - never let this stop the loop
        return True
    finally:
        session.close()


def _next_run_after(task: TaskDefinition, cfg, last_run_at: datetime) -> datetime:
    """When ``task`` is next due, given it last ran at ``last_run_at``.

    The one place this is computed, used after a run finishes, when the
    worker boots and finds a persisted last run to build on, and when a
    setting change asks for an immediate reschedule (``schedule_state``,
    this module's own ``_run``, and ``reschedule_changed_tasks`` respectively).
    Recomputed from the *current* config every time rather than persisted
    itself, so whichever of those last touched a task is using its latest
    interval, not a stale one.
    """
    if task.dynamic_interval is not None:
        interval = task.dynamic_interval(cfg)
    else:
        interval = getattr(cfg, task.interval_setting)
        if task.interval_unit == "minutes":
            interval *= 60
    return last_run_at + timedelta(seconds=interval)


def _task_applicable(task: TaskDefinition, cfg, has_indexer_flag: bool) -> bool:
    """Whether ``task`` could do anything at all right now, mode aside."""
    if task.requires == "indexer":
        return has_indexer_flag
    if task.requires == "qbittorrent":
        return cfg.qb_configured
    if task.requires == "shoko":
        return cfg.shoko_configured
    return True


# --------------------------------------------------------------------------
# The registry itself. Declared after the job bodies above so it can
# reference them directly - see worker/tasks.py for what each field means.
# Order matters for two things: within one tick, tasks run in this order
# (so, e.g., reconcile settles before the passes that build on what it
# found), and a task placed right after another can be triggered by it
# within the same tick (queue_downloads after rss_scan).
# --------------------------------------------------------------------------

TASKS: tuple[TaskDefinition, ...] = (
    TaskDefinition(
        id="rss_scan", category="feeds", job=_run_rss_scan_task, is_async=True,
        interval_setting="rss_scan_interval_minutes", interval_unit="minutes",
        enabled_setting="rss_enabled", startup_delay_seconds=_STARTUP_DELAY_SECONDS,
        requires="indexer",
    ),
    TaskDefinition(
        id="queue_downloads", category="feeds", job=_run_queue_job, requires="indexer",
    ),
    TaskDefinition(
        id="torrent_monitor", category="downloads", job=_run_torrent_monitor_task, is_async=True,
        interval_setting="torrent_monitor_interval_seconds",
        enabled_setting="torrent_monitor_enabled",
        startup_delay_seconds=_STARTUP_DELAY_SECONDS / 6,
        requires="qbittorrent",
        dynamic_interval=_torrent_monitor_interval,
    ),
    TaskDefinition(
        id="anime_search", category="downloads", job=_run_search_task, is_async=True,
        enabled_setting="search_enabled", manual_bypasses_enabled=False, requires="indexer",
        timeout_seconds=7200.0,
    ),
    TaskDefinition(
        id="torrent_housekeeping", category="downloads", job=_run_housekeeping_job,
        interval_setting="torrent_housekeeping_interval_seconds",
        enabled_setting="torrent_housekeeping_enabled",
        startup_delay_seconds=_STARTUP_DELAY_SECONDS, requires="qbittorrent",
    ),
    TaskDefinition(
        id="anidb_identity_refresh", category="identity", job=_run_anidb_identity_job,
        interval_setting="anidb_identity_refresh_interval_seconds",
        enabled_setting="anidb_identity_refresh_enabled",
        startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    ),
    TaskDefinition(
        # Manual only since build 160, at the user's request: adopting Shoko's
        # library is expensive enough to want a deliberate press, not a timer -
        # *"I would even pause this."* The button lives on the Shoko Server
        # page (`POST /api/shoko/adopt-all`, same underlying `adopt_all`); this
        # row keeps it reachable from the Schedule page too.
        id="shoko_adoption", category="shoko", job=_run_shoko_adopt_job,
        requires="shoko", timeout_seconds=7200.0,
    ),
    TaskDefinition(
        id="attention_review", category="watchlist", job=_run_attention_review_job,
        interval_setting="attention_review_interval_seconds", enabled_setting="attention_review_enabled",
        startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    ),
    TaskDefinition(
        id="shoko_status", category="shoko", job=_run_shoko_status_task, is_async=True,
        interval_setting="shoko_status_interval_seconds", enabled_setting="shoko_status_enabled",
        requires="shoko",
    ),
    TaskDefinition(
        # Manual only since build 160, at the user's request: this is a
        # "check when I ask" pass, not a background one.
        id="metadata_backfill", category="watchlist", job=_run_metadata_backfill_task, is_async=True,
    ),
    TaskDefinition(
        # Manual only since build 160, at the user's request - see
        # `metadata_backfill` above for the reasoning.
        id="metadata_status", category="watchlist", job=_run_metadata_status_task, is_async=True,
    ),
    TaskDefinition(
        # Manual only, like the other watchlist passes (build 194): a "fix
        # what is missing" button over every anime - covers, the files and
        # library from Shoko, metadata from the calendar and a provider,
        # episode rows. Never overwrites a value the user set.
        id="watchlist_repair", category="watchlist", job=_run_watchlist_repair_task, is_async=True,
        timeout_seconds=7200.0,
    ),
    TaskDefinition(
        id="calendar_auto_update", category="calendar", job=_run_calendar_auto_update_task, is_async=True,
        interval_setting="calendar_auto_update_interval_seconds", enabled_setting="calendar_auto_update",
        timeout_seconds=7200.0,
    ),
    TaskDefinition(
        id="conversion_review", category="watchlist", job=_run_conversion_review,
        interval_setting="conversion_review_interval_seconds", enabled_setting="conversion_review_enabled",
    ),
    TaskDefinition(
        id="disk_recheck", category="library", job=_run_disk_recheck_job,
        interval_setting="disk_recheck_interval_seconds", enabled_setting="disk_recheck_enabled",
        startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    ),
)

# Populated now that every task above exists. WorkerState.tasks starts empty
# so the dataclass itself does not need to know about TASKS.
worker_state.tasks.update({task.id: TaskRuntimeState() for task in TASKS})


def reschedule_changed_tasks(changed_fields: set[str]) -> None:
    """Recompute ``next_run_at`` right away for a task whose own interval or
    enabled field was just written (build 163, STATE.md Open item 9).

    Without this, the tick loop only ever recomputes ``next_run_at`` after a
    task actually runs (see ``_next_run_after`` and ``_run`` below) - so
    editing an *enabled* task's interval through the Schedule page's own
    editor, Settings, or Reset had no effect until whatever was already
    scheduled under the old interval fired once more. Lowering
    ``calendar_auto_update`` from a week to an hour would not actually have
    sped anything up for up to a week.

    Called from the settings routes right after a save, with the field names
    that save actually touched - never from ``_run`` itself, which already
    keeps every task's schedule current as part of its own tick.
    """
    if not changed_fields:
        return
    relevant = [
        task
        for task in TASKS
        if task.interval_setting is not None
        and (task.interval_setting in changed_fields or task.enabled_setting in changed_fields)
    ]
    if not relevant:
        return
    cfg = _read_settings()
    has_indexer_flag = _read_has_indexer()
    for task in relevant:
        state = worker_state.tasks[task.id]
        if state.running:
            continue  # its own completion will set next_run_at from the new config
        enabled = True if task.enabled_setting is None else bool(getattr(cfg, task.enabled_setting))
        enabled = enabled and _task_applicable(task, cfg, has_indexer_flag)
        if enabled and state.last_run_at is not None:
            state.next_run_at = _next_run_after(task, cfg, state.last_run_at)
        else:
            # Disabled, blocked, or never run yet - None is what "due
            # immediately once that stops being true" already means to the
            # tick loop (see the comment on the same line in `_run`).
            state.next_run_at = None


async def _run_with_limit(task: TaskDefinition) -> dict[str, object] | None:
    """Run one task, giving up on it after ``task.timeout_seconds`` (build 197)."""
    if task.is_async:
        work = _safe_job_async(task.id, task.job)
    else:
        work = asyncio.to_thread(_safe_job, task.id, task.job)
    limit = task.timeout_seconds
    if not limit:
        return await work
    try:
        return await asyncio.wait_for(work, limit)
    except TimeoutError:
        minutes = max(1, round(limit / 60))
        logger.error("%s did not finish within %s minutes and was stopped", task.id, minutes)
        return {"error": f"did not finish within {minutes} minutes and was stopped"}


async def _run() -> None:
    startup = datetime.now(UTC)
    startup_cfg = await asyncio.to_thread(_read_settings)
    startup_has_indexer = await asyncio.to_thread(_read_has_indexer)

    # Build 161: a redeploy used to lose every task's memory of when it last
    # ran, because that lived only in this process's own TaskRuntimeState - so
    # a weekly task started its week over on every restart, and an install
    # redeployed often enough never let one actually elapse. Restored from
    # `schedule_task_state` before anything else runs, and pruned of task ids
    # the registry no longer has (a merge or a rename, e.g. build 160's
    # Housekeeping).
    await asyncio.to_thread(schedule_state.prune, SessionLocal, [task.id for task in TASKS])
    persisted = await asyncio.to_thread(schedule_state.load_all, SessionLocal)

    for task in TASKS:
        row = persisted.get(task.id)
        state = worker_state.tasks[task.id]
        if row is not None:
            state.last_run_at = row.last_run_at
            state.last_result = row.last_result
            state.last_error = row.last_error
        if task.interval_setting is None:
            continue
        # A disabled task's next_run_at stays None rather than a stale
        # startup-time stamp that only grows more "overdue" forever - the
        # tick loop below treats None as due immediately once it is enabled,
        # which is what turning the switch on should do.
        enabled = True if task.enabled_setting is None else bool(getattr(startup_cfg, task.enabled_setting))
        if not (enabled and _task_applicable(task, startup_cfg, startup_has_indexer)):
            continue
        if row is not None and row.last_run_at is not None:
            # Picks up right where the interval says it should be - due
            # immediately if that moment has already passed, which is the
            # correct answer for "the process was down for a while", not a
            # bug to guard against.
            state.next_run_at = _next_run_after(task, startup_cfg, row.last_run_at)
        else:
            # Never run before (a fresh install, or a task id that did not
            # exist until this build) - the original behaviour: stagger the
            # very first run a little after boot.
            state.next_run_at = startup + timedelta(seconds=task.startup_delay_seconds)

    while worker_state.running:
        now = datetime.now(UTC)
        cfg = await asyncio.to_thread(_read_settings)
        has_indexer_flag = await asyncio.to_thread(_read_has_indexer)

        for task in TASKS:
            if not task.implemented:
                continue
            state = worker_state.tasks[task.id]
            if state.running:
                continue
            enabled = True if task.enabled_setting is None else bool(getattr(cfg, task.enabled_setting))
            enabled = enabled and _task_applicable(task, cfg, has_indexer_flag)
            manual_due = state.requested and (task.manual_bypasses_enabled or enabled)
            scheduled_due = (
                task.interval_setting is not None
                and enabled
                and now >= (state.next_run_at or now)
            )
            if not (manual_due or scheduled_due):
                continue

            state.requested = False
            state.running = True
            state.last_manual = manual_due
            try:
                result = await _run_with_limit(task)
            finally:
                state.running = False

            state.last_run_at = datetime.now(UTC)
            state.last_result = result if isinstance(result, dict) else {"result": result}
            # Truthy check, not "key present": a job that includes an "error"
            # key set to None (a clean run reporting its own status field
            # rather than a failure) must not read as one - see the comment
            # on `_run_anidb_identity_job`'s return value for the bug this
            # caused once already.
            error_value = result.get("error") if isinstance(result, dict) else None
            state.last_error = str(error_value) if error_value else None
            # Only a task that could actually fire on its own gets a real
            # next_run_at - otherwise a manual run of a disabled task would
            # leave the Schedule page claiming a next execution that will
            # never happen, since `enabled` still gates scheduled_due above.
            if task.interval_setting is not None and enabled:
                state.next_run_at = _next_run_after(task, cfg, state.last_run_at)
            elif task.interval_setting is not None:
                state.next_run_at = None

            # Survives this process, unlike everything else about `state` -
            # see schedule_state and the note at the top of this function.
            await asyncio.to_thread(
                schedule_state.save, SessionLocal, task.id,
                last_run_at=state.last_run_at,
                last_result=state.last_result,
                last_error=state.last_error,
            )

        await asyncio.sleep(_TICK_SECONDS)

    for state in worker_state.tasks.values():
        state.next_run_at = None


async def start_worker() -> None:
    global _task
    if worker_state.running:
        return
    worker_state.running = True
    _task = asyncio.create_task(_run(), name="aniflow-worker")
    logger.info("worker started")


async def stop_worker() -> None:
    global _task
    worker_state.running = False
    if _task is not None:
        _task.cancel()
        with suppress(asyncio.CancelledError):
            await _task
        _task = None
    for state in worker_state.tasks.values():
        state.running = False
        state.requested = False
        state.next_run_at = None
    logger.info("worker stopped")
