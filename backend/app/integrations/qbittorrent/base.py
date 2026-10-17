"""qBittorrent WebUI API adapter (verified against the qBittorrent 5.0 docs).

Implements exactly what AniFlow needs and nothing more:

- cookie login (``POST /api/v2/auth/login``, SID cookie, Referer header)
- add torrent by URL with a category and a marker tag
- list torrents scoped to one category (optionally by tag)
- delete torrents (files are only deleted when explicitly requested)
- read and delete tags, so AniFlow's own markers can be swept up

The client is synchronous because it is used from worker threads.
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from backend.app.integrations.torrent_client import TorrentClientError

logger = logging.getLogger(__name__)

# Torrent states that count as "still downloading" versus "finished".
_ACTIVE_STATES = {
    "allocating", "downloading", "metaDL", "pausedDL", "queuedDL",
    "stalledDL", "checkingDL", "forcedDL", "checkingResumeData", "moving",
}
_ERROR_STATES = {"error", "missingFiles"}

#: Every state qBittorrent can report, in its own spelling. Declared because
#: the Downloads page shows this word to the user: "stalledUP" and "queuedDL"
#: reached the screen untranslated, and there is no way to know the list is
#: complete without writing it down. Both the 4.x (``paused*``) and 5.x
#: (``stopped*``) spellings are here - the client may be either.
QB_STATES = (
    "error",
    "missingFiles",
    "uploading",
    "pausedUP",
    "stoppedUP",
    "queuedUP",
    "stalledUP",
    "checkingUP",
    "forcedUP",
    "allocating",
    "downloading",
    "metaDL",
    "pausedDL",
    "stoppedDL",
    "queuedDL",
    "stalledDL",
    "checkingDL",
    "forcedDL",
    "checkingResumeData",
    "moving",
    "unknown",
)


class QBittorrentError(TorrentClientError):
    """The qBittorrent WebUI could not be used as configured."""


@dataclass(frozen=True)
class TorrentInfo:
    info_hash: str
    name: str
    state: str
    progress: float
    category: str
    content_path: str | None = None
    save_path: str | None = None
    size: int | None = None
    dlspeed: int | None = None
    upspeed: int | None = None
    eta: int | None = None
    num_seeds: int | None = None
    num_leechs: int | None = None
    ratio: float | None = None
    #: The client's own tags for this torrent (build 94). Sent by
    #: ``/torrents/info`` all along and dropped here, which left "which of
    #: these are AniFlow's" answerable only one request per download row -
    #: and services/torrent_sweep.py has to ask it of every torrent at once.
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TorrentFileInfo:
    """One file inside a torrent as the client reports it (build 213)."""

    index: int
    #: The path inside the torrent, torrent folder first, as the client shows it.
    name: str
    size: int
    #: 0 = do not download, 1 = normal, 6 = high, 7 = maximum.
    priority: int
    progress: float


class QBittorrentClient:
    """Small, typed WebUI v2 client for one qBittorrent instance."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers={"Referer": self._base_url, "Origin": self._base_url},
            follow_redirects=False,
        )
        self._logged_in = False

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QBittorrentClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- authentication ----------------------------------------------------

    def _login(self) -> None:
        try:
            response = self._client.post(
                "/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.ConnectError as exc:
            raise QBittorrentError(
                f"Could not connect to {self._base_url} - host unreachable, port closed, or wrong protocol (http vs https)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise QBittorrentError(
                f"Connection to {self._base_url} timed out - wrong host/port, or the service did not answer in time"
            ) from exc
        except httpx.HTTPError as exc:
            raise QBittorrentError(f"Could not reach qBittorrent at {self._base_url}: {type(exc).__name__}: {exc}") from exc

        body = response.text.strip()
        if response.status_code == 200 and body == "Ok.":
            return
        # Never include credentials in error messages.
        if response.status_code == 403:
            raise QBittorrentError(
                "qBittorrent rejected the login with HTTP 403: wrong username/password, "
                "or this IP is temporarily banned after previous failed attempts"
            )
        if response.status_code in (200, 201, 202, 204) and not body:
            # Empty success-looking answer is not the qBittorrent WebUI login
            # (that always answers "Ok."). Probe a known endpoint to tell the
            # user whether they are even talking to qBittorrent.
            probe = self._client.get("/api/v2/app/version")
            if probe.status_code == 200 and probe.text.strip():
                # Reachable qBittorrent without authentication - continue.
                self._logged_in = True
                return
            hint = (
                f"qBittorrent answered HTTP {probe.status_code} on /api/v2/app/version"
                if probe.status_code != 404
                else "the version endpoint returned 404, so this does not look like a qBittorrent WebUI"
            )
            raise QBittorrentError(
                f"The login endpoint answered HTTP {response.status_code} with an empty body - "
                f"something other than the qBittorrent WebUI seems to listen on {self._base_url}. "
                f"Probe result: {hint}. Double-check URL, port and that the WebUI is enabled."
            )
        raise QBittorrentError(
            f"Login failed unexpectedly (HTTP {response.status_code}, response: {body[:80] or '<empty>'!r}). "
            "Is the URL pointing at the qBittorrent WebUI root?"
        )

    def _ensure_session(self) -> None:
        if not self._logged_in:
            self._login()
            self._logged_in = True

    def _request(self, method: str, path: str, *, ok_extra: tuple[int, ...] = (), **kwargs: object) -> httpx.Response:
        self._ensure_session()
        response = self._client.request(method, path, **kwargs)  # type: ignore[arg-type]
        if response.status_code in (401, 403):
            # Session expired (or was revoked): log in once more and retry.
            self._logged_in = False
            self._ensure_session()
            response = self._client.request(method, path, **kwargs)  # type: ignore[arg-type]
        if response.status_code == 404 or response.status_code in ok_extra:
            return response
        if response.status_code >= 400:
            raise QBittorrentError(
                f"qBittorrent request {method} {path} failed with HTTP {response.status_code}"
                + (f": {response.text[:120]}" if response.text else "")
            )
        return response

    # -- operations ----------------------------------------------------------

    def version(self) -> str:
        response = self._request("GET", "/api/v2/app/version")
        return response.text.strip()

    def default_save_path(self) -> str:
        """qBittorrent's configured download folder (its own view)."""
        response = self._request("GET", "/api/v2/app/defaultSavePath")
        return response.text.strip()

    def add_torrents(
        self, urls: list[str], *, category: str, tag: str | None = None
    ) -> bool:
        """Queue several torrents in **one** request (build 120).

        qBittorrent's add endpoint takes a newline-separated list, and using it
        is the answer to a bug reported three times: adding a season of
        twenty-one episodes put six torrents in the client and the rest arrived
        a minute or two later, through the retry pass.

        The question that led here was the right one - *"instead of sending 21
        rapid posts, is there not a way to group stuff?"* - and yes: one POST
        instead of twenty-one is fewer chances to be rate-limited, fewer
        round-trips through a reverse proxy, and one answer to read.

        Returns True when qBittorrent accepted the batch. Which torrents
        actually arrived is not in that answer, so the caller identifies them
        itself - see ``info_hash_of`` - and anything missing is handed over
        one at a time afterwards.
        """
        wanted = [url for url in urls if url]
        if not wanted:
            return True
        data: dict[str, str] = {"urls": "\n".join(wanted), "category": category}
        if tag:
            data["tags"] = tag
        # What was sent, before it is sent (build 133). A download that lands
        # outside the category folder is otherwise unprovable after the fact:
        # the category is the only thing AniFlow tells the client about where
        # the file goes, and there was no record of having told it.
        logger.info(
            "adding %s torrent(s): category=%r tags=%r",
            len(wanted),
            category,
            tag or "",
        )
        response = self._request(
            "POST", "/api/v2/torrents/add", ok_extra=(409,), data=data
        )
        if response.status_code == 409:
            # Every one of them already present, which is a success here.
            return True
        body = response.text.strip()
        if body in ("", "Ok.") or _json_add_succeeded(response, body):
            return True
        logger.warning(
            "qBittorrent rejected a batch of %s torrent(s): %r", len(wanted), body[:120]
        )
        return False


    def add_torrent(self, torrent_url: str, *, category: str, tag: str | None = None) -> bool:
        """Queue one torrent. Returns True when qBittorrent accepted it.

        qBittorrent answers ``Ok.`` on success and ``Fails.`` with 200 when the
        URL could not be fetched / the torrent was rejected. We treat
        ``Fails.`` as a hard failure so the caller can surface it, but the
        caller in :pymod:`backend.app.services.downloads` falls back to a tag
        lookup – a ``Fails.`` that still created a torrent (e.g. duplicate of
        a torrent added seconds earlier) will be recovered on the next sync.
        """
        data: dict[str, str] = {"urls": torrent_url, "category": category}
        if tag:
            data["tags"] = tag
        # See add_torrents: the category is the whole of what AniFlow says
        # about placement, so it is logged (build 133).
        logger.info("adding 1 torrent: category=%r tags=%r", category, tag or "")
        response = self._request("POST", "/api/v2/torrents/add", ok_extra=(409,), data=data)
        if response.status_code == 200:
            body = response.text.strip()
            if body == "Ok.":
                return True
            if body == "Fails.":
                logger.warning("qBittorrent rejected torrent url %r: %r", torrent_url[:120], body)
                return False
            # 5.1 answers with counts instead of "Ok." (build 214).
            if _json_add_succeeded(response, body):
                return True
            # Empty body with 200 is treated as success (some qBittorrent builds)
            return body == "" or body == "Ok."
        # HTTP 409: duplicate add - the torrent is already in the client,
        # which counts as success for our idempotent queueing.
        logger.info("torrent already present in client")
        return True

    def add_torrent_file(
        self, data: bytes, filename: str, *, category: str, tag: str | None = None, stopped: bool = True
    ) -> bool:
        """Queue one torrent from the .torrent file's own bytes, stopped (build 213).

        Stopped so that its files can be chosen before a single byte is fetched. Both spellings of the
        flag are sent - 4.x calls it ``paused``, 5.x ``stopped`` - and the one a build does not know is
        ignored, so the same call works on either. Whether it really arrived stopped is not taken on trust:
        the caller reads the state back before choosing files (see ``set_file_priority``).
        """
        form: dict[str, str] = {"category": category}
        if tag:
            form["tags"] = tag
        if stopped:
            form["paused"] = "true"
            form["stopped"] = "true"
        logger.info("adding 1 torrent file: category=%r tags=%r stopped=%s", category, tag or "", stopped)
        response = self._request(
            "POST",
            "/api/v2/torrents/add",
            ok_extra=(409,),
            data=form,
            files={"torrents": (filename, data, "application/x-bittorrent")},
        )
        if response.status_code == 409:
            return True  # already there, which is a success for an idempotent queue
        body = response.text.strip()
        if body in ("", "Ok."):
            return True
        if body.startswith("{"):
            if _json_add_succeeded(response, body):
                return True
            logger.warning("qBittorrent rejected a torrent file %r: %r", filename[:80], body[:200])
            return False
        logger.warning("qBittorrent rejected a torrent file %r: %r", filename[:80], body[:120])
        if body == "Fails.":
            return False
        # Something not understood: say what it was rather than guess.
        raise QBittorrentError(f"qBittorrent answered {body[:120]!r} to the torrent file")

    def torrent_files(self, info_hash: str) -> list[TorrentFileInfo]:
        """The files of one torrent, in the client's own order and with its own priorities."""
        response = self._request("GET", "/api/v2/torrents/files", params={"hash": info_hash.lower()})
        if response.status_code == 404:
            return []
        files: list[TorrentFileInfo] = []
        for position, entry in enumerate(response.json()):
            files.append(
                TorrentFileInfo(
                    index=int(entry.get("index", position)),
                    name=str(entry.get("name", "")),
                    size=int(entry.get("size", 0) or 0),
                    priority=int(entry.get("priority", 1)),
                    progress=float(entry.get("progress", 0.0) or 0.0),
                )
            )
        return files

    def set_file_priority(self, info_hash: str, indexes: Sequence[int], priority: int) -> None:
        """Give these files a priority (0 means "do not download"), and check that it took.

        The lesson ``pause_torrent`` and ``delete_torrent`` learned: an accepted request is not proof. A
        reverse proxy can answer 200 to anything, and a client that ignored this would then download the
        whole season while AniFlow believed it was fetching one episode. So the priorities are read back.
        """
        wanted = sorted(set(int(index) for index in indexes))
        if not wanted:
            return
        self._request(
            "POST",
            "/api/v2/torrents/filePrio",
            data={"hash": info_hash.lower(), "id": "|".join(str(index) for index in wanted), "priority": str(priority)},
        )
        now = {file.index: file.priority for file in self.torrent_files(info_hash)}
        wrong = [index for index in wanted if now.get(index) != priority]
        if wrong:
            raise QBittorrentError(
                f"qBittorrent did not set the priority of {len(wrong)} file(s) of the torrent to {priority}"
                " (a reverse proxy in front of the WebUI can answer for it without passing the request on)."
            )

    def start_torrent(self, info_hash: str) -> None:
        """Start a stopped torrent, and check that it is no longer stopped.

        5.x serves ``/torrents/start`` and 4.x ``/torrents/resume``; both are tried, the state is read back.
        """
        attempted: list[str] = []
        for path in ("/api/v2/torrents/start", "/api/v2/torrents/resume"):
            response = self._request("POST", path, data={"hashes": info_hash})
            if response.status_code == 404:
                continue
            attempted.append(path)
            state = self.torrent_state(info_hash)
            if state is None:
                raise QBittorrentError("the torrent is not in the client")
            if not is_stopped(state):
                logger.info("started torrent %s via %s (state %s)", info_hash, path, state)
                return
        raise QBittorrentError(
            "qBittorrent did not start the torrent"
            + (f" (tried {', '.join(attempted)})" if attempted else " (neither /torrents/start nor /torrents/resume exists)")
        )

    def list_torrents(self) -> list[TorrentInfo]:
        """Every torrent in the client, whatever its category (build 218: which tags are really in use)."""
        response = self._request("GET", "/api/v2/torrents/info")
        return [_torrent_from(entry) for entry in response.json()]

    def find_torrents(self, *, category: str, tag: str | None = None) -> list[TorrentInfo]:
        params: dict[str, str] = {"category": category}
        if tag:
            params["tag"] = tag
        response = self._request("GET", "/api/v2/torrents/info", params=params)
        return [_torrent_from(entry) for entry in response.json()]

    def find_by_hash(self, info_hash: str) -> TorrentInfo | None:
        """One torrent by hash, in whatever category it happens to be in.

        The category filter is what ``find_torrents`` is for, and it is right
        for "what is AniFlow downloading". It is wrong for "is this torrent
        still here": a torrent whose category was changed - by hand, or by a
        client that lost its categories in an upgrade - is present and working
        while a categorised lookup reports it gone. That is how two finished
        downloads came to be labelled *missing* on the Downloads page.
        """
        response = self._request(
            "GET", "/api/v2/torrents/info", params={"hashes": info_hash.lower()}
        )
        for entry in response.json():
            if str(entry.get("hash", "")).lower() == info_hash.lower():
                return _torrent_from(entry)
        return None

    def torrent_state(self, info_hash: str) -> str | None:
        """The client's own word for what this torrent is doing right now.

        ``None`` means the client does not know the torrent at all.
        """
        response = self._request("GET", "/api/v2/torrents/info", params={"hashes": info_hash})
        for entry in response.json():
            if str(entry.get("hash", "")).lower() == info_hash.lower():
                return str(entry.get("state", "unknown"))
        return None

    def pause_torrent(self, info_hash: str) -> None:
        """Stop a torrent so it no longer seeds, keeping it in the client.

        qBittorrent renamed this endpoint: 5.x serves ``/torrents/stop`` and
        4.x ``/torrents/pause``. Both are tried, and - the part that matters -
        the torrent's own state is read back afterwards. An accepted request is
        not proof: a build that does not implement the name can answer 200, and
        a reverse proxy in front of the WebUI can answer 200 for anything, so
        trusting the status code silently left torrents seeding.
        """
        attempted: list[str] = []
        for path in ("/api/v2/torrents/stop", "/api/v2/torrents/pause"):
            response = self._request("POST", path, data={"hashes": info_hash})
            if response.status_code == 404:
                continue  # this build spells it the other way
            attempted.append(path)
            state = self.torrent_state(info_hash)
            if state is None:
                # Gone from the client entirely: nothing left to stop, and the
                # sync pass will notice the disappearance on its own.
                logger.info("torrent %s is no longer in the client", info_hash)
                return
            if is_stopped(state):
                logger.info("stopped torrent %s via %s (state %s)", info_hash, path, state)
                return
            logger.debug("%s returned %s but state is still %s", path, response.status_code, state)
        raise QBittorrentError(
            "qBittorrent did not stop the torrent"
            + (f" (tried {', '.join(attempted)})" if attempted else
               " (neither /torrents/stop nor /torrents/pause exists)")
        )

    def list_tags(self) -> list[str]:
        """Every tag qBittorrent knows, including tags on no torrent at all."""
        response = self._request("GET", "/api/v2/torrents/tags")
        return [str(tag) for tag in response.json()]

    def delete_tags(self, tags: Sequence[str]) -> None:
        """Remove tags from the client's global tag list.

        qBittorrent takes them comma-separated; our tags are
        ``aniflow-<uuid>``, so they can never contain the separator.
        """
        if not tags:
            return
        self._request("POST", "/api/v2/torrents/deleteTags", data={"tags": ",".join(tags)})

    def delete_torrent(self, info_hash: str, *, delete_files: bool = False) -> None:
        """Take a torrent out of the client, and check that it went.

        The same lesson `pause_torrent` learned, a build later and the hard
        way: **an accepted request is not proof**. This deployment reaches its
        client through an HTTPS reverse proxy, and a proxy can answer 200 to
        anything - a redirect to a login page, a cached response, a rule that
        passes GET and swallows POST. AniFlow believed the status code, the
        hourly sweep believed it again every hour, and the torrents sat there
        seeding for weeks with nothing anywhere saying so.

        So the torrent is read back. Gone is success; still there is a
        failure, and a failure that says which endpoint was tried.

        Never deletes the files - see AGENTS.md. ``delete_files`` exists for
        one caller that owns its own temporary data.
        """
        response = self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": info_hash, "deleteFiles": "true" if delete_files else "false"},
        )
        if self.torrent_state(info_hash) is None:
            logger.info("removed torrent %s from the client", info_hash)
            return
        raise QBittorrentError(
            "qBittorrent still has the torrent after being asked to delete it"
            f" (/api/v2/torrents/delete answered HTTP {response.status_code})."
            " A reverse proxy in front of the WebUI can answer for it without"
            " passing the request on."
        )


def _tags_from(raw) -> tuple[str, ...]:
    """qBittorrent sends tags as one comma-separated string."""
    if not raw:
        return ()
    if isinstance(raw, str):
        return tuple(tag.strip() for tag in raw.split(",") if tag.strip())
    return tuple(str(tag).strip() for tag in raw if str(tag).strip())


def _torrent_from(entry: dict) -> TorrentInfo:
    """One entry of qBittorrent's /torrents/info array."""
    return TorrentInfo(
        info_hash=entry["hash"],
        name=entry.get("name", ""),
        state=entry.get("state", "unknown"),
        progress=float(entry.get("progress", 0.0)),
        category=entry.get("category", ""),
        content_path=entry.get("content_path") or None,
        save_path=entry.get("save_path") or None,
        size=entry.get("size"),
        dlspeed=entry.get("dlspeed"),
        upspeed=entry.get("upspeed"),
        eta=entry.get("eta"),
        tags=_tags_from(entry.get("tags")),
        num_seeds=entry.get("num_seeds"),
        num_leechs=entry.get("num_leechs"),
        ratio=float(entry.get("ratio", 0)) if entry.get("ratio") is not None else None,
    )


def _json_add_succeeded(response: httpx.Response, body: str) -> bool:
    """Whether a 5.1-style answer to "add torrent" says something was added.

    5.1 replies with counts (``success_count``, ``failure_count``, ``pending_count``) where older builds
    said ``Ok.``. Something added, or still being fetched, is a success; reading only ``Ok.`` called a
    successful add a refusal (build 214).
    """
    if not body.startswith("{"):
        return False
    try:
        counts = response.json()
    except ValueError:
        return False
    if not isinstance(counts, dict):
        return False
    try:
        return int(counts.get("success_count") or 0) > 0 or int(counts.get("pending_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def is_stopped(state: str) -> bool:
    """Whether a raw qBittorrent state means "not running".

    Both spellings count: 4.x reports ``pausedUP``/``pausedDL`` and 5.x renamed
    them to ``stoppedUP``/``stoppedDL``. ``error`` and ``missingFiles`` are
    stopped too - the torrent is certainly not seeding - which matters after a
    move, where the data the torrent served is gone by design.
    """
    lowered = state.lower()
    return lowered.startswith(("paused", "stopped")) or lowered in {"error", "missingfiles"}


def download_state(state: str) -> str:
    """Map a raw qBittorrent state onto the AniFlow DownloadStatus vocabulary."""
    if state in _ERROR_STATES:
        return "ATTENTION"
    if state in _ACTIVE_STATES:
        return "DOWNLOADING"
    # uploading/pausedUP/stalledUP/queuedUP/forcedUP/checkingUP => finished.
    if state in {"uploading", "pausedUP", "stalledUP", "queuedUP", "forcedUP", "checkingUP"}:
        return "COMPLETED"
    return "DOWNLOADING"

