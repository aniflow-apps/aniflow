"""Reusable release-profile templates shared across anime."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc
from backend.app.models.release_profile import CensorPreference


class ProfileTemplate(Base):
    """A named set of deterministic release rules.

    Applying a template to an anime copies its values into that anime's
    profile immediately (copy semantics - later template edits never change
    already-configured anime silently). The link is remembered for display.
    """

    __tablename__ = "profile_templates"
    __table_args__ = (UniqueConstraint("name", name="uq_profile_template_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    subgroups: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(8))
    media_source: Mapped[str | None] = mapped_column(String(16))
    censor_preference: Mapped[CensorPreference] = mapped_column(
        Enum(CensorPreference), default=CensorPreference.ANY, nullable=False
    )
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
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: The file in the configuration folder this row mirrors, without its
    #: extension. The folder is the source of truth; this is the link back to
    #: it, kept on the row so renaming an object moves its file without
    #: changing the id anything else refers to.
    config_slug: Mapped[str | None] = mapped_column(String(120), index=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False)


__all__ = ["ProfileTemplate"]
