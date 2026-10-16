"""API schema for the Schedule page: every background task, live."""
from datetime import datetime

from pydantic import BaseModel


class ScheduleTask(BaseModel):
    id: str
    category: str
    #: False only for the one item nobody has built yet (STATE.md open item 5).
    implemented: bool
    #: Whether the Schedule page offers a "run now" trigger for this task.
    manual: bool
    enabled: bool
    #: "no_indexer" | "no_qbittorrent" | null - why this task cannot do
    #: anything right now even if `enabled` is true (nothing to search or
    #: sync). See worker/tasks.py's `requires`.
    blocked_reason: str | None
    #: Always seconds, even for rss_scan, which stores its interval in
    #: minutes internally. Null for a task with no independent schedule.
    interval_seconds: int | None
    #: The AppSettings field names this task's own editor (build 161) reads
    #: and writes - null when there is nothing to edit (no independent
    #: schedule at all: `interval_setting` decides whether the Schedule
    #: page offers an editor for this row in the first place).
    enabled_setting: str | None
    interval_setting: str | None
    #: "seconds" | "minutes" - which unit `interval_setting` is stored in, so
    #: the editor sends the value back in the field's own unit.
    interval_unit: str
    running: bool
    requested: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_result: dict[str, object] | None
    last_error: str | None


class RunTaskResponse(BaseModel):
    started: bool
