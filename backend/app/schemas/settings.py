"""API schemas for user-editable application settings."""
from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    """Partial update. Secrets: omit to keep, empty string to clear."""

    torrent_client: str | None = Field(default=None, pattern="^(qbittorrent|deluge|transmission)$")
    qb_url: str | None = Field(default=None, max_length=300)
    qb_username: str | None = Field(default=None, max_length=80)
    qb_password: str | None = Field(default=None, max_length=200)
    qb_category: str | None = Field(default=None, max_length=60)

    # How the file gets into the library: copied (seeding survives) or moved.
    file_operation: str | None = Field(default=None, pattern="^(copy|move|hardlink)$")
    after_import_action: str | None = Field(default=None, pattern="^(pause|remove|keep)$")
    #: Saved from the release-change panel rather than the Settings page, so
    #: the choice survives leaving the page (build 90).
    reacquire_mode: str | None = Field(
        default=None, pattern="^(backup|replace|when_replaced|keep)$"
    )

    calendar_hide_in_library: bool | None = None
    calendar_hide_adult: bool | None = None
    calendar_hide_no_cover: bool | None = None
    calendar_auto_update: bool | None = None
    time_format: str | None = None
    date_format: str | None = None
    calendar_hidden_kinds: str | None = None
    calendar_mark_sequels: bool | None = None
    calendar_show_popularity: bool | None = None
    calendar_sort: str | None = Field(default=None, pattern="^(airing|name|popularity|recommended)$")

    # Was missing entirely, so the toggle could neither be saved nor read back.
    rss_enabled: bool | None = None
    rss_scan_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    torrent_monitor_interval_seconds: int | None = Field(default=None, ge=5, le=3600)

    #: Everything else that runs on a timer - the Scheduling feature. See
    #: services/app_settings.py::AppSettings for what each one governs.
    torrent_monitor_enabled: bool | None = None
    anidb_identity_refresh_enabled: bool | None = None
    anidb_identity_refresh_interval_seconds: int | None = Field(default=None, ge=3600, le=2592000)
    shoko_status_enabled: bool | None = None
    shoko_status_interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    torrent_housekeeping_enabled: bool | None = None
    torrent_housekeeping_interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    attention_review_enabled: bool | None = None
    attention_review_interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    calendar_auto_update_interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    conversion_review_enabled: bool | None = None
    conversion_review_interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    disk_recheck_enabled: bool | None = None
    disk_recheck_interval_seconds: int | None = Field(default=None, ge=60, le=604800)

    metadata_provider: str | None = Field(default=None, pattern="^(anilist|kitsu)$")

    toast_corner: str | None = Field(
        default=None, pattern="^(top-right|top-left|bottom-right|bottom-left)$"
    )
    season_rollover: str | None = Field(default=None, pattern="^(ask|auto|off)$")
    automate_new_anime: bool | None = None

    #: MyAnimeList's API client id, for the Calendar sources experiment.
    #: Accepted and returned but not rendered anywhere yet: the source is
    #: being tried, not adopted, and the id is set in aniflow.yaml. Declared
    #: all the same, because a setting the API cannot reach is how build 70
    #: shipped a page that silently refused to save.
    mal_client_id: str | None = Field(default=None, max_length=120)

    shoko_url: str | None = Field(default=None, max_length=300)
    shoko_api_key: str | None = Field(default=None, max_length=200)

    #: "" | "basic" | "advanced". A testing aid; see services/app_mode.py.
    app_mode_override: str | None = Field(default=None, pattern="^(|basic|advanced)$")

    #: Internet-facing requests only. See services/outbound.py.
    proxy_kind: str | None = Field(default=None, pattern="^(none|http|socks4|socks5|socks5h)$")
    proxy_host: str | None = Field(default=None, max_length=255)
    proxy_port: int | None = Field(default=None, ge=1, le=65535)
    proxy_username: str | None = Field(default=None, max_length=120)
    proxy_password: str | None = Field(default=None, max_length=200)
    proxy_check_url: str | None = Field(default=None, max_length=300)

    #: Comma-separated, most-wanted first. Validated by the parser, which
    #: drops unknown names rather than refusing the whole save.
    title_search_order: str | None = Field(default=None, max_length=120)
    title_folder_order: str | None = Field(default=None, max_length=120)
    #: What the interface calls an anime. Display only (build 85).
    title_display_order: str | None = Field(default=None, max_length=120)
    #: Which look to use.
    theme: str | None = Field(default=None, max_length=60)
    #: Whether the interface offers the developer tools (build 94).
    #:
    #: Build 89 left this out on purpose - a switch that hides itself is a
    #: thing to press by accident - and it is now asked for, so that a
    #: release build can be tried without editing a file. The warning travels
    #: with the control instead.
    debug_mode: bool | None = None

    search_enabled: bool | None = None
    search_max_pages: int | None = Field(default=None, ge=1, le=10)
    search_max_results: int | None = Field(default=None, ge=1, le=1000)

    log_level: str | None = Field(default=None, pattern="^(ERROR|WARNING|INFO|DEBUG)$")
    log_components: str | None = Field(default=None, max_length=500)
    log_component_levels: str | None = Field(default=None, max_length=1000)

    # An IANA name (Europe/Berlin), or empty to follow the container's TZ.
    # Not pattern-checked: the list is the tz database, and an unknown name is
    # answered with a clear fallback rather than a validation error.
    timezone: str | None = Field(default=None, max_length=64)

    language: str | None = Field(default=None, pattern="^(en|de)$")


class StoragePath(BaseModel):
    """One storage root, as the Settings page shows it.

    ``container`` is the same on every install and answers nothing on its own;
    ``host`` is the folder it was mounted from, which is the one the user
    recognises. ``host`` is null when it cannot be determined rather than
    guessed at.
    """

    key: str
    container: str | None = None
    host: str | None = None
    exists: bool = False
    writable: bool = False
    #: False for a root this mode never uses, so the page can say so instead of
    #: reporting a missing folder as a problem.
    used: bool = True


class SettingsResponse(BaseModel):
    torrent_client: str
    qb_url: str | None
    qb_username: str
    qb_category: str
    has_qb_password: bool
    anime_library_root: str
    download_root: str
    # Where these settings are stored, so the page can point at the file.
    config_file: str

    # Read-only: set in docker-compose, recorded in the database on first run.
    mode: str
    # The hand-over folder, in Shoko Server mode. Null in standalone mode.
    import_root: str | None
    #: The storage roots with their host folders and whether they are usable.
    storage_paths: list[StoragePath] = []
    file_operation: str
    after_import_action: str
    reacquire_mode: str = "backup"

    calendar_hide_in_library: bool
    calendar_hide_adult: bool
    calendar_hide_no_cover: bool
    calendar_auto_update: bool
    time_format: str
    date_format: str
    calendar_hidden_kinds: str
    calendar_mark_sequels: bool
    calendar_show_popularity: bool
    calendar_sort: str

    metadata_provider: str
    toast_corner: str
    season_rollover: str
    automate_new_anime: bool = True

    #: Not a secret: a client id names the application rather than
    #: authorising anything, and MyAnimeList's own documentation has clients
    #: send it from a browser.
    mal_client_id: str = ""

    #: Shoko Server's API. The key is never echoed back, only whether one is
    #: stored - the same rule as every other secret here.
    shoko_url: str
    has_shoko_api_key: bool = False

    app_mode_override: str
    #: What the interface should actually offer, after the rule and the
    #: override have both been applied. Read-only.
    app_mode: str = "advanced"
    #: Whether anything is configured that could find a release at all.
    has_indexer: bool = False

    proxy_kind: str
    proxy_host: str
    proxy_port: int | None = None
    proxy_username: str
    has_proxy_password: bool = False
    proxy_check_url: str

    title_search_order: str
    title_folder_order: str
    title_display_order: str
    #: Editable since build 94, and still written to aniflow.yaml.
    debug_mode: bool = True
    theme: str = "aniflow-dark"

    rss_enabled: bool
    rss_scan_interval_minutes: int
    torrent_monitor_interval_seconds: int

    torrent_monitor_enabled: bool
    anidb_identity_refresh_enabled: bool
    anidb_identity_refresh_interval_seconds: int
    shoko_status_enabled: bool
    shoko_status_interval_seconds: int
    torrent_housekeeping_enabled: bool
    torrent_housekeeping_interval_seconds: int
    attention_review_enabled: bool
    attention_review_interval_seconds: int
    calendar_auto_update_interval_seconds: int
    conversion_review_enabled: bool
    conversion_review_interval_seconds: int
    disk_recheck_enabled: bool
    disk_recheck_interval_seconds: int

    search_enabled: bool
    search_max_pages: int
    search_max_results: int

    log_level: str
    log_components: str | None
    log_component_levels: str | None

    timezone: str
    #: The zone actually in force, which is the TZ variable when the setting is
    #: empty - and UTC when neither resolves.
    effective_timezone: str = ""
    #: Set when the configured name could not be resolved on this host, so the
    #: page can say "asked for X, running on UTC" instead of implying X works.
    timezone_unresolved: str | None = None

    language: str


class SettingsTestResponse(BaseModel):
    ok: bool
    detail: str | None = None


class ExitAddressResponse(BaseModel):
    """Which address the internet sees AniFlow coming from.

    A proxy that is configured but not working looks exactly like one that is
    working, until something fails hours later - so this is a button, and its
    answer says whether the request went through the proxy at all.
    """

    ok: bool = False
    #: False means the answer describes a *direct* connection, which is worth
    #: saying out loud when a proxy was expected.
    proxied: bool = False
    ip: str | None = None
    country: str | None = None
    #: The country as a flag emoji, built from its code - no image, no lookup.
    flag: str | None = None
    detail: str | None = None


class ShokoTestRequest(BaseModel):
    """Draft credentials, so Test works before anything is saved."""

    url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=200)


class PathCheckRequest(BaseModel):
    """Connection details to check with.

    Optional: sent by the Settings page so the button works on values that are
    on screen but not saved yet. Anything omitted falls back to what is stored.
    """

    qb_url: str | None = Field(default=None, max_length=300)
    qb_username: str | None = Field(default=None, max_length=80)
    qb_password: str | None = Field(default=None, max_length=200)
    #: Which client the draft is for (the install wizard checks before anything is saved); the stored one when missing.
    client: str | None = Field(default=None, pattern="^(qbittorrent|deluge|transmission)$")


class PathCheckResponse(BaseModel):
    ok: bool
    message: str
    qb_default_save_path: str | None = None
    aniflow_download_root: str
    aniflow_library_root: str
    qb_category: str | None = None
    translated_example: str | None = None
    sample_content_path: str | None = None
    sample_translated: str | None = None
    sample_within_root: bool | None = None
    # The full source -> target pair the importer would use for the newest
    # download, so the panel shows where a file would actually land.
    sample_source_path: str | None = None
    sample_target_path: str | None = None
    sample_media_folder: str | None = None
    download_root_exists: bool | None = None
    download_root_writable: bool | None = None
    library_root_exists: bool | None = None
    library_root_writable: bool | None = None


class QBittorrentTestRequest(BaseModel):
    """Unsaved draft values; missing fields fall back to stored settings."""

    url: str | None = Field(default=None, max_length=300)
    username: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)
    #: Which client the draft is for; the stored choice when missing.
    client: str | None = Field(default=None, pattern="^(qbittorrent|deluge|transmission)$")


