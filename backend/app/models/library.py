"""A library: a name and one target folder (build 192, 0.8.5).

Until now AniFlow had one media root. A library is a named place an anime can
live: the user mounts folders (``/series1``, ``/ecchi``, ``/current_season``)
and gives each one a name and, optionally, a few "fits" tags that let AniFlow
*suggest* it in Choose release. AniFlow never assigns one silently - see
docs/libraries-plan.md.

Several libraries may share a folder: someone with one big anime folder still
gets "Airing now" and "Earlier seasons" as two libraries over one directory.

``key`` is set only on Upcoming, the one special library (no folder, cannot be
renamed or removed). Every other library is the user's own; there must always
be at least one with a folder.
"""
from datetime import datetime

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

#: The libraries that always exist.
SYSTEM_AIRING_NOW = "airing_now"
SYSTEM_EARLIER = "earlier_seasons"
#: Anime that have not started: no files, so no folder (build 198).
SYSTEM_UPCOMING = "upcoming"


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Set for system libraries only; unique so each exists once.
    key: Mapped[str | None] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Absolute path as AniFlow's container sees it, e.g. ``/series1``.
    folder: Mapped[str] = mapped_column(String(500), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Suggestion tags, all optional. (The kind and censor tags were removed in build 252: nothing read them.)
    #: ``adult`` | ``not_adult`` | NULL (does not matter)
    fits_adult: Mapped[str | None] = mapped_column(String(12))
    #: ``current`` | ``past`` | NULL. Only a hint for the library suggested in
    #: Choose release: an untagged library takes the seasons no other claims.
    fits_season: Mapped[str | None] = mapped_column(String(12))
    #: Which of Shoko's import folders this is. Shoko reports its own
    #: container paths, which differ from AniFlow's, so the user links them
    #: rather than AniFlow guessing from the path.
    shoko_folder_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )

    @property
    def is_system(self) -> bool:
        return self.key is not None

    @property
    def has_folder(self) -> bool:
        """False for Upcoming, which holds anime that have no files to put anywhere."""
        return bool(self.folder)


__all__ = [
    "SYSTEM_AIRING_NOW",
    "SYSTEM_EARLIER",
    "SYSTEM_UPCOMING",
    "Library",
]
