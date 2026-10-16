"""Torrent downloads queued in the external client."""
import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.anime import Anime, Episode


class DownloadStatus(str, enum.Enum):
    QUEUED = "QUEUED"            # handed to the torrent client, hash pending
    DOWNLOADING = "DOWNLOADING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"          # deterministic no-op (duplicate / superseded)
    ATTENTION = "ATTENTION"      # needs user action (new version, client error)
    FAILED = "FAILED"            # could not be handed to the client at all
    REMOVED = "REMOVED"          # removed from the client by user action


class TorrentDownload(Base):
    """One torrent in qBittorrent, owned by exactly one episode.

    The episode link is a real foreign key. Earlier versions stored an episode
    *number string* here and matched it against ``episodes.number``, which
    silently failed whenever the two sides disagreed on zero padding
    ("01" vs "1") - that left episodes stuck in their old status after a
    successful download.
    """

    __tablename__ = "downloads"
    __table_args__ = (
        # One torrent per RSS release ever; duplicate torrents are impossible.
        UniqueConstraint("release_id", name="uq_download_release"),
        UniqueConstraint("info_hash", name="uq_download_info_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised for the anime-scoped Downloads/Attention queries only; the
    # episode relationship stays the single source of truth for identity.
    anime_id: Mapped[str | None] = mapped_column(
        ForeignKey("anime.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The RSS Release this came from, when it came from a feed scan at all.
    # Uniqueness comes from uq_download_release in __table_args__.
    release_id: Mapped[str | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    # The episode-scoped release that was queued, when queued from a candidate.
    episode_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("episode_releases.id", ondelete="SET NULL"), nullable=True
    )

    info_hash: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Human-readable name of what was queued (the release's raw name).
    label: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[DownloadStatus] = mapped_column(
        Enum(DownloadStatus), default=DownloadStatus.QUEUED, nullable=False, index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    qb_state: Mapped[str | None] = mapped_column(String(32))
    attention_reason: Mapped[str | None] = mapped_column(String(80))
    #: When this torrent was first not found in the client, or None while it is
    #: there. A torrent being added, checked and categorised is not
    #: instantaneous, so the first miss is remembered rather than reported -
    #: see MISSING_GRACE in services/downloads.py.
    missing_since: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: When this download last started getting nowhere - the client reporting
    #: `stalledDL` with no progress (build 99). Cleared the moment a byte
    #: arrives, so "how long has this been stuck" is answerable without
    #: keeping a history.
    stalled_since: Mapped[datetime | None] = mapped_column(UtcDateTime)
    error: Mapped[str | None] = mapped_column(String(255))
    #: For one wanted episode of a batch torrent (build 213, docs/batch-episodes-plan.md): the row that owns
    #: the torrent, and the file inside it this row's episode is. Null on every ordinary row. A row with
    #: these has no ``info_hash`` of its own - the owner keeps the torrent, so the unique constraint and
    #: everything built on "one torrent, one row" still hold.
    batch_download_id: Mapped[str | None] = mapped_column(String(36), index=True)
    batch_file: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)

    episode: Mapped["Episode"] = relationship(back_populates="downloads")
    anime: Mapped["Anime | None"] = relationship(viewonly=True)

    @property
    def episode_number(self) -> int | None:
        """The episode this belongs to, for API payloads that show a number."""
        return self.episode.number if self.episode is not None else None


__all__ = ["DownloadStatus", "TorrentDownload"]
