"""Deluge Web UI adapter (build 227).

Deluge is reached through its **Web UI** (``deluge-web``, port 8112): a
JSON-RPC endpoint at ``/json`` that also exposes every Core method. The shape
below is from Deluge's own documentation - ``POST /json`` with
``{"method", "params", "id"}``, ``auth.login`` with the Web UI password, a
``_session_id`` cookie, and ``web.connected`` / ``web.get_hosts`` /
``web.connect`` to attach the Web UI to the daemon - and the Core calls
(``add_torrent_magnet(uri, options)``, ``add_torrent_file(name, base64,
options)``, ``get_torrents_status(filter, keys)``, ``pause_torrents`` /
``resume_torrents`` / ``remove_torrent(id, remove_data)``,
``set_torrent_options(ids, {"file_priorities": [...]})``).

What it does **not** know from the docs, and takes from Deluge's source as
the author remembers it, is marked below: the status key names, that
``progress`` is a percentage, and the state words. Those are the first things
to check if a real Deluge disagrees.

**Speaking qBittorrent.** The rest of AniFlow reads qBittorrent's vocabulary
(see ``integrations/torrent_client.py``), so Deluge's ``Seeding`` becomes
``uploading``, its progress 0-100 becomes 0-1, and a file priority of 4
("normal") becomes 1.

**Tags.** Deluge has no tags, and its Label plugin allows one label per
torrent. The label is AniFlow's *category*. AniFlow's per-download tag is
kept in memory instead (``_TAGS``): it is only needed to find a torrent again
right after adding it, when the hash is not yet known, and an adapter that
computes the hash itself - from the magnet, or from the ``.torrent`` file it
fetched - mostly knows it already. A tag lost to a restart costs one retry.
"""
import base64
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Sequence

import httpx

from backend.app.integrations.qbittorrent.base import TorrentFileInfo, TorrentInfo, is_stopped
from backend.app.integrations.torrent_client import TorrentClientError

logger = logging.getLogger(__name__)


class DelugeError(TorrentClientError):
    """The Deluge Web UI could not be used as configured."""


class _NotAuthenticated(Exception):
    """The session ended; one new login is worth trying."""


#: Every torrent AniFlow files under its label carries this one tag, so the
#: sweep that recognises AniFlow's torrents by the ``aniflow-`` prefix finds
#: them (and then matches each to its download by hash).
PSEUDO_TAG = "aniflow-deluge"

#: What is asked of every torrent. ``label`` only exists while the Label
#: plugin is enabled; a key Deluge does not know is left out of the answer.
_KEYS = [
    "name", "state", "progress", "save_path", "total_size", "download_payload_rate",
    "upload_payload_rate", "eta", "num_seeds", "num_peers", "ratio", "label",
]
_FILE_KEYS = ["files", "file_progress", "file_priorities"]

_HASH = re.compile(r"^[0-9a-f]{40}$")
_MAGNET_HASH = re.compile(r"xt=urn:btih:([0-9a-zA-Z]+)", re.IGNORECASE)

#: (client address, tag) -> info hash, newest last. Bounded: a tag is only
#: wanted for the minutes after an add.
_TAGS: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_TAGS_KEPT = 1000


def _remember(base_url: str, tag: str | None, info_hash: str | None) -> None:
    if not tag or not info_hash:
        return
    _TAGS[(base_url, tag)] = info_hash
    _TAGS.move_to_end((base_url, tag))
    while len(_TAGS) > _TAGS_KEPT:
        _TAGS.popitem(last=False)


def _hash_of_magnet(uri: str) -> str | None:
    """The info hash in a magnet link, as 40 lowercase hex characters."""
    found = _MAGNET_HASH.search(uri)
    if not found:
        return None
    value = found.group(1)
    if _HASH.match(value.lower()):
        return value.lower()
    if len(value) == 32:  # base32 form
        try:
            return base64.b32decode(value.upper()).hex()
        except (ValueError, TypeError):
            return None
    return None


def _label_id(category: str) -> str:
    """A category as the Label plugin accepts it: lowercase, a-z 0-9 _ - only."""
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", (category or "").lower()).strip("-")
    return cleaned[:32] or "aniflow"


def qb_state(state: str, progress: float, *, upload_rate: float = 0.0) -> str:
    """Deluge's state word in qBittorrent's.

    ``progress`` is 0-1. Deluge's states (from its source): Allocating,
    Checking, Downloading, Seeding, Paused, Error, Queued, Moving.
    """
    finished = progress >= 0.999
    word = (state or "").lower()
    if word == "downloading":
        return "downloading"
    if word == "seeding":
        return "uploading" if upload_rate > 0 else "stalledUP"
    if word == "paused":
        return "pausedUP" if finished else "pausedDL"
    if word == "queued":
        return "queuedUP" if finished else "queuedDL"
    if word == "checking":
        return "checkingUP" if finished else "checkingDL"
    if word == "allocating":
        return "allocating"
    if word == "moving":
        return "moving"
    if word == "error":
        return "error"
    return "unknown"


def _to_deluge_priority(priority: int) -> int:
    """AniFlow's 0 / 1 / 6 / 7 as Deluge's 0 (skip) / 4 (normal) / 7 (high)."""
    if priority <= 0:
        return 0
    return 7 if priority >= 6 else 4


def _from_deluge_priority(priority: int) -> int:
    """Back again, so what was written compares equal to what was meant."""
    if priority <= 0:
        return 0
    if priority <= 4:
        return 1
    return 7 if priority >= 7 else 6


class DelugeClient:
    """A small client for one Deluge Web UI."""

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        # The Web UI has a password and no username; the parameter is only here
        # so every client is built the same way.
        self._base_url = base_url.rstrip("/")
        self._password = password
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._sleep = sleep
        self._logged_in = False
        self._next_id = 0
        self._labels_for: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DelugeClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- transport -----------------------------------------------------------

    def _call(self, method: str, params: list) -> object:
        self._next_id += 1
        try:
            response = self._client.post("/json", json={"method": method, "params": params, "id": self._next_id})
        except httpx.ConnectError as exc:
            raise DelugeError(
                f"Could not connect to {self._base_url} - host unreachable, port closed, or wrong protocol (http vs https)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise DelugeError(f"Connection to {self._base_url} timed out - wrong host/port, or no answer in time") from exc
        except httpx.HTTPError as exc:
            raise DelugeError(f"Could not reach Deluge at {self._base_url}: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise DelugeError(
                f"Deluge answered HTTP {response.status_code} for {method}"
                + (f": {response.text[:120]}" if response.text else "")
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise DelugeError(
                f"{self._base_url}/json did not answer JSON-RPC - is the address the Deluge Web UI "
                "(usually http://host:8112), not the daemon port?"
            ) from exc
        if not isinstance(body, dict):
            raise DelugeError(f"Deluge answered {type(body).__name__} to {method}, not a JSON-RPC object")
        error = body.get("error")
        if error:
            message = str(error.get("message") if isinstance(error, dict) else error)
            if "not authenticated" in message.lower():
                raise _NotAuthenticated(message)
            raise DelugeError(f"Deluge: {message[:200]}")
        return body.get("result")

    def _login(self) -> None:
        ok = self._call("auth.login", [self._password])
        if ok is not True:
            raise DelugeError("Deluge rejected the Web UI password (the default is 'deluge')")
        if self._call("web.connected", []):
            return
        hosts = self._call("web.get_hosts", [])
        if not isinstance(hosts, list) or not hosts:
            raise DelugeError(
                "The Deluge Web UI is not connected to a daemon and has none to connect to - "
                "open its Connection Manager and add the daemon"
            )
        online = [host for host in hosts if isinstance(host, list) and len(host) > 3 and str(host[3]).lower() == "online"]
        chosen = (online or hosts)[0]
        self._call("web.connect", [chosen[0]])

    def _rpc(self, method: str, *params: object) -> object:
        if not self._logged_in:
            self._login()
            self._logged_in = True
        try:
            return self._call(method, list(params))
        except _NotAuthenticated:
            self._logged_in = False
            self._login()
            self._logged_in = True
            try:
                return self._call(method, list(params))
            except _NotAuthenticated as exc:
                raise DelugeError("Deluge keeps ending the session - check the Web UI password") from exc

    # -- labels ---------------------------------------------------------------

    def _prepare_label(self, category: str, *, create: bool) -> str:
        """The label for this category, with the plugin that provides it switched on.

        Without a label AniFlow could not tell its own torrents from anyone
        else's in the client, and the sweep that stops finished torrents must
        never touch a torrent that is not AniFlow's - so a Deluge without the
        Label plugin is refused, not guessed at.
        """
        label = _label_id(category)
        if self._labels_for == label:
            return label
        enabled = self._rpc("core.get_enabled_plugins")
        if not isinstance(enabled, list) or "Label" not in enabled:
            self._rpc("core.enable_plugin", "Label")
            for _ in range(10):
                self._sleep(0.5)
                enabled = self._rpc("core.get_enabled_plugins")
                if isinstance(enabled, list) and "Label" in enabled:
                    break
            else:
                raise DelugeError(
                    "Deluge's Label plugin is not enabled and could not be enabled from here. AniFlow files its "
                    "torrents under a label: turn on Label in Deluge (Preferences, Plugins) and try again."
                )
        if create:
            existing = self._rpc("label.get_labels")
            if not isinstance(existing, list) or label not in existing:
                self._rpc("label.add", label)
        self._labels_for = label
        return label

    # -- reading ----------------------------------------------------------------

    def _status(self, ids: Sequence[str] | None, keys: list[str]) -> dict[str, dict]:
        filters: dict = {"id": [item.lower() for item in ids]} if ids is not None else {}
        result = self._rpc("core.get_torrents_status", filters, keys)
        return result if isinstance(result, dict) else {}

    def _info(self, info_hash: str, entry: dict, *, ours: bool) -> TorrentInfo:
        progress = float(entry.get("progress") or 0.0) / 100.0
        state = qb_state(str(entry.get("state", "")), progress, upload_rate=float(entry.get("upload_payload_rate") or 0))
        name = str(entry.get("name") or "")
        save_path = str(entry.get("save_path") or "")
        tags: tuple[str, ...] = ()
        if ours:
            known = [tag for (base, tag), found in _TAGS.items() if base == self._base_url and found == info_hash]
            tags = (PSEUDO_TAG, *known)
        eta = entry.get("eta")
        return TorrentInfo(
            info_hash=info_hash,
            name=name,
            state=state,
            progress=progress,
            category=str(entry.get("label") or ""),
            content_path=f"{save_path.rstrip('/')}/{name}" if save_path and name else None,
            save_path=save_path or None,
            size=entry.get("total_size"),
            dlspeed=entry.get("download_payload_rate"),
            upspeed=entry.get("upload_payload_rate"),
            eta=int(eta) if isinstance(eta, (int, float)) and eta >= 0 else None,
            num_seeds=entry.get("num_seeds"),
            num_leechs=entry.get("num_peers"),
            ratio=float(entry["ratio"]) if entry.get("ratio") is not None else None,
            tags=tags,
        )

    def check_ready(self, category: str = "aniflow") -> None:
        """Raise unless Deluge can file torrents under a label - the Label plugin is on, or could be switched on.

        For the checklist (build 232): a Deluge that answers the version call but has no Label plugin would add
        torrents and then refuse to list them, which is a worse way to find out.
        """
        self._prepare_label(category, create=False)

    def version(self) -> str:
        try:
            result = self._rpc("daemon.get_version")
        except DelugeError:
            result = self._rpc("daemon.info")
        return str(result)

    def default_save_path(self) -> str:
        return str(self._rpc("core.get_config_value", "download_location") or "")

    def list_torrents(self) -> list[TorrentInfo]:
        label = None
        try:
            label = self._prepare_label("aniflow", create=False)
        except DelugeError:
            pass  # a listing without labels still lists
        return [
            self._info(info_hash, entry, ours=bool(label) and entry.get("label") == label)
            for info_hash, entry in self._status(None, _KEYS).items()
        ]

    def find_torrents(self, *, category: str, tag: str | None = None) -> list[TorrentInfo]:
        label = self._prepare_label(category, create=False)
        found = [
            self._info(info_hash, entry, ours=True)
            for info_hash, entry in self._status(None, _KEYS).items()
            if entry.get("label") == label
        ]
        if tag:
            wanted = _TAGS.get((self._base_url, tag))
            return [torrent for torrent in found if wanted and torrent.info_hash == wanted]
        return found

    def find_by_hash(self, info_hash: str) -> TorrentInfo | None:
        entries = self._status([info_hash], _KEYS)
        entry = entries.get(info_hash.lower())
        if entry is None:
            return None
        return self._info(info_hash.lower(), entry, ours=False)

    def torrent_state(self, info_hash: str) -> str | None:
        torrent = self.find_by_hash(info_hash)
        return torrent.state if torrent else None

    def torrent_files(self, info_hash: str) -> list[TorrentFileInfo]:
        entry = self._status([info_hash], _FILE_KEYS).get(info_hash.lower())
        if not entry:
            return []
        files = entry.get("files") or []
        progress = entry.get("file_progress") or []
        priorities = entry.get("file_priorities") or []
        result: list[TorrentFileInfo] = []
        for position, item in enumerate(files):
            index = int(item.get("index", position))
            result.append(
                TorrentFileInfo(
                    index=index,
                    name=str(item.get("path", "")),
                    size=int(item.get("size", 0) or 0),
                    priority=_from_deluge_priority(int(priorities[index])) if index < len(priorities) else 1,
                    progress=float(progress[index]) if index < len(progress) else 0.0,
                )
            )
        return result

    # -- adding ---------------------------------------------------------------

    def _fetch_torrent(self, url: str) -> bytes:
        """The .torrent file at an address, fetched here: Deluge's own fetch is not reachable from this API."""
        from backend.app.core.version import user_agent
        from backend.app.services.outbound import client_kwargs

        try:
            response = httpx.get(
                url,
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": user_agent()},
                **client_kwargs(url),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DelugeError(f"Could not fetch the torrent file {url[:100]}: {type(exc).__name__}: {exc}") from exc
        return response.content

    @staticmethod
    def _already_there(exc: DelugeError) -> bool:
        text = str(exc).lower()
        return "already" in text or "duplicate" in text

    def _add_file_bytes(self, data: bytes, filename: str, label: str, tag: str | None, *, paused: bool) -> str | None:
        from backend.app.services.torrent_files import TorrentFormatError, parse_torrent

        try:
            info_hash = parse_torrent(data).info_hash.lower()
        except TorrentFormatError as exc:
            raise DelugeError(f"{filename[:80]} is not a torrent file: {exc}") from exc
        try:
            self._rpc("core.add_torrent_file", filename, base64.b64encode(data).decode("ascii"), {"add_paused": paused})
        except DelugeError as exc:
            if not self._already_there(exc):
                raise
        self._rpc("label.set_torrent", info_hash, label)
        _remember(self._base_url, tag, info_hash)
        return info_hash

    def _add_one(self, url: str, label: str, tag: str | None) -> bool:
        if url.startswith("magnet:"):
            info_hash = _hash_of_magnet(url)
            try:
                self._rpc("core.add_torrent_magnet", url, {"add_paused": False})
            except DelugeError as exc:
                if not self._already_there(exc):
                    raise
            if info_hash:
                self._rpc("label.set_torrent", info_hash, label)
                _remember(self._base_url, tag, info_hash)
            return True
        data = self._fetch_torrent(url)
        return self._add_file_bytes(data, "aniflow.torrent", label, tag, paused=False) is not None

    def add_torrents(self, urls: list[str], *, category: str, tag: str | None = None) -> bool:
        wanted = [url for url in urls if url]
        if not wanted:
            return True
        label = self._prepare_label(category, create=True)
        logger.info("adding %s torrent(s): label=%r", len(wanted), label)
        accepted = True
        for url in wanted:
            try:
                accepted = self._add_one(url, label, tag) and accepted
            except DelugeError as exc:
                logger.warning("Deluge rejected a torrent: %s", exc)
                accepted = False
        return accepted

    def add_torrent(self, torrent_url: str, *, category: str, tag: str | None = None) -> bool:
        label = self._prepare_label(category, create=True)
        logger.info("adding 1 torrent: label=%r", label)
        return self._add_one(torrent_url, label, tag)

    def add_torrent_file(
        self, data: bytes, filename: str, *, category: str, tag: str | None = None, stopped: bool = True
    ) -> bool:
        label = self._prepare_label(category, create=True)
        logger.info("adding 1 torrent file: label=%r stopped=%s", label, stopped)
        return self._add_file_bytes(data, filename, label, tag, paused=stopped) is not None

    # -- changing ------------------------------------------------------------------

    def set_file_priority(self, info_hash: str, indexes: Sequence[int], priority: int) -> None:
        """Give these files a priority (0 = skip) and read it back, as the qBittorrent adapter does."""
        wanted = sorted({int(index) for index in indexes})
        if not wanted:
            return
        entry = self._status([info_hash], ["file_priorities"]).get(info_hash.lower()) or {}
        current = [int(value) for value in (entry.get("file_priorities") or [])]
        if not current:
            raise DelugeError("Deluge does not report the files of this torrent yet")
        target = _to_deluge_priority(priority)
        for index in wanted:
            if index < len(current):
                current[index] = target
        self._rpc("core.set_torrent_options", [info_hash.lower()], {"file_priorities": current})
        now = {file.index: file.priority for file in self.torrent_files(info_hash)}
        wrong = [index for index in wanted if now.get(index) != _from_deluge_priority(target)]
        if wrong:
            raise DelugeError(
                f"Deluge did not set the priority of {len(wrong)} file(s) of the torrent to {priority}"
                " (a reverse proxy in front of the Web UI can answer for it without passing the request on)."
            )

    def _first_working(self, calls: Sequence[tuple[str, tuple]]) -> None:
        """Try the plural Core call, then the singular one a different Deluge version has."""
        last: DelugeError | None = None
        for method, args in calls:
            try:
                self._rpc(method, *args)
                return
            except DelugeError as exc:
                last = exc
        if last is not None:
            raise last

    def start_torrent(self, info_hash: str) -> None:
        self._first_working([("core.resume_torrents", ([info_hash.lower()],)), ("core.resume_torrent", (info_hash.lower(),))])
        state = self.torrent_state(info_hash)
        if state is None:
            raise DelugeError("the torrent is not in the client")
        if is_stopped(state):
            raise DelugeError("Deluge did not start the torrent")

    def pause_torrent(self, info_hash: str) -> None:
        self._first_working([("core.pause_torrents", ([info_hash.lower()],)), ("core.pause_torrent", (info_hash.lower(),))])
        state = self.torrent_state(info_hash)
        if state is None:
            logger.info("torrent %s is no longer in the client", info_hash)
            return
        if not is_stopped(state):
            raise DelugeError("Deluge did not stop the torrent")

    def delete_torrent(self, info_hash: str, *, delete_files: bool = False) -> None:
        """Remove a torrent and check that it went. Never deletes files unless asked to."""
        try:
            self._rpc("core.remove_torrent", info_hash.lower(), bool(delete_files))
        except DelugeError:
            if self.torrent_state(info_hash) is not None:
                raise
        if self.torrent_state(info_hash) is not None:
            raise DelugeError(
                "Deluge still has the torrent after being asked to remove it"
                " (a reverse proxy in front of the Web UI can answer for it without passing the request on)."
            )

    # -- tags: none in Deluge ---------------------------------------------------------

    def list_tags(self) -> list[str]:
        """Deluge has no tags, so there are none to prune."""
        return []

    def delete_tags(self, tags: Sequence[str]) -> None:
        return None
