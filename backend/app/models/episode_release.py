"""Releases discovered for one specific episode.

A release is what a provider actually published; the anime's profile is what
the user wants. The values overlap (both carry a subgroup and a resolution)
but they mean different things, so release metadata is stored in full even
when it repeats the profile.

Several releases may exist for the same episode (different subgroups,
resolutions or versions). AniFlow keeps all of them and picks one when
queueing - it never deletes or supersedes them here.

This replaces the old ``available_releases`` table, which could hold only one
row per (anime, episode) and linked to the episode by number string. The RSS
scanner's own ``releases`` table is a separate concern: it is the per-feed
audit log that carries the MATCHED/REJECTED verdicts.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.anime import Episode


class EpisodeRelease(Base):
    __tablename__ = "episode_releases"
    __table_args__ = (
        # The same provider item is recorded once per episode; re-scanning
        # updates the existing row instead of piling up duplicates.
        UniqueConstraint("episode_id", "provider", "provider_id", name="uq_episode_release_provider_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    raw_name: Mapped[str] = mapped_column(String(500), nullable=False)
    #: How this release was found: "rss" for a feed item, "indexer" for a
    #: search hit. Never a site name - which site was asked is the
    #: indexer's business, and a row outliving an indexer must not leave a
    #: dangling brand in the data.
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="indexer")
    provider_id: Mapped[str] = mapped_column(String(80), nullable=False)
    magnet_url: Mapped[str | None] = mapped_column(String(1000))
    torrent_url: Mapped[str | None] = mapped_column(String(500))
    size: Mapped[int | None] = mapped_column(Integer)
    seeders: Mapped[int | None] = mapped_column(Integer)
    subgroup: Mapped[str | None] = mapped_column(String(80))
    resolution: Mapped[str | None] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)

    episode: Mapped["Episode"] = relationship(back_populates="releases")

    @property
    def download_url(self) -> str | None:
        """What the torrent client should be handed. Magnets win when present."""
        return self.magnet_url or self.torrent_url


__all__ = ["EpisodeRelease"]
