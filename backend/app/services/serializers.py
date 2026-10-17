"""Episode-oriented presentation of an anime.

The relational schema stays normalised - anime, episode, episode_release,
download all live in their own tables with foreign keys between them. This
module is the one place that turns those rows into the shape a human wants to
read, where everything about episode 1 sits *inside* episode 1:

    anime -> profile -> storage -> episodes[] -> { releases[], download,
                                                   import, media }

Nothing is looked up by episode number here; the relationships carry the
structure. Both the detail API and the debug dump build on this so the two can
never drift apart.
"""
from datetime import UTC, time
from typing import Any

from backend.app.models.anime import Anime, Episode
from backend.app.services.metadata_fill import manual_fields
from backend.app.services.storage import StorageRoots, storage_roots


def serialize_release(release) -> dict[str, Any]:
    return {
        "id": release.id,
        "raw_name": release.raw_name,
        "provider": release.provider,
        "provider_id": release.provider_id,
        "has_magnet": bool(release.magnet_url),
        "magnet_url": release.magnet_url,
        "has_torrent": bool(release.torrent_url),
        "torrent_url": release.torrent_url,
        "seeders": release.seeders,
        "size": release.size,
        "subgroup": release.subgroup,
        "resolution": release.resolution,
        "version": release.version,
    }


def serialize_download(download, episode: Episode) -> dict[str, Any] | None:
    """The download stage. ``episode`` is not repeated - it is the parent."""
    if download is None:
        return None
    return {
        "id": download.id,
        # The torrent client's category subfolder, when it uses one.
        # NULL means the file sits directly in the download root.
        "folder": episode.download_folder,
        "filename": episode.download_filename,
        "status": download.status.value,
        "progress": download.progress,
        "qb_state": download.qb_state,
        "info_hash": download.info_hash,
        "error": download.error,
        "attention_reason": download.attention_reason,
        "label": download.label,
        "version": download.version,
    }


def episode_air_date(episode: Episode, anime: Anime | None = None) -> dict[str, str] | None:
    """The day an episode came out or is due, and how that is known (build 216).

    ``recorded`` is a date somebody stored for this very episode. Otherwise the premiere is counted from, a
    week per episode (services/calendar_schedule.py): ``exact`` for the premiere itself, ``from_premiere`` for
    the rest - an estimate, and the row says so. None when there is nothing to count from.
    """
    from backend.app.models.anime import EpisodeType
    from backend.app.services.calendar_schedule import episode_airing
    from backend.app.services.clock import to_display

    when = episode.aired_at
    if when is not None:
        aware = when if when.tzinfo is not None else when.replace(tzinfo=UTC)
        # A date with no time of day (Shoko gives one) is that day everywhere, not midnight in London.
        if aware.astimezone(UTC).time() == time(0, 0):
            day = aware.astimezone(UTC).date()
        else:
            day = to_display(aware).date()
        return {"on": day.isoformat(), "basis": "recorded"}
    owner = anime if anime is not None else episode.anime
    if episode.episode_type is not EpisodeType.REGULAR or owner is None or owner.air_start_date is None:
        return None
    found = episode_airing(
        episode.number, air_start_date=owner.air_start_date, episode_count=owner.effective_episode_count
    )
    return {"on": found.on.isoformat(), "basis": found.basis} if found else None


def serialize_episode(episode: Episode, *, shoko_mode: bool | None = None) -> dict[str, Any]:
    """One episode as the API sends it.

    ``shoko_mode`` decides two of the user-facing states (the hand-over says
    "moving to library" or "moving to Shoko"). Passed in rather than read here
    so a caller serialising forty episodes asks the question once - and so this
    function stays testable without the mode machinery.
    """
    from backend.app.services.episode_view import view_for

    anime = episode.anime
    if shoko_mode is None:
        from backend.app.core.mode import AppMode, current_mode

        shoko_mode = current_mode() is AppMode.SHOKO_SERVER
    return {
        "id": episode.id,
        "number": episode.number,
        "title": episode.title,
        "episode_type": episode.episode_type.value,
        # The internal value, still sent: the Downloads page and the debug
        # views show it, and it is what every existing test asserts on.
        "status": episode.status.value,
        # And what a person reads, derived in one place (build 125). See
        # services/episode_view.py - this is the field the episode row shows,
        # replacing a per-row choice between two vocabularies.
        "view": view_for(episode, anime=anime, shoko_mode=bool(shoko_mode)).as_dict(),
        "aired_at": episode.aired_at,
        # The day it came out or is due, and whether that is a stored date or counted weekly from the
        # premiere (build 216).
        "air_date": episode_air_date(episode, anime),
        "error": episode.error,
        "skip_reason": episode.skip_reason,
        "releases": [serialize_release(release) for release in _sorted_releases(episode)],
        "download": serialize_download(episode.download, episode),
        # The files next to this episode's own (build 189): backups, originals
        # kept by a swap, an older version. Each with what it is, for the tag
        # and the delete button; only the ones still on disk by their notes.
        "copies": [
            {
                "id": row.id,
                "kind": row.kind,
                "filename": row.filename,
                "size_bytes": row.size_bytes,
                "subgroup": row.subgroup,
                "resolution": row.resolution,
                "recorded_at": row.recorded_at,
            }
            for row in episode.copies
            if row.removed_at is None
        ],
        "import": {
            # The import folder belongs to the anime; repeated here only
            # because the stage reads as a unit.
            "folder": anime.import_folder,
            # Non-null means the file is in the import folder and Shoko Server
            # has not filed it yet - see services/shoko.py.
            "filename": episode.import_filename,
            "started_at": episode.import_started_at,
        },
        "media": {
            "filename": episode.media_filename,
            "size_bytes": episode.media_size_bytes,
            "imported_at": episode.media_imported_at,
            "version": episode.media_version,
            # What the media server will make of it - see
            # The compatibility check that wrote these went in build 126.
            # Still sent so the payload shape does not change in the same
            # build as the deletion; always null for anything new.
            "playback": episode.media_playback,
            "summary": episode.media_summary,
            "findings": list(episode.media_findings or []),
            "scanned_at": episode.media_scanned_at,
        },
    }


def _sorted_releases(episode: Episode) -> list:
    """Most seeded first - the order AniFlow would pick from."""
    return sorted(episode.releases, key=lambda item: (-(item.seeders or 0), item.raw_name))


def sorted_episodes(anime: Anime) -> list[Episode]:
    """Episodes in display order: regular first, then numerically (0 before 1)."""
    return sorted(anime.episodes, key=lambda item: (item.episode_type.value, item.number))


def serialize_profile(anime: Anime) -> dict[str, Any] | None:
    profile = anime.profile
    if profile is None:
        return None
    return {
        "subgroups": list(profile.subgroups),
        "resolution": profile.resolution,
        "media_source": profile.media_source,
        "censor_preference": profile.censor_preference.value,
        "require_title_match": profile.require_title_match,
        # Which template this came from, so a summary can name it instead of
        # listing the rules again.
        "template_id": profile.template_id,
        "template_name": profile.template.name if profile.template is not None else None,
    }


def serialize_anime(anime: Anime, *, roots: StorageRoots | None = None) -> dict[str, Any]:
    """The full episode-oriented view of one anime."""
    resolved = roots or storage_roots()
    from backend.app.core.mode import AppMode, current_mode

    shoko_mode = current_mode() is AppMode.SHOKO_SERVER
    return {
        "anime": {
            "id": anime.id,
            "primary_provider": anime.primary_provider,
            "primary_provider_id": anime.primary_provider_id,
            "local_name": anime.local_name,
            "display_name": anime.display_name,
            "title_english": anime.title_english,
            "title_romaji": anime.title_romaji,
            "title_native": anime.title_native,
            "aliases": list(anime.aliases or []),
            "lifecycle": anime.lifecycle.value,
            "entry_mode": anime.entry_mode,
            # The library it lives in (build 192).
            "library_id": anime.library_id,
            "library_pinned": anime.library_pinned,
            "is_adult": anime.effective_is_adult,
            "adult_flag": anime.is_adult,
            "episode_count": anime.effective_episode_count,
            # Whether what is on screen is the user's own value, so the detail
            # page knows there is an override for Reset to drop.
            "schedule_is_manual": anime.schedule_is_manual,
            "episode_count_is_manual": anime.episode_count_is_manual,
            "season_is_manual": "season" in manual_fields(anime),
            "releases_ignored": anime.releases_ignored,
            "indexer_id": anime.indexer_id,
            "feed_source_id": anime.feed_source_id,
            "custom_feed_url": anime.custom_feed_url,
            "release_name": anime.release_name,
            "media_folder": anime.media_folder,
            "import_folder": anime.import_folder,
            "created_at": anime.created_at,
            "updated_at": anime.updated_at,
        },
        "profile": serialize_profile(anime),
        "storage": resolved.as_dict(),
        "episodes": [
            serialize_episode(episode, shoko_mode=shoko_mode)
            for episode in sorted_episodes(anime)
        ],
    }


__all__ = [
    "serialize_anime",
    "serialize_download",
    "serialize_episode",
    "serialize_profile",
    "serialize_release",
    "sorted_episodes",
]
