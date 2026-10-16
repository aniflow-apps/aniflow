"""The task registry: the shape of everything that runs on a timer.

One ``TaskDefinition`` per background job, naming the ``AppSettings`` fields
that hold its interval and its enabled flag (or ``None`` for a task with no
independent schedule - a manual-only action, or the one item nobody has built
yet). ``worker/runtime.py`` builds the actual ``TASKS`` tuple from these types
once its job functions exist, and its ``_run()`` loop iterates that tuple once
per tick - nothing here executes a job itself.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TaskDefinition:
    #: Stable, never renamed once shipped - it is also the settings-file key
    #: prefix and the Schedule page's row identity.
    id: str
    #: Groups rows on the Schedule page: feeds | downloads | shoko | watchlist
    #: | calendar | identity | library.
    category: str
    #: The callable that does the work. ``None`` only for a task that has not
    #: been built yet (see ``implemented``).
    job: Callable[[], object] | Callable[[], Awaitable[object]] | None
    #: Which ``AppSettings`` field holds the interval, or ``None`` for a task
    #: that only ever runs when asked (search, queueing).
    interval_setting: str | None = None
    #: The unit that field is stored in. Only ``rss_scan_interval_minutes``
    #: is minutes; every interval added since is seconds, matching
    #: ``torrent_monitor_interval_seconds``.
    interval_unit: str = "seconds"
    #: Which ``AppSettings`` field holds the on/off switch, or ``None`` for a
    #: task that is always eligible to run (it only has a manual trigger, or
    #: it is the AniDB dump refresh, which has always run unconditionally).
    enabled_setting: str | None = None
    #: The job is awaited directly rather than run in a worker thread.
    is_async: bool = False
    #: Offers a "Run now" trigger. Off for the handful that only ever piggy-
    #: backed on another job's tick and were never meant to be pressed by
    #: hand (metadata/shoko status checks, the watchlist upkeep passes).
    manual: bool = True
    #: A manual request still runs even while the task's own switch is off -
    #: matching how "Scan now" has always worked regardless of automatic RSS
    #: scanning. The one exception is search, which has always respected
    #: ``search_enabled`` even when pressed by hand.
    manual_bypasses_enabled: bool = True
    #: Not built yet (the disk re-check from STATE.md open item 5). The
    #: Schedule page still shows the row; the loop skips it entirely.
    implemented: bool = True
    #: What has to exist for this task to be able to do anything at all -
    #: "indexer" (nothing can be searched or matched without one), "qbittorrent"
    #: (nothing to sync, reconcile or settle without a client), or "shoko"
    #: (nothing to adopt or check without a configured Shoko Server). ``None``
    #: for a task that needs none of these. Distinct from ``enabled_setting``:
    #: this is a fact about the installation, not a switch anyone set - a
    #: Basic-mode install (no indexer) had every download/RSS task ticking
    #: and reporting green with nothing to do, which is what this exists to
    #: stop. See runtime.py's ``_run()`` and schedule.py's ``_view()``.
    requires: str | None = None
    #: How long after the worker starts before this task's first scheduled
    #: run is due - staggered so a cold start does not fire every task in the
    #: same instant.
    startup_delay_seconds: float = 0.0
    #: Overrides ``interval_setting`` at scheduling time when present - the
    #: configured field stays what a purely-idle pass runs at, and this
    #: decides whether the *next* run should instead be sooner (build 160:
    #: the torrent status sync tightens to 5s while something is actually
    #: downloading, rather than polling qBittorrent that often all the time).
    dynamic_interval: Callable[[Any], float] | None = None
    #: How long one run may take before it is stopped and reported as failed
    #: (build 197). The worker runs its tasks one after another, so a task
    #: that never finishes used to hold up every task behind it - the RSS
    #: scan, the download checks - with the Schedule page saying "running..."
    #: for days. ``None`` means no limit. A job running in a thread cannot be
    #: killed, only walked away from: the worker moves on and the thread ends
    #: whenever it ends.
    timeout_seconds: float | None = 3600.0


@dataclass
class TaskRuntimeState:
    """What the registry knows about one task right now."""

    running: bool = False
    requested: bool = False
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_result: dict[str, object] | None = None
    last_error: str | None = None
    #: Whether the run currently in flight (or the one that just finished)
    #: was a manual "Run now" rather than the automatic tick - set by the
    #: loop right before it calls the job, read by a job body that behaves
    #: differently when a person asked for it directly (build 160: the
    #: Housekeeping task's torrent-sweep step is manual-only).
    last_manual: bool = False
