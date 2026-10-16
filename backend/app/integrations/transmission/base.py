"""Transmission RPC adapter (build 234).

Transmission is reached over its RPC endpoint, ``/transmission/rpc``, on the port its web interface uses (9091). From
its specification: every request carries an ``X-Transmission-Session-Id`` header; a missing or stale one is answered
with HTTP 409 and the right id in the response headers, and the request is sent again with it; HTTP Basic
authentication when the server has a username and password set.

**Two protocols.** Transmission 4.1 introduced JSON-RPC 2.0 with snake_case names (``torrent_get``, ``hash_string``).
The older protocol - ``{"method": "torrent-get", "arguments": {...}}``, kebab-case arguments and camelCase fields - is
what 3.x and 4.0 speak, and 4.1 still answers it though it is deprecated. This adapter works in snake_case inside and
translates at the wire, and picks the protocol by asking ``session-get`` the old way (which every version
understands) and reading the RPC version: 4.1 or newer (semver 6.0.0) is JSON-RPC from then on.

**Speaking qBittorrent.** The rest of AniFlow reads qBittorrent's vocabulary (see ``integrations/torrent_client.py``):
Transmission's status 6 "seeding" becomes ``uploading`` or ``stalledUP``, its ``percentDone`` is already 0-1, a file
that is not wanted has priority 0.

**Labels are tags.** Transmission (3.00 and newer) keeps a list of labels on every torrent, so AniFlow's category and
its per-download tag are both real labels, as in qBittorrent, and nothing needs remembering in memory. The client
has no separate registry of labels: one exists only while a torrent carries it.

What the specification does not cover, and is taken from Transmission's source as the author remembers it, is marked
below: the legacy argument spellings and the field list. Those are the first things to check if a real Transmission
disagrees.
"""
import base64
import logging
import re
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.app.integrations.qbittorrent.base import TorrentFileInfo, TorrentInfo, is_stopped
from backend.app.integrations.torrent_client import TorrentClientError

logger = logging.getLogger(__name__)


class TransmissionError(TorrentClientError):
    """The Transmission RPC could not be used as configured."""


#: Transmission 4.1.0, the first to speak JSON-RPC 2.0 (rpc_version_semver 6.0.0).
JSONRPC_SEMVER = (6, 0, 0)

#: What is asked of every torrent. Taken from Transmission's own field list; a field a version does not know is
#: left out of its answer.
_FIELDS = [
    "id", "hash_string", "name", "status", "percent_done", "download_dir", "total_size", "rate_download",
    "rate_upload", "eta", "peers_sending_to_us", "peers_getting_from_us", "upload_ratio", "labels",
    "is_finished", "error", "error_string", "is_stalled", "metadata_percent_complete",
]
_FILE_FIELDS = ["hash_string", "files", "file_stats"]

_HASH = re.compile(r"^[0-9a-f]{40}$")


def _kebab(name: str) -> str:
    return name.replace("_", "-")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _snake(name: str) -> str:
    """``hashString`` and ``torrent-added`` and ``rpc-version-semver`` as ``hash_string`` ..."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name.replace("-", "_")).lower()


def _snake_keys(value):
    """Every key of a legacy answer in snake_case, however deep."""
    if isinstance(value, dict):
        return {_snake(str(key)): _snake_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snake_keys(item) for item in value]
    return value


def _rpc_url(base: str) -> str:
    """The RPC address from whatever the person typed: ``http://host:9091`` is enough."""
    parts = urlsplit(base.strip())
    path = parts.path.rstrip("/")
    if path.endswith("/rpc"):
        pass
    elif path.endswith("/transmission"):
        path += "/rpc"
    else:
        path += "/transmission/rpc"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _semver(text: object) -> tuple[int, int, int]:
    found = re.match(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(part) for part in found.groups()) if found else (0, 0, 0)  # type: ignore[return-value]


def qb_state(entry: dict) -> str:
    """Transmission's status in qBittorrent's words.

    ``status``: 0 stopped, 1 queued to verify, 2 verifying, 3 queued to download, 4 downloading, 5 queued to seed,
    6 seeding. ``error`` 3 is a local error (the torrent stops); 1 and 2 are tracker complaints and do not.
    """
    status = int(entry.get("status") or 0)
    finished = float(entry.get("percent_done") or 0) >= 0.999 or bool(entry.get("is_finished"))
    if int(entry.get("error") or 0) == 3:
        text = str(entry.get("error_string") or "").lower()
        return "missingFiles" if "no data found" in text or "not found" in text else "error"
    if status == 0:
        return "pausedUP" if finished else "pausedDL"
    if status in (1, 2):
        return "checkingUP" if finished else "checkingDL"
    if status == 3:
        return "queuedDL"
    if status == 4:
        if float(entry.get("metadata_percent_complete") if entry.get("metadata_percent_complete") is not None else 1) < 1:
            return "metaDL"
        return "stalledDL" if entry.get("is_stalled") else "downloading"
    if status == 5:
        return "queuedUP"
    if status == 6:
        return "uploading" if float(entry.get("rate_upload") or 0) > 0 else "stalledUP"
    return "unknown"


class TransmissionClient:
    """A small client for one Transmission."""

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._url = _rpc_url(base_url)
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            auth=httpx.BasicAuth(username, password) if (username or password) else None,
            follow_redirects=False,
        )
        self._session_id: str | None = None
        self._jsonrpc: bool | None = None  # unknown until the first call
        self._next_id = 0

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TransmissionClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- transport -----------------------------------------------------------

    def _post(self, payload: dict) -> dict:
        headers: dict[str, str] = {}
        for _attempt in range(3):
            if self._session_id:
                headers["X-Transmission-Session-Id"] = self._session_id
            try:
                response = self._client.post(self._url, json=payload, headers=headers)
            except httpx.ConnectError as exc:
                raise TransmissionError(
                    f"Could not connect to {self._base_url} - host unreachable, port closed, or wrong protocol (http vs https)"
                ) from exc
            except httpx.TimeoutException as exc:
                raise TransmissionError(f"Connection to {self._base_url} timed out - wrong host/port, or no answer in time") from exc
            except httpx.HTTPError as exc:
                raise TransmissionError(f"Could not reach Transmission at {self._base_url}: {type(exc).__name__}: {exc}") from exc
            if response.status_code == 409:
                # The CSRF handshake: take the id from the answer and send the request again.
                session = response.headers.get("X-Transmission-Session-Id")
                if not session:
                    raise TransmissionError("Transmission answered 409 without a session id - is this its RPC address?")
                self._session_id = session
                continue
            break
        if response.status_code == 401:
            raise TransmissionError("Transmission rejected the login (HTTP 401): wrong username or password")
        if response.status_code == 404:
            # Found on a Pi: the address field still held Deluge's after the client was switched, and Deluge's web
            # server answered 404 with a page of its own. Say what is probably wrong, not just the number.
            other = " That page looks like Deluge's, not Transmission's." if "No Such Resource" in response.text else ""
            raise TransmissionError(
                f"Nothing answers at {self._url} (HTTP 404). Is the address Transmission's - usually http://host:9091?"
                f"{other} Another program may be listening on that port."
            )
        if response.status_code >= 400:
            raise TransmissionError(
                f"Transmission answered HTTP {response.status_code}" + (f": {response.text[:120]}" if response.text else "")
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TransmissionError(
                f"{self._url} did not answer RPC - is the address Transmission's (usually http://host:9091)?"
            ) from exc
        if not isinstance(body, dict):
            raise TransmissionError(f"Transmission answered {type(body).__name__}, not an RPC object")
        return body

    def _detect(self) -> None:
        """Pick the protocol: ask the old way, which every version answers, and read the RPC version."""
        if self._jsonrpc is not None:
            return
        body = self._post({"method": "session-get", "arguments": {"fields": ["version", "rpc-version-semver"]}})
        arguments = _snake_keys(body.get("arguments") or {})
        self._jsonrpc = _semver(arguments.get("rpc_version_semver")) >= JSONRPC_SEMVER
        logger.debug("Transmission %s: using %s", arguments.get("version"), "JSON-RPC 2.0" if self._jsonrpc else "the legacy protocol")

    def _rpc(self, method: str, **arguments: object) -> dict:
        """A call in snake_case, answered in snake_case, whichever protocol the server speaks."""
        self._detect()
        if self._jsonrpc:
            self._next_id += 1
            body = self._post({"jsonrpc": "2.0", "method": method, "params": arguments, "id": self._next_id})
            if "error" in body:
                error = body["error"] if isinstance(body["error"], dict) else {"message": str(body["error"])}
                raise TransmissionError(f"Transmission: {str(error.get('message') or error)[:200]}")
            result = body.get("result")
            return result if isinstance(result, dict) else {}
        translated = {_kebab(key): value for key, value in arguments.items()}
        if "fields" in translated:
            translated["fields"] = [_camel(str(field)) for field in translated["fields"]]  # type: ignore[union-attr]
        body = self._post({"method": _kebab(method), "arguments": translated})
        if body.get("result") != "success":
            raise TransmissionError(f"Transmission: {str(body.get('result'))[:200]}")
        return _snake_keys(body.get("arguments") or {})

    # -- reading ----------------------------------------------------------------

    def _session(self) -> dict:
        return self._rpc("session_get", fields=["version", "download_dir", "rpc_version_semver"])

    def version(self) -> str:
        return str(self._session().get("version") or "")

    def default_save_path(self) -> str:
        return str(self._session().get("download_dir") or "")

    def check_ready(self, category: str = "aniflow") -> None:
        """Raise unless this Transmission has labels (3.00 and newer, rpc semver 5.2.0)."""
        semver = _semver(self._session().get("rpc_version_semver"))
        if semver != (0, 0, 0) and semver < (5, 2, 0):
            raise TransmissionError(
                "This Transmission is too old for labels (they arrived in 3.00) - AniFlow files its torrents under a label."
            )

    def _torrents(self, ids: Sequence[str] | None = None, fields: list[str] | None = None) -> list[dict]:
        arguments: dict[str, object] = {"fields": fields or _FIELDS}
        if ids is not None:
            arguments["ids"] = [item.lower() for item in ids]
        result = self._rpc("torrent_get", **arguments)
        torrents = result.get("torrents") or []
        return [entry for entry in torrents if isinstance(entry, dict)]

    @staticmethod
    def _info(entry: dict, category: str | None = None) -> TorrentInfo:
        labels = [str(label) for label in (entry.get("labels") or [])]
        chosen = category if category and category in labels else (labels[0] if labels and category is None else "")
        download_dir = str(entry.get("download_dir") or "")
        name = str(entry.get("name") or "")
        eta = entry.get("eta")
        return TorrentInfo(
            info_hash=str(entry.get("hash_string") or "").lower(),
            name=name,
            state=qb_state(entry),
            progress=float(entry.get("percent_done") or 0.0),
            category=chosen,
            content_path=f"{download_dir.rstrip('/')}/{name}" if download_dir and name else None,
            save_path=download_dir or None,
            size=entry.get("total_size"),
            dlspeed=entry.get("rate_download"),
            upspeed=entry.get("rate_upload"),
            eta=int(eta) if isinstance(eta, (int, float)) and eta >= 0 else None,
            num_seeds=entry.get("peers_sending_to_us"),
            num_leechs=entry.get("peers_getting_from_us"),
            ratio=float(entry["upload_ratio"]) if entry.get("upload_ratio") not in (None, -1, -2) else None,
            tags=tuple(label for label in labels if label != chosen),
        )

    def list_torrents(self) -> list[TorrentInfo]:
        return [self._info(entry) for entry in self._torrents()]

    def find_torrents(self, *, category: str, tag: str | None = None) -> list[TorrentInfo]:
        found = []
        for entry in self._torrents():
            labels = [str(label) for label in (entry.get("labels") or [])]
            if category not in labels or (tag and tag not in labels):
                continue
            found.append(self._info(entry, category))
        return found

    def find_by_hash(self, info_hash: str) -> TorrentInfo | None:
        for entry in self._torrents([info_hash]):
            if str(entry.get("hash_string") or "").lower() == info_hash.lower():
                return self._info(entry)
        return None

    def torrent_state(self, info_hash: str) -> str | None:
        torrent = self.find_by_hash(info_hash)
        return torrent.state if torrent else None

    def torrent_files(self, info_hash: str) -> list[TorrentFileInfo]:
        entries = self._torrents([info_hash], _FILE_FIELDS)
        if not entries:
            return []
        files = entries[0].get("files") or []
        stats = entries[0].get("file_stats") or []
        result: list[TorrentFileInfo] = []
        for index, item in enumerate(files):
            stat = stats[index] if index < len(stats) and isinstance(stats[index], dict) else {}
            length = int(item.get("length") or 0)
            done = int(item.get("bytes_completed") or 0)
            wanted = bool(stat.get("wanted", True))
            priority = 0 if not wanted else (6 if int(stat.get("priority") or 0) > 0 else 1)
            result.append(
                TorrentFileInfo(
                    index=index,
                    name=str(item.get("name") or ""),
                    size=length,
                    priority=priority,
                    progress=(done / length) if length else 0.0,
                )
            )
        return result

    # -- adding -------------------------------------------------------------------

    def _add(self, *, labels: list[str], paused: bool, filename: str | None = None, metainfo: bytes | None = None) -> bool:
        arguments: dict[str, object] = {"paused": paused, "labels": labels}
        if filename is not None:
            arguments["filename"] = filename
        else:
            arguments["metainfo"] = base64.b64encode(metainfo or b"").decode("ascii")
        result = self._rpc("torrent_add", **arguments)
        added = result.get("torrent_added")
        if added:
            # Labels in the add itself need Transmission 4.0; 3.00 takes them only through torrent_set, and a
            # version that ignored them would leave the torrent unfindable by its tag.
            info_hash = str(added.get("hash_string") or "").lower()
            if info_hash and not self._carries(info_hash, labels):
                self._rpc("torrent_set", ids=[info_hash], labels=labels)
            return True
        # A duplicate is a success (the same rule as qBittorrent's 409) and is left as it is: it may be somebody's
        # own torrent, which AniFlow has no business relabelling.
        return bool(result.get("torrent_duplicate"))

    def _carries(self, info_hash: str, labels: list[str]) -> bool:
        entries = self._torrents([info_hash], ["hash_string", "labels"])
        have = {str(label) for label in ((entries[0].get("labels") if entries else None) or [])}
        return set(labels) <= have

    @staticmethod
    def _labels(category: str, tag: str | None) -> list[str]:
        return [category, *([tag] if tag else [])]

    def add_torrents(self, urls: list[str], *, category: str, tag: str | None = None) -> bool:
        wanted = [url for url in urls if url]
        if not wanted:
            return True
        logger.info("adding %s torrent(s): labels=%r", len(wanted), self._labels(category, tag))
        accepted = True
        for url in wanted:
            try:
                accepted = self._add(filename=url, labels=self._labels(category, tag), paused=False) and accepted
            except TransmissionError as exc:
                logger.warning("Transmission rejected a torrent: %s", exc)
                accepted = False
        return accepted

    def add_torrent(self, torrent_url: str, *, category: str, tag: str | None = None) -> bool:
        logger.info("adding 1 torrent: labels=%r", self._labels(category, tag))
        return self._add(filename=torrent_url, labels=self._labels(category, tag), paused=False)

    def add_torrent_file(
        self, data: bytes, filename: str, *, category: str, tag: str | None = None, stopped: bool = True
    ) -> bool:
        logger.info("adding 1 torrent file: labels=%r stopped=%s", self._labels(category, tag), stopped)
        return self._add(metainfo=data, labels=self._labels(category, tag), paused=stopped)

    # -- changing --------------------------------------------------------------------

    def set_file_priority(self, info_hash: str, indexes: Sequence[int], priority: int) -> None:
        """Choose which files are fetched (priority 0 = not wanted) and read it back."""
        wanted = sorted({int(index) for index in indexes})
        if not wanted:
            return
        ids = [info_hash.lower()]
        if priority <= 0:
            self._rpc("torrent_set", ids=ids, files_unwanted=wanted)
        else:
            arguments: dict[str, object] = {"files_wanted": wanted}
            arguments["priority_high" if priority >= 6 else "priority_normal"] = wanted
            self._rpc("torrent_set", ids=ids, **arguments)
        now = {file.index: file.priority for file in self.torrent_files(info_hash)}
        expected = 0 if priority <= 0 else (6 if priority >= 6 else 1)
        wrong = [index for index in wanted if now.get(index) != expected]
        if wrong:
            raise TransmissionError(
                f"Transmission did not set the priority of {len(wrong)} file(s) of the torrent to {priority}"
                " (a reverse proxy in front of it can answer for it without passing the request on)."
            )

    def start_torrent(self, info_hash: str) -> None:
        self._rpc("torrent_start", ids=[info_hash.lower()])
        state = self.torrent_state(info_hash)
        if state is None:
            raise TransmissionError("the torrent is not in the client")
        if is_stopped(state):
            raise TransmissionError("Transmission did not start the torrent")

    def pause_torrent(self, info_hash: str) -> None:
        self._rpc("torrent_stop", ids=[info_hash.lower()])
        state = self.torrent_state(info_hash)
        if state is None:
            logger.info("torrent %s is no longer in the client", info_hash)
            return
        if not is_stopped(state):
            raise TransmissionError("Transmission did not stop the torrent")

    def delete_torrent(self, info_hash: str, *, delete_files: bool = False) -> None:
        """Remove a torrent and check that it went. Never deletes files unless asked to."""
        self._rpc("torrent_remove", ids=[info_hash.lower()], delete_local_data=bool(delete_files))
        if self.torrent_state(info_hash) is not None:
            raise TransmissionError(
                "Transmission still has the torrent after being asked to remove it"
                " (a reverse proxy in front of it can answer for it without passing the request on)."
            )

    # -- tags: labels -----------------------------------------------------------------

    def list_tags(self) -> list[str]:
        """Every label some torrent carries (Transmission has no registry of labels apart from the torrents)."""
        found: set[str] = set()
        for entry in self._torrents(None, ["hash_string", "labels"]):
            found.update(str(label) for label in (entry.get("labels") or []))
        return sorted(found)

    def delete_tags(self, tags: Sequence[str]) -> None:
        """Take these labels off every torrent that carries them."""
        gone = {str(tag) for tag in tags}
        if not gone:
            return
        for entry in self._torrents(None, ["hash_string", "labels"]):
            labels = [str(label) for label in (entry.get("labels") or [])]
            if gone & set(labels):
                self._rpc(
                    "torrent_set",
                    ids=[str(entry.get("hash_string") or "").lower()],
                    labels=[label for label in labels if label not in gone],
                )
