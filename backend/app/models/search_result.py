"""Search results from the indexers the user configured.

Separate from RSS Release table to keep discovery sources distinct
while reusing the same matching/tagging pipeline.
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.anime import Anime


class SearchDecision(str, Enum):
    """Decision made by AniFlow after evaluating a search result against a profile."""

    MATCHED = "MATCHED"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"
    AMBIGUOUS = "AMBIGUOUS"
    SKIPPED = "SKIPPED"
    ATTENTION = "ATTENTION"


class SearchResult(Base):
    """A single search result from an indexer, evaluated against an anime."""

    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_search_result_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Provider identification
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    # Search context
    query_used: Mapped[str] = mapped_column(String(500), nullable=False)
    page: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Raw provider data
    raw_name: Mapped[str] = mapped_column(String(500), nullable=False)
    torrent_url: Mapped[str | None] = mapped_column(String(500))
    magnet_url: Mapped[str | None] = mapped_column(String(500))
    size: Mapped[int | None] = mapped_column(Integer)
    seeders: Mapped[int | None] = mapped_column(Integer)
    leechers: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    category: Mapped[str | None] = mapped_column(String(80))

    # Parsed snapshot (same fields as Release)
    parsed_title: Mapped[str | None] = mapped_column(String(500))
    subgroup: Mapped[str | None] = mapped_column(String(80))
    episode: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(8))
    media_source: Mapped[str | None] = mapped_column(String(16))

    # Decision & anime link
    decision: Mapped[SearchDecision] = mapped_column(
        SAEnum(SearchDecision), default=SearchDecision.IGNORED, nullable=False
    )
    anime_id: Mapped[str | None] = mapped_column(ForeignKey("anime.id", ondelete="SET NULL"), nullable=True, index=True)
    failure_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ambiguous_anime_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False
    )

    # Relationship
    anime: Mapped["Anime"] = relationship(back_populates="search_results")


__all__ = ["SearchResult", "SearchDecision"]