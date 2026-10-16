"""The file a swap is replacing, remembered until the new one lands (116).

Asked for after a walk through what a subgroup swap does: *"for the case we
swap the subgroup, we actually have to save somewhere the previous filenames,
so the delete stuff happens. And when its done then we can remove it."*

Until this build a swap had two answers and both dealt with the old file
immediately - ``replace`` deleted it, ``backup`` renamed it out of the way -
so between pressing the button and the new release finishing there was no
episode there at all. On a show that is eleven episodes in, that is eleven
gaps for however long the new group takes, and if the new release turns out
not to exist the gaps are permanent.

This is the third answer: leave the file where it is, write down which file it
was, and delete it once the replacement has actually been imported. Nothing
else in AniFlow could hold that note - the episode row is cleared by the swap
(that is what makes it look for the new release), and the file is no longer
anything AniFlow claims to have.

A queue of work rather than a property of the show, which is why it is a
table of its own: rows arrive when a swap happens, leave when the replacement
arrives, and an empty table is the normal state.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc


class SupersededFile(Base):
    """One file kept on disk until its replacement has arrived."""

    __tablename__ = "superseded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    #: Which kind of leftover this is (build 119).
    #:
    #: ``awaiting_replacement``
    #:     Kept where it is until the new release has been imported, then
    #:     deleted automatically. What this table was built for.
    #: ``renamed``
    #:     Renamed to ``.backup`` by a swap and left for the user. Never
    #:     deleted by AniFlow - *"maybe the user later wants to remove
    #:     them"* - only listed, so removing them is possible at all.
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="awaiting_replacement",
        server_default="awaiting_replacement",
    )
    anime_id: Mapped[str] = mapped_column(
        ForeignKey("anime.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The episode whose replacement this is waiting for. The row is cleared
    #: by the swap, so this is a number and a promise rather than a file.
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Where the file is, in full. Stored absolute on purpose: the library
    #: folder can be renamed or re-rooted between the swap and the arrival,
    #: and a path resolved later could point somewhere else entirely.
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    #: Just the name, for saying out loud which file is being waited on.
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    #: What it was, so the Downloads and detail pages can say "the SubsPlease
    #: copy is still there" rather than naming a path at somebody.
    subgroup: Mapped[str | None] = mapped_column(String(80))
    resolution: Mapped[str | None] = mapped_column(String(16))
    release_name: Mapped[str | None] = mapped_column(String(300))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )
    #: When the old copy was actually deleted. The row is kept briefly after
    #: that rather than dropped, so "what happened to my old files" has an
    #: answer for longer than the instant it happens in.
    removed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Why it is still there, when it is. A file the user has open, a
    #: read-only mount, a path that moved - all of which leave a copy behind
    #: that somebody has to know about.
    error: Mapped[str | None] = mapped_column(String(300))


__all__ = ["SupersededFile"]
