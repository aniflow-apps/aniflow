"""Fetching the one poster each show was given (build 143).

The snapshot engine chooses a poster and stores the provider and the URL; it
does not download anything, because a picture download has no business holding
a database transaction open.

So the bytes are fetched here, afterwards, in a pass that can fail entirely
without costing anything: a show with no poster on disk is listed with the
provider's own URL until the pass catches up, and the page looks the same
either way.

One file per Work, under ``<data>/calendar-art/v2``. v1 kept one copy per
source so the sources could be compared on their artwork - the calendar only
ever draws one, and five downloads for one picture is four too many.
"""
from __future__ import annotations

import httpx
from sqlalchemy import select

from backend.app.core.logging import for_component
from backend.app.db import calendar_store
from backend.app.models.calendar import CalendarArtwork

logger = for_component("Calendar")

#: The folder, reusing the sources page's own layout so one thing knows where
#: calendar art lives.
ART_KEY = "v2"


def art_path(work_id: str):
    """Where this Work's poster is, if it has been fetched."""
    from backend.app.services.calendar_providers import art_dir

    directory = art_dir(ART_KEY)
    found = sorted(directory.glob(f"{work_id}.*"))
    return found[0] if found else None


async def fetch_pending(limit: int = 200, *, on_progress: object = None) -> int:
    """Download posters that have been chosen but not fetched. Returns how many.

    Never raises. A poster that cannot be fetched stays a URL, which is a
    working state rather than a broken one.
    """
    from backend.app.services.calendar_providers import download_picture

    try:
        sessions = calendar_store.sessions()
    except calendar_store.CalendarStoreBlocked as exc:
        logger.warning("calendar art skipped: %s", exc)
        return 0

    with sessions() as session:
        pending = list(
            session.scalars(
                select(CalendarArtwork)
                .where(CalendarArtwork.file.is_(None))
                .limit(limit)
            )
        )
        if not pending:
            return 0
        wanted = [(row.work_id, row.source_url) for row in pending if row.source_url]

    # Said out loud because this is the slow part of an update and the only
    # part with nothing else to show for itself: a first season is up to two
    # hundred pictures, one at a time.
    logger.info("fetching %s calendar posters", len(wanted))
    from backend.app.services import calendar_trace

    calendar_trace.record("artwork", f"fetching {len(wanted)} posters", wanted=len(wanted))
    fetched: dict[str, str] = {}
    # Through the proxy when one is set (build 235): posters come from the providers' servers on the internet, and
    # this was the request that went out directly. The same rule as everywhere else - a broken proxy fails, it does
    # not become a direct connection. One client for the whole batch, so one decision, made on the first address.
    from backend.app.services.outbound import client_kwargs

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, **client_kwargs(wanted[0][1] if wanted else None)) as client:
        for done, (work_id, url) in enumerate(wanted, start=1):
            name = await download_picture(client, ART_KEY, work_id, url)
            if name:
                fetched[work_id] = name
            if on_progress is not None:
                on_progress(done, len(wanted))  # type: ignore[operator]

    if not fetched:
        return 0
    with sessions() as session:
        for work_id, name in fetched.items():
            row = session.get(CalendarArtwork, work_id)
            if row is not None:
                row.file = name
        session.commit()
    logger.info("fetched %s calendar posters", len(fetched))
    calendar_trace.record(
        "artwork",
        f"fetched {len(fetched)} of {len(wanted)} posters",
        fetched=len(fetched),
        missed=len(wanted) - len(fetched),
    )
    return len(fetched)
