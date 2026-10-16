"""Backup and restore (build 266). See ``services/backup.py`` for what the archive holds and how it is kept safe.

The upload is the raw zip as the request body, like the AniDB export: multipart would need a package this install
does not carry. It is streamed to disk because a backup with the calendar art is hundreds of megabytes.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.app.services import backup

router = APIRouter()


class RestoreRequest(BaseModel):
    id: str
    groups: list[str]
    keep_local: bool = False


@router.get("/groups")
def groups() -> list[dict]:
    """What an export could hold right now, with sizes - the Export card's checkboxes."""
    return backup.describe()


@router.get("/export")
def export(groups: str = "") -> FileResponse:
    """The archive for the comma-separated groups (everything but the calendar art when none are named)."""
    wanted = [g for g in groups.split(",") if g] or list(backup.DEFAULT_GROUPS)
    unknown = [g for g in wanted if g not in backup.GROUPS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown part of the backup: {', '.join(unknown)}")
    try:
        path = backup.build_backup(wanted)
    except backup.BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=backup.backup_name(),
        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
    )


@router.post("/inspect")
async def inspect(request: Request) -> dict:
    """Take an uploaded backup, check it and say what is in it. Nothing is changed yet."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > backup.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is too large to be an AniFlow backup")
    staged_id, path = backup.new_staging_file()
    size = 0
    try:
        with path.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > backup.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="The file is too large to be an AniFlow backup")
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    try:
        return backup.inspect(staged_id)
    except (backup.BackupError, backup.zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/restore")
def restore(body: RestoreRequest) -> dict:
    """Restore the chosen parts of a checked upload."""
    try:
        return backup.restore(body.id, body.groups, keep_local=body.keep_local)
    except (backup.BackupError, backup.zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/staged/{staged_id}")
def forget(staged_id: str) -> dict:
    """The person changed their mind: drop the stored upload."""
    backup.discard(staged_id)
    return {"ok": True}
