"""RSS/Atom feed fetching for user-configured sources.

Generic RSS 2.0 and Atom structures, plus the few namespaced fields torrent
indexers commonly publish (info hash, seeders, size). Those are read when
present and ignored when absent, so no feed is required to provide them.

The info hash matters for durability: a feed item's link points at a
``.torrent`` on the indexer, which stops working once the item is removed. From
the hash a self-contained magnet can be built that keeps working, which is why
a discovered release is stored with one whenever the feed offers it.

The adapter never interprets titles - parsing release names is the parser
service's job.
"""
import base64
import binascii
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

from backend.app.core.version import user_agent
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)


class FeedError(Exception):
    """A feed could not be fetched or parsed."""


@dataclass(frozen=True)
class RssItem:
    guid: str
    title: str
    link: str | None = None
    published_at: datetime | None = None
    #: Namespaced extras, when the feed publishes them.
    info_hash: str | None = None
    seeders: int | None = None
    size: int | None = None
    #: A magnet the feed itself supplied (Tokyo Tosho puts one in its
    #: description), trackers and all. Preferred over one built from the hash.
    magnet: str | None = None

    @property
    def magnet_url(self) -> str | None:
        """A self-contained magnet: the feed's own, else one built from the hash.

        The built one is deliberately trackerless: the hash is enough for the
        client to find peers via DHT, and inventing tracker URLs would be
        guesswork.
        """
        if self.magnet:
            return self.magnet
        if not self.info_hash:
            return None
        return f"magnet:?xt=urn:btih:{self.info_hash}&dn={quote(self.title)}"


class FeedFetcher(Protocol):
    def __call__(self, url: str) -> list[RssItem]: ...


def _text(element: ElementTree.Element) -> str:
    return (element.text or "").strip()


def _first(parent: ElementTree.Element, *paths: str) -> ElementTree.Element | None:
    for path in paths:
        found = parent.find(path)
        if found is not None:
            return found
    return None


_HEX40 = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)

#: Sizes as indexers write them, e.g. "1.4 GiB".
_SIZE_UNITS = {
    "b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}


def _info_hash(element: ElementTree.Element) -> str | None:
    """The torrent's info hash, from any namespace that publishes one."""
    found = _first(element, "{*}infoHash", "{*}info_hash", "{*}infohash")
    if found is None:
        return None
    value = _text(found)
    return value.lower() if _HEX40.match(value) else None


def _int_field(element: ElementTree.Element, *paths: str) -> int | None:
    found = _first(element, *paths)
    if found is None:
        return None
    value = _text(found).replace(",", "")
    try:
        return int(value)
    except ValueError:
        return None


def _size_field(element: ElementTree.Element) -> int | None:
    """Size in bytes, accepting both a raw byte count and "1.4 GiB"."""
    found = _first(element, "{*}size", "{*}contentLength")
    if found is None:
        return None
    return _parse_size(_text(found))


def _parse_size(text: str) -> int | None:
    """A size in bytes from "1234" or "1.4 GiB"."""
    raw = text.replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    match = re.match(r"^([\d.]+)\s*([KMGT]?i?B)$", raw, re.IGNORECASE)
    if not match:
        return None
    multiplier = _SIZE_UNITS.get(match.group(2).lower())
    if multiplier is None:
        return None
    try:
        return int(float(match.group(1)) * multiplier)
    except ValueError:
        return None


_MAGNET = re.compile(r"""magnet:\?xt=urn:btih:([A-Za-z0-9]+)[^"'<>\s]*""")
#: "Size: 5.29GB" (Tokyo Tosho) and "Total Size</strong>: 31.05 GB" (Animetosho),
#: read from the description with its tags removed.
_DESCRIPTION_SIZE = re.compile(r"Size\s*:\s*([\d.,]+\s*[KMGT]?i?B)\b", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]*>")


def _hex_hash(value: str) -> str | None:
    """An info hash as 40 hex characters, from hex or the 32-letter base32 form."""
    if _HEX40.match(value):
        return value.lower()
    if len(value) == 32:
        try:
            return base64.b32decode(value.upper()).hex()
        except (binascii.Error, ValueError):
            return None
    return None


def _from_description(element: ElementTree.Element) -> tuple[str | None, str | None, int | None]:
    """(info hash, magnet, size) a feed wrote into its description text.

    Tokyo Tosho has no hash or size element: its description is a little HTML
    page carrying the magnet link and "Size: 5.29GB". Only used for what the
    namespaced fields did not give, and only the item's own description.
    """
    found = _first(element, "{*}description")
    text = _text(found) if found is not None else ""
    if not text:
        return None, None, None
    magnet_match = _MAGNET.search(text)
    # Animetosho escapes the link's ampersands (&amp;tr=...); a client would
    # take them literally.
    magnet = html.unescape(magnet_match.group(0)) if magnet_match else None
    info_hash = _hex_hash(magnet_match.group(1)) if magnet_match else None
    if magnet and not info_hash:
        magnet = None  # a magnet we cannot read the hash of is not one to trust
    size_match = _DESCRIPTION_SIZE.search(html.unescape(_TAGS.sub(" ", text)))
    size = _parse_size(size_match.group(1)) if size_match else None
    return info_hash, magnet, size


def _parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            logger.debug("unparseable feed date: %s", raw)
            return None
    if parsed is None:
        return None
    return parsed


def parse_feed(xml_bytes: bytes) -> list[RssItem]:
    """Parse RSS 2.0 ``<item>`` and Atom ``<entry>`` elements into items."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise FeedError(f"feed is not valid XML: {exc}") from exc

    items: list[RssItem] = []

    # RSS 2.0 (namespace-agnostic so feeds with custom namespaces still work).
    for element in root.findall(".//{*}item"):
        title_element = _first(element, "{*}title")
        guid_element = _first(element, "{*}guid")
        link_element = _first(element, "{*}link")
        title = _text(title_element) if title_element is not None else ""
        guid = _text(guid_element) if guid_element is not None else ""
        link = _text(link_element) if link_element is not None else None
        if not guid and not link:
            guid = title
        if not title or not guid:
            continue
        published_element = _first(element, "{*}pubDate", "{*}date")
        info_hash = _info_hash(element)
        size = _size_field(element)
        magnet = None
        if info_hash is None or size is None:
            text_hash, magnet, text_size = _from_description(element)
            info_hash = info_hash or text_hash
            size = size if size is not None else text_size
            if info_hash and text_hash and info_hash != text_hash:
                magnet = None  # the two disagree: trust the element, not the prose
        items.append(
            RssItem(
                guid=guid,
                title=title,
                link=link or None,
                published_at=_parse_published(_text(published_element) if published_element is not None else None),
                info_hash=info_hash,
                seeders=_int_field(element, "{*}seeders"),
                size=size,
                magnet=magnet,
            )
        )

    if items:
        return items

    # Atom. ".//" matches entries at any depth, including direct children.
    for element in root.findall(".//{*}entry"):
        title_element = _first(element, "{*}title")
        entry_id = _first(element, "{*}id")
        link_element = _first(element, "{*}link")
        href = link_element.get("href") if link_element is not None else None
        title = _text(title_element) if title_element is not None else ""
        guid = _text(entry_id) if entry_id is not None else ""
        if not guid:
            guid = href or title
        if not title or not guid:
            continue
        updated = _first(element, "{*}updated", "{*}published")
        items.append(
            RssItem(
                guid=guid,
                title=title,
                link=href,
                published_at=_parse_published(_text(updated) if updated is not None else None),
                info_hash=_info_hash(element),
                seeders=_int_field(element, "{*}seeders"),
                size=_size_field(element),
            )
        )
    return items


def fetch_feed(url: str, *, timeout: float = 15.0) -> list[RssItem]:
    """Fetch and parse one feed. Network or parse problems raise FeedError."""
    # A feed or indexer URL is usually on the internet and follows the proxy
    # setting; one on your own network is detected and left direct.
    from backend.app.services.outbound import client_kwargs

    try:
        response = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": user_agent()},
            **client_kwargs(url),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedError(f"feed request failed: {exc}") from exc
    return parse_feed(response.content)
