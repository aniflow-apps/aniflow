"""RSS feed sources and discovered releases with their evaluation outcome."""
import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.anime import Anime


class FeedSource(Base):
    """A user-configured RSS feed that AniFlow is allowed to scan."""

    __tablename__ = "feed_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: Set when an indexer stands this feed up from its definition file. Such a
    #: source is managed: it can be made the default, but not edited or deleted
    #: on its own, and it is removed with the indexer that owns it. Two records
    #: describing the same site must not be able to drift apart.
    indexer_id: Mapped[str | None] = mapped_column(
        ForeignKey("indexers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: The file in the configuration folder this row mirrors, without its
    #: extension. The folder is the source of truth; this is the link back to
    #: it, kept on the row so renaming an object moves its file without
    #: changing the id anything else refers to.
    config_slug: Mapped[str | None] = mapped_column(String(120), index=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)


class ReleaseDecision(str, enum.Enum):
    MATCHED = "MATCHED"      # satisfies a release profile; candidate for download
    REJECTED = "REJECTED"    # belongs to a tracked anime but violates its profile
    IGNORED = "IGNORED"      # untracked title or no profile configured
    AMBIGUOUS = "AMBIGUOUS"  # title matches more than one tracked anime


class Release(Base):
    """One torrent/release discovered through RSS, with the deterministic verdict."""

    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("feed_source_id", "guid", name="uq_release_feed_item"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # SET NULL keeps history even if the source feed is deleted later.
    feed_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("feed_sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    guid: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_name: Mapped[str] = mapped_column(String(500), nullable=False)
    torrent_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    # Snapshot of the parsed release-name fields at discovery time.
    parsed_title: Mapped[str | None] = mapped_column(String(500))
    subgroup: Mapped[str | None] = mapped_column(String(80))
    episode: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(8))
    media_source: Mapped[str | None] = mapped_column(String(16))
    video_codec: Mapped[str | None] = mapped_column(String(8))
    audio_codec: Mapped[str | None] = mapped_column(String(8))
    censored: Mapped[bool | None]
    unknown_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    decision: Mapped[ReleaseDecision] = mapped_column(Enum(ReleaseDecision), nullable=False)
    anime_id: Mapped[str | None] = mapped_column(
        ForeignKey("anime.id", ondelete="SET NULL"), nullable=True, index=True
    )
    failure_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ambiguous_anime_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    #: When the user cleared this row off the discovery list. The row itself
    #: stays: it is the guid memory that stops the next scan from reporting
    #: the same item as a new find.
    cleared_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: When the queue pass decided this release needs nothing further
    #: (build 135). Distinct from ``cleared_at``, which is the RSS list being
    #: tidied and says nothing about queueing.
    #:
    #: The pass used to remember this by *creating a download row* - a
    #: "skipped: duplicate" entry with no action on it - because its pending
    #: query is "a MATCHED release with no download row". That made the row
    #: load-bearing: Clear all on the Downloads page deleted the memory along
    #: with the list, and the next pass re-created every row it had just
    #: removed. Reported as *"the downloads are fully back"*.
    #:
    #: Set when the episode already has this version, when a newer one is on
    #: disk, and when somebody clears the release's download row - the last
    #: because a cleared row means "I have dealt with this", which is the
    #: same instruction build 128 wrote down for a cleared torrent. An
    #: explicit Download on the episode goes through
    #: ``queue_episode_releases`` and is not gated by this.
    settled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    anime: Mapped["Anime | None"] = relationship(viewonly=True)
