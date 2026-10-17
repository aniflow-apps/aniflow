"""Indexers: the sites AniFlow searches for releases.

An indexer is whatever the user points it at. AniFlow ships none and knows
nothing about any particular site: a name and a URL that returns a feed is the
whole configuration, so a public build carries no site-specific code, no
bundled definitions, and no default that quietly works for one tracker.

Separate from ``FeedSource`` on purpose, for now. A feed source is read on a
schedule and reports whatever the site published; an indexer is *asked a
question* ("what do you have for this title?"). The same site usually serves
both, and one day these will merge - but the RSS side works, and merging is not
a prerequisite for making search configurable.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc


class Indexer(Base):
    """One searchable source, as the user configured it."""

    __tablename__ = "indexers"
    __table_args__ = (UniqueConstraint("name", name="uq_indexer_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    #: The URL to ask, as typed. Either a bare site address or a full search
    #: URL carrying the user's own filters; both forms are supported so the
    #: filtering a site offers in its query string can be used as-is instead of
    #: being re-modelled in AniFlow's UI.
    url: Mapped[str] = mapped_column(String(1000), nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Which one searches use. With several configured the default is asked;
    #: exactly one is default at a time.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: The file in <CONFIG_DIR>/indexers this row mirrors, without its
    #: extension. The folder is the source of truth; this is the link back to
    #: it, kept on the row so renaming an indexer moves its file without
    #: changing the id its managed feed source refers to.
    config_slug: Mapped[str | None] = mapped_column(String(120), index=True)

    #: When a search last reached it, so a silently broken indexer is visible.
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(String(250))

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), onupdate=now_utc, nullable=False
    )


__all__ = ["Indexer"]
