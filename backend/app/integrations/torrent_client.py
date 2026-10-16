"""Which torrent client AniFlow talks to, and what it asks of one (build 227).

Until now the only client was qBittorrent and its class was named all over the
code. A second client (Deluge) needed three things that belong to neither: one
error type the callers can catch whichever client they have, a statement of the
operations AniFlow actually uses, and one place that turns the setting into a
client.

**The vocabulary stays qBittorrent's.** Every other part of AniFlow reads a
torrent's state as ``uploading`` / ``stalledUP`` / ``pausedDL`` ... and a file's
priority as 0 (skip) or 1, and the Downloads page translates exactly those
words. An adapter for another client therefore speaks that vocabulary - Deluge
reports ``Seeding`` and the adapter says ``uploading`` - rather than every
caller learning a second one.
"""
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # the adapters import this module for the error type, so not at runtime
    from backend.app.integrations.qbittorrent.base import TorrentFileInfo, TorrentInfo

#: The values of the ``torrent_client`` setting.
CLIENTS = ("qbittorrent", "deluge", "transmission")

#: How each is written for people.
CLIENT_NAMES = {"qbittorrent": "qBittorrent", "deluge": "Deluge", "transmission": "Transmission"}


class TorrentClientError(Exception):
    """A torrent client could not be used as configured (any client)."""


class TorrentClient(Protocol):
    """What AniFlow asks of a torrent client - and nothing more.

    The qBittorrent adapter is the reference. An operation that reports
    success is one the adapter has read back (a stop, a delete, a file
    priority): an accepted request is not proof, and a reverse proxy in front
    of a client can answer 200 to anything.
    """

    def close(self) -> None: ...

    def version(self) -> str: ...

    def default_save_path(self) -> str: ...

    def add_torrents(self, urls: list[str], *, category: str, tag: str | None = None) -> bool: ...

    def add_torrent(self, torrent_url: str, *, category: str, tag: str | None = None) -> bool: ...

    def add_torrent_file(
        self, data: bytes, filename: str, *, category: str, tag: str | None = None, stopped: bool = True
    ) -> bool: ...

    def torrent_files(self, info_hash: str) -> "list[TorrentFileInfo]": ...

    def set_file_priority(self, info_hash: str, indexes: Sequence[int], priority: int) -> None: ...

    def start_torrent(self, info_hash: str) -> None: ...

    def pause_torrent(self, info_hash: str) -> None: ...

    def list_torrents(self) -> "list[TorrentInfo]": ...

    def find_torrents(self, *, category: str, tag: str | None = None) -> "list[TorrentInfo]": ...

    def find_by_hash(self, info_hash: str) -> "TorrentInfo | None": ...

    def torrent_state(self, info_hash: str) -> str | None: ...

    def list_tags(self) -> list[str]: ...

    def delete_tags(self, tags: Sequence[str]) -> None: ...

    def delete_torrent(self, info_hash: str, *, delete_files: bool = False) -> None: ...


def client_name(kind: str | None) -> str:
    """The client's name as people write it; qBittorrent for anything unknown."""
    return CLIENT_NAMES.get((kind or "qbittorrent").lower(), "qBittorrent")


def client_for(kind: str | None, url: str, username: str, password: str, *, timeout: float = 15.0) -> TorrentClient:
    """The adapter for the configured client.

    One address, one username, one password for every client - the settings
    keep their ``qb_`` names so nobody's configuration file changes. Deluge's
    Web UI has a password and no username, so it ignores the username;
    Transmission's username and password are both optional.
    """
    chosen = (kind or "qbittorrent").lower()
    if chosen == "deluge":
        from backend.app.integrations.deluge.base import DelugeClient

        return DelugeClient(url, username, password, timeout=timeout)
    if chosen == "transmission":
        from backend.app.integrations.transmission.base import TransmissionClient

        return TransmissionClient(url, username, password, timeout=timeout)
    from backend.app.integrations.qbittorrent.base import QBittorrentClient

    return QBittorrentClient(url, username, password, timeout=timeout)
