"""Anime aliases with provenance and RSS analysis run history."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc


class AliasSource(str, enum.Enum):
    PROVIDER = "PROVIDER"  # came from the metadata provider's synonym list
    USER = "USER"          # entered manually by the user
    LEARNED = "LEARNED"    # learned from a manually assigned release title


class AnimeAlias(Base):
    """One alternative name for an anime, with its origin."""

    __tablename__ = "anime_aliases"
    __table_args__ = (UniqueConstraint("anime_id", "value", name="uq_anime_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    anime_id: Mapped[str] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[AliasSource] = mapped_column(
        Enum(AliasSource), default=AliasSource.PROVIDER, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)

    anime = relationship("Anime", viewonly=True)


class AnalysisRun(Base):
    """Header of one RSS-analyze execution (results stay ephemeral)."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    found: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    exact: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    high: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    review: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unknown: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


__all__ = ["AliasSource", "AnalysisRun", "AnimeAlias"]
