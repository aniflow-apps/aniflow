"""AniDB's title dump, and the episode numbering read from it (build 83).

What is left of the build-83 experiment's schema after build 149 took the
comparison tables out. Four tables, in ``data/calendar.db``:

* ``anidb_anime`` and ``anidb_title`` - AniDB's own title dump, the only
  complete list of "this show, by every name it goes by, with one id". It is
  the identity spine the calendar matches onto, and by far the most expensive
  thing in the file: seventy-five thousand rows, fetched at most weekly, and
  never by scraping the site.
* ``anidb_numbering`` - where a season sits in a show's absolute episode
  count, derived from the same dump.
* ``dump_state`` - when the dump was last fetched, so it is not fetched again.

``calendar_entry``, ``source_refresh``, ``work``, ``work_id`` and
``work_title`` were the comparison's own, one row per source per show, kept
unmerged because the whole question was where the sources disagreed. It was
answered - the answer was "merge them all", which is Calendar v2 - and schema
version 9 drops them.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.calendar_store import CalendarBase
from backend.app.db.types import UtcDateTime


class AnidbAnime(CalendarBase):
    """One anime as AniDB knows it: an id and its primary title."""

    __tablename__ = "anidb_anime"

    aid: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: AniDB's own main title - romaji, and the name Shoko usually shows.
    primary_title: Mapped[str] = mapped_column(String(400), nullable=False)


class AnidbNumbering(CalendarBase):
    """Where one AniDB entry sits in its franchise's numbering (build 112).

    The question this answers: *a group writes "39" - which episode is that?*
    Some groups number from the start of the franchise and never stop, so the
    same file is "01" to Erai-raws and "39" to SubsPlease, and the only way to
    tell is to know how many episodes came before this entry.

    AniDB has the pieces and nothing has them assembled: each season is its own
    entry with its own count, and a Prequel relation to the one before it. Add
    the counts along that chain and the answer falls out - Re:Zero season 4 is
    25 + 13 + 12 + 16 = 66 episodes in, so its first is the franchise's 67th.

    **The pool.** Every entry the walk touches is written down here, not only
    the one that was asked about: resolving season 4 teaches AniFlow where
    seasons 1, 2 and 3 begin as well, and a finished season's length never
    changes. It is in the calendar database because that is where the AniDB
    tables already live and because everything in here can be fetched again -
    which is the definition of what belongs in that file.

    The title dump cannot fill this: it carries titles and ids and nothing
    else. The counts come from Shoko (which caches AniDB's own records) or
    from AniDB's HTTP API, one entry at a time, and from a person typing the
    number in when neither is available.
    """

    __tablename__ = "anidb_numbering"

    aid: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(400))
    #: TV, Movie, OVA, Web - a movie in the middle of a chain is not counted.
    kind: Mapped[str | None] = mapped_column(String(30))
    #: How many episodes this entry itself has.
    episode_count: Mapped[int | None] = mapped_column(Integer)
    #: The entry immediately before this one, when AniDB says there is one.
    prequel_aid: Mapped[int | None] = mapped_column(Integer)
    #: The franchise number of this entry's first episode. 1 for a first
    #: season; 67 for Re:Zero's fourth. None while the chain is incomplete.
    absolute_start: Mapped[int | None] = mapped_column(Integer)
    #: Where the number came from: shoko, anidb, manual. A number a person
    #: typed outranks anything fetched, and says so.
    source: Mapped[str | None] = mapped_column(String(20))
    checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class AnidbTitle(CalendarBase):
    """One spelling of one anime, normalised for comparison.

    The dump carries every name a show is known by - primary, official per
    language, synonyms and short forms - which is exactly why it can match a
    Shoko series named in Japanese against a watchlist entry named in English.
    """

    __tablename__ = "anidb_title"
    __table_args__ = (UniqueConstraint("title_norm", "aid", name="uq_anidb_title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: The title with case, punctuation and spacing removed. Indexed, because
    #: this is the column every lookup goes through.
    title_norm: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    #: The spelling as AniDB wrote it, punctuation and all (build 112).
    #:
    #: Nothing in AniFlow matches on this - matching is what the normalised
    #: column is for - but a pool of every name every anime goes by, in every
    #: language AniDB records, is worth having as itself. Asked for on the
    #: grounds that it might one day serve something other than AniFlow, and
    #: that is reason enough: the dump already comes down every week, and
    #: keeping the original text costs one column.
    title: Mapped[str | None] = mapped_column(String(400))
    aid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: 1 primary, 2 synonym, 3 short, 4 official. Kept so a match can say how
    #: good it is: an official title agreeing is worth more than a short form.
    kind: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    language: Mapped[str | None] = mapped_column(String(20))


class DumpState(CalendarBase):
    """When the title dump was last fetched and read. One row.

    Shown on the Shoko Server page, because that is where the dump's value is
    visible: it is what stops Shoko offering a series the watchlist already
    has.
    """

    __tablename__ = "dump_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    downloaded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: When the last attempt was made, successful or not. Separate from
    #: ``downloaded_at`` so a failing download can be backed off without
    #: looking as though a file arrived.
    attempted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    parsed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: How much is in it, which is the only useful proof it parsed correctly.
    anime_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes_downloaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Set when the last attempt failed, so the page can say why rather than
    #: showing an old date with no explanation.
    last_error: Mapped[str | None] = mapped_column(String(400))
