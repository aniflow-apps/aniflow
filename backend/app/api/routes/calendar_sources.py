"""What is left of the build-83 comparison experiment (build 149).

One endpoint, and it exists for one reason: **watchlist rows point at it.**

Builds 83 to 87 downloaded each season from five sources side by side so the
numbers could decide which source to keep. They did, and the answer was *all
of them, merged* - which is what Calendar v2 does, from build 141. The
comparison page, its downloader, its per-source tables and the merge that read
them are gone. Build 83 said of this file: *"a temporary page ... once that is
answered it and these routes come out again"*. This is that.

The posters it downloaded do not go, because the *Repair watchlist covers*
tool wrote this URL into ``anime.cover_url`` for anybody who ran it. Those
files are on disk, those rows point here, and dropping the route would blank
a cover with no way to get it back short of re-adding the anime. So the files
stay where they are and this serves them.

Nothing writes new ones. A cover fetched today is served by
``/api/calendar/art/{work_id}`` - see ``services/calendar_art.py``.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.app.services import calendar_providers

router = APIRouter()


@router.get("/{key}/art/{source_id}", include_in_schema=False)
def source_art(key: str, source_id: str) -> FileResponse:
    """Serve a poster the v1 downloader stored, for a row that points at it.

    Not a path from the caller: the filename comes from disk by glob, so a
    key and an id cannot address anything outside the folder.
    """
    target = calendar_providers.picture_path(key.strip(), source_id.strip())
    if target is None:
        raise HTTPException(status_code=404, detail="No picture was downloaded for this entry")
    return FileResponse(target, headers={"Cache-Control": "public, max-age=3600"})
