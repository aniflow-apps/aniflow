"""The Schedule page: every background task, live, plus a manual trigger."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.schemas.schedule import RunTaskResponse, ScheduleTask
from backend.app.services.app_mode import has_indexer
from backend.app.services.app_settings import AppSettings, load_app_settings, save_app_settings
from backend.app.worker.runtime import TASKS, reschedule_changed_tasks, worker_state
from backend.app.worker.tasks import TaskDefinition

router = APIRouter()

_BY_ID: dict[str, TaskDefinition] = {task.id: task for task in TASKS}


def _blocked_reason(task: TaskDefinition, cfg: AppSettings, has_indexer_flag: bool) -> str | None:
    """Why ``task`` cannot do anything right now, distinct from its enabled
    switch - see worker/tasks.py's ``requires``. ``None`` reported settings
    are on, not why they might sit idle."""
    if task.requires == "indexer" and not has_indexer_flag:
        return "no_indexer"
    if task.requires == "qbittorrent" and not cfg.qb_configured:
        return "no_qbittorrent"
    if task.requires == "shoko" and not cfg.shoko_configured:
        return "no_shoko"
    return None


def _view(task: TaskDefinition, cfg: AppSettings, has_indexer_flag: bool) -> ScheduleTask:
    state = worker_state.tasks[task.id]
    enabled = True if task.enabled_setting is None else bool(getattr(cfg, task.enabled_setting))
    interval_seconds: int | None = None
    if task.interval_setting is not None:
        interval_seconds = getattr(cfg, task.interval_setting)
        if task.interval_unit == "minutes":
            interval_seconds *= 60
    return ScheduleTask(
        id=task.id,
        category=task.category,
        implemented=task.implemented,
        manual=task.manual,
        enabled=enabled,
        blocked_reason=_blocked_reason(task, cfg, has_indexer_flag),
        interval_seconds=interval_seconds,
        enabled_setting=task.enabled_setting,
        interval_setting=task.interval_setting,
        interval_unit=task.interval_unit,
        running=state.running,
        requested=state.requested,
        last_run_at=state.last_run_at,
        next_run_at=state.next_run_at,
        last_result=state.last_result,
        last_error=state.last_error,
    )


@router.get("/tasks", response_model=list[ScheduleTask])
def list_tasks(db: Session = Depends(get_db)) -> list[ScheduleTask]:
    cfg = load_app_settings(db)
    has_indexer_flag = has_indexer(db)
    return [_view(task, cfg, has_indexer_flag) for task in TASKS]


@router.post("/tasks/{task_id}/run", response_model=RunTaskResponse)
def run_task(task_id: str) -> RunTaskResponse:
    """Ask the worker to run this task now.

    Not implemented and not manual both answer ``started: false`` rather than
    an error - a stale Schedule page pressing a button that just changed
    should see "it didn't start", not a failure banner.
    """
    task = _BY_ID.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    if not task.implemented or not task.manual:
        return RunTaskResponse(started=False)
    return RunTaskResponse(started=worker_state.request_run(task_id))


@router.post("/tasks/{task_id}/reset", response_model=ScheduleTask)
def reset_task(task_id: str, db: Session = Depends(get_db)) -> ScheduleTask:
    """Put this task's own switch and interval back to what a fresh install
    would use (build 161) - the environment's own value, which is also what
    an env var override means to set, not a hard-coded literal repeated here.

    A task with no independent schedule has nothing to reset; refused as
    "cannot reset" rather than silently doing nothing, since a button that
    should not have been shown is a frontend bug worth finding this way.
    """
    task = _BY_ID.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    if task.interval_setting is None:
        raise HTTPException(status_code=400, detail="This task has no schedule to reset")

    env = get_settings()
    updates: dict[str, object] = {task.interval_setting: getattr(env, task.interval_setting)}
    if task.enabled_setting is not None:
        updates[task.enabled_setting] = getattr(env, task.enabled_setting)
    save_app_settings(db, updates)
    # So the reset is reflected in "Next Execution" immediately rather than
    # only after this task's next run under whatever was already scheduled
    # (build 163, STATE.md Open item 9).
    reschedule_changed_tasks(set(updates))

    cfg = load_app_settings(db)
    has_indexer_flag = has_indexer(db)
    return _view(task, cfg, has_indexer_flag)
