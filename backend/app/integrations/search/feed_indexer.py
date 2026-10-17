"""Searching a user-configured indexer.

No site is built in. The user supplies a URL; this asks it a question and reads
the answer as a feed. Everything site-specific - which categories exist, how
they are spelled, which filters a tracker offers - lives in that URL, where the
site itself already documents it. That is the whole reason a public build can
ship with no indexer at all.

Two URL forms are accepted, because both are things people actually have:

* a **search template** carrying the user's own filters, with the query either
  marked explicitly (``…&q={query}``) or left as a trailing empty parameter
  (``…&q=``), which is what a site's own search box produces;
* a **bare address** (``https://example.tld``), which gets a ``?q=`` appended.

The response must be a feed. Reading one is generic - RSS 2.0 or Atom, plus the
namespaced seeders/size/hash fields trackers publish - while reading a search
*page* would mean knowing that site's HTML, which is exactly the coupling being
removed here.
"""
import logging
import re
from urllib.parse import quote_plus, urlsplit

from backend.app.integrations.rss.base import FeedError, RssItem, fetch_feed
from backend.app.integrations.search.base import SearchError, SearchRequest, SearchResult

logger = logging.getLogger(__name__)

#: Placeholders a template may carry.
QUERY_TOKEN = "{query}"
PAGE_TOKEN = "{page}"

#: Query-string switches that conventionally turn a site's search page into a
#: feed. Tried only by the explicit Test action, never during a real search:
#: it is a convenience over a convention, not knowledge about any one site.
FEED_HINTS: tuple[str, ...] = ("page=rss", "rss=1", "format=rss", "feed=rss")


class NoIndexerConfigured(SearchError):
    """Nothing to search: no indexer has been added yet.

    Its own type so the UI can offer to add one instead of reporting a failure
    the user cannot act on.
    """


def build_search_url(template: str, query: str, *, page: int = 1) -> str:
    """The URL to request for one search term.

    Paging only happens when the template says where the page number goes.
    Guessing a parameter name would be site knowledge, and a wrong guess turns
    every page into a repeat of the first one.
    """
    term = quote_plus(query.strip())
    url = template.strip()

    if QUERY_TOKEN in url:
        url = url.replace(QUERY_TOKEN, term)
    elif "?" in url:
        head, _, tail = url.partition("?")
        parameters = tail.split("&") if tail else []
        # An empty-valued parameter is where the term belongs - it is what a
        # site's own search URL looks like once the box is cleared. Anywhere in
        # the string, not only at the end: appending a second one instead would
        # leave two of them, and a site that reads the first would answer with
        # its plain feed rather than a search.
        empty = next(
            (index for index, part in enumerate(parameters) if part.endswith("=") and part != "="),
            None,
        )
        if empty is not None:
            parameters[empty] += term
            url = f"{head}?{'&'.join(parameters)}"
        elif parameters:
            url = f"{head}?{'&'.join([*parameters, f'q={term}'])}"
        else:
            url = f"{head}?q={term}"
    else:
        url = f"{url.rstrip('/')}/?q={term}"

    if PAGE_TOKEN in url:
        url = url.replace(PAGE_TOKEN, str(max(page, 1)))
    return url


def supports_paging(template: str) -> bool:
    """Whether this template can ask for anything past the first page."""
    return PAGE_TOKEN in template


def feed_variants(url: str) -> list[str]:
    """The URL plus conventional feed forms of it, in order.

    Used by the Test action when a URL answers with a page instead of a feed,
    so a user who pasted their browser's address bar is told what to use rather
    than left with "that did not work".
    """
    variants = [url]
    for hint in FEED_HINTS:
        if hint.split("=")[0] + "=" in url:
            continue  # already carries this switch
        variants.append(_with_parameter(url, hint))
    return variants


def _with_parameter(url: str, parameter: str) -> str:
    """Add one query parameter, keeping any empty slot in place.

    Appending blindly would push a trailing ``q=`` out of last position. That
    is survivable now that the term fills an empty slot wherever it sits, but
    the URL a user is asked to adopt should still read the way they wrote it.
    """
    if "?" not in url:
        return f"{url}?{parameter}"
    head, _, tail = url.partition("?")
    parameters = tail.split("&") if tail else []
    empty = next(
        (index for index, part in enumerate(parameters) if part.endswith("=") and part != "="),
        None,
    )
    if empty is None:
        parameters.append(parameter)
    else:
        parameters.insert(empty, parameter)
    return f"{head}?{'&'.join(parameters)}"


class FeedIndexerProvider:
    """Asks one configured indexer for releases matching a request."""

    def __init__(
        self,
        url: str,
        *,
        name: str = "indexer",
        timeout: float = 15.0,
        fetcher=None,
        total_template: str | None = None,
        total_pattern: str | None = None,
        listing_template: str | None = None,
        listing_item: str | None = None,
        reader=None,
    ) -> None:
        if not url or not url.strip():
            raise NoIndexerConfigured("This indexer has no URL")
        self._template = url.strip()
        self.name = name
        self._timeout = timeout
        self._fetch = fetcher or (lambda target: fetch_feed(target, timeout=timeout))
        # How to read the site's own "out of N results", when its definition
        # file says where that is. Both come from the file: this class knows
        # no site, which is the whole point of it.
        self._total_template = (total_template or "").strip() or None
        self._total_pattern = (total_pattern or "").strip() or None
        self._read = reader or (lambda target: _read_text(target, timeout=timeout))
        # The site's own search page, for the sites whose feed cannot page
        # (build 107). When a file supplies one it is what a search reads; the
        # feed stays as the fallback, so a page that changes shape costs the
        # extra results rather than the search.
        self._listing_template = (listing_template or "").strip() or None
        self._listing_item = (listing_item or "").strip() or None
        #: The address of the most recent request, for the debug page to show.
        self.last_url: str | None = None
        #: Which definition file described this site, for the debug page to
        #: name when something it needs is not in there.
        self.definition_file: str | None = None

    # -- SearchProvider ----------------------------------------------------

    def search(self, request: SearchRequest, *, page: int = 1, limit: int = 50) -> list[SearchResult]:
        query = self._build_query(request)
        if self.reads_listing:
            found = self._from_listing(query, page=page, limit=limit)
            if found is not None:
                return found
            # Nothing readable came back. The feed below is the fallback, and
            # it has the page-one ceiling this was meant to get past - so it
            # is a worse answer rather than no answer.
            logger.info("indexer %s: falling back to the feed", self.name)
        if page > 1 and not supports_paging(self._template):
            # Without a {page} token every page would be page one again, and
            # the caller would count the same results several times.
            return []
        target = build_search_url(self._template, query, page=page)
        self.last_url = target
        logger.debug("indexer %s: %s", self.name, target)
        try:
            items = self._fetch(target)
        except FeedError as exc:
            raise SearchError(f"{self.name} could not be searched: {exc}") from exc
        return [self._to_result(item) for item in items][: max(limit, 1)]

    @property
    def reads_listing(self) -> bool:
        """Whether this indexer's file says where its search page is."""
        return bool(self._listing_template and self._listing_item)

    def _from_listing(
        self, query: str, *, page: int, limit: int
    ) -> list[SearchResult] | None:
        """One page of the site's own search, or None if it could not be read.

        None rather than an empty list on purpose: "this page holds nothing"
        and "this page could not be read" are different answers, and only the
        second one is a reason to go and ask the feed instead.
        """
        from backend.app.integrations.search.listing import read_listing

        target = build_search_url(self._listing_template, query, page=page)
        self.last_url = target
        logger.debug("indexer %s: %s", self.name, target)
        try:
            body = self._read(target)
        except Exception as exc:  # noqa: BLE001 - the feed is the fallback
            logger.warning("indexer %s: the search page could not be fetched: %s", self.name, exc)
            return None
        rows = read_listing(body, item_marker=self._listing_item, base_url=target)
        if not rows and page > 1:
            # Page one was readable, so an empty page after it is the end of
            # the listing. Falling back to the feed here would ask a feed that
            # ignores the page number for page one again.
            return []
        if not rows:
            # An empty *last* page is ordinary, and the caller already knows
            # how to stop on one - but it cannot be told apart from a page
            # this could not read, and being wrong the other way would mean
            # silently searching one page for ever.
            return None
        return [
            SearchResult(
                id=row.id,
                title=row.title,
                link=row.link,
                magnet=row.magnet,
                size=row.size,
                seeders=row.seeders,
                leechers=None,
                published_at=row.published_at,
                category=None,
            )
            for row in rows
        ][: max(limit, 1)]

    @property
    def reports_total(self) -> bool:
        """Whether this indexer's file says where its result count is."""
        return bool(self._total_template and self._total_pattern)

    def total(self, request: SearchRequest) -> int | None:
        """How many results the site itself says this search has.

        One request, to the site's own search page rather than to its feed -
        a feed is a list of items and has nowhere to put a total. Returns None
        when the file does not say where the number is, when the page cannot
        be read, or when the words are not on it: a missing count is a blank
        column, never an error, because nothing depends on it.
        """
        if not self.reports_total:
            return None
        target = build_search_url(self._total_template, self._build_query(request))
        self.last_url = target
        logger.debug("indexer %s: %s", self.name, target)
        try:
            body = self._read(target)
        except Exception as exc:  # noqa: BLE001 - a count is never worth failing a search
            logger.debug("indexer %s: no result count (%s)", self.name, exc)
            return None
        found = re.search(self._total_pattern, body or "", re.IGNORECASE)
        if not found:
            return None
        try:
            return int(found.group(1).replace(",", "").replace(".", "").replace(" ", ""))
        except (IndexError, ValueError):
            return None

    def close(self) -> None:
        """Nothing is held open; present so callers can treat providers alike."""

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _build_query(request: SearchRequest) -> str:
        """The search term, from the parts the caller pinned down.

        The site does the matching for this string; AniFlow re-checks every
        result against the release profile afterwards, so a loose query costs
        nothing but a longer answer.
        """
        parts = [request.anime]
        for extra in (request.subgroup, request.resolution, getattr(request, "media_source", None)):
            if extra:
                parts.append(str(extra))
        return " ".join(part for part in parts if part)

    @staticmethod
    def _to_result(item: RssItem) -> SearchResult:
        return SearchResult(
            id=item.guid or item.link or item.title,
            title=item.title,
            link=item.link,
            magnet=item.magnet_url,
            size=item.size,
            seeders=item.seeders,
            leechers=None,
            published_at=item.published_at,
            category=None,
        )


def _read_text(url: str, *, timeout: float) -> str:
    """One page, as text. Used only for a site's own result count."""
    import httpx

    from backend.app.integrations.rss.base import user_agent
    from backend.app.services.outbound import client_kwargs

    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent()},
        **client_kwargs(url),
    )
    response.raise_for_status()
    return response.text


def looks_like_feed_url(url: str) -> bool:
    """A cheap sanity check for the Test action's message, not a gate."""
    lowered = urlsplit(url).query.lower()
    return any(hint.split("=")[0] + "=" in lowered for hint in FEED_HINTS)


__all__ = [
    "FEED_HINTS",
    "FeedIndexerProvider",
    "NoIndexerConfigured",
    "build_search_url",
    "feed_variants",
    "looks_like_feed_url",
    "supports_paging",
]
