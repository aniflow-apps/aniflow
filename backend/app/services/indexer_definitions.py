"""How to search a site, read from the indexer's own file.

An indexer is a file in ``<CONFIG_DIR>/indexers``. Beside the name and address
that make it an indexer, the file says how that site wants to be asked a
question - what its search parameter is called, which category to restrict to,
how to get a feed rather than a page - because AniFlow cannot know that from
the address alone.

AniFlow ships no indexers. The site-specific part is what keeps the project
itself free of any particular tracker, and a file means someone can add a site
without waiting for a release.

A file is small on purpose::

    name: Example
    url: https://example.tld
    search_url: "{base}/?page=rss&c=1_2&q={query}&s=seeders&o=desc&p={page}"
    rss_url: "{base}/?page=rss&c=1_2"
    enabled: true
    default: true

``{base}`` is the site address, ``{query}`` the search term, ``{page}`` an
optional page number. ``rss_url`` is optional; with it, the same indexer also
stands up a feed source for the site.

``search_url_native`` is optional and solves a real problem: a site that
files releases by the language they were translated into puts the ones named
in Japanese somewhere else entirely. On Nyaa the ordinary template restricts
to ``c=1_2`` (English-translated), so searching with a Japanese title finds
nothing at all - not because the release is absent but because it is in
``c=1_3``. The second template says where those live. Which one to use is the
caller's decision, because only the caller knows which spelling it is about
to search with.

An address written *with* a query string is never overridden: those are the
user's own filters and they are the more specific instruction.
"""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from backend.app.core.logging import for_component
from backend.app.services.config_store import (
    INDEXERS,
    as_bool,
    as_list,
    as_text,
    read_folder,
)

logger = for_component("Indexer")


@dataclass(frozen=True)
class IndexerDefinition:
    """One site's search mechanics, as read from its file."""

    name: str
    sites: tuple[str, ...]
    search_url: str
    #: Where the site keeps releases named in its own language, when it keeps
    #: them apart at all. See the module docstring: without this, a search
    #: with a Japanese title looks for it in the English-translated category
    #: and reports "nothing found" for something that is there.
    search_url_native: str | None = None
    #: The site's own feed, when the file supplies one. Optional: a site can be
    #: searchable without publishing a feed, and the UI says so rather than
    #: inventing a URL.
    rss_url: str | None = None
    #: The site's own search page, which is not the same thing as its feed
    #: (build 107). Nyaa's feed serves seventy-five items and ignores the page
    #: number; its search page pages properly - ten pages of seventy-five for
    #: the search that made this necessary. With this, a search reads the
    #: pages; without it, the feed, exactly as before.
    #:
    #: ``listing_item`` is what an address in a result row carries - "/view/"
    #: for Nyaa. Everything else about reading the page is generic and lives
    #: in integrations/search/listing.py.
    listing_url: str | None = None
    listing_url_native: str | None = None
    listing_item: str | None = None
    #: Where the site states how many results a search has, and the words to
    #: read it from (build 105). A feed cannot carry that number - it is a
    #: list of items - so this points at the site's own search page and says
    #: what its counting line looks like. Both optional, and a file with
    #: neither simply has no count to show.
    total_url: str | None = None
    total_url_native: str | None = None
    total_pattern: str | None = None
    #: Everything on this site is adult (Sukebei). An anime added from it is
    #: marked Adult, so Hide adult and the adult libraries apply without the
    #: user ticking each show.
    adult: bool = False
    source_file: str = ""

    def resolve(self, base_url: str, *, native: bool = False) -> str:
        """The search template for a given site address.

        ``native`` asks for the other category. Falls back to the ordinary
        template when the file has no second one: a site that does not sort by
        language has nothing to fall back *to*, and a search is better than a
        refusal.
        """
        template = (self.search_url_native if native else None) or self.search_url
        return template.replace("{base}", base_url.rstrip("/"))

    @property
    def sorts_by_language(self) -> bool:
        """Whether this site keeps translated and untranslated releases apart."""
        return bool(self.search_url_native)

    def resolve_listing(self, base_url: str, *, native: bool = False) -> str | None:
        """The search page to read, if this file says the site has one."""
        template = (self.listing_url_native if native else None) or self.listing_url
        if not template:
            return None
        return template.replace("{base}", base_url.rstrip("/"))

    def resolve_total(self, base_url: str, *, native: bool = False) -> str | None:
        """The page to read this site's own result count from, if it has one."""
        template = (self.total_url_native if native else None) or self.total_url
        if not template:
            return None
        return template.replace("{base}", base_url.rstrip("/"))

    def resolve_rss(self, base_url: str) -> str | None:
        """The feed URL for a given site address, if this file has one."""
        if not self.rss_url:
            return None
        return self.rss_url.replace("{base}", base_url.rstrip("/"))


def definitions_dir() -> Path:
    """Where indexer files live: ``<CONFIG_DIR>/indexers``."""
    return INDEXERS.path


def site_key(url: str) -> str:
    """A site address reduced to what identifies it.

    Scheme and host only, lowercased, ``www.`` dropped: a file should match
    whether the address was written ``https://Example.tld/`` or
    ``https://www.example.tld``.
    """
    parts = urlsplit(url.strip())
    host = (parts.netloc or parts.path).lower().strip("/")
    if host.startswith("www."):
        host = host[4:]
    return host


def has_query(url: str) -> bool:
    """Whether the address already says how to search.

    An address carrying a query string is the user's own instruction - their
    categories, their filters - and outranks any template.
    """
    return "?" in url


def _parse(entry: dict) -> IndexerDefinition | None:
    """One file's search mechanics, or None when it has none to give.

    Not an error: a file may legitimately be an address with no template, in
    which case AniFlow falls back to its generic URL rules. Only a file that
    tries to say something and gets it wrong is worth a warning.
    """
    data = entry["data"]
    filename = entry["path"].name
    search_url = as_text(data.get("search_url"))
    if not search_url:
        return None
    if "{query}" not in search_url:
        # Without it the term has nowhere to go and every search returns the
        # site's front page - the failure this file exists to prevent.
        logger.warning("indexer %s: search_url has no {query}", filename)
        return None

    # The file's own address is the site it describes; `sites` stays supported
    # for one file covering several mirrors.
    addresses = as_list(data.get("sites")) or [as_text(data.get("url")) or ""]
    keys = tuple(sorted({site_key(site) for site in addresses if site}))
    if not keys:
        logger.warning("indexer %s: no usable site address", filename)
        return None

    native = as_text(data.get("search_url_native"))
    if native and "{query}" not in native:
        logger.warning("indexer %s: search_url_native has no {query}", filename)
        native = None

    listing_url = as_text(data.get("listing_url"))
    if listing_url and "{query}" not in listing_url:
        logger.warning("indexer %s: listing_url has no {query}", filename)
        listing_url = None
    listing_native = as_text(data.get("listing_url_native"))
    if listing_native and "{query}" not in listing_native:
        logger.warning("indexer %s: listing_url_native has no {query}", filename)
        listing_native = None
    listing_item = as_text(data.get("listing_item"))
    if listing_url and not listing_item:
        # Without it there is no way to tell a result row from the navigation
        # around it that does not amount to knowing the site.
        logger.warning("indexer %s: listing_url without listing_item", filename)
        listing_url = None

    total_url = as_text(data.get("total_url"))
    if total_url and "{query}" not in total_url:
        # The count has to be the count *for this search*. Without the term
        # it would be the site's total library, which is a different and
        # useless number.
        logger.warning("indexer %s: total_url has no {query}", filename)
        total_url = None
    total_native = as_text(data.get("total_url_native"))
    if total_native and "{query}" not in total_native:
        logger.warning("indexer %s: total_url_native has no {query}", filename)
        total_native = None
    total_pattern = as_text(data.get("total_pattern"))
    if total_url and not total_pattern:
        logger.warning("indexer %s: total_url without total_pattern", filename)
        total_url = None

    return IndexerDefinition(
        name=as_text(data.get("name")) or entry["slug"],
        sites=keys,
        search_url=search_url,
        search_url_native=native,
        rss_url=as_text(data.get("rss_url")),
        listing_url=listing_url,
        listing_url_native=listing_native,
        listing_item=listing_item,
        total_url=total_url,
        total_url_native=total_native,
        total_pattern=total_pattern,
        adult=as_bool(data.get("adult"), False),
        source_file=filename,
    )


def load_definitions() -> list[IndexerDefinition]:
    """Every file that says how to search its site.

    Cheap to call: the folder is re-read only when a file in it changed, which
    is what lets an edit take effect without a restart.
    """
    found: list[IndexerDefinition] = []
    for entry in read_folder(INDEXERS):
        definition = _parse(entry)
        if definition is not None:
            found.append(definition)
    return found


def find_for_url(url: str) -> IndexerDefinition | None:
    """The file that governs this address, if any.

    None for an address that already carries a query string - the user said
    how to search, and that wins - and None when no file claims the site,
    which leaves the generic URL rules in charge.
    """
    if not url or has_query(url):
        return None
    key = site_key(url)
    if not key:
        return None
    for definition in load_definitions():
        if key in definition.sites:
            return definition
    return None


def resolve_search_template(
    url: str, *, native: bool = False
) -> tuple[str, IndexerDefinition | None]:
    """The URL template to search with, and the file it came from.

    Falls back to the address itself, which the generic rules then work out
    how to turn into a query. ``native`` asks for the template covering names
    in the site's own language - see IndexerDefinition.resolve.
    """
    definition = find_for_url(url)
    if definition is None:
        return url, None
    return definition.resolve(url, native=native), definition


__all__ = [
    "IndexerDefinition",
    "definitions_dir",
    "find_for_url",
    "has_query",
    "load_definitions",
    "resolve_search_template",
    "site_key",
]
