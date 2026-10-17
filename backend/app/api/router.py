from fastapi import APIRouter, Depends

from backend.app.api.deps import require_session
from backend.app.api.routes import (
    activity,
    analyze,
    anidb,
    anime,
    auth,
    backup,
    calendar,
    calendar_sources,
    cover_proxy,
    downloads,
    indexers,
    libraries,
    rollover,
    profile_templates,
    release_profiles,
    rss,
    schedule,
    search,
    library_match,
    progress,
    settings,
    setup,
    shoko,
    system,
    themes,
    tv,
)

# Every route under /api requires a session, except the handful named in
# deps.OPEN_PATHS. Declared here so a new router cannot forget it.
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
# Open, by the list in deps.py: without these there is no way to acquire a
# session at all.
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# The install wizard (build 137). Behind the session like everything else:
# the account is created first, which logs the browser in.
api_router.include_router(setup.router, prefix="/setup", tags=["setup"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
# Backup and restore (build 266).
api_router.include_router(backup.router, prefix="/backup", tags=["backup"])
api_router.include_router(profile_templates.router, prefix="/profiles", tags=["profiles"])
api_router.include_router(release_profiles.router, prefix="/anime", tags=["release-profiles"])
# Finding an anime's files in the library (build 139). Under /anime because
# every call is about one anime, like the profile routes above.
api_router.include_router(library_match.router, prefix="/anime", tags=["library-match"])
api_router.include_router(anime.router, prefix="/anime", tags=["anime"])
api_router.include_router(calendar.router, prefix="/calendar", tags=["calendar"])
api_router.include_router(cover_proxy.router, prefix="/cover", tags=["covers"])
# The build-83 metadata experiment. Temporary, and written to come out again:
# nothing else imports it, and it touches only data/calendar.db.
api_router.include_router(
    calendar_sources.router, prefix="/calendar-sources", tags=["calendar-sources"]
)
api_router.include_router(rss.router, prefix="/rss", tags=["rss"])
api_router.include_router(schedule.router, prefix="/schedule", tags=["schedule"])
api_router.include_router(indexers.router, prefix="/indexers", tags=["indexers"])
api_router.include_router(downloads.router, prefix="/downloads", tags=["downloads"])
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])
api_router.include_router(activity.router, prefix="/activity", tags=["activity"])
api_router.include_router(shoko.router, prefix="/shoko", tags=["shoko"])
api_router.include_router(anidb.router, prefix="/anidb", tags=["anidb"])
# Libraries: named target folders (build 192, 0.8.5).
api_router.include_router(libraries.router, prefix="/libraries", tags=["libraries"])
api_router.include_router(rollover.router, prefix="/rollover", tags=["rollover"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(analyze.router, prefix="/analyze", tags=["analyze"])
api_router.include_router(themes.router, prefix="/themes", tags=["themes"])
# The television API (build 181). Open to the session gate by deps.OPEN_PREFIXES
# and guarded by its own token; the art routes by a signed key per image.
api_router.include_router(tv.router, prefix="/tv/v1", tags=["tv"])
api_router.include_router(tv.art_router, prefix="/tv/v1/art", tags=["tv"])
api_router.include_router(tv.pair_router, prefix="/tv/v1/pair", tags=["tv"])
# Making and revoking TV tokens: behind the ordinary session.
api_router.include_router(tv.devices_router, prefix="/tv-devices", tags=["tv"])
