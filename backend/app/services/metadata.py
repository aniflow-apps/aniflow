"""Which metadata source AniFlow asks, and what happens when it will not answer.

**One source at a time, chosen in Settings.** AniList by default, Kitsu as the
alternative. This replaced a fallback chain, and the reason is that the two
sources do not describe the same world: ids are per-provider (AniList's 52991
and Kitsu's 52991 are different shows), episode counts and titles differ, and
Kitsu has no broadcast times at all. A chain therefore built a library half
from each - an anime added during an outage kept a Kitsu id for ever, and a
calendar served from Kitsu looked wrong beside one served from AniList.

What replaced it is a cache, which is the better answer to an unreliable API:
a season line-up barely changes in a day, so serving yesterday's beats asking
somebody else. See services/calendar.py.

**Fetching one anime by id never used the selected source and still does
not.** An id belongs to the provider that issued it, so the provider named in
the request is the only one asked. An anime added through Kitsu keeps working
after a switch to AniList; it simply stops being refreshed by the new source,
which is the honest outcome of the two not sharing ids.

Switching sources clears the cached calendar: it was built from the other
source's answers, and mixing them is exactly what this module now avoids.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.core.logging import for_component
from backend.app.integrations.metadata.anidb import AnidbDumpProvider
from backend.app.integrations.metadata.anilist import AniListProvider
from backend.app.integrations.metadata.base import AnimeCandidate, MetadataProvider
from backend.app.integrations.metadata.kitsu import KitsuProvider

logger = for_component("Metadata")

#: Every provider AniFlow can talk to, by the name its candidates carry.
PROVIDERS: dict[str, MetadataProvider] = {
    "anilist": AniListProvider(),
    "kitsu": KitsuProvider(),
}

#: The default when nothing is configured yet. AniList: it has the airing
#: times, the genres and the episode counts, and its ids are what most of a
#: library already uses.
DEFAULT_PROVIDER = "anilist"

#: Every source that can be selected, for validating the setting.
SELECTABLE = tuple(PROVIDERS)

#: Sources that may be *searched* but never selected as the metadata source
#: (build 95). Searching is a list somebody picks from, so an extra source can
#: only add entries; selecting one would decide where every later refresh
#: comes from, which is a different question with a different answer.
#:
#: Built on demand rather than at import: MyAnimeList reads its client id in
#: its constructor, so a long-lived instance would hold whatever was
#: configured when the process started.
def _jikan() -> MetadataProvider:
    from backend.app.integrations.metadata.jikan import JikanProvider

    return JikanProvider()


def _shikimori() -> MetadataProvider:
    from backend.app.integrations.metadata.shikimori import ShikimoriProvider

    return ShikimoriProvider()


def _mal() -> MetadataProvider | None:
    """MyAnimeList, but only when it has the client id it needs.

    Without one it fails on every request, and a failure line for a source
    nobody configured is noise in the one message that has to stay readable.
    """
    from backend.app.integrations.metadata.mal import MalProvider

    mal = MalProvider()
    return mal if getattr(mal, "configured", False) else None


def _anidb() -> MetadataProvider:
    return AnidbDumpProvider()


#: How the fallbacks are built, in the order they are asked.
#:
#: Factories rather than instances, for two reasons. MyAnimeList reads its
#: client id in its constructor, so a long-lived instance would hold whatever
#: was configured when the process started. And a list of factories is a seam
#: the test suite can empty - without that, adding this chain sent every
#: add-anime test to the real Jikan, which is the same trap build 89 fell into
#: with a merged provider dict.
FALLBACK_FACTORIES: list = [_jikan, _shikimori, _mal, _anidb]


def searchable_fallbacks() -> list[MetadataProvider]:
    """Every source worth asking after the selectable two, in order."""
    found: list[MetadataProvider] = []
    for factory in FALLBACK_FACTORIES:
        adapter = factory()
        if adapter is not None:
            found.append(adapter)
    return found


#: Providers that can resolve an id but must never be *selected* (build 89).
#:
#: AniDB reads the downloaded title dump, so it can name an id and nothing
#: else - no cover, no episode count, no airing time. Choosing it as the
#: metadata source would be choosing to have none. But adopting a Shoko
#: Server series starts from an AniDB id, and resolving that id is exactly
#: what it can do.
BY_ID_ONLY: dict[str, MetadataProvider] = {"anidb": AnidbDumpProvider()}


def by_id(name: str) -> MetadataProvider | None:
    """The provider that can answer for an id issued by ``name``.

    PROVIDERS first, and deliberately the object itself rather than a copy of
    it: the routes expose that dict as their provider seam and the tests
    replace entries in it. A merged copy made here would ignore them, which
    is how build 89 briefly sent every add-anime test to the real AniList.
    """
    return PROVIDERS.get(name) or BY_ID_ONLY.get(name)


@dataclass(frozen=True)
class ProviderStatus:
    """Whether metadata is reaching AniFlow, and from where.

    ``state`` is deliberately about the *situation*, not the provider:

    * ``ok`` - the selected source answered;
    * ``unreachable`` - it did not;
    * ``unknown`` - nothing has been asked yet.

    There is no "degraded" any more: with one source there is no such thing as
    an answer from somewhere else. An outage is covered by the cache instead,
    and the calendar says how old what it is showing is.

    The provider's name stays here on the server. The status line names the
    situation instead, because which database answered is AniFlow's problem to
    solve and not a thing to explain in a sidebar.
    """

    state: str
    #: Who answered, for the log and the tooltip on the server side only.
    provider: str | None = None
    checked_at: datetime | None = None


#: How long a remembered outcome counts as current. Long, because every real
#: search and every calendar page refreshes it for free; refresh() only covers
#: the case where nobody has asked the app anything.
STATUS_TTL = timedelta(minutes=10)

_status = ProviderStatus("unknown")


def _remember(state: str, provider: str | None) -> None:
    global _status
    _status = ProviderStatus(state, provider, datetime.now(UTC))


def status() -> ProviderStatus:
    """What the last real question produced. Never asks anything itself."""
    return _status


async def refresh() -> ProviderStatus:
    """Ask a provider something, but only if nothing recent has.

    Called from the worker's tick rather than from /health. /health is what
    Docker decides to restart the container over, and a provider that hangs
    for its full timeout would have turned an unreachable API into a restart
    loop. Here a slow answer costs nothing but a slightly late status line.
    """
    remembered = _status
    if remembered.checked_at is not None and datetime.now(UTC) - remembered.checked_at < STATUS_TTL:
        return remembered
    try:
        # Any query does: an empty result still means somebody answered, and
        # one letter is the cheapest question there is.
        await search("a", 1)
    except NoProviderAnswered:
        pass  # already recorded
    except Exception:  # noqa: BLE001 - a status line must not break the tick
        logger.debug("metadata check failed for an unexpected reason", exc_info=True)
    return _status


class NoProviderAnswered(RuntimeError):
    """Every provider was asked and none of them answered.

    Carries what each one said, because "the internet is down" and "one API
    changed its response shape" need different things from the user and read
    identically without the detail.
    """

    def __init__(self, failures: list[str]) -> None:
        super().__init__("; ".join(failures) or "no metadata provider is configured")
        self.failures = failures


def provider(name: str) -> MetadataProvider | None:
    """One provider by name, for the calls that must not fall back."""
    return PROVIDERS.get(name)


async def selected(session=None) -> str:
    """Which source to ask. The setting, or the default while there is none."""
    from backend.app.services.app_settings import load_app_settings

    try:
        name = (load_app_settings(session).metadata_provider or "").strip().lower()
    except Exception:  # noqa: BLE001 - a lookup must not fail on a config read
        return DEFAULT_PROVIDER
    return name if name in PROVIDERS else DEFAULT_PROVIDER


async def search(query: str, limit: int = 20, *, preferred: str | None = None) -> list[AnimeCandidate]:
    """Search the selected source.

    An empty result is an answer, not a failure: a source that is up and has
    nothing for "asdf" is telling the truth.

    ``preferred`` names a source explicitly and is honoured when it is one
    AniFlow knows - the calendar passes the provider whose cache it is filling.
    Anything else falls back to the setting rather than to another provider.
    """
    async def ask(adapter: MetadataProvider) -> list[AnimeCandidate]:
        return await adapter.search_anime(query, limit)

    return await _answer(ask, preferred=preferred, what=f"search for {query!r}")


async def search_everywhere(query: str, limit: int = 20, *, preferred: str | None = None) -> list[AnimeCandidate]:
    """Search every source, best first, and return the first real answer.

    Only for the add-anime box. See the module docstring for why this is not
    how anything else asks.

    An empty result from a source that is up is not an answer here - the next
    source is asked. That differs from :func:`search`, deliberately: with one
    source, "nothing" is the truth about the thing you chose; with six, it is
    a reason to ask the next one.
    """
    order = await _search_order(preferred)
    failures: list[str] = []
    for adapter in order:
        name = getattr(adapter, "name", "?")
        try:
            results = await adapter.search_anime(query, limit)
        except httpx.HTTPError as exc:
            reason = str(exc) or type(exc).__name__
            logger.warning("%s could not answer search for %r: %s", name, query, reason)
            failures.append(f"{name}: {reason}")
            continue
        except Exception as exc:  # noqa: BLE001 - one broken source must not end the search
            reason = str(exc) or type(exc).__name__
            logger.warning("%s failed searching for %r: %s", name, query, reason)
            failures.append(f"{name}: {reason}")
            continue
        if results:
            # Every real question doubles as a health check, which is why the
            # status line is usually right without anyone probing anything.
            if name in PROVIDERS:
                _remember("ok", name)
            return results
        failures.append(f"{name}: nothing found")
    raise NoProviderAnswered(failures)


async def _search_order(preferred: str | None) -> list[MetadataProvider]:
    """The sources to ask, best first, without repeating one."""
    chosen = (preferred or "").strip().lower()
    if chosen not in PROVIDERS:
        chosen = await selected()
    order: list[MetadataProvider] = [PROVIDERS[chosen]]
    order += [adapter for name, adapter in PROVIDERS.items() if name != chosen]
    order += searchable_fallbacks()
    return order


async def season(name: str, year: int, *, preferred: str | None = None) -> list[AnimeCandidate]:
    """The season line-up from the selected source."""
    async def ask(adapter: MetadataProvider) -> list[AnimeCandidate]:
        if not hasattr(adapter, "get_season"):
            raise httpx.HTTPError(f"{getattr(adapter, 'name', '?')} has no season list")
        return await adapter.get_season(name, year)

    return await _answer(ask, preferred=preferred, what=f"the {name} {year} line-up")


async def _answer(ask, *, preferred: str | None, what: str) -> list[AnimeCandidate]:
    """Ask the one source that is selected. No second attempt elsewhere.

    Raising rather than trying somebody else is the point: the caller decides
    what to do about an outage, and for the calendar that decision is "serve
    what is cached and say how old it is" - which is a better answer than a
    different provider's thinner data.
    """
    name = preferred if preferred in PROVIDERS else await selected()
    adapter = PROVIDERS.get(name)
    if adapter is None:  # pragma: no cover - SELECTABLE is built from PROVIDERS
        _remember("unreachable", None)
        raise NoProviderAnswered([f"{name} is not a metadata source AniFlow knows"])
    try:
        results = await ask(adapter)
    except httpx.HTTPError as exc:
        reason = str(exc) or type(exc).__name__
        logger.warning("%s could not answer %s: %s", name, what, reason)
        _remember("unreachable", name)
        raise NoProviderAnswered([f"{name}: {reason}"]) from exc
    # Every real question doubles as a health check, which is why the status
    # line is usually right without anyone probing anything.
    _remember("ok", name)
    return results


__all__ = [
    "DEFAULT_PROVIDER",
    "FALLBACK_FACTORIES",
    "search_everywhere",
    "searchable_fallbacks",
    "BY_ID_ONLY",
    "PROVIDERS",
    "by_id",
    "SELECTABLE",
    "STATUS_TTL",
    "NoProviderAnswered",
    "ProviderStatus",
    "refresh",
    "provider",
    "selected",
    "search",
    "season",
    "status",
]
