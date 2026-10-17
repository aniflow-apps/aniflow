"""Torrents somebody cleared on purpose (build 128).

Two features made opposite promises about one torrent, and the newer one won
silently:

* **Clear** / **Clear all** forget AniFlow's download row and leave the torrent
  in the client and the file on disk.
* **Orphan adoption** (build 119) adopts any finished torrent in AniFlow's
  category that no row knows about - and its docstring names "a cleared
  Downloads list" as one of the cases it exists to rescue.

Both worked as designed, which is why *"Clear all ... does only clear it temp
the stuff in there comes back"* had no obvious cause: after a clear, every
finished torrent in the category satisfies the orphan rule perfectly.

So a clear now writes the hash down. The background pass skips what is in
here; **Fix missing episodes does not**, because pressing that is the user
asking for adoption out loud, and the last explicit instruction should win.

Why the info hash and not the download id: the row is gone by definition. The
torrent is what is left, and its hash is the only identifier both sides agree
on - it is also what `uq_download_info_hash` already keys downloads by.
"""
from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.types import UtcDateTime, now_utc


class ClearedTorrent(Base):
    """One torrent the user told AniFlow to forget."""

    __tablename__ = "cleared_torrents"

    info_hash: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: What it was called, for the log line when it is skipped. Nothing reads
    #: it to make a decision - it exists so "skipped 4 torrents you cleared"
    #: can name them.
    label: Mapped[str | None] = mapped_column(String(500))
    cleared_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=now_utc, server_default=func.now(), nullable=False
    )


__all__ = ["ClearedTorrent"]
