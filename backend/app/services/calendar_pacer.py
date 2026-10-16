"""How fast the Calendar is allowed to ask (build 142).

Calendar v2 replaces a background trickle with things a person can press. The
old prefetch fetched one season every thirty seconds and was, by accident, its
own rate limit. Initial Load is six seasons across five providers - and AniList
alone pages up to six times per season - so the same work now arrives as a
burst unless something holds it back.

This is that something. One place, so Initial Load, Update Calendar and an
on-demand season load cannot each decide separately that they are the
exception.

The rule is the same one already in force elsewhere in AniFlow: sequential,
spaced, and slower after a refusal. Nyaa is searched one term at a time with a
pause between them; the AniDB dump is fetched at most weekly. A free API that
answers 429 has told you something, and the correct response is not to ask
again immediately.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from backend.app.core.logging import for_component

logger = for_component("Calendar")

#: Seconds between two requests to the same provider.
#:
#: AniList is the one that matters: it publishes a small per-minute budget,
#: answers 429 with ``Retry-After``, and a single season is already six calls.
#: The others are more forgiving, and one second is politeness rather than
#: arithmetic.
INTERVALS = {
    "anilist": 2.0,
    "mal": 1.0,
    "jikan": 1.5,  # unofficial, and the strictest of the lot in practice
    "kitsu": 1.0,
    "shikimori": 1.0,
}
DEFAULT_INTERVAL = 1.0

#: What a refusal costs. Doubling per consecutive failure, from the provider's
#: own interval, capped so a provider that is down for a day does not end up
#: waiting a day before noticing it came back.
BACKOFF_CAP_SECONDS = 300.0


@dataclass
class _Provider:
    last_call: float = 0.0
    failures: int = 0


@dataclass
class Pacer:
    """Spacing for one run. Not global state, so a test can own one.

    ``sleep`` and ``clock`` are injected for the same reason: a test that
    proves the spacing is real should not take the spacing to run.
    """

    sleep: object = field(default=None)
    clock: object = field(default=None)
    _providers: dict[str, _Provider] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sleep is None:
            self.sleep = asyncio.sleep
        if self.clock is None:
            self.clock = time.monotonic

    def _state(self, provider: str) -> _Provider:
        return self._providers.setdefault(provider, _Provider())

    def interval(self, provider: str) -> float:
        """How long this provider must be left alone for, right now."""
        base = INTERVALS.get(provider, DEFAULT_INTERVAL)
        state = self._state(provider)
        if not state.failures:
            return base
        return min(base * (2**state.failures), BACKOFF_CAP_SECONDS)

    async def before(self, provider: str) -> float:
        """Wait until this provider may be asked again. Returns what it cost.

        Called immediately before the request rather than after the previous
        one, so time spent on everything else - parsing, identity resolution,
        writing to the database - counts towards the interval instead of being
        added to it.
        """
        state = self._state(provider)
        if not state.last_call:
            state.last_call = self.clock()  # type: ignore[operator]
            return 0.0
        due = state.last_call + self.interval(provider)
        now = self.clock()  # type: ignore[operator]
        waited = 0.0
        if due > now:
            waited = due - now
            await self.sleep(waited)  # type: ignore[operator]
        state.last_call = self.clock()  # type: ignore[operator]
        return waited

    def succeeded(self, provider: str) -> None:
        """Clear the backoff. One good answer is enough."""
        self._state(provider).failures = 0

    def failed(self, provider: str) -> None:
        """Slow down. Repeatedly, if it keeps happening.

        Born out of an AniList outage that was asked about twice a minute for
        days, which produced nothing except a log nobody could read.
        """
        state = self._state(provider)
        state.failures += 1
        logger.debug(
            "%s failed %s time(s) in a row; next interval %.1fs",
            provider,
            state.failures,
            self.interval(provider),
        )
