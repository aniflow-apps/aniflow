"""Reading a tracker's own search page, for the sites whose feed cannot page.

Nyaa's feed serves seventy-five items and ignores the page number - the same
seventy-five, whatever ``&p=`` says. Its search page pages perfectly well:
*"Displaying results 1-75 out of 700 results"*, ten pages of them. Confirmed by
fetching both and comparing them item for item.

That leaves a choice between a feed that is easy to read and cannot reach past
the first page, and a page that can. This module reads the page.

**What is site-specific lives in the definition file**, exactly as the search
template and the result count do: which URL to ask, and what an anchor in a
result row looks like. What is left here is how a listing of torrents is
shaped, which is the same everywhere anyone has built one:

* a **row** is a table row containing a link to an item;
* its **name** is that link's title, or its text when it carries no title;
* its **file** is the ``.torrent`` link in the same row, its **magnet** the
  ``magnet:`` one;
* its **size** is the cell that reads like a size - "1.5 GiB";
* its **date** is the cell carrying a timestamp;
* its **seeders** are the first plain number after that date, because every
  such table puts seeders, leechers and downloads in that order.

Read with the standard library's own HTML parser. No dependency was added for
this, and nothing here knows a class name or a column position - a table with
its columns in a different order still reads correctly, and one this cannot
read at all returns nothing rather than something wrong. The caller treats
that as "the listing did not work" and falls back to the feed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from backend.app.core.logging import for_component

logger = for_component("Indexer")

#: "1.5 GiB", "900.4 MiB", "222 B" - a number and a unit, and nothing else.
SIZE = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)\s*(B|KB|KiB|MB|MiB|GB|GiB|TB|TiB)$", re.IGNORECASE)

#: What each unit is worth. Both spellings, because sites use both and mean
#: the binary one either way.
UNITS: dict[str, int] = {
    "b": 1,
    "kb": 1024, "kib": 1024,
    "mb": 1024 ** 2, "mib": 1024 ** 2,
    "gb": 1024 ** 3, "gib": 1024 ** 3,
    "tb": 1024 ** 4, "tib": 1024 ** 4,
}


@dataclass
class ListingRow:
    """One result, as the site's own page presents it."""

    id: str
    title: str
    link: str | None = None
    magnet: str | None = None
    size: int | None = None
    seeders: int | None = None
    published_at: datetime | None = None


@dataclass
class _Cell:
    """One table cell while it is being read."""

    text: str = ""
    timestamp: int | None = None


@dataclass
class _Row:
    """One table row while it is being read."""

    cells: list[_Cell] = field(default_factory=list)
    #: Every href in the row, and the title of the anchor that carried it.
    links: list[tuple[str, str | None, str]] = field(default_factory=list)


class _TableReader(HTMLParser):
    """Every table row of a page, as cells and links.

    Deliberately forgiving: unclosed cells, nested tables and stray markup are
    all things real pages do, and a reader that refuses them would be a reader
    that works on one site. Rows nothing recognises are dropped later.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self._row: _Row | None = None
        self._cell: _Cell | None = None
        self._anchor: tuple[str, str | None] | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value for name, value in attrs}
        if tag == "tr":
            self._row = _Row()
        elif tag in ("td", "th") and self._row is not None:
            self._cell = _Cell()
            stamp = values.get("data-timestamp")
            if stamp and stamp.isdigit():
                self._cell.timestamp = int(stamp)
            self._row.cells.append(self._cell)
        elif tag == "a" and self._row is not None:
            href = values.get("href")
            if href:
                self._anchor = (href, values.get("title"))
                self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None and self._row is not None:
            href, title = self._anchor
            self._row.links.append((href, title, "".join(self._anchor_text).strip()))
            self._anchor = None
            self._anchor_text = []
        elif tag in ("td", "th"):
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text += data
        if self._anchor is not None:
            self._anchor_text.append(data)


def read_listing(html: str, *, item_marker: str, base_url: str = "") -> list[ListingRow]:
    """Every result on one search page, in the order the site listed it.

    ``item_marker`` is what an anchor's address carries when it points at a
    result - "/view/" for Nyaa - and comes from the indexer's definition file.
    Without it there is no way to tell a result row from the navigation around
    it that does not amount to knowing the site.
    """
    reader = _TableReader()
    try:
        reader.feed(html or "")
        reader.close()
    except Exception as exc:  # noqa: BLE001 - a page that will not parse is not a crash
        logger.debug("listing could not be read: %s", exc)
        return []

    found: list[ListingRow] = []
    seen: set[str] = set()
    for row in reader.rows:
        # An address with a fragment points *inside* the item's page - the
        # comments on it - and carries that link's own words ("1 comment")
        # rather than the release's name. The row's real link is the one
        # without it.
        item = next(
            (
                (href, title, text)
                for href, title, text in row.links
                if item_marker in href and "#" not in href
            ),
            None,
        )
        if item is None:
            continue
        href, title, text = item
        # The title attribute rather than the text: a listing truncates what it
        # shows and keeps the whole name in the attribute, and the whole name
        # is the one thing the parser cannot do without.
        name = (title or text or "").strip()
        if not name:
            continue
        identifier = urljoin(base_url, href.split("#")[0]) if base_url else href.split("#")[0]
        if identifier in seen:
            # A row links its own comments as well; one row is one result.
            continue
        seen.add(identifier)
        found.append(
            ListingRow(
                id=identifier,
                title=name,
                link=_first(row, base_url, lambda address: address.endswith(".torrent")),
                magnet=_first(row, "", lambda address: address.startswith("magnet:")),
                size=_size_of(row),
                seeders=_seeders_of(row),
                published_at=_published_at(row),
            )
        )
    return found


def _first(row: _Row, base_url: str, matches) -> str | None:
    """The first address in this row the test accepts, made absolute."""
    for href, _title, _text in row.links:
        if matches(href):
            return urljoin(base_url, href) if base_url else href
    return None


def _size_of(row: _Row) -> int | None:
    """The cell that reads like a size, in bytes."""
    for cell in row.cells:
        found = SIZE.match(cell.text.strip())
        if not found:
            continue
        try:
            amount = float(found.group(1).replace(",", "."))
        except ValueError:
            continue
        return int(amount * UNITS[found.group(2).lower()])
    return None


def _published_at(row: _Row) -> datetime | None:
    """The cell carrying a timestamp, which is the only one that can be read.

    A printed date would need the site's format and time zone; the timestamp
    beside it needs neither.
    """
    for cell in row.cells:
        if cell.timestamp:
            return datetime.fromtimestamp(cell.timestamp, UTC)
    return None


def _seeders_of(row: _Row) -> int | None:
    """The first plain number after the date.

    Every listing of torrents puts seeders, leechers and downloads in that
    order after the date, so the first of the three is the one worth having.
    Before the date there are numbers that are not counts - the size, the
    episode in a name - which is why the date is where counting starts.
    """
    after_date = False
    for cell in row.cells:
        if cell.timestamp:
            after_date = True
            continue
        if not after_date:
            continue
        text = cell.text.strip().replace(",", "")
        if text.isdigit():
            return int(text)
    return None


__all__ = ["ListingRow", "read_listing"]
