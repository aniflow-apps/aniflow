"""``GET /api/cover?url=`` - a provider's artwork, served by AniFlow.

See ``services/cover_proxy.py`` for why. The browser never talks to the
provider's image host; it asks here, behind the same session as the rest of the
API.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.app.services import cover_proxy

router = APIRouter()


@router.get("", include_in_schema=False)
async def provider_cover(url: str) -> FileResponse:
    try:
        target = await cover_proxy.fetch(url)
    except cover_proxy.CoverRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except cover_proxy.CoverUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # The file is named by its address, so one URL is one picture for good.
    return FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})
