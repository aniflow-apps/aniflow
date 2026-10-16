"""Actionable Attention records and the Activity event log."""
import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.anime import Anime, Episode


class AttentionKind(str, enum.Enum):
    NEW_VERSION = "NEW_VERSION"              # V2/V3 after owned V1 - needs user decision
    AMBIGUOUS_RELEASE = "AMBIGUOUS_RELEASE"  # title matches several tracked anime
    IMPORT_BLOCKED = "IMPORT_BLOCKED"        # importer could not proceed safely
    DOWNLOAD_MISSING = "DOWNLOAD_MISSING"    # torrent vanished from the client
    FEED_FAILED = "FEED_FAILED"              # an RSS source could not be read
    NEEDS_CONVERSION = "NEEDS_CONVERSION"    # added without an indexer; has no release name
    NO_RELEASES = "NO_RELEASES"              # the indexer answered, and nothing it returned fits
    DOWNLOAD_STALLED = "DOWNLOAD_STALLED"    # in the client, connected, and nobody is seeding it
    METADATA_CHANGED = "METADATA_CHANGED"    # the provider now disagrees with a value the user set
    #: A MATCHED release for a known anime whose episode number could not be
    #: read from its name (build 168) - a batch, most often. Used to be filed
    #: as AMBIGUOUS_RELEASE, which is about not knowing *which anime*; this is
    #: never that - anime_id is already set - and offering "assign to anime"
    #: for it was a fix for a different problem than the one raised.
    EPISODE_NOT_IDENTIFIABLE = "EPISODE_NOT_IDENTIFIABLE"
    #: One episode has two files on disk (build 189): an original kept by
    #: "Keep as original file", or a v1 left next to a v2. AniFlow keeps both
    #: and asks which to delete - see services/duplicate_files.py.
    DUPLICATE_FILES = "DUPLICATE_FILES"


class AttentionStatus(str, enum.Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"      # the underlying action was taken
    DISMISSED = "DISMISSED"    # user decided no action is needed


class Attention(Base):
    """One exceptional state that requires a user decision.

    Only one OPEN record per (kind, subject) exists at any time; repeated
    occurrences refresh the existing record instead of spamming duplicates.
    """

    __tablename__ = "attention"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[AttentionKind] = mapped_column(Enum(AttentionKind), nullable=False, index=True)
    status: Mapped[AttentionStatus] = mapped_column(
        Enum(AttentionStatus), default=AttentionStatus.OPEN, nullable=False, index=True
    )

    anime_id: Mapped[str | None] = mapped_column(ForeignKey("anime.id", ondelete="SET NULL"), nullable=True, index=True)
    release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id", ondelete="SET NULL"), nullable=True)
    download_id: Mapped[str | None] = mapped_column(ForeignKey("downloads.id", ondelete="SET NULL"), nullable=True)
    #: Which episode, when the record is about one (build 90). Most kinds are
    #: not: a feed that will not load is about a feed. It exists because a
    #: record about a *file* has no download row to be reached through, and
    #: because filing four bad files under one anime collapses them into one.
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    feed_source_id: Mapped[str | None] = mapped_column(ForeignKey("feed_sources.id", ondelete="SET NULL"), nullable=True)

    # Machine-readable extra context, e.g. the concrete import blocker code.
    detail: Mapped[str | None] = mapped_column(String(120))
    # Free-text note the user may attach when resolving/dismissing.
    note: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Activity(Base):
    """Append-only log of notable automated events."""

    __tablename__ = "activity"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    anime_id: Mapped[str | None] = mapped_column(ForeignKey("anime.id", ondelete="SET NULL"), nullable=True, index=True)
    #: Which episode, when the entry is about one (build 164). Most entries
    #: are not: a feed that will not load is about a feed. Added for the same
    #: reason `Attention.episode_id` was - an anime with several episodes
    #: moving at once needs its history told apart per episode.
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)

    anime: Mapped["Anime | None"] = relationship(viewonly=True)
    episode: Mapped["Episode | None"] = relationship(viewonly=True)


__all__ = ["Activity", "Attention", "AttentionKind", "AttentionStatus"]
