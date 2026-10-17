"""What the Schedule page remembers between restarts (build 161).

Reported: a redeploy loses every task's "when did this last happen", because
that lived only in the worker's in-memory `TaskRuntimeState` - so a task on a
1-week interval starts over from "just booted" every time AniFlow itself is
redeployed, and on an install that redeploys often, its own schedule never
actually gets to elapse. This table is that memory, kept alongside the app's
own data (survives a redeploy the same way the database always has), read at
worker startup and written after every run - see worker/runtime.py.
"""
from datetime import datetime

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime


class ScheduleTaskState(Base):
    """One task's last-known run, independent of the process that ran it."""

    __tablename__ = "schedule_task_state"

    task_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(400), nullable=True)


__all__ = ["ScheduleTaskState"]
