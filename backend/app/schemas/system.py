from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    worker: str
    qbittorrent: str = "disabled"
    #: ok | unreachable | unknown. See services/metadata.py. The provider's
    #: name is deliberately not here.
    metadata: str = "unknown"
    #: disabled | ok | unauthorised | unreachable | unknown. Remembered rather
    #: than probed here - see services/shoko_status.py for why this endpoint
    #: does not make a third live call.
    shoko: str = "disabled"
    timestamp: datetime


class AppInfoResponse(BaseModel):
    name: str = "AniFlow"
    version: str
    #: Only ever grows. Two containers claiming the same version can be
    #: different code; two claiming the same build cannot, which is what makes
    #: it possible to tell a change that did not work from one that never
    #: arrived. Shown next to the version as `v.0.1.0 b.68`.
    build: int = 0
    #: The same pair plus the date, as one filename-safe string, matching
    #: BUILD.md. Handy to read back off a running server.
    build_string: str = ""
    environment: str
    #: The timezone in force, and the weekday and time it is there. The
    #: watchlist highlights today's column with this, so "today" and the
    #: airing weekdays it sits next to are derived from the same clock.
    timezone: str = "UTC"
    today_weekday: int = 0
    server_time: str = ""
    #: Which storage pipeline runs. Several screens change shape with it:
    #: in Shoko Server mode AniFlow does not name library folders and cannot
    #: show one until Shoko has created it.
    mode: str = "standalone"
    #: Where the configuration folders live. The Indexer page names the exact
    #: path to put files in, which is more use than describing it.
    config_dir: str = "/config"
    #: "basic" or "advanced". Basic means no indexer is configured, so nothing
    #: can be downloaded and every screen about downloading is hidden rather
    #: than offered and broken. See services/app_mode.py.
    app_mode: str = "advanced"
    #: How many tracked anime were added in Basic mode and still need a release
    #: name before AniFlow can match anything for them. Zero in Basic mode:
    #: there is nothing to convert them for yet.
    needs_conversion: int = 0
    #: Whether a torrent client has been given an address. Without one a
    #: download request leaves the release pending and says nothing, so the
    #: screens about downloading warn (build 227).
    torrent_client_configured: bool = False


class DebugLogResponse(BaseModel):
    ok: bool
    message: str
