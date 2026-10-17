from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    # 9630: 8000 collides with half the self-hosted world. The container
    # serves on this port and the Dockerfile exposes it.
    app_port: int = Field(default=9630, ge=1, le=65535)
    # Errors only, by default. INFO is what you want while working on AniFlow
    # and not what a running install needs: it wrote a line for every pass of
    # every worker job. Raise it on the Settings page when chasing something.
    #
    # The one-time setup code is unaffected - it is announced through a path
    # no level can filter (see services/auth.log_token), because a code nobody
    # can read is the same as no way in.
    log_level: str = "ERROR"
    # Where the editable configuration file lives (``CONFIG_DIR/aniflow.yaml``).
    # Deliberately separate from the database, which stays under DATABASE_URL:
    # one folder holds settings a human edits, the other holds AniFlow's data.
    config_dir: Path | None = None
    # Which timezone to present times in. Empty means "use the TZ environment
    # variable", which is what the container sets; the settings file can
    # override it without a redeploy.
    timezone: str = ""
    # Writable state that is not the database: uploaded covers, for now. Same
    # volume as the database, so a backup of ./data carries both.
    data_dir: Path = Path("/data")
    database_url: str = "sqlite:////data/aniflow.db"
    frontend_origins: str = "http://localhost:5173,http://localhost:9630"
    # Container-side mount points. Fixed by convention (see docker-compose);
    # only the HOST paths are configurable in .env.
    #
    # AniFlow knows three storage stages: download -> import -> media.
    # ``anime_library_root`` is the media root (the canonical library).
    # ``import_root`` is the hand-over folder Shoko Server reads from. Like
    # the other two it is a fixed container path - mounting the volume is all
    # a deployment has to do. Nothing reads it in standalone mode.
    anime_library_root: Path = Path("/media")
    download_root: Path = Path("/downloads")
    import_root: Path | None = Path("/import")
    # Where the library folder picker starts looking (build 192). Every folder
    # the user mounts in docker-compose is a directory under it; the picker
    # hides the container's own (see services/libraries.py). Overridable so a
    # test can point it at a temporary directory.
    library_browse_root: Path = Path("/")

    # Which storage pipeline runs. Set in docker-compose and effectively
    # permanent for a library - see core/mode.py for why it is not a setting.
    # ``MODE`` is accepted because that is what docker-compose reads most
    # naturally, but ``ANIFLOW_MODE`` is the documented name: a bare ``MODE``
    # in a container environment belongs to nobody in particular.
    mode: str = Field(
        default="standalone",
        pattern="^(standalone|shokoserver)$",
        validation_alias=AliasChoices("ANIFLOW_MODE", "MODE", "mode"),
    )
    # Season calendar view options. Stored because they are the user's choice,
    # not a per-visit whim.
    calendar_hide_in_library: bool = True
    calendar_hide_adult: bool = True
    rss_enabled: bool = True
    # 30 minutes since build 252 (it was 60; an episode of a busy night fell off the feed between scans): the interval indexers ask
    # for, while short enough that items cannot scroll off a busy feed
    # between scans (a feed only exposes its most recent N entries).
    rss_scan_interval_minutes: int = Field(default=30, ge=1)
    # Seconds, not minutes: a one-minute floor made download progress look
    # frozen in the UI, which refreshes every 1.5s.
    torrent_monitor_interval_seconds: int = Field(default=30, ge=5, le=3600)

    # Everything below belongs to the Scheduling feature: every other
    # background pass gets an on/off switch and a configurable interval,
    # following the same shape as the two fields above. See
    # worker/tasks.py for what each task actually does.
    torrent_monitor_enabled: bool = True
    # Never asked for more than once a week regardless of this value - see
    # the note on worker._run_anidb_identity_job. The floor still keeps
    # somebody from setting a pointlessly tight check cadence.
    anidb_identity_refresh_enabled: bool = True
    anidb_identity_refresh_interval_seconds: int = Field(default=604800, ge=3600, le=2592000)
    shoko_status_enabled: bool = True
    # 1 hour (build 161, agreed with the user - raised again from build 159's
    # 15 minutes): a ping this cheap does not need to be that eager.
    shoko_status_interval_seconds: int = Field(default=3600, ge=60, le=604800)
    # Orphan-adoption, reconcile and torrent-sweep combined into one task
    # (build 160, agreed with the user): the three used to be separate rows
    # doing overlapping "is this download row still telling the truth" work
    # on three separate timers. Sweep still only runs inline (right after an
    # import) or when this task's own "Run" is pressed by hand - see
    # worker.runtime._run_housekeeping_job.
    torrent_housekeeping_enabled: bool = True
    # 6 hours (build 161: was defaulted to 30 minutes at build 160 by
    # judgement call, without asking - the user's correction).
    torrent_housekeeping_interval_seconds: int = Field(default=21600, ge=60, le=604800)
    attention_review_enabled: bool = True
    attention_review_interval_seconds: int = Field(default=1800, ge=60, le=604800)
    # The check cadence only. Changed at build 160, at the user's request:
    # used to check hourly whether the current season alone had gone stale;
    # now unconditionally refreshes the current season plus the next two,
    # once a week - see worker._run_calendar_auto_update_task.
    calendar_auto_update_interval_seconds: int = Field(default=604800, ge=60, le=604800)
    conversion_review_enabled: bool = True
    # Six hours, not one (build 160, agreed with the user): what this task
    # raises - the "convert to an indexer" nudge, and a "future" entry whose
    # premiere date has passed - changes on the order of hours to days, never
    # minutes, so an hour was still checking far more often than either
    # reason needs.
    conversion_review_interval_seconds: int = Field(default=21600, ge=60, le=604800)
    # Built at build 160 (STATE.md open item 5): re-checks every anime's
    # linked media folder against the database, the same reconciliation
    # `library.scan_anime_folder` already does for a manual Rescan, just
    # unattended and library-wide. Six hours: slow enough not to matter, and
    # a person can still press Run for an answer right now.
    disk_recheck_enabled: bool = True
    disk_recheck_interval_seconds: int = Field(default=21600, ge=60, le=604800)
    anilist_api_url: str = "https://graphql.anilist.co"
    # The second opinion, asked only when AniList cannot be reached. Kitsu
    # runs its own database on its own infrastructure, which is the point:
    # Jikan sat in front of MyAnimeList and went down with it. No key, no
    # registration. See services/metadata.py for what does and does not fall
    # back - ids never do.
    kitsu_api_url: str = "https://kitsu.io/api/edge"
    external_request_timeout_seconds: float = Field(default=15, gt=0, le=60)

    # qBittorrent WebUI integration. Credentials stay deployment configuration
    # and are never logged or returned by the API.
    # Which torrent client to talk to. Only qBittorrent is implemented; the
    # field exists so a second client can be added without changing how
    # settings are shaped.
    torrent_client: str = Field(default="qbittorrent", pattern="^(qbittorrent|deluge|transmission)$")
    qb_url: str | None = None
    qb_username: str = "admin"
    qb_password: str = ""
    qb_category: str = "AniFlow"
    # The category prefix is auto-stripped: qB stores under
    # <download_root>/<category>/ but AniFlow maps the file into
    # <download_root>/ directly. Both containers are expected to mount the
    # download folder on the same absolute path; there is no remote-root knob.

    # How a finished download reaches its next stage - the library in
    # standalone mode, the import folder in Shoko Server mode.
    # copy: the download stays put and can keep seeding (default).
    # move: the file is relocated, so seeding cannot continue.
    # hardlink: a second name for the same data (build 272) - one copy on disk, and the download keeps seeding. Needs
    #   the download folder and the library on one filesystem and one mount.
    file_operation: str = Field(default="copy", pattern="^(copy|move|hardlink)$")
    # What happens to the torrent once its episode is in the library:
    #   pause  - stop seeding, keep it in the client (visible confirmation)
    #   remove - delete the torrent entry, never the files
    #   keep   - leave it running
    after_import_action: str = Field(default="pause", pattern="^(pause|remove|keep)$")

    # What the release-change panel does with the files you already have
    # (build 90). Remembered because it was not, and re-choosing it on every
    # visit is how somebody eventually deletes a season they meant to keep.
    #
    #   backup  - rename each file to .backup and leave it (the safe default)
    #   replace - delete them
    reacquire_mode: str = Field(default="backup", pattern="^(backup|replace|when_replaced|keep)$")

    # No media-server integration (removed in build 233): a library refresh was all it did, and a media server -
    # Jellyfin or Plex - should be offered something worth connecting for before it comes back.

    # Apply pending Alembic migrations automatically at startup. Keep enabled
    # unless migrations are managed externally (Docker CMD already runs them;
    # a second run is idempotent).
    auto_migrate: bool = True

    # Search limits. Which sites are searched is configured as indexers,
    # not here: AniFlow ships none and the user adds them.
    # Which of an anime's titles to prefer, most-wanted first. Two separate
    # orders because they answer different questions: one is taste (what to
    # call the folder), the other decides whether a search finds anything.
    # Which metadata source AniFlow asks. One at a time, by choice: two
    # sources disagree about ids, titles and episode counts, and a fallback
    # chain means a library half-built from each. See services/metadata.py.
    metadata_provider: str = "anilist"

    # Which corner short-lived notifications appear in. Per-deployment
    # rather than per-browser because it is a preference about a screen, and
    # the screen is usually the same one.
    toast_corner: str = "top-right"

    # What happens when a season changes (build 205): ask | auto | off. Moving an
    # anime out of Upcoming is always automatic; this is about the moves that
    # carry folders - finished and waiting shows to the earlier seasons, and a
    # show found airing again back to the current one.
    season_rollover: str = "ask"

    # Whether an anime that is added, or given its release group later, starts with Automate on
    # (build 239). On: adding is the decision to have AniFlow look after it.
    automate_new_anime: bool = True

    # Whether the interface offers the developer tools (build 89).
    #
    # Gates the Debug tab, the Release Inspector, the repair tools and the
    # database record: everything that reaches behind the normal flow.
    #
    # Build 89 gave it no control on the Settings screen, on the grounds that
    # a switch which hides itself is a thing to press by accident. Build 94
    # adds one anyway, asked for so that a release build can be tried without
    # editing this file - with the warning kept beside it. Turning it off in
    # the interface hides the control as well, and this file turns it back on.
    #
    # Off by default. It was on while AniFlow was being built, and a fresh
    # install is no longer that: the first one anybody set up came with the
    # developer's tools on display. The tools stay in the code rather than
    # being deleted - the difference between shipping them and offering them -
    # and `debug_mode: true` in aniflow.yaml brings them back.
    debug_mode: bool = False

    # Which look the interface uses (build 89). Only one exists; the fields
    # are here so the Settings screen can describe a theme, and the system
    # that loads a second one is build 90's.
    theme: str = "aniflow-dark"

    # The calendar's own filters, remembered per deployment rather than per
    # browser: they are preferences about a screen, and it is usually the same
    # screen. Adult content is hidden by default - a filter that protects you
    # only after you have found the thing it was meant to hide has already
    # failed once.
    calendar_hide_no_cover: bool = False
    #: Kinds the calendar leaves out, upper case and comma-separated.
    #:
    #: Music videos, adverts and trailers by default - which is what the
    #: merge did for everybody until build 101. It is a preference now, so
    #: somebody who wants the music videos can have them.
    calendar_hidden_kinds: str = "MUSIC,CM,PV"
    calendar_mark_sequels: bool = True
    calendar_show_popularity: bool = True
    # How the calendar orders a season: airing | name | popularity.
    calendar_sort: str = "airing"

    #: Whether AniFlow may update the calendar without being asked (build 144).
    #:
    #: Off for a while, at the user's request while Calendar v2 was being
    #: worked on: *"can we for now disable automatic updates all of them"*.
    #: On again from build 233, after a fresh install ended in an empty calendar
    #: and an existing one sat two weeks behind with its Schedule row greyed out:
    #: *"lets enable this again"*. With it on the worker keeps the current and
    #: the next two seasons up to date (weekly), and navigating to a season
    #: nobody has loaded fetches it. An install whose settings file already says
    #: ``calendar_auto_update: false`` keeps that until it is switched on from
    #: the Schedule page - a stored choice is never overridden by a new default.
    #:
    #: A setting rather than deleted code, because the behaviour itself is
    #: right: a calendar that never fills itself in is a calendar somebody has
    #: to remember to maintain.
    calendar_auto_update: bool = True

    #: How to write a time: ``24h`` or ``12h`` (build 146).
    #:
    #: Reported as *"the date and time is US, I do not like this"*. The
    #: browser's own locale was deciding, which on a machine set to US English
    #: means 04:30 PM and 7/3/2026 however European the person reading it is.
    #: A setting rather than a guess, because the browser's locale is a fact
    #: about the machine and this is a preference about the page.
    time_format: str = "24h"
    #: ``dmy`` (default), ``mdy`` or ``ymd``.
    date_format: str = "dmy"

    # What the interface calls an anime: the Calendar and the Watchlist show
    # the first of these an anime actually has. Display only - the folder name
    # and the search terms have their own orders, because they answer
    # different questions. See services/title_preference.py.
    title_display_order: str = "english,romaji,alternative,japanese"

    # MyAnimeList's API client id, for the metadata experiment (build 84).
    # Only an id - MyAnimeList's read-only endpoints need no OAuth, no user
    # login and no redirect URL, so there is nothing else to configure and
    # nothing to keep fresh. Empty means the source reports itself as needing
    # a credential, which is how every other token-gated source behaves.
    # No Settings field yet, on purpose: it is being tried, not adopted.
    mal_client_id: str = ""

    # Shoko Server's API, for reading what Shoko knows. The file hand-over
    # needs none of this and keeps working without it - see services/shoko.py.
    # The key is Shoko's own API key from its settings; AniFlow never asks for
    # a Shoko username and password, because holding the credentials to
    # somebody's whole media server to read an episode list is the wrong
    # trade.
    shoko_url: str = ""
    shoko_api_key: str = ""

    # Testing aid, and temporary: forces Basic or Advanced regardless of
    # whether an indexer exists. Empty means "decide from the indexers", which
    # is how it is meant to work and how it will work once the automatic rule
    # has been lived with. See services/app_mode.py.
    app_mode_override: str = ""

    # Where AniFlow's own internet requests go out from. none | http |
    # socks4 | socks5 | socks5h. Only AniList and the indexers/feeds are
    # proxied - qBittorrent and Shoko are on your own network, and the
    # torrent traffic is qBittorrent's own business entirely.
    proxy_kind: str = "none"
    proxy_host: str = ""
    proxy_port: int | None = None
    proxy_username: str = ""
    proxy_password: str = ""
    # The service the "check my address" button asks. Any endpoint returning
    # JSON with an ip/country field works; swap it for one you trust.
    # Empty: the built-in services are used (services/outbound.EXIT_PROVIDERS). A value here is asked first, and
    # only an addition - one that fails, or rate-limits, falls through to the others.
    proxy_check_url: str = ""

    # Not the same order twice on purpose: you search for the name release
    # groups use, and you read the name you know the show by.
    title_search_order: str = "romaji,english,alternative,japanese"
    title_folder_order: str = "english,romaji,alternative,japanese"

    search_enabled: bool = True
    search_max_pages: int = Field(default=3, ge=1, le=10)
    search_max_results: int = Field(default=200, ge=1, le=1000)
    search_request_timeout_seconds: float = Field(default=15, gt=0, le=60)

    # Logging settings. Errors only by default - see the note on the other
    # log_level above; these two must not disagree.
    log_level: str = "ERROR"
    log_file: str | None = None
    log_file_max_bytes: int = Field(default=10_000_000, ge=1_000_000)
    log_file_backup_count: int = Field(default=5, ge=1)
    log_components: str | None = None  # comma-separated component names to enable (None = all)
    log_component_levels: str | None = None  # JSON dict of component:level overrides


    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
