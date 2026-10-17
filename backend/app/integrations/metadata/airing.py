"""Whether a show has started, is running, or is over (build 148).

The one field on a calendar card that answers "can I watch this yet". It has
had a column, a merge policy and a default since build 141 and nothing to put
in it, because no adapter extracted it - so every entry read ``UNKNOWN`` and
the page could not tell *starts next week* from *finished two years ago*.

Every source publishes it and every source spells it differently:

===========  ==============================================================
AniList      ``NOT_YET_RELEASED`` ``RELEASING`` ``FINISHED`` ``CANCELLED``
             ``HIATUS``
MyAnimeList  ``not_yet_aired`` ``currently_airing`` ``finished_airing``
Jikan        ``Not yet aired`` ``Currently Airing`` ``Finished Airing``
Kitsu        ``unreleased`` ``upcoming`` ``tba`` ``current`` ``finished``
Shikimori    ``anons`` ``ongoing`` ``released``
===========  ==============================================================

So this is one table rather than five ``if`` chains in five adapters, and the
same reasoning as ``adult.py`` beside it: a rule that has to be re-applied per
source is a rule four sources will eventually get wrong.

**Two decisions worth stating.**

``HIATUS`` is reported as ``AIRING``. Only AniList publishes it, and a sixth
state carried by one source is that source's vocabulary rather than the
calendar's. A show on hiatus has started and has not finished, which is what
``AIRING`` is opposite ``NOT_YET_AIRED`` and ``FINISHED`` for. It is the
lesser of two inaccuracies; the alternative was to say it had finished.

Kitsu's ``tba`` means "no date announced", which is a statement about the
*schedule* rather than about the show, and it is grouped with the other two
ways of saying it has not started. Anything unrecognised becomes ``UNKNOWN``
rather than a guess - a new word from a source is a thing to look up, not to
round to the nearest state.
"""
from __future__ import annotations

#: What the calendar stores. Five words for what a dozen providers' words mean.
#:
#: Defined here, in the layer that produces them, and re-exported by
#: ``models/calendar.py`` so that the database's vocabulary and the adapters'
#: cannot drift apart. This module imports nothing, which is what makes that
#: direction safe.
STATUS_NOT_YET_AIRED = "NOT_YET_AIRED"
STATUS_AIRING = "AIRING"
STATUS_FINISHED = "FINISHED"
STATUS_CANCELLED = "CANCELLED"
STATUS_UNKNOWN = "UNKNOWN"

#: Every state the calendar can hold, for the interface to name and for tests
#: to enumerate rather than copy.
AIRING_STATUSES: tuple[str, ...] = (
    STATUS_NOT_YET_AIRED,
    STATUS_AIRING,
    STATUS_FINISHED,
    STATUS_CANCELLED,
    STATUS_UNKNOWN,
)

#: Each source's spelling, folded to lower case and underscored. Anything not
#: in here is unknown rather than guessed at.
_SAID: dict[str, str] = {
    # Not started.
    "not_yet_released": STATUS_NOT_YET_AIRED,
    "not_yet_aired": STATUS_NOT_YET_AIRED,
    "unreleased": STATUS_NOT_YET_AIRED,
    "upcoming": STATUS_NOT_YET_AIRED,
    "tba": STATUS_NOT_YET_AIRED,
    "anons": STATUS_NOT_YET_AIRED,
    # Running.
    "releasing": STATUS_AIRING,
    "currently_airing": STATUS_AIRING,
    "current": STATUS_AIRING,
    "ongoing": STATUS_AIRING,
    "airing": STATUS_AIRING,
    # See the note above: one source's word, and it means "started, not over".
    "hiatus": STATUS_AIRING,
    # Over.
    "finished": STATUS_FINISHED,
    "finished_airing": STATUS_FINISHED,
    "released": STATUS_FINISHED,
    "completed": STATUS_FINISHED,
    # Abandoned.
    "cancelled": STATUS_CANCELLED,
    "canceled": STATUS_CANCELLED,
    "discontinued": STATUS_CANCELLED,
}


def airing_status(said: object) -> str:
    """One source's word for where a show is, as the calendar's word.

    Returns ``UNKNOWN`` for nothing, for an empty string and for a word this
    table has never seen - all three of which mean the same thing to a page
    deciding whether to draw a badge.
    """
    if said is None:
        return STATUS_UNKNOWN
    key = "_".join(str(said).split()).lower()
    return _SAID.get(key, STATUS_UNKNOWN)
