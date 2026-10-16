"""Talking to Shoko Server over its API (build 75).

Until now AniFlow and Shoko communicated entirely through folders: a file is
placed in a drop source, Shoko identifies and moves it, AniFlow finds it
again. That works without credentials and without Shoko knowing AniFlow
exists, and it stays the mechanism for handing files over.

What folders cannot answer is everything Shoko *knows*: which episodes a
series has, which of them have files, what it could not identify. That needs
the API, which needs a key.

The key is Shoko's own API key, taken from its settings. AniFlow never asks
for a Shoko username and password: exchanging those for a key would mean
holding the credentials to somebody's whole media server in order to read a
list of episodes.

Read-only, deliberately. Nothing here writes to Shoko - not because the API
would refuse, but because two programs renaming the same library is how a
library gets ruined, and Shoko is the one that owns it.
"""
from __future__ import annotations

import httpx

from backend.app.core.logging import for_component
from backend.app.core.version import user_agent

logger = for_component("Shoko")


class ShokoError(RuntimeError):
    """Shoko could not be reached, or refused the key."""


class _MethodNotAllowed(Exception):
    """Shoko answered 405: right address, wrong HTTP method."""


class ShokoClient:
    """A thin read-only client for one Shoko Server."""

    def __init__(self, url: str, api_key: str, *, timeout: float = 10.0) -> None:
        if not (url or "").strip():
            raise ShokoError("No Shoko Server URL is configured")
        self._url = url.strip().rstrip("/")
        self._key = (api_key or "").strip()
        self._timeout = timeout

    # -- the two questions a Test button has to answer ---------------------

    def version(self) -> str:
        """Whether something that looks like Shoko is at that address.

        Asked without the key on purpose: it separates "wrong address" from
        "wrong key", which are the two mistakes people actually make and which
        one combined error message cannot tell apart.
        """
        payload = self._get("/api/v3/Init/Status", authenticated=False)
        version = payload.get("ServerVersion") or payload.get("Version")
        state = payload.get("State") or payload.get("ServerState")
        if version:
            return str(version) if not state else f"{version} ({state})"
        # A JSON answer from that path is Shoko enough to proceed.
        return str(state or "unknown version")

    def check_key(self) -> dict[str, object]:
        """Whether the key works, and what Shoko says about its library."""
        if not self._key:
            raise ShokoError("No API key is configured")
        return self._get("/api/v3/Dashboard/Stats")

    # -- what it is for ----------------------------------------------------

    def stats(self) -> dict[str, object]:
        """Shoko's own dashboard numbers: series, files, missing episodes."""
        return self._get("/api/v3/Dashboard/Stats")

    def series(self, page: int = 1, size: int = 50) -> dict[str, object]:
        return self._get("/api/v3/Series", params={"page": page, "pageSize": size})

    def unrecognised_files(self, page: int = 1, size: int = 50) -> dict[str, object]:
        """Files Shoko has but could not identify - somebody has to decide."""
        return self._get("/api/v3/File", params={"page": page, "pageSize": size, "include_unrecognized": "only"})

    def import_folders(self) -> object:
        return self._get("/api/v3/ImportFolder")

    # -- AniDB, through Shoko's own cache of it (build 112) -----------------
    #
    # Shoko keeps AniDB's records for everything it has ever had a reason to
    # look at, which is more than it holds: the seasons related to a series in
    # the library are in there too, with their episode counts. That is exactly
    # what deciding "this file called 39 is episode 1 of season 4" needs, and
    # asking Shoko for it costs nothing and reaches AniDB not at all.

    def anidb_anime(self, aid: int) -> dict[str, object]:
        """One AniDB entry as Shoko has it. Raises if it has never seen it."""
        return self._get(f"/api/v3/Series/AniDB/{int(aid)}")

    def anidb_relations(self, aid: int) -> list[dict]:
        """What AniDB says this entry is related to - prequels and sequels."""
        answer = self._get(f"/api/v3/Series/AniDB/{int(aid)}/Relations")
        found = answer.get("data", answer)
        return found if isinstance(found, list) else []

    def fetch_anidb_anime(self, aid: int) -> None:
        """Have Shoko fetch an entry it has never seen.

        This is the one call here that reaches AniDB, and it does so as
        *Shoko*: its client registration, its rate limit, its ban status. That
        is the point of going through it rather than asking AniDB directly.

        ``createSeriesEntry`` is deliberately left off: this fills a metadata
        cache and must not add anything to somebody's library.
        """
        self._post(
            f"/api/v3/Series/AniDB/{int(aid)}/Refresh",
            params={"immediate": "true", "skipTmdbUpdate": "true"},
        )

    def series_files(self, shoko_id: int, size: int = 200) -> list[dict]:
        """Every file Shoko holds for one series (build 117).

        For reading the subgroup and the quality off the names, which is the
        only place either of them is written down for a library AniFlow did
        not download itself.
        """
        answer = self._get(
            f"/api/v3/Series/{int(shoko_id)}/File",
            params={"pageSize": size, "includeXRefs": "true"},
        )
        rows = answer.get("List") or answer.get("data") or []
        return [row for row in rows if isinstance(row, dict)]

    def image_bytes(self, path: str) -> bytes:
        """One image, as bytes, fetched with the API key (build 117).

        Fetched here rather than pointed at from the browser: Shoko's image
        endpoints want the key, the browser has no business holding it, and a
        poster stored locally keeps working when Shoko is restarting.
        """
        return self._raw(path)

    def run_import(self) -> None:
        """Ask Shoko to run its import now, over every import folder.

        Shoko does this on its own schedule, which can be a long time apart -
        so a file AniFlow handed over may sit in the import folder until the
        next run. This is the same as pressing Run Import in Shoko's own
        interface; what it finds is queued inside Shoko.
        """
        self._action("/api/v3/Action/RunImport")

    # There is deliberately no "sync MyList" here (build 248). Shoko's /Action/SyncMyList queues the deletion
    # of every file on the user's AniDB MyList that Shoko does not hold - 12,383 of them on the first run,
    # on an account with 2,940 anime - and AniFlow's list is an export the user uploads instead.
    # tests/test_aniflow_never_syncs_mylist.py fails if the call comes back.

    def anidb_known(self) -> list[dict]:
        """Every AniDB anime Shoko has a record of, in its library or not."""
        answer = self._get("/api/v3/Series/AniDB", params={"pageSize": 0})
        rows = answer.get("List") or answer.get("data") or []
        return [row for row in rows if isinstance(row, dict)]

    def anidb_ban(self) -> dict[str, object]:
        """Whether AniDB is currently refusing this Shoko. Never guess it."""
        return self._get("/api/v3/AniDB/BanStatus")

    # -- internals ---------------------------------------------------------

    def _action(self, path: str) -> None:
        """One of Shoko's ``/Action/...`` calls, whichever method this Shoko wants.

        Reported on a real server: Run Import and Sync MyList both answered
        "405 for /api/v3/Action/..." - Method Not Allowed. Shoko's actions are
        GETs in the versions that publish them that way and POSTs in others,
        and AniFlow only ever sent POST. So GET is tried first and, on a 405,
        POST - once each, never a loop. Any other answer is final, and a 405 on
        both is reported as such rather than as a single method's failure.
        """
        try:
            self._send("GET", path)
        except _MethodNotAllowed:
            try:
                self._send("POST", path)
            except _MethodNotAllowed as exc:
                raise ShokoError(
                    f"Shoko answered 405 for {path} with both GET and POST"
                ) from exc

    def _post(self, path: str, *, params: dict | None = None) -> None:
        """A call that asks Shoko to do something rather than to answer.

        Nothing is read back: every one of these queues work inside Shoko, and
        what came of it is found by asking again afterwards.
        """
        try:
            self._send("POST", path, params=params)
        except _MethodNotAllowed as exc:
            raise ShokoError(f"Shoko answered 405 for {path}") from exc

    def _send(self, method: str, path: str, *, params: dict | None = None) -> None:
        if not self._key:
            raise ShokoError("No API key is configured")
        headers = {
            "User-Agent": user_agent(),
            "Accept": "application/json",
            "apikey": self._key,
        }
        try:
            with httpx.Client(timeout=self._timeout, headers=headers) as client:
                response = client.request(method, f"{self._url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise ShokoError(f"{self._url} could not be reached: {exc}") from exc
        if response.status_code in (401, 403):
            raise ShokoError("Shoko refused the API key")
        if response.status_code == 405:
            raise _MethodNotAllowed()
        if response.is_error:
            raise ShokoError(f"Shoko answered {response.status_code} for {path}")

    def _raw(self, path: str) -> bytes:
        """A GET whose answer is not JSON - an image, today."""
        if not self._key:
            raise ShokoError("No API key is configured")
        headers = {"User-Agent": user_agent(), "apikey": self._key}
        try:
            with httpx.Client(timeout=self._timeout, headers=headers) as client:
                response = client.get(f"{self._url}{path}")
        except httpx.HTTPError as exc:
            raise ShokoError(f"{self._url} could not be reached: {exc}") from exc
        if response.status_code in (401, 403):
            raise ShokoError("Shoko refused the API key")
        if response.is_error:
            raise ShokoError(f"Shoko answered {response.status_code} for {path}")
        return response.content

    def _get(self, path: str, *, params: dict | None = None, authenticated: bool = True) -> dict:
        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        if authenticated:
            if not self._key:
                raise ShokoError("No API key is configured")
            # Shoko's own header name for its API keys.
            headers["apikey"] = self._key
        target = f"{self._url}{path}"
        try:
            # Not proxied: Shoko is on the same network as AniFlow, and
            # services/outbound.py leaves local addresses alone anyway.
            with httpx.Client(timeout=self._timeout, headers=headers) as client:
                response = client.get(target, params=params)
        except httpx.HTTPError as exc:
            raise ShokoError(f"{self._url} could not be reached: {exc}") from exc

        if response.status_code in (401, 403):
            raise ShokoError("Shoko refused the API key")
        if response.status_code == 404:
            raise ShokoError(f"Shoko has no {path} - is this Shoko Server 4 or newer?")
        if response.is_error:
            raise ShokoError(f"Shoko answered {response.status_code} for {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ShokoError(f"{self._url} answered something that is not JSON") from exc
        return payload if isinstance(payload, dict) else {"data": payload}


def client_from_settings(settings=None) -> ShokoClient:
    """A client built from the stored settings, or a clear refusal."""
    from backend.app.services.app_settings import load_app_settings

    resolved = settings if settings is not None else load_app_settings()
    return ShokoClient(
        getattr(resolved, "shoko_url", "") or "",
        getattr(resolved, "shoko_api_key", "") or "",
    )


__all__ = ["ShokoClient", "ShokoError", "client_from_settings"]
