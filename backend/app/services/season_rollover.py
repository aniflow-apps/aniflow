"""The season rollover: put each anime in the library that matches what it is doing (build 205).

See docs/season-rollover-plan.md. Three kinds of move:

* **Upcoming -> the current-season library**, always, silently: there are no
  files, so only the database changes.
* **Current -> earlier** (finished at a season change, or waiting between
  parts) and **earlier -> current** (an anime found airing in the earlier
  library, or a parked one that aired again): these move folders, so they
  follow the user's setting - ask (a banner on the watchlist), automatic, off.

Which library is "the current one" and "the earlier one" is what the Season tag
on a library says; with not exactly one library carrying the tag the target is
left open and the user is asked which. An anime is never moved while a download
or an import is in flight, never when the user pinned it, and automatic mode
moves only a few at a time, so the first pass on a big watchlist is spread out.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.models.anime import Anime, AnimeLifecycle, EpisodeStatus
from backend.app.models.library import Library
from backend.app.services import libraries
from backend.app.services.season_state import (
    State,
    classify,
    current_season_index,
    episode_facts,
    facts_of,
    is_pinned,
    season_index,
    season_of,
    set_pinned,
)

logger = for_component("Library")

MODES = ("ask", "auto", "off")

#: Automatic mode moves at most this many anime per pass.
AUTO_BATCH = 10

#: Why an anime is proposed for a move.
FINISHED = "finished"
WAITING = "waiting"
RUNNING = "running"

_BUSY = (EpisodeStatus.DOWNLOADING, EpisodeStatus.DOWNLOADED, EpisodeStatus.IMPORTING)


@dataclass(frozen=True)
class Proposal:
    anime_id: str
    name: str
    #: finished | waiting | running
    reason: str
    from_library_id: int
    #: The library it should go to, or None when the user has to say which.
    target_id: int | None

    @property
    def group(self) -> str:
        """``earlier`` (finished, waiting) or ``airing`` (running again)."""
        return "airing" if self.reason == RUNNING else "earlier"


def mode() -> str:
    from backend.app.services.app_settings import load_app_settings

    value = getattr(load_app_settings(), "season_rollover", "ask")
    return value if value in MODES else "ask"


@dataclass
class _Context:
    libraries: dict[int, Library]
    current_target: int | None
    past_target: int | None
    has_current: bool
    has_earlier: bool
    now: datetime
    now_index: int


def _context(db: Session, now: datetime) -> _Context:
    rows = {lib.id: lib for lib in db.scalars(select(Library))}
    foldered = [lib for lib in rows.values() if lib.has_folder]
    current = [lib for lib in foldered if lib.fits_season == "current"]
    past = [lib for lib in foldered if lib.fits_season == "past"]
    return _Context(
        libraries=rows,
        current_target=current[0].id if len(current) == 1 else None,
        past_target=past[0].id if len(past) == 1 else None,
        has_current=bool(current),
        # Somewhere other than the current library for finished shows to go.
        has_earlier=any(lib.fits_season != "current" for lib in foldered),
        now=now,
        now_index=current_season_index(now.date()),
    )


def _proposal(anime: Anime, context: _Context, owned: int, last) -> Proposal | None:
    library = context.libraries.get(anime.library_id) if anime.library_id is not None else None
    if library is None or not library.has_folder:
        return None
    facts = facts_of(anime, owned, last)
    state = classify(facts, context.now)
    name = anime.display_name or anime.local_name
    if library.fits_season == "current" and context.has_earlier:
        if state is State.FINISHED:
            index = season_index(season_of(facts))
            if index is not None and index < context.now_index:
                return Proposal(anime.id, name, FINISHED, library.id, context.past_target)
        elif state is State.WAITING:
            return Proposal(anime.id, name, WAITING, library.id, context.past_target)
    elif library.fits_season == "past" and context.has_current and state is State.RUNNING:
        return Proposal(anime.id, name, RUNNING, library.id, context.current_target)
    return None


def proposals(db: Session, now: datetime | None = None) -> list[Proposal]:
    """Every anime that is not where it should be, in watchlist order (newest first)."""
    moment = now or datetime.now(UTC)
    context = _context(db, moment)
    seen = episode_facts(db)
    found: list[Proposal] = []
    for anime in db.scalars(select(Anime).where(Anime.library_id.is_not(None)).order_by(Anime.created_at.desc())):
        owned, last = seen.get(anime.id, (0, None))
        proposal = _proposal(anime, context, owned, last)
        if proposal is not None:
            found.append(proposal)
    return found


def pin_if_against_the_rules(db: Session, anime: Anime) -> bool:
    """After a move by hand: if the rules would move it again, pin it - the user meant it."""
    moment = datetime.now(UTC)
    owned, last = episode_facts(db, [anime.id]).get(anime.id, (0, None))
    if _proposal(anime, _context(db, moment), owned, last) is None:
        return False
    set_pinned(anime, True)
    return True


def promote_upcoming(db: Session, now: datetime | None = None) -> int:
    """Anime in Upcoming whose season has come go to the current-season library.

    Always automatic: they have no files, so only the database changes. The
    lifecycle becomes Active as they leave (``library_move``). Returns how many.
    """
    from backend.app.services import library_move

    moment = now or datetime.now(UTC)
    context = _context(db, moment)
    upcoming = libraries.upcoming_library(db)
    target = context.current_target or libraries.default_library(db).id
    ready: list[str] = []
    for anime in db.scalars(select(Anime).where(Anime.library_id == upcoming.id)):
        if is_pinned(anime) or anime.lifecycle in (AnimeLifecycle.DROPPED, AnimeLifecycle.ARCHIVED):
            continue
        started = anime.air_start_date is not None and anime.air_start_date <= moment.date()
        index = season_index(season_of(anime))
        if started or (index is not None and index <= context.now_index):
            ready.append(anime.id)
    if not ready:
        return 0
    results = library_move.move_anime(db, ready, target, move_files=False, by_hand=False)
    moved = sum(1 for r in results if r.outcome in ("moved", "reassigned"))
    if moved:
        logger.info("%d anime left Upcoming: their season has started", moved)
    return moved


def _is_busy(anime: Anime) -> bool:
    return any(episode.status in _BUSY for episode in anime.episodes)


def apply(db: Session, found: list[Proposal], *, limit: int | None = None) -> dict[str, int]:
    """Carry out proposals that have a target. Returns counts of what happened."""
    from backend.app.services import library_move

    counts = {"moved": 0, "skipped_busy": 0, "no_target": 0, "refused": 0}
    by_target: dict[int, list[str]] = {}
    taken = 0
    for proposal in found:
        if proposal.target_id is None:
            counts["no_target"] += 1
            continue
        if limit is not None and taken >= limit:
            break
        anime = db.get(Anime, proposal.anime_id)
        if anime is None:
            continue
        if _is_busy(anime):
            counts["skipped_busy"] += 1
            continue
        by_target.setdefault(proposal.target_id, []).append(anime.id)
        taken += 1
    for target, ids in by_target.items():
        for result in library_move.move_anime(db, ids, target, move_files=True, by_hand=False):
            if result.outcome in ("moved", "reassigned"):
                counts["moved"] += 1
            elif result.outcome == "refused":
                counts["refused"] += 1
    return counts


def run(db: Session, now: datetime | None = None) -> dict[str, object]:
    """One pass, for the worker: Upcoming first, then - in automatic mode - the rest."""
    summary: dict[str, object] = {"promoted": promote_upcoming(db, now)}
    if mode() == "auto":
        summary["auto"] = apply(db, proposals(db, now), limit=AUTO_BATCH)
    return summary


_last_run = 0.0

#: The worker's busy job calls this every minute; seasons do not change that fast.
PASS_SECONDS = 600


def run_in(session_factory, *, now: float | None = None) -> dict[str, object]:
    """``run`` at most every ten minutes, for the worker."""
    import time

    global _last_run
    moment = now if now is not None else time.monotonic()
    if _last_run and moment - _last_run < PASS_SECONDS:
        return {}
    _last_run = moment
    session = session_factory()
    try:
        return run(session)
    finally:
        session.close()


def reset() -> None:
    """For tests."""
    global _last_run
    _last_run = 0.0


def pin(db: Session, anime_ids: list[str], pinned: bool = True) -> int:
    """"Keep here" (or take it back): the rollover leaves a pinned anime alone."""
    changed = 0
    for anime_id in anime_ids:
        anime = db.get(Anime, anime_id)
        if anime is not None and is_pinned(anime) != pinned:
            set_pinned(anime, pinned)
            changed += 1
    db.commit()
    return changed


__all__ = [
    "AUTO_BATCH",
    "MODES",
    "Proposal",
    "apply",
    "mode",
    "pin",
    "pin_if_against_the_rules",
    "promote_upcoming",
    "proposals",
    "run",
    "reset",
    "run_in",
]
