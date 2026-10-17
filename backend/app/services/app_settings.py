"""Effective application settings: environment defaults + the config file.

Settings live in ``CONFIG_DIR/aniflow.yaml`` (see services/config_file.py), an
editable file that the Settings UI and a text editor both write. The database
holds AniFlow's *data*, not its configuration.

Secrets (torrent client password, media server API key) are never returned by
the API - the UI only sees whether they are set.
"""
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.models.setting import Setting
from backend.app.services.config_file import (
    config_path,
    read_config_file,
    write_config_file,
)

logger = for_component("Config")


@dataclass
class AppSettings:
    """Everything editable through the settings UI."""

    torrent_client: str
    qb_url: str | None
    qb_username: str
    qb_password: str
    qb_category: str

    file_operation: str
    after_import_action: str
    #: What a release change does with the existing files (build 90).
    reacquire_mode: str

    calendar_hide_in_library: bool
    calendar_hide_adult: bool
    #: Hide the shows no source had artwork for - mostly shorts and obscure
    #: listings (build 86).
    calendar_hide_no_cover: bool
    #: airing | name | popularity.
    calendar_sort: str
    calendar_auto_update: bool
    time_format: str
    date_format: str
    #: Kinds to leave out, upper case: "MUSIC,CM,PV". A list rather than a
    #: flag each, so a kind a source invents next year shows rather than
    #: needing a migration - appearing is the safer failure (build 101).
    calendar_hidden_kinds: str
    #: Mark an entry when another season of the same show is already tracked,
    #: and mark it differently when a season in between is missing.
    calendar_mark_sequels: bool
    #: Show how many people have it, as one to five stars relative to the
    #: season on screen.
    calendar_show_popularity: bool
    #: Mark the entries carrying no AniDB id - the ones Shoko and AniFlow can
    #: only recognise by name. Never hidden by default: that would drop about
    #: a third of a season before anybody touched a setting.


    rss_enabled: bool
    rss_scan_interval_minutes: int
    torrent_monitor_interval_seconds: int

    #: Everything below is the Scheduling feature: every other background
    #: pass, each with its own on/off switch and interval, in seconds unless
    #: named otherwise. See worker/tasks.py for what each one actually does -
    #: the names here are deliberately the same as the task ids.
    torrent_monitor_enabled: bool
    #: The AniDB title dump and the id-mapping list are never asked for more
    #: often than once a week regardless of this value - see the note on
    #: worker._run_anidb_identity_job.
    anidb_identity_refresh_enabled: bool
    anidb_identity_refresh_interval_seconds: int
    shoko_status_enabled: bool
    shoko_status_interval_seconds: int
    #: Orphan-adoption, reconcile and torrent-sweep, combined into one task
    #: (build 160) - see worker/runtime.py's ``_run_housekeeping_job``.
    torrent_housekeeping_enabled: bool
    torrent_housekeeping_interval_seconds: int
    attention_review_enabled: bool
    attention_review_interval_seconds: int
    #: How often to check whether the calendar needs updating - the enabled
    #: flag stays ``calendar_auto_update`` above. Since build 160 this check
    #: no longer skips a "not stale yet" week; it just runs the full
    #: current-plus-two-seasons update every time it is due.
    calendar_auto_update_interval_seconds: int
    conversion_review_enabled: bool
    conversion_review_interval_seconds: int
    #: Re-checks every anime's linked media folder against the database on a
    #: timer (build 160) - see worker/runtime.py's ``_run_disk_recheck_job``.
    disk_recheck_enabled: bool
    disk_recheck_interval_seconds: int

    #: "anilist" | "kitsu" - the one source AniFlow asks.
    metadata_provider: str

    #: "top-right" | "top-left" | "bottom-right" | "bottom-left".
    toast_corner: str
    season_rollover: str
    #: Automate starts on for a new anime, and for a future entry once its group is chosen (build 239).
    automate_new_anime: bool

    #: MyAnimeList's API client id, for the Calendar sources experiment.
    #: No Settings field yet - it is being tried, not adopted - but it is
    #: written and read here so it survives a settings save like everything
    #: else in the file. Not a secret field: a client id identifies the
    #: application rather than authorising anything, and MyAnimeList's own
    #: documentation has clients embed it in browser requests.
    mal_client_id: str

    #: Shoko Server's API. Read-only; the folder hand-over needs neither.
    shoko_url: str
    shoko_api_key: str

    #: Testing aid: "basic" | "advanced" | "" (decide from the indexers).
    app_mode_override: str

    #: See services/outbound.py - internet-facing requests only.
    proxy_kind: str
    proxy_host: str
    proxy_port: int | None
    proxy_username: str
    proxy_password: str
    proxy_check_url: str

    #: Whether the interface offers the developer tools. aniflow.yaml only -
    #: there is no control for it, by design (build 89).
    debug_mode: bool
    #: Which look the interface uses. One exists; build 90 adds the rest.
    theme: str

    #: Ordered, most-wanted first. See services/title_preference.py.
    title_search_order: str
    title_folder_order: str
    #: What the interface calls an anime. Display only - no filename and no
    #: search depends on it (build 85).
    title_display_order: str

    search_enabled: bool
    search_max_pages: int
    search_max_results: int

    #: Timezone for displayed times and log timestamps. Empty falls back to
    #: the TZ environment variable, then UTC.
    timezone: str

    log_level: str
    log_components: str | None
    log_component_levels: str | None

    language: str

    @property
    def qb_configured(self) -> bool:
        return bool(self.qb_url)

    @property
    def shoko_configured(self) -> bool:
        return bool(self.shoko_url and self.shoko_api_key)

    @property
    def moves_files(self) -> bool:
        """Whether importing takes the file away from the download folder."""
        return self.file_operation == "move"


# Fields that must never leave the backend once set.
_SECRET_FIELDS = {"qb_password", "proxy_password", "shoko_api_key"}

#: What a blank torrent category falls back to.
#:
#: Blank is not a neutral value for this setting, which is why it is refused
#: rather than passed on. AniFlow asks qBittorrent for "the torrents in this
#: category" and builds the download path out of it, and qBittorrent reads an
#: empty category as *"the torrents with no category at all"* - which is
#: everyone else's downloads, not AniFlow's none.
_CATEGORY_FALLBACK = "AniFlow"

#: Values already complained about, so a bad one is reported once rather than
#: on every settings read.
_category_warned: set[str] = set()


def clean_category(value: object) -> str:
    """The torrent category as it will actually be used: trimmed, never blank.

    Leading and trailing spaces are removed (build 133). A space is invisible
    in a settings field and invisible in a YAML file, and it does not stay
    invisible afterwards: ``"AniFlow "`` is a *different* qBittorrent category
    from ``"AniFlow"``, and AniFlow would then look for its downloads in a
    folder whose name ends in a space. Reported as *"having a space seems like
    a bug"* - it is.

    Spaces *inside* the name are left alone. "Ani Flow" is a legitimate
    category and a legitimate folder name, and renaming it is not this
    function's business.
    """
    text = str(value or "").strip()
    if text:
        return text
    env_default = str(get_settings().qb_category or "").strip()
    return env_default or _CATEGORY_FALLBACK


def _note_corrected_category(raw: object, used: str) -> None:
    """Say once that the configured category could not be used as written."""
    text = "" if raw is None else str(raw)
    if text == used or text in _category_warned:
        return
    _category_warned.add(text)
    logger.warning(
        "the torrent category %r cannot be used as written; using %r instead"
        " - a blank category makes qBittorrent answer with every"
        " uncategorised torrent in the client",
        text,
        used,
    )


def _defaults() -> dict[str, object]:
    env = get_settings()
    return {
        "torrent_client": env.torrent_client,
        "qb_url": env.qb_url,
        "qb_username": env.qb_username,
        "qb_password": env.qb_password,
        "qb_category": env.qb_category,
        "file_operation": env.file_operation,
        "after_import_action": env.after_import_action,
        "reacquire_mode": env.reacquire_mode,
        "calendar_hide_in_library": env.calendar_hide_in_library,
        "calendar_hide_adult": env.calendar_hide_adult,
        "calendar_hidden_kinds": env.calendar_hidden_kinds,
        "calendar_mark_sequels": env.calendar_mark_sequels,
        "calendar_show_popularity": env.calendar_show_popularity,
        "calendar_hide_no_cover": env.calendar_hide_no_cover,
        "calendar_sort": env.calendar_sort,
        "calendar_auto_update": env.calendar_auto_update,
        "time_format": env.time_format,
        "date_format": env.date_format,
        "rss_enabled": env.rss_enabled,
        "rss_scan_interval_minutes": env.rss_scan_interval_minutes,
        "torrent_monitor_interval_seconds": env.torrent_monitor_interval_seconds,
        "torrent_monitor_enabled": env.torrent_monitor_enabled,
        "anidb_identity_refresh_enabled": env.anidb_identity_refresh_enabled,
        "anidb_identity_refresh_interval_seconds": env.anidb_identity_refresh_interval_seconds,
        "shoko_status_enabled": env.shoko_status_enabled,
        "shoko_status_interval_seconds": env.shoko_status_interval_seconds,
        "torrent_housekeeping_enabled": env.torrent_housekeeping_enabled,
        "torrent_housekeeping_interval_seconds": env.torrent_housekeeping_interval_seconds,
        "attention_review_enabled": env.attention_review_enabled,
        "attention_review_interval_seconds": env.attention_review_interval_seconds,
        "calendar_auto_update_interval_seconds": env.calendar_auto_update_interval_seconds,
        "conversion_review_enabled": env.conversion_review_enabled,
        "conversion_review_interval_seconds": env.conversion_review_interval_seconds,
        "disk_recheck_enabled": env.disk_recheck_enabled,
        "disk_recheck_interval_seconds": env.disk_recheck_interval_seconds,
        "metadata_provider": env.metadata_provider,
        "toast_corner": env.toast_corner,
        "season_rollover": env.season_rollover,
        "automate_new_anime": env.automate_new_anime,
        "mal_client_id": env.mal_client_id,
        "shoko_url": env.shoko_url,
        "shoko_api_key": env.shoko_api_key,
        "app_mode_override": env.app_mode_override,
        "proxy_kind": env.proxy_kind,
        "proxy_host": env.proxy_host,
        "proxy_port": env.proxy_port,
        "proxy_username": env.proxy_username,
        "proxy_password": env.proxy_password,
        "proxy_check_url": env.proxy_check_url,
        "title_search_order": env.title_search_order,
        "title_folder_order": env.title_folder_order,
        "title_display_order": env.title_display_order,
        "debug_mode": env.debug_mode,
        "theme": env.theme,
        "search_enabled": env.search_enabled,
        "search_max_pages": env.search_max_pages,
        "search_max_results": env.search_max_results,
        "timezone": env.timezone or (os.environ.get("TZ") or "").strip(),
        "log_level": env.log_level,
        "log_components": env.log_components,
        "log_component_levels": env.log_component_levels,
        "language": "en",
    }


#: How a time may be written. 24-hour is the default (build 146).
TIME_FORMATS = ("24h", "12h")

#: How a date may be written: day-first by default.
DATE_FORMATS = ("dmy", "mdy", "ymd")


def validate_combination(values: dict[str, object]) -> None:
    """Reject settings that contradict each other.

    Moving the file out of the download folder and leaving the torrent seeding
    are mutually exclusive: the data the torrent would serve is gone. The UI
    disables the pairing, and this catches anyone calling the API directly.
    """
    if values.get("file_operation") == "move" and values.get("after_import_action") == "keep":
        raise ValueError(
            "A moved file cannot keep seeding - the download folder no longer "
            "has it. Choose 'remove' or 'pause' for the torrent, or copy instead "
            "of moving."
        )
    # Refused rather than silently corrected: the config file is
    # hand-editable, and a typo that quietly becomes the default is a typo
    # somebody keeps making.
    for field, allowed in (("time_format", TIME_FORMATS), ("date_format", DATE_FORMATS)):
        value = values.get(field)
        if value is not None and value not in allowed:
            raise ValueError(
                f"{field} must be one of {', '.join(allowed)} (got {value!r})"
            )


def load_app_settings(session: Session | None = None) -> AppSettings:
    """The effective settings: environment defaults with the file layered on top.

    ``session`` is accepted for call-site compatibility and is not read - the
    configuration no longer lives in the database. It is still required by the
    one-time import in :func:`migrate_settings_from_database`.
    """
    values = _defaults()
    values.update(read_config_file())
    # Checked on every read rather than only on save, because the file is
    # hand-editable and is never replaced by a deploy - so a value AniFlow
    # itself never wrote is the normal case, not the exception (build 133).
    raw_category = values.get("qb_category")
    values["qb_category"] = clean_category(raw_category)
    _note_corrected_category(raw_category, values["qb_category"])
    return AppSettings(**values)


#: Guards the read-modify-write below (build 162). `write_config_file` itself
#: is atomic per call, but the read of the *current* file happens first and
#: separately - two concurrent saves (the Schedule page's "Reset all",
#: pressed once, fires one request per task) each read the same starting
#: file, merge their own field in, and write back, so the second write can
#: both lose the first one's change and collide with it mid-``os.replace`` on
#: Windows. Reported as 3 of 9 concurrent resets coming back 500.
_save_lock = threading.Lock()


def save_app_settings(session: Session | None, updates: dict[str, object]) -> None:
    """Persist the provided keys to the config file; the rest keeps its value.

    The whole file is rewritten from the effective settings, so it always shows
    every setting explicitly - which is what makes it editable by hand.
    """
    known = {f.name for f in fields(AppSettings)}
    with _save_lock:
        values = asdict(load_app_settings())
        for key, value in updates.items():
            if key in known:
                values[key] = value
        # Saved as it will be used, so the file and the behaviour cannot
        # disagree and a typed-in space is corrected where the user can see
        # it (build 133).
        values["qb_category"] = clean_category(values.get("qb_category"))
        write_config_file(values)


def repair_config_file() -> Path | None:
    """Correct a config file value that cannot be used as written, once (133).

    Normalising on read keeps AniFlow working, but the file is the thing the
    user edits and the thing that survives a deploy, so a value that had to be
    corrected is corrected *there* as well - otherwise the file keeps saying
    one thing while AniFlow does another, for ever.

    Only the torrent category so far, and only when it actually differs.
    Returns the path written, or None when there was nothing to do.
    """
    stored = read_config_file()
    if "qb_category" not in stored:
        return None
    cleaned = clean_category(stored.get("qb_category"))
    if stored.get("qb_category") == cleaned:
        return None
    values = asdict(load_app_settings())
    values["qb_category"] = cleaned
    path = write_config_file(values)
    logger.info(
        "the torrent category in %s was %r and has been corrected to %r",
        path,
        stored.get("qb_category"),
        cleaned,
    )
    return path


def migrate_settings_from_database(session: Session) -> Path | None:
    """One-time import of settings that were stored in the database.

    Runs at startup. It only ever *creates* the file: once a config file exists
    it is the source of truth, and the leftover database rows are ignored.
    Returns the path written, or None when there was nothing to do.
    """
    if config_path().exists():
        return None
    stored = {
        setting.key: setting.value
        for setting in session.scalars(select(Setting))
        if setting.key in {f.name for f in fields(AppSettings)}
    }
    values = _defaults()
    values.update(stored)
    try:
        path = write_config_file(values)
    except OSError as exc:
        logger.error("could not create the config file (%s); using defaults", exc)
        return None
    if stored:
        logger.info("imported %s setting(s) from the database into %s", len(stored), path)
    else:
        logger.info("created a starter config file at %s", path)
    return path


def mask_secrets(values: dict[str, object]) -> dict[str, object]:
    masked = dict(values)
    for key in _SECRET_FIELDS:
        present = bool(masked.get(key))
        masked[f"has_{key}"] = present
        masked.pop(key, None)
    return masked


__all__ = ["AppSettings", "load_app_settings", "mask_secrets", "save_app_settings"]
