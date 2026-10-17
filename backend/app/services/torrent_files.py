"""Read a .torrent file: its info hash and the files inside it (build 213).

The reason it exists (docs/batch-episodes-plan.md): a batch torrent holds a whole season, and an anime that
is missing one episode needs only that file out of it. qBittorrent can download chosen files, but to choose
them the file list has to be known, and a magnet has none until the client has fetched its metadata - which
a stopped magnet does not do on older versions. The .torrent file carries the list inside it, so reading it
here makes the choice possible before anything is added, on any client version.

Standard library only. Bencode is small enough to read by hand, and the one thing that must be exact - the
info hash, SHA-1 of the *original bytes* of the ``info`` dictionary - is taken from the byte span the reader
saw, never from a re-encoding, because a re-encoded dictionary can differ from the one that was hashed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: A torrent file is small; a megabyte is already a very large one. The cap stops a wrong URL (a web page,
#: an error document) being read as a torrent.
MAX_TORRENT_BYTES = 8 * 1024 * 1024


class TorrentFormatError(ValueError):
    """The bytes are not a torrent AniFlow can read."""


@dataclass(frozen=True)
class TorrentFile:
    """One file inside a torrent, as qBittorrent will list it."""

    #: Its position in the torrent: what the client's file-priority call takes.
    index: int
    #: The path inside the torrent, starting with the torrent's own folder name for a multi-file torrent -
    #: the same string the client shows.
    path: str
    size: int

    @property
    def name(self) -> str:
        """Just the file name."""
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class TorrentMeta:
    info_hash: str
    name: str
    files: tuple[TorrentFile, ...]

    @property
    def is_batch(self) -> bool:
        return len(self.files) > 1


class _Reader:
    """A bencode reader that remembers where the ``info`` dictionary started and ended."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0
        self.info_span: tuple[int, int] | None = None

    def value(self, depth: int = 0):
        if depth > 32:
            raise TorrentFormatError("nested too deeply")
        if self.position >= len(self.data):
            raise TorrentFormatError("ended early")
        head = self.data[self.position:self.position + 1]
        if head == b"i":
            end = self.data.index(b"e", self.position)
            text = self.data[self.position + 1:end]
            self.position = end + 1
            try:
                return int(text)
            except ValueError as exc:
                raise TorrentFormatError("a bad integer") from exc
        if head == b"l":
            self.position += 1
            items = []
            while self.data[self.position:self.position + 1] != b"e":
                items.append(self.value(depth + 1))
            self.position += 1
            return items
        if head == b"d":
            self.position += 1
            result: dict[bytes, object] = {}
            while self.data[self.position:self.position + 1] != b"e":
                key = self.value(depth + 1)
                if not isinstance(key, bytes):
                    raise TorrentFormatError("a dictionary key that is not text")
                start = self.position
                result[key] = self.value(depth + 1)
                if depth == 0 and key == b"info":
                    self.info_span = (start, self.position)
            self.position += 1
            return result
        if head.isdigit():
            colon = self.data.index(b":", self.position)
            length = int(self.data[self.position:colon])
            start = colon + 1
            if start + length > len(self.data):
                raise TorrentFormatError("a string that runs past the end")
            self.position = start + length
            return self.data[start:start + length]
        raise TorrentFormatError("not bencode")


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def parse_torrent(data: bytes) -> TorrentMeta:
    """The hash, name and files of a .torrent file. Raises ``TorrentFormatError`` for anything else."""
    if not data or len(data) > MAX_TORRENT_BYTES:
        raise TorrentFormatError("empty or far too large")
    reader = _Reader(data)
    try:
        top = reader.value()
    except (ValueError, IndexError) as exc:
        if isinstance(exc, TorrentFormatError):
            raise
        raise TorrentFormatError("not bencode") from exc
    if not isinstance(top, dict) or b"info" not in top or reader.info_span is None:
        raise TorrentFormatError("no info dictionary")
    info = top[b"info"]
    if not isinstance(info, dict):
        raise TorrentFormatError("the info is not a dictionary")

    start, end = reader.info_span
    info_hash = hashlib.sha1(data[start:end]).hexdigest()
    name = _text(info.get(b"name.utf-8") or info.get(b"name") or b"")
    if not name:
        raise TorrentFormatError("the torrent has no name")

    files: list[TorrentFile] = []
    listed = info.get(b"files")
    if isinstance(listed, list):
        for index, entry in enumerate(listed):
            if not isinstance(entry, dict):
                raise TorrentFormatError("a file entry that is not a dictionary")
            parts = entry.get(b"path.utf-8") or entry.get(b"path")
            length = entry.get(b"length")
            if not isinstance(parts, list) or not isinstance(length, int):
                raise TorrentFormatError("a file entry with no path or length")
            path = "/".join([name, *(_text(part) for part in parts)])
            files.append(TorrentFile(index=index, path=path, size=length))
    else:
        length = info.get(b"length")
        if not isinstance(length, int):
            raise TorrentFormatError("no files and no length")
        files.append(TorrentFile(index=0, path=name, size=length))
    return TorrentMeta(info_hash=info_hash, name=name, files=tuple(files))
