"""The full step-by-step history of one episode's journey (build 165).

Separate from ``Activity`` on purpose. Activity is a curated diary - one
meaningful event per acquisition, not dozens of technical ones (an earlier
build undid exactly the mistake of logging every pass there). The In Progress
page asked for the opposite: every real change in what an episode's own state
says, so its history reads as the whole story rather than the highlights.
Keeping the two apart means neither has to compromise for the other - Activity
stays quiet, and this stays complete.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc

if TYPE_CHECKING:
    from backend.app.models.anime import Episode


class EpisodeProgressEvent(Base):
    """One change in ``episode_view.view_for()``'s answer for this episode.

    Written from the single place every episode's status is recomputed
    (``services.episode_state.refresh_episode_status``), so nothing that
    changes an episode needs to remember to log it separately.
    """

    __tablename__ = "episode_progress_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: One of services/episode_view.py:STATES. Null for the very first event
    #: an episode ever gets (there is nothing to say it changed *from*).
    from_state: Mapped[str | None] = mapped_column(String(40))
    to_state: Mapped[str] = mapped_column(String(40), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=now_utc, server_default=func.now(), nullable=False)

    episode: Mapped["Episode"] = relationship(viewonly=True)


__all__ = ["EpisodeProgressEvent"]
