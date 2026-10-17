"""The editable configuration file: ``CONFIG_DIR/aniflow.yaml``.

This is where settings live. The Settings UI writes it and a human can edit it
with nano - both end up in the same place, so there is no second source of truth
to reconcile. The database stays purely for AniFlow's data (anime, episodes,
downloads, history).

Three layers, innermost wins:

1. code defaults (``core/config.Settings``)
2. environment / ``.env``
3. this file

A value absent from the file falls through to the environment, so an existing
deployment behaves the same before and after the file appears. Edits are picked
up without a restart: the file's modification time is checked on every read.

The file holds the qBittorrent password and the media-server API key in plain
text, exactly as the database did before it, so it is created with owner-only
permissions where the platform supports that.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Config")

CONFIG_FILENAME = "aniflow.yaml"

# Written in this order, with these headings, so the file reads like the
# Settings page rather than like a dictionary dump.
SETTING_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Torrent client. Only qBittorrent is implemented so far.",
        ("torrent_client", "qb_url", "qb_username", "qb_password", "qb_category"),
    ),
    (
        # No explanation here on purpose: the Settings page says all of this
        # next to the controls themselves, and two copies drift apart.
        "What happens to a finished download.",
        (
            "file_operation", "after_import_action",
            "torrent_monitor_enabled", "torrent_monitor_interval_seconds",
        ),
    ),
    (
        "What changing an anime's release does with the files you already\n"
        "have: backup renames each to .backup and leaves it, replace deletes\n"
        "them. Chosen in the panel that does it; kept here so the choice\n"
        "survives.",
        ("reacquire_mode",),
    ),
    (
        "Feed scanning.",
        ("rss_enabled", "rss_scan_interval_minutes"),
    ),
    (
        "Everything else that runs on a timer (the Scheduling feature).\n"
        "Every pass gets its own switch and interval in seconds - see the\n"
        "Schedule page, which shows each one with what it actually does.\n"
        "Shoko library adoption and filling in metadata/schedules are manual\n"
        "only now (buttons on the Shoko Server page and the Watchlist),\n"
        "not shown here - see the Schedule page for those.\n"
        "torrent_housekeeping: orphaned torrents, what actually happened to\n"
        "                      an unsettled download, and (only when run by\n"
        "                      hand) settling one the import step missed.\n"
        "anidb_identity_refresh: the AniDB title dump and id-mapping list -\n"
        "                        never fetched more than once a week no\n"
        "                        matter how low this is set.\n"
        "shoko_status: noticing Shoko Server come back or go away.\n"
        "attention_review: closing warnings whose reason has gone away.\n"
        "conversion_review: flagging anime added without an indexer that\n"
        "                   now need converting.\n"
        "disk_recheck: re-checking every anime's linked media folder against\n"
        "              the database, the same check a manual Rescan does.",
        (
            "anidb_identity_refresh_enabled", "anidb_identity_refresh_interval_seconds",
            "shoko_status_enabled", "shoko_status_interval_seconds",
            "torrent_housekeeping_enabled", "torrent_housekeeping_interval_seconds",
            "attention_review_enabled", "attention_review_interval_seconds",
            "conversion_review_enabled", "conversion_review_interval_seconds",
            "disk_recheck_enabled", "disk_recheck_interval_seconds",
        ),
    ),
    (
        "Season calendar view options.",
        ("calendar_hide_in_library", "calendar_hide_adult"),
    ),
    (
        "Search limits. Which sites are searched comes from the files in\n"
        "the indexers folder, not from here.",
        (
            "search_enabled",
            "search_max_pages",
            "search_max_results",
        ),
    ),
    (
        "Logging. Empty log_components means every component.",
        ("log_level", "log_components", "log_component_levels"),
    ),
    (
        "Timezone as an IANA name. Empty uses the container's TZ variable.",
        ("timezone",),
    ),
    (
        "Which metadata source to ask: anilist | kitsu.\n"
        "One at a time. Switching clears the cached calendar and rebuilds it\n"
        "from the new source; anime already in the watchlist keep the ids\n"
        "they were added with.",
        ("metadata_provider",),
    ),
    (
        "Where short notifications appear:\n"
        "top-right | top-left | bottom-right | bottom-left",
        ("toast_corner",),
    ),
    (
        "What happens when a season changes: ask | auto | off.\n"
        "Anime leave Upcoming by themselves. For the rest - finished or\n"
        "waiting shows to the earlier seasons, a show found airing again back\n"
        "to the current one - ask puts a notice on the watchlist, auto moves\n"
        "the folders itself (a few at a time), off does nothing.",
        ("season_rollover",),
    ),
    (
        "Whether a new anime starts with Automate on: true | false.\n"
        "Applies to an anime you add and to a future entry once you choose\n"
        "its release group. You can still switch it per anime.",
        ("automate_new_anime",),
    ),
    (
        "Developer tools in the interface: the Debug tab, the Release\n"
        "Inspector, the repair buttons, the database record. Set here only:\n"
        "the Settings page has no switch for it.\n"
        "\n"
        "!! Leave this off unless you are developing AniFlow. The tools behind\n"
        "!! it reach past the normal flow: they delete files, rewrite database\n"
        "!! rows and re-run imports. Nothing warns you twice.",
        ("debug_mode",),
    ),
    (
        "Which look the interface uses. One exists so far.",
        ("theme",),
    ),
    (
        "MyAnimeList's API client id, for the Calendar sources experiment.\n"
        "Only the Client ID is needed - not the Client Secret, and no redirect\n"
        "URL: the endpoints AniFlow reads need no user login. Get one at\n"
        "https://myanimelist.net/apiconfig. Leave empty to skip that source.",
        ("mal_client_id",),
    ),
    (
        "Shoko Server's API, for reading what Shoko knows about your library.\n"
        "Optional: handing files over to Shoko works through folders and needs\n"
        "none of this. The key is Shoko's own API key, from Shoko's settings.",
        ("shoko_url", "shoko_api_key"),
    ),
    (
        "Basic or Advanced, and normally neither: leave this empty and AniFlow\n"
        "decides from whether an indexer is configured. With no indexer it\n"
        "cannot download anything, so it hides everything about downloading\n"
        "and behaves as a watchlist and calendar.\n"
        "Set to basic or advanced only to try the other mode without adding or\n"
        "removing an indexer. This override is a testing aid and will go.",
        ("app_mode_override",),
    ),
    (
        "Where AniFlow's own internet requests go out from.\n"
        "proxy_kind: none | http | socks4 | socks5 | socks5h\n"
        "            socks5h resolves names at the proxy; socks5 resolves them\n"
        "            here first, which tells your own resolver what you asked.\n"
        "Only AniList and the indexers/feeds go through it. qBittorrent and\n"
        "Shoko are on your own network and always go direct - and the\n"
        "torrent traffic itself is qBittorrent's, not AniFlow's, so nothing\n"
        "here hides it. Use qBittorrent's own proxy settings for that.",
        (
            "proxy_kind",
            "proxy_host",
            "proxy_port",
            "proxy_username",
            "proxy_password",
            "proxy_check_url",
        ),
    ),
    (
        "Which of an anime's titles to prefer, best first.\n"
        "Any of: english, romaji, alternative, japanese - anything you leave\n"
        "out is still used as a fallback, in that order.\n"
        "title_search_order: what to ask an indexer for. Release groups use\n"
        "                    romaji almost everywhere, so english first will\n"
        "                    find less.\n"
        "title_folder_order: what to call the folder AniFlow creates. Not used\n"
        "                    for the library in shokoserver mode - Shoko names\n"
        "                    that itself.\n"
        "title_display_order: what the Calendar and the Watchlist call an anime\n"
        "                     on screen. Display only - it renames nothing and\n"
        "                     changes no search.",
        ("title_search_order", "title_folder_order", "title_display_order"),
    ),
    (
        "The calendar's own filters and ordering, remembered so the page opens\n"
        "the way you left it.\n"
        "calendar_sort: airing | name | popularity | recommended\n"
        "calendar_hidden_kinds: which kinds to leave out, comma-separated and\n"
        "                       upper case - TV, MOVIE, OVA, ONA, SPECIAL,\n"
        "                       MUSIC, CM, PV. A kind not named here is shown,\n"
        "                       so one invented next year appears rather than\n"
        "                       vanishing.",
        (
            "calendar_hide_in_library",
            "calendar_hide_adult",
            "calendar_hide_no_cover",
            "calendar_auto_update",
            "calendar_auto_update_interval_seconds",
            "time_format",
            "date_format",
            "calendar_sort",
            "calendar_hidden_kinds",
            "calendar_mark_sequels",
            "calendar_show_popularity",
        ),
    ),
    (
        "Interface language: en | de",
        ("language",),
    ),
)

_HEADER = """# AniFlow configuration
#
# Edit this file with any text editor and save - AniFlow notices the change on
# its own, no restart needed. The Settings page writes the same file, and doing
# so rewrites these comments, so keep your own notes elsewhere.
#
# Removing a line is allowed: the value then falls back to the environment
# (.env / compose) and finally to AniFlow's own default.
#
# This file contains credentials in plain text. Keep it readable only by the
# user AniFlow runs as.
"""

# Tests and one-off tooling point this somewhere harmless.
_override_path: Path | None = None
_cache: tuple[Path, float, int, dict[str, Any]] | None = None


def set_config_path(path: Path | None) -> None:
    """Point the config file somewhere else (tests, tooling). Clears the cache."""
    global _override_path
    _override_path = path
    invalidate_cache()


def invalidate_cache() -> None:
    global _cache
    _cache = None


def config_path() -> Path:
    if _override_path is not None:
        return _override_path
    env = get_settings()
    base = env.config_dir or Path("/config")
    return base / CONFIG_FILENAME


#: Settings that no longer exist. A file written by an older build still has them; they are ignored without a
#: warning on every read (build 233: the media-server settings).
RETIRED_SETTINGS = frozenset({"media_server", "jellyfin_url", "jellyfin_api_key"})


def _known_fields() -> set[str]:
    from backend.app.services.app_settings import AppSettings

    return {f.name for f in fields(AppSettings)}


def read_config_file() -> dict[str, Any]:
    """Whatever the file currently says, or ``{}`` when there is no file.

    A malformed file is reported and ignored rather than taking the app down:
    the environment defaults still describe a working installation.
    """
    global _cache
    path = config_path()
    try:
        stat = path.stat()
    except OSError:
        return {}

    if _cache is not None:
        cached_path, mtime, size, values = _cache
        if cached_path == path and mtime == stat.st_mtime and size == stat.st_size:
            return values

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.error("config file %s could not be read (%s); using defaults", path, exc)
        return {}

    if raw is None:
        values: dict[str, Any] = {}
    elif not isinstance(raw, dict):
        logger.error("config file %s does not contain a mapping; using defaults", path)
        return {}
    else:
        known = _known_fields()
        values = {key: value for key, value in raw.items() if key in known}
        for unknown in sorted(set(raw) - known - RETIRED_SETTINGS):
            logger.warning("config file %s: ignoring unknown setting %r", path, unknown)

    _cache = (path, stat.st_mtime, stat.st_size, values)
    logger.debug("config file %s loaded (%s settings)", path, len(values))
    return values


def render_config_file(values: dict[str, Any]) -> str:
    """The file's text: header, then one commented group per section."""
    lines = [_HEADER]
    for comment, names in SETTING_GROUPS:
        lines.append("")
        for comment_line in comment.split("\n"):
            lines.append(f"# {comment_line}")
        for name in names:
            if name not in values:
                continue
            # safe_dump per key so quoting and null/true/false stay valid YAML.
            lines.append(yaml.safe_dump({name: values[name]}, allow_unicode=True).rstrip("\n"))
    return "\n".join(lines) + "\n"


def write_config_file(values: dict[str, Any]) -> Path:
    """Replace the file with these settings, atomically.

    Written to a temporary file in the same directory and moved into place, so a
    crash halfway through cannot leave a half-written config behind.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_config_file(values)

    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".aniflow-config-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        try:
            os.chmod(tmp_path, 0o600)  # credentials in plain text
        except OSError:  # noqa: BLE001 - not every filesystem supports it
            pass
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    invalidate_cache()
    logger.info("configuration written to %s", path)
    return path
