"""Read and write `ScheduleTaskState` - see the model for why it exists.

Kept separate from worker/runtime.py so the worker loop does not need to know
anything about SQL; it only calls `load_all` once at startup and `save` after
every run.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.orm import sessionmaker, Session

from backend.app.models.schedule_state import ScheduleTaskState


def load_all(session_factory: sessionmaker[Session]) -> dict[str, ScheduleTaskState]:
    """Every persisted task, keyed by id."""
    session = session_factory()
    try:
        return {row.task_id: row for row in session.query(ScheduleTaskState).all()}
    finally:
        session.close()


def _apply(
    session: Session,
    task_id: str,
    *,
    last_run_at: datetime | None,
    last_result: dict | None,
    last_error: str | None,
) -> None:
    row = session.get(ScheduleTaskState, task_id)
    if row is None:
        row = ScheduleTaskState(task_id=task_id)
        session.add(row)
    row.last_run_at = last_run_at
    row.last_result = last_result
    row.last_error = last_error


def save(
    session_factory: sessionmaker[Session],
    task_id: str,
    *,
    last_run_at: datetime | None,
    last_result: dict | None,
    last_error: str | None,
) -> None:
    """Upsert one task's state, opening and closing its own session. Never
    raises - a write that fails leaves the previous persisted state in place,
    which is still useful after a restart, rather than taking down the tick
    that triggered it."""
    session = session_factory()
    try:
        _apply(session, task_id, last_run_at=last_run_at, last_result=last_result, last_error=last_error)
        session.commit()
    except Exception:  # noqa: BLE001 - a persistence failure must never stop the worker
        session.rollback()
    finally:
        session.close()


def save_with_session(
    session: Session,
    task_id: str,
    *,
    last_run_at: datetime | None,
    last_result: dict | None,
    last_error: str | None,
) -> None:
    """Same as `save`, against a session the caller already has and will
    commit and close itself (build 161: an HTTP route with its own manual
    trigger - the Shoko page's "Take from Shoko" - updates the same row a
    scheduled run would, without opening a second connection to do it, so the
    Schedule page agrees no matter which button was pressed)."""
    _apply(session, task_id, last_run_at=last_run_at, last_result=last_result, last_error=last_error)


def prune(session_factory: sessionmaker[Session], valid_task_ids: Iterable[str]) -> None:
    """Drop rows for a task id that no longer exists in the registry.

    Runs once at startup, not every tick: a task removed or merged away
    (`orphan_adoption`/`reconcile`/`torrent_sweep` folding into
    `torrent_housekeeping` at build 160, say) would otherwise leave a row
    behind forever - harmless, but clutter nobody asked to keep.
    """
    session = session_factory()
    try:
        valid = set(valid_task_ids)
        stale = [
            row for row in session.query(ScheduleTaskState).all() if row.task_id not in valid
        ]
        for row in stale:
            session.delete(row)
        if stale:
            session.commit()
    except Exception:  # noqa: BLE001 - never let cleanup stop the worker
        session.rollback()
    finally:
        session.close()


__all__ = ["load_all", "save", "save_with_session", "prune"]
