"""Persistent storage for per-anime release profiles."""
import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.profile_template import ProfileTemplate
    from backend.app.models.anime import Anime


class CensorPreference(str, enum.Enum):
    ANY = "ANY"
    UNCENSORED = "UNCENSORED"
    CENSORED = "CENSORED"


class ReleaseProfile(Base):
    """The explicit, deterministic release choices for exactly one anime.

    An unset optional field means "any value is acceptable"; a set field means
    the release must carry exactly that value. Unknown release metadata is
    never silently accepted when the profile demands the field be known.
    """

    __tablename__ = "release_profiles"
    __table_args__ = (UniqueConstraint("anime_id", name="uq_release_profile_anime"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    anime_id: Mapped[str] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), nullable=False, index=True)

    # Empty list means every subgroup is acceptable; otherwise the release
    # subgroup must be one of these entries (case-insensitive).
    subgroups: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Canonical values such as "1080p"; None means any resolution.
    resolution: Mapped[str | None] = mapped_column(String(8))
    # Canonical tokens such as "web_dl", "bluray"; None means any source.
    media_source: Mapped[str | None] = mapped_column(String(16))
    censor_preference: Mapped[CensorPreference] = mapped_column(
        Enum(CensorPreference), default=CensorPreference.ANY, nullable=False
    )

    # Explicit switches that make "unknown metadata" a mismatch instead of an
    # acceptance. See AGENTS.md: unknown is not automatically a mismatch.
    #: Edition markers every release must carry: ["uncensored", "dual-audio"]
    #: (build 97). The canonical words from services/release_edition.py, which
    #: reads them out of a release name.
    #:
    #: A list rather than a column each, because the axes are endless - a
    #: platform, a subtitle set, a dub - and a column per fansub fashion means
    #: a migration per fashion. Empty means "any edition", which is what every
    #: profile written before this meant.
    edition: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Edition markers a release must *not* carry (build 98). The copies of
    #: one episode are alternatives: the row that is not the dual-audio one
    #: has to refuse the dual-audio file, which carries everything the plain
    #: row asks for and one thing more.
    edition_not: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    require_title_match: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_resolution_known: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_source_known: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_censor_known: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Set when this profile's values were copied from a template. Purely
    # informational: template edits never propagate automatically.
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_templates.id", ondelete="SET NULL"), nullable=True
    )
    # Read-only: used to name the template a profile came from.
    template: Mapped["ProfileTemplate | None"] = relationship(viewonly=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)

    anime: Mapped["Anime"] = relationship(back_populates="profile")


__all__ = ["CensorPreference", "ReleaseProfile"]
