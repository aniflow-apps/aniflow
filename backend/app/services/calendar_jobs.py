"""One calendar update at a time, and a way to watch it (build 143).

Deliberately small. The v2 brief says not to build a job framework for the
Calendar when a general one is coming, and it is right: this is one task, one
status record, and no queue beyond a list of seasons still to do.

What it must get right is narrow:

* **Only one runs.** Pressing Update twice means "do it", not "do it twice".
* **The page keeps its data while it runs.** Nothing is cleared at the start;
  the engine replaces a season's contents only when a snapshot is applied.
* **A failure is a status, not a disappearance.** An update that fails leaves
  the last good line-up exactly where it was.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from backend.app.core.logging import for_component
from backend.app.db.types import now_utc
from backend.app.services import calendar_trace
from backend.app.services.calendar_pacer import Pacer

logger = for_component("Calendar")


@dataclass
class JobState:
    """What the page draws while an update is happening."""

    running: bool = False
    #: ``update`` for the button, ``season`` for one loaded on demand.
    kind: str = ""
    #: The season being worked on right now.
    year: int = 0
    season: str = ""
    #: What is happening inside that season, in words a page can print: the
    #: source being asked, or the poster download that follows it.
    #:
    #: Seasons alone were not enough to watch. One season is five sources and
    #: then up to two hundred pictures, so "season 1 of 3" could stand
    #: unchanged for minutes while a great deal was happening.
    step: str = ""
    #: Posters fetched so far in this season, for the step that has no other
    #: way of showing progress.
    pictures: int = 0
    #: How many there are to fetch, known once the season's snapshots are in.
    #: Zero until then - a bar that guesses is worse than no bar.
    pictures_total: int = 0
    #: Sources asked and sources to ask, for this season. Known up front,
    #: which is what makes the first half of the work measurable at all.
    sources_done: int = 0
    sources_total: int = 0
    #: Seasons finished and seasons asked for, so a bar is honest rather than
    #: guessed - it is zero until the work is actually known.
    done: int = 0
    total: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: str = ""
    error: str | None = None
    #: Which seasons this run is still going to touch, in order.
    pending: list[tuple[int, str]] = field(default_factory=list)


_state = JobState()
_task: asyncio.Task | None = None

#: Called once, when the current run finishes without an error (build 148).
#:
#: Module state rather than a field on ``JobState``, which is copied out to
#: every status response - a callable has no business travelling with one.
#: The only user is the initial fill, which records that it has happened; the
#: alternative was for this module to import the note store and know what an
#: initial fill is, which is one more thing for a job runner to know than it
#: needs to.
_after: object = None


def status() -> JobState:
    """A copy, so it cannot change while the response is being built."""
    return JobState(**{**vars(_state), "pending": list(_state.pending)})


def running_for(year: int, season: str) -> bool:
    """Whether this exact season is being loaded right now.

    What lets a page say "this season is being fetched" rather than the
    vaguer "something is happening".
    """
    if not _state.running:
        return False
    if (_state.year, _state.season) == (year, season):
        return True
    return (year, season) in _state.pending


def reset() -> None:
    """For tests, and for a process that is shutting down."""
    global _task, _state, _after
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None
    _state = JobState()
    _after = None


def start(
    seasons: list[tuple[int, str]],
    *,
    kind: str = "update",
    on_success: object = None,
) -> JobState:
    """Begin an update, unless one is already running.

    Returns the state either way. A second press while the first is running is
    not an error and does not queue a second run - the answer to "is it
    working" is the status, not a new job.

    ``on_success`` is called once the whole run has finished and only if
    nothing failed. A caller that arrives while a run is already going does
    not get one: its seasons join that run, and its callback would be a claim
    about work it did not define.
    """
    global _task, _after
    if _state.running:
        # Seasons the caller wants that this run was not already going to do
        # are added rather than refused: asking for a season while an update
        # is in flight should still get you that season.
        for entry in seasons:
            if entry not in _state.pending and entry != (_state.year, _state.season):
                _state.pending.append(entry)
                _state.total += 1
        return status()

    _state.running = True
    _state.kind = kind
    _state.done = 0
    _state.total = len(seasons)
    _state.started_at = now_utc()
    _state.finished_at = None
    _state.error = None
    _state.summary = ""
    _state.pending = list(seasons)
    _after = on_success
    calendar_trace.record(
        "job",
        f"{kind} started for {len(seasons)} season(s)",
        seasons=[f"{season} {year}" for year, season in seasons],
    )
    # Built before the try, and closed if it cannot be scheduled: calling
    # _run() already makes the coroutine, so letting create_task fail around
    # it leaves one nobody will ever await.
    coroutine = _run()
    try:
        _task = asyncio.create_task(coroutine)
    except RuntimeError:
        coroutine.close()
        # No event loop - a synchronous caller, or a test that is not async.
        # Refusing here beats raising out of a page request: the calendar is
        # simply not updated, which the status already has a way of saying.
        _state.running = False
        _after = None
        _state.error = "no event loop to run the update on"
        logger.warning("a calendar update was asked for with no event loop running")
        return status()
    return status()


async def _run() -> None:
    """Work through the seasons, one at a time, paced.

    One pacer for the whole run, so five seasons do not each start by
    deciding that they may ask immediately.
    """
    from backend.app.services.calendar_update import load_season

    global _after
    pacer = Pacer()
    applied = 0
    refused = 0
    #: Set only where the loop runs out of seasons. A cancelled run reaches
    #: the `finally` with no error on it and has still done nothing that
    #: anybody should record.
    completed = False
    try:
        while _state.pending:
            year, season = _state.pending.pop(0)
            _state.year, _state.season = year, season
            from backend.app.services.calendar_update import SEASON_PROVIDERS

            _state.step = ""
            _state.pictures = 0
            _state.pictures_total = 0
            _state.sources_done = 0
            _state.sources_total = len(SEASON_PROVIDERS)

            def announce(provider: str) -> None:
                _state.step = provider
                _state.sources_done += 1

            report = await load_season(year, season, pacer=pacer, on_step=announce)
            applied += len(report.applied) - len(report.refused)
            refused += len(report.refused)
            # After the season, not during it: the posters are chosen inside
            # the transaction and fetched outside it.
            from backend.app.services.calendar_art import fetch_pending

            _state.step = "artwork"

            def counted(done: int, total: int) -> None:
                _state.pictures = done
                _state.pictures_total = total

            await fetch_pending(on_progress=counted)
            _state.step = ""
            _state.done += 1
        _state.summary = f"{applied} snapshots applied, {refused} refused"
        completed = True
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        raise
    except Exception as exc:  # noqa: BLE001 - a failed update is a status
        # Nothing to undo: the engine applies a season or leaves it alone, so
        # whatever was on screen before this is still there.
        _state.error = f"{type(exc).__name__}: {exc}"
        logger.exception("the calendar update failed")
    finally:
        done, _after = _after, None
        if done is not None and completed and _state.error is None:
            try:
                done()  # type: ignore[operator]
            except Exception:  # noqa: BLE001 - a note is not worth a failed run
                logger.warning("the callback after a calendar run failed", exc_info=True)
        _state.running = False
        _state.finished_at = now_utc()
        _state.year, _state.season = 0, ""
        logger.info("calendar update finished: %s", _state.error or _state.summary)
        calendar_trace.record(
            "job",
            _state.error or f"finished: {_state.summary}",
            level="error" if _state.error else "info",
        )
