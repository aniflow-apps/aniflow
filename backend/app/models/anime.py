import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Date, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.download import TorrentDownload
    from backend.app.models.library import Library
    from backend.app.models.rss import FeedSource
    from backend.app.models.episode_release import EpisodeRelease
    from backend.app.models.release_profile import ReleaseProfile
    from backend.app.models.search_result import SearchResult
    from backend.app.models.superseded import SupersededFile


def canonical_provider_ids(ids):
    """``provider_ids`` with MyAnimeList under one name, ``myanimelist``.

    The adapters always wrote ``myanimelist``; the offline id list and the
    calendar say ``mal``, which nobody reading the data would take for
    MyAnimeList. Both ended up stored (44 anime held both, 22 only ``mal``), so
    a reader that knew one spelling missed the other. One name is written now;
    when both are present the one that was already ``myanimelist`` wins.
    """
    if not ids or "mal" not in ids:
        return ids
    fixed = {key: value for key, value in ids.items() if key != "mal"}
    if not fixed.get("myanimelist"):
        fixed["myanimelist"] = ids["mal"]
    return fixed


class AnimeLifecycle(str, enum.Enum):
    UPCOMING = "UPCOMING"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    DROPPED = "DROPPED"
    ARCHIVED = "ARCHIVED"


class EpisodeType(str, enum.Enum):
    REGULAR = "regular"
    SPECIAL = "special"
    OVA = "ova"
    MOVIE = "movie"
    OTHER = "other"


class EpisodeStatus(str, enum.Enum):
    """The single state an episode is in, as shown in the UI.

    The stages map onto the storage stages (download -> import -> media):

    - ``UNAVAILABLE``: nothing found for this episode yet (not aired, or no
      release discovered). AniFlow cannot act on it.
    - ``AVAILABLE``: at least one release is known and the episode is not on
      disk. This is the state that offers a download.
    - ``DOWNLOADING``: handed to the torrent client and still running.
    - ``DOWNLOADED``: the torrent finished but the file is still only in the
      download root - it has not been placed in the media library yet.
    - ``IMPORTING``: Shoko Server mode only. The file has been handed over -
      it sits in the import folder - and Shoko has not yet identified it,
      renamed it and moved it into the library. Neither the download folder
      nor the library is where this episode lives right now, which is why no
      other state describes it honestly.
    - ``COMPLETE``: the file sits at its canonical media location.
    - ``MISSING``: it was ``COMPLETE`` but the media file is gone (deleted
      outside AniFlow). Purely a UI distinction: internally this behaves like
      ``AVAILABLE`` so the episode can simply be downloaded again.
    - ``UPDATE``: a newer release/version than the one on disk exists. The
      decision is left to the user; AniFlow never replaces silently.
    - ``ERROR``: something went wrong that does not fit any other state.
    """

    UNAVAILABLE = "UNAVAILABLE"
    AVAILABLE = "AVAILABLE"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    IMPORTING = "IMPORTING"
    COMPLETE = "COMPLETE"
    MISSING = "MISSING"
    UPDATE = "UPDATE"
    ERROR = "ERROR"


#: States from which a (re-)download may be offered. ``MISSING`` is included on
#: purpose - it is the same situation as ``AVAILABLE`` with a different label.
DOWNLOADABLE_STATUSES = (EpisodeStatus.AVAILABLE, EpisodeStatus.MISSING, EpisodeStatus.UNAVAILABLE)

#: States that mean "the torrent client is currently responsible for this".
IN_FLIGHT_STATUSES = (EpisodeStatus.DOWNLOADING, EpisodeStatus.DOWNLOADED)


class Anime(Base):
    __tablename__ = "anime"
    __table_args__ = (
        UniqueConstraint("primary_provider", "primary_provider_id", name="uq_anime_provider_identity"),
        UniqueConstraint("local_name", name="uq_anime_local_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    primary_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_provider_id: Mapped[str] = mapped_column(String(80), nullable=False)
    local_name: Mapped[str] = mapped_column(String(180), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title_english: Mapped[str | None] = mapped_column(String(255))
    title_romaji: Mapped[str | None] = mapped_column(String(255))
    title_native: Mapped[str | None] = mapped_column(String(255))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    cover_url: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(50))
    episode_count: Mapped[int | None] = mapped_column(Integer)
    #: The user's own episode count. See ``air_slot_override_at``.
    episode_count_override: Mapped[int | None] = mapped_column(Integer)
    #: How far this anime's releases are numbered past its own episode 1
    #: (build 112).
    #:
    #: Zero for almost everything: a group that restarts at 1 each season
    #: needs nothing. 66 for a group that counts from the start of the
    #: franchise - Re:Zero season 4 is SubsPlease's 67th file, so 67 minus 66
    #: is episode 1. Decided once in the setup dialog, because by the time the
    #: weekly episode arrives through the RSS scan nothing else remembers.
    episode_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lifecycle: Mapped[AnimeLifecycle] = mapped_column(Enum(AnimeLifecycle), default=AnimeLifecycle.UPCOMING, nullable=False)
    #: ``basic`` when this anime was added with no indexer configured, so it is
    #: a name and a lifecycle and nothing else. Recorded rather than derived:
    #: an anime with no release name might be a tracker entry, or a download
    #: entry somebody never finished, and those want opposite treatment the
    #: moment an indexer appears. See services/app_mode.py.
    entry_mode: Mapped[str] = mapped_column(String(16), default="advanced", nullable=False)
    drop_mode: Mapped[str | None] = mapped_column(String(40))
    # When set, the worker queues whatever this anime has available on its own,
    # every cycle - and feed matches for it reach the torrent client without
    # anyone pressing a button. The column default stays off so no other code
    # path can start downloading by accident; adding an anime to the watchlist
    # sets it explicitly (see services/anime.py).
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # When the show premiered, as the provider reports it.
    air_start_date: Mapped[date | None] = mapped_column(Date)
    #: The season the providers place the show in, and its year (build 190):
    #: WINTER | SPRING | SUMMER | FALL. Read from the calendar by
    #: services/anime_season.py, because the premiere date alone files a show
    #: that starts on 26 September under summer, and one added months ahead
    #: cannot be filed under its season until its first episode airs.
    season: Mapped[str | None] = mapped_column(String(10))
    season_year: Mapped[int | None] = mapped_column(Integer)
    # One instant a known episode airs/aired, in UTC. The weekly schedule reads
    # the weekday and time of day off this: a single instant is unambiguous,
    # where a stored "Tuesday 17:00" would silently be wrong in another
    # timezone. Null for shows the provider gives no schedule for.
    air_slot_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: What the *user* says the slot is, when the provider is wrong or silent.
    #: Kept apart from the provider's own value so "reset to the provider" is
    #: clearing this rather than fetching anything again - and so a metadata
    #: refresh cannot quietly undo a correction.
    air_slot_override_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # A cover the user uploaded, which wins over the provider's artwork.
    # Just the filename; the folder comes from settings.data_dir.
    custom_cover: Mapped[str | None] = mapped_column(String(120))
    provider_ids: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)

    @validates("provider_ids")
    def _one_name_for_myanimelist(self, _key, value):
        return canonical_provider_ids(value)

    #: AniDB's id for this show, as a number (build 116).
    #:
    #: Also written to ``provider_ids["anidb"]``, where it has lived since
    #: build 83 - this is the same value in a form that can be indexed and
    #: asked about. It is the one id Shoko Server states for everything it
    #: holds, and the one the numbering resolver walks prequel chains by, so
    #: "which anime is aid 19242" has to be a query rather than a scan.
    anidb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    #: Every name AniDB files this show under, by language (build 116).
    #:
    #: ``{"main": "Saga of Tanya the Evil II", "en": "Saga of Tanya the Evil
    #: Season 2", "ja": <the Japanese title>}`` - ``main`` being AniDB's own main
    #: title, which is romaji and the name Shoko shows. Filled from the weekly
    #: dump: no network, and never the website. See services/anidb_titles.py.
    #:
    #: Why it is worth storing at all: an anime adopted through AniList
    #: arrives with AniList's three titles, and for a sequel those are
    #: routinely a different name than the one the groups release under. This
    #: is the set of names that agrees with Shoko and with the trackers.
    anidb_titles: Mapped[dict[str, str]] = mapped_column(
        JSON, default=dict, nullable=False, server_default="{}"
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    #: When a metadata source was last asked about this anime (build 123).
    #:
    #: The backfill's bookmark. "Has a gap" alone cannot tell an entry nobody
    #: has asked about from one the provider has already answered "no idea"
    #: for - a finished show with no broadcast time is a permanent gap, not a
    #: chore - and a pass that cannot tell those apart asks for ever. Null
    #: means never asked, which is where every row starts.
    #: See services/metadata_fill.py.
    metadata_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Stop looking for releases of this one (build 123).
    #:
    #: *"I downloaded this years ago, I do not care."*  An old series imported
    #: from somewhere else has its files, a release name that came from a
    #: renamed file rather than a torrent, and no future episodes - so every
    #: pass that hunts for releases spends its time finding nothing for it,
    #: and the download icon is permanently red about a situation nobody
    #: intends to change.
    #:
    #: Distinct from ``automation_enabled``, which pauses *downloading* while
    #: AniFlow keeps watching: this says do not even look. And distinct from
    #: DROPPED, which is about the show rather than about its releases - an
    #: ignored anime is still in the library, still counted, still playable.
    releases_ignored: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )
    feed_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("feed_sources.id", ondelete="SET NULL"), nullable=True
    )
    # Read-only: the id is what gets written, this is for naming it in the UI.
    feed_source: Mapped["FeedSource | None"] = relationship(viewonly=True)
    custom_feed_url: Mapped[str | None] = mapped_column(Text)
    #: The indexer this anime's releases were chosen from (build 219). Searches
    #: for its new episodes ask this one; null means the default indexer, which
    #: is what every anime did before there could be a second. Set to null when
    #: the indexer is deleted, so the anime falls back rather than going quiet.
    indexer_id: Mapped[str | None] = mapped_column(
        ForeignKey("indexers.id", ondelete="SET NULL"), nullable=True
    )

    # How releases name this anime, e.g. "Youjo Senki S2" for
    # "[Group] Show Name S2 - 01 (720p) [7D581341].mkv". Captured from a
    # real release when the anime is added, because providers name shows quite
    # differently from the metadata provider - matching feed items against
    # title_romaji/title_english simply does not work for seasoned shows.
    #
    # Deliberately separate from media_folder even though the two start out
    # identical: the library folder may be renamed later (Shoko Server/AniDB), and
    # that must not silently stop new episodes from being discovered.
    release_name: Mapped[str | None] = mapped_column(String(255))

    # Storage folders, stored as data instead of derived from the title on every
    # use: Shoko Server may name the folder "Saijo no Osewa S1" for an
    # anime titled "Saijo no Osewa", and the historical value must not shift.
    # Both are relative to the corresponding storage root, one level deep.
    media_folder: Mapped[str | None] = mapped_column(String(255))
    import_folder: Mapped[str | None] = mapped_column(String(255))

    # The library this anime lives in (build 192, 0.8.5): which folder its
    # ``media_folder`` is relative to. Backfilled to the default library for
    # anime that predate libraries; NULL only until that has run.
    library_id: Mapped[int | None] = mapped_column(
        ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    library: Mapped["Library | None"] = relationship(lazy="joined")

    # Adult, as the user (or the source) says (build 192). One flag, not a
    # ladder: it drives the watchlist's hide/show button and nothing else.
    is_adult: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)

    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="anime",
        cascade="all, delete-orphan",
        order_by="(Episode.episode_type, Episode.number)",
    )
    profile: Mapped["ReleaseProfile | None"] = relationship(
        back_populates="anime", cascade="all, delete-orphan", uselist=False
    )
    search_results: Mapped[list["SearchResult"]] = relationship(back_populates="anime")

    @property
    def adult_flag(self) -> bool:
        """Only the flag - what the user ticks on the page, without the library's part."""
        return bool(self.is_adult)

    @property
    def effective_is_adult(self) -> bool:
        """Adult for the watchlist: the flag, or a library marked Adult (build 198)."""
        return bool(self.is_adult or (self.library is not None and self.library.fits_adult == "adult"))

    # -- derived, for the API ------------------------------------------------
    # Read-only views over the stored columns, so every endpoint that builds an
    # AnimeSummary from a row gets them for free.

    @property
    def effective_air_slot_at(self) -> datetime | None:
        """The slot everything should read: the user's, or the provider's."""
        return self.air_slot_override_at or self.air_slot_at

    @property
    def airs_weekday(self) -> int | None:
        """0 = Monday .. 6 = Sunday, in the display timezone."""
        from backend.app.services.schedule import slot_weekday

        return slot_weekday(self.effective_air_slot_at, self.air_start_date)

    @property
    def airs_time(self) -> str | None:
        """``HH:MM`` in the display timezone, when a time is known at all."""
        from backend.app.services.schedule import slot_time

        return slot_time(self.effective_air_slot_at)

    @property
    def library_pinned(self) -> bool:
        """The user said "keep it here": the season rollover leaves it alone (build 205)."""
        raw = self.metadata_json if isinstance(self.metadata_json, dict) else {}
        return bool(raw.get("_library_pinned"))

    @property
    def effective_episode_count(self) -> int | None:
        """The count everything should read: the user's, or the provider's."""
        return self.episode_count_override or self.episode_count

    @property
    def schedule_is_manual(self) -> bool:
        """Whether the airing slot on screen is the user's own."""
        return self.air_slot_override_at is not None

    @property
    def episode_count_is_manual(self) -> bool:
        return self.episode_count_override is not None

    @property
    def has_custom_cover(self) -> bool:
        return bool(self.custom_cover)


def _release_rank(release, profile) -> tuple[int, int, int, datetime]:
    """How much AniFlow wants this release. Bigger is better.

    Read as: does it come from a group the profile asks for, is it the
    resolution the profile asks for, how well is it seeded, and how new is it.

    A profile that names nothing scores every release the same on the first
    two, which leaves seeders deciding - the behaviour before build 90, kept
    for the case it was right about.

    Deliberately *not* a filter. A profile stating a group AniFlow has found
    nothing from yet would filter every candidate away and leave the episode
    stuck at "available" with nothing to hand over; ranking still offers the
    best of what exists, and the row says which release it is.
    """
    wanted_groups = {name.strip().casefold() for name in (getattr(profile, "subgroups", None) or []) if name}
    wanted_resolution = (getattr(profile, "resolution", None) or "").strip().casefold()
    group = (release.subgroup or "").strip().casefold()
    matches_group = 1 if wanted_groups and group in wanted_groups else 0
    matches_resolution = (
        1 if wanted_resolution and (release.resolution or "").strip().casefold() == wanted_resolution else 0
    )
    return (matches_group, matches_resolution, release.seeders or 0, release.created_at)


def _is_leftover(row, siblings) -> bool:
    """Whether this download row is only the shadow of another row for the same torrent.

    Two shapes, both from ``_claim_hash``'s world: the row that gave up the
    torrent (SKIPPED, ``duplicate_of_another_row``), and a row that never got a
    hash at all while a sibling for the same release did. A hashless row with
    no such sibling is a real, stuck download and stays the episode's download.
    """
    if row.status.value == "SKIPPED" and row.attention_reason == "duplicate_of_another_row":
        return True
    if row.info_hash is not None or row.batch_download_id is not None or not row.label:
        return False
    return any(
        other is not row
        and other.info_hash is not None
        and other.label == row.label
        and other.status.value not in ("SKIPPED", "REMOVED", "FAILED")
        for other in siblings
    )


class Episode(Base):
    """One episode of one anime - the spine of AniFlow's data model.

    Everything that belongs to an episode hangs off this row by foreign key
    (releases, download, media). Nothing is joined by episode *number* any
    more; ``number`` is an integer purely so ordering and display are correct
    and episode 0 is representable.

    The three filenames are deliberately independent. They are identical today
    but a later naming stage may rename the media file without rewriting the
    historical download name, so no stage's name is derived from another.
    """

    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("anime_id", "number", "episode_type", name="uq_episode_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    anime_id: Mapped[str] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    episode_type: Mapped[EpisodeType] = mapped_column(Enum(EpisodeType), default=EpisodeType.REGULAR, nullable=False)
    status: Mapped[EpisodeStatus] = mapped_column(
        Enum(EpisodeStatus), default=EpisodeStatus.UNAVAILABLE, nullable=False, index=True
    )
    aired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    provider_episode_ids: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)

    # --- stage 1: download root ---------------------------------------------
    # There is no per-anime download folder, but the torrent client usually
    # writes into a category subfolder (<download_root>/<qb_category>/). That
    # folder is stored per episode rather than re-derived from the current
    # setting, so changing the category later cannot orphan existing files.
    # NULL means "directly in the download root".
    download_folder: Mapped[str | None] = mapped_column(String(255))
    download_filename: Mapped[str | None] = mapped_column(String(500))

    # --- stage 2: import root (Shoko Server mode) ---------------------------
    # What AniFlow put in the import folder, and enough about it to recognise
    # the same file again after Shoko renames it and moves it into the media
    # root. ``import_inode`` is the reliable one: Shoko's move preserves the
    # inode, so the file can be identified with certainty rather than by
    # comparing titles that AniList and AniDB spell differently. The size is
    # the fallback for the case where Shoko copied instead of moving.
    import_filename: Mapped[str | None] = mapped_column(String(500))
    import_inode: Mapped[int | None] = mapped_column(BigInteger)
    import_size_bytes: Mapped[int | None] = mapped_column(Integer)
    # When the hand-over happened, so "Shoko has not picked this up" can be
    # told apart from "Shoko is still working on it".
    import_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    # --- stage 3: media root (canonical location, under anime.media_folder) --
    media_filename: Mapped[str | None] = mapped_column(String(500))
    media_size_bytes: Mapped[int | None] = mapped_column(Integer)
    media_imported_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Release version behind the media file, so a newer release can be spotted
    # (the UPDATE state) without re-parsing filenames.
    media_version: Mapped[int | None] = mapped_column(Integer)

    # --- stage 4: what is actually in the file (build 87) -------------------
    # Whether the media server will play it as it is or has to re-encode it
    # first. Read from the file once it is in the library and kept here, so
    # the watchlist can say so without reading two hundred files - see
    # Written by the media-server compatibility check, which build 126
    # deleted. The columns are kept for one build so 126 needs no
    # migration and stays reversible by folder restore; nothing writes
    # them any more and nothing reads them to decide anything.
    #
    # direct | transcode | unknown. NULL means it has not been looked at.
    media_playback: Mapped[str | None] = mapped_column(String(12))
    #: A one-line summary for the interface: "1080p · H.264 High 10 · 10-bit".
    media_summary: Mapped[str | None] = mapped_column(String(200))
    #: The reasons, as codes the interface translates: ten_bit, burn_in,
    #: audio_transcode. Stored as a list so a file with three problems reports
    #: three rather than the first one found.
    media_findings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    media_scanned_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    error: Mapped[str | None] = mapped_column(String(255))

    #: Why nothing will be fetched for this episode although it has no file here (item 36b): the
    #: user's AniDB list says they have it. A code from services/reasons.py, never a guess. It stops
    #: the automatic paths and the batch planner; the episode's own Download button and "fetch them
    #: too" still work, and a release change clears it. See services/episode_skip.py.
    skip_reason: Mapped[str | None] = mapped_column(String(40))

    #: When this episode left the In Progress page for the Archive (165) -
    #: automatically the moment it reaches COMPLETE, or by hand for a stuck
    #: episode the user is done looking at (see services/progress.py). Cleared
    #: automatically the next time the episode's status actually changes (see
    #: episode_state.refresh_episode_status) - an archived COMPLETE episode
    #: that later goes MISSING or gets a new version is news again and
    #: reappears in In Progress on its own. Named `progress_dismissed_at`
    #: through build 164, when dismissal was still a manual, per-row action;
    #: renamed once completion started archiving on its own, since "dismissed"
    #: no longer described most of what set it.
    progress_archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)

    anime: Mapped[Anime] = relationship(back_populates="episodes")
    #: Every note about a file that is *not* this episode's own (build 189):
    #: renamed backups, originals kept by a swap, a v1 next to a v2. Read-only
    #: and loaded with the episodes, so the detail page lists them without a
    #: query per row.
    copies: Mapped[list["SupersededFile"]] = relationship(
        "SupersededFile",
        primaryjoin="Episode.id == SupersededFile.episode_id",
        viewonly=True,
        lazy="selectin",
        order_by="SupersededFile.recorded_at",
    )
    releases: Mapped[list["EpisodeRelease"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="EpisodeRelease.seeders.desc()",
    )
    downloads: Mapped[list["TorrentDownload"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="TorrentDownload.created_at",
    )

    @property
    def download(self) -> "TorrentDownload | None":
        """The download that currently represents this episode.

        Rows are kept for history (a removed torrent stays visible in the
        Downloads page), so the newest one is the relevant one - except a
        row that is only a leftover of another one. Three rows for one torrent
        are not exotic (see ``_claim_hash`` in services/downloads.py), and the
        newest of them is often the hashless stray that never reached the
        client: it read "torrent gone" on an episode whose real download had
        finished and been imported (build 237).
        """
        if not self.downloads:
            return None
        live = [row for row in self.downloads if not _is_leftover(row, self.downloads)]
        return (live or self.downloads)[-1]

    @property
    def best_release(self) -> "EpisodeRelease | None":
        """The release AniFlow would queue.

        The profile first, seeders second (build 90). Until then this was
        seeders alone, which meant a release the user had explicitly moved
        away from could still win - and did: after changing SubsPlease to
        Erai-raws, the SubsPlease rows were still here, still the best seeded,
        and still what the Download button handed over.

        Seeders remain the tie-break, because among releases that all match
        the profile the most-seeded is the one that will actually arrive.
        """
        if not self.releases:
            return None
        profile = self.anime.profile if self.anime is not None else None
        return max(self.releases, key=lambda item: _release_rank(item, profile))
