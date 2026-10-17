"""When each episode of a show is due (build 146).

    *"when fetching the data we also should fetch the release date of an
    episode, because I want to implement this on the watchlist. [...] for
    animes where we do not have it in theory we can also fake it. If we know
    the initial release date [...] we also know then the episode 2 will come
    out 1 week later, so we could do it by math (+7 days)."*

Two ways to answer, and the difference between them matters enough to return
it alongside the date:

* **From the anchor.** A provider published "episode N airs at this instant" -
  AniList does, on every season query - so every other episode is that instant
  plus seven days per episode between them, counting in either direction. The
  anchor episode itself is a published fact.
* **From the premiere.** No anchor, but a start date: episode 1 is the
  premiere and the rest follow weekly.

Both are arithmetic past the one known point. Nothing here pretends to know
about a broadcast break, a recap week, or a schedule that was never weekly in
the first place - which is exactly why the answer says how it was arrived at.
A watchlist can show a date it derived differently from one a provider stated,
and can stop trusting a derived one the moment a file turns up that disagrees.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

#: How long a week is. Named because the assumption is the whole point: almost
#: every airing anime is weekly, and the ones that are not are why the answer
#: is labelled rather than presented as fact.
WEEK = timedelta(days=7)

#: The episode a premiere date describes.
FIRST_EPISODE = 1

EXACT = "exact"  # the provider said this episode airs at this instant
FROM_ANCHOR = "from_anchor"  # counted from an episode the provider did name
FROM_PREMIERE = "from_premiere"  # counted from the start date, weekly


@dataclass(frozen=True)
class Airing:
    """When an episode is due, and how confident that is.

    ``at`` carries a time of day only when it was derived from an anchor; a
    premiere date is a date, and inventing a time for it would be inventing
    the one part of the answer nobody published.
    """

    episode: int
    at: datetime | date
    #: ``exact`` | ``from_anchor`` | ``from_premiere``.
    basis: str

    @property
    def on(self) -> date:
        """The day, whichever shape the answer arrived in."""
        return self.at.date() if isinstance(self.at, datetime) else self.at


def episode_airing(
    episode: int,
    *,
    air_slot_at: datetime | None = None,
    air_slot_episode: int | None = None,
    air_start_date: date | None = None,
    episode_count: int | None = None,
) -> Airing | None:
    """When this episode is due, or None when there is nothing to count from.

    Deliberately a free function over plain values rather than a method on a
    Work: the watchlist has its own rows, and this has to be usable from both
    sides without either importing the other's models.
    """
    if episode < 1:
        return None
    # An episode past the end of the show is not a schedule question, it is a
    # wrong number - and answering it would invent a date for something that
    # does not exist.
    if episode_count is not None and episode_count > 0 and episode > episode_count:
        return None

    if air_slot_at is not None and air_slot_episode is not None and air_slot_episode > 0:
        if episode == air_slot_episode:
            return Airing(episode=episode, at=air_slot_at, basis=EXACT)
        return Airing(
            episode=episode,
            at=air_slot_at + WEEK * (episode - air_slot_episode),
            basis=FROM_ANCHOR,
        )

    if air_start_date is not None:
        if episode == FIRST_EPISODE:
            return Airing(episode=episode, at=air_start_date, basis=EXACT)
        return Airing(
            episode=episode,
            at=air_start_date + WEEK * (episode - FIRST_EPISODE),
            basis=FROM_PREMIERE,
        )

    # A show with neither an anchor nor a premiere date. Announced, usually,
    # and not yet scheduled - and a guess would be worse than silence.
    return None


def schedule(
    *,
    air_slot_at: datetime | None = None,
    air_slot_episode: int | None = None,
    air_start_date: date | None = None,
    episode_count: int | None = None,
) -> list[Airing]:
    """Every episode of a show whose length is known.

    Empty when the episode count is not known: a weekly series with no count
    would otherwise run for ever.
    """
    if not episode_count or episode_count < 1:
        return []
    found = [
        episode_airing(
            number,
            air_slot_at=air_slot_at,
            air_slot_episode=air_slot_episode,
            air_start_date=air_start_date,
            episode_count=episode_count,
        )
        for number in range(1, episode_count + 1)
    ]
    return [airing for airing in found if airing is not None]
