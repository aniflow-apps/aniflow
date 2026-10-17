"""Whether Shoko Server is answering, for the line in the sidebar (build 79).

The status block had a slot free. Build 79 removed the metadata fallback, and
with it the amber *"metadata on backup source"* state - there is no such thing
as an answer from somewhere else any more. Shoko Server is the other machine
AniFlow depends on and had nothing on that block at all, so it takes the slot.

Deliberately **not** probed from ``/health``. That endpoint is what Docker
restarts the container over, on a five-second budget, and it already makes a
live call (the torrent client) with a three-second timeout. A second
would make a slow LAN service look like an unhealthy AniFlow - the same
reasoning that moved the metadata probe off that path in build 78.

So the outcome is remembered instead. Every real question AniFlow asks Shoko
records it for free - the Shoko page and the watchlist's *Only in Shoko*
filter both go through ``shoko_library.overview`` - and the worker's tick
checks separately when nothing has asked for a while.

The states are the ones the user can act on, which is why "reachable but the
key was refused" is not folded into "unreachable": they send you to different
screens, and telling them apart is the whole point of the Test button in
Settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.core.logging import for_component
from backend.app.integrations.shoko.client import ShokoError, client_from_settings

logger = for_component("Shoko")

#: How long a remembered outcome counts as current. Long, because opening the
#: Shoko page or the watchlist filter refreshes it for free; the periodic
#: check only covers an instance where nobody has asked.
STATUS_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class ShokoStatus:
    """What AniFlow last learned about Shoko Server.

    * ``disabled`` - no URL or no key: Shoko is optional and the file
      hand-over works through folders without it, so this is a normal state
      and the sidebar says nothing at all.
    * ``ok`` - reachable, and the key was accepted.
    * ``unauthorised`` - reachable, and the key was refused. Settings, not
      the network.
    * ``unreachable`` - nothing answered at that address.
    * ``unknown`` - nothing has asked yet.
    """

    state: str
    detail: str | None = None
    checked_at: datetime | None = None


_status = ShokoStatus("unknown")


def remember(state: str, detail: str | None = None) -> None:
    """Record an outcome. Called by anything that talks to Shoko."""
    global _status
    _status = ShokoStatus(state, detail, datetime.now(UTC))


def status() -> ShokoStatus:
    """What the last question produced. Asks nothing itself."""
    return _status


def configured() -> bool:
    """Whether there is a Shoko Server to ask at all.

    In standalone mode the answer is no, whatever the settings say. AniFlow
    owns the library there and Shoko is not in the pipeline, so a URL left
    over from an experiment must not put a Shoko status line in the sidebar
    of an installation that has nothing to do with it - reported as
    *"[it] should be hidden in standalone mode"* (build 137).
    """
    from backend.app.core.mode import AppMode, current_mode
    from backend.app.services.app_settings import load_app_settings

    try:
        if current_mode() is not AppMode.SHOKO_SERVER:
            return False
        settings = load_app_settings()
    except Exception:  # noqa: BLE001 - a status line must not fail on a config read
        return False
    return bool((settings.shoko_url or "").strip() and (settings.shoko_api_key or "").strip())


def check(*, force: bool = False) -> ShokoStatus:
    """Ask Shoko whether it is there, unless something recently did.

    Two small requests: the address without the key, then the key. That is
    what makes "wrong address" and "wrong key" different answers rather than
    one unhelpful one.
    """
    if not configured():
        remember("disabled")
        return _status
    remembered = _status
    # Only an answer from Shoko itself counts as fresh. "unknown" and
    # "disabled" are states about AniFlow's own configuration, and a stale
    # "disabled" is exactly the wrong thing to hold on to: it is what the
    # status said before the URL and key were filled in, and holding it for
    # ten minutes after that meant the sidebar said nothing while Shoko sat
    # there answering.
    if (
        not force
        and remembered.checked_at is not None
        and remembered.state in ("ok", "unauthorised", "unreachable")
        and datetime.now(UTC) - remembered.checked_at < STATUS_TTL
    ):
        return remembered
    try:
        client = client_from_settings()
    except ShokoError as exc:
        remember("disabled", str(exc))
        return _status
    try:
        client.version()
    except ShokoError as exc:
        remember("unreachable", str(exc))
        return _status
    try:
        client.check_key()
    except ShokoError as exc:
        remember("unauthorised", str(exc))
        return _status
    remember("ok")
    return _status


def check_now(*, timeout: float = 2.0) -> str:
    """The cheapest possible "is Shoko there", for a first page load (118).

    Asked for because the status line said *"Shoko Server not checked yet"*
    until something else happened to ask - which on a quiet installation
    could be half an hour: *"is it possible to have when the user access the
    page the first time to get some kind of very fast and very quick check"*.

    So: one request to ``/api/v3/Init/Status``, which needs no API key and
    answers with the version. It is the thinnest thing Shoko exposes; the
    library walk that decides the watchlist colours stays on the worker where
    it cannot hold a page up. A two-second timeout, because this runs while
    somebody is waiting for a page and a slow answer is worth less than a
    fast "not checked yet".
    """
    from backend.app.integrations.shoko.client import ShokoError, client_from_settings

    try:
        client = client_from_settings()
    except ShokoError as exc:
        remember("disabled", str(exc))
        return "disabled"
    client._timeout = timeout  # noqa: SLF001 - this call owns its own patience
    try:
        client.version()
    except ShokoError as exc:
        remember("unreachable", str(exc))
        return "unreachable"
    remember("ok")
    return "ok"


__all__ = [
    "STATUS_TTL",
    "ShokoStatus",
    "check",
    "check_now",
    "configured",
    "remember",
    "status",
]
