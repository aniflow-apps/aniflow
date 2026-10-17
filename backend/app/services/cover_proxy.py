"""Provider artwork, fetched by AniFlow instead of by the visitor's browser.

A cover is a URL on somebody else's image server (AniList, Kitsu, MyAnimeList,
Shikimori, AniDB). Handing that URL to the browser makes the browser ask that
server directly, which tells it the visitor's address, browser and the page they
were on - for every poster, on every visit. For a tool other people run on their
own machines that is not ours to give away.

So the browser asks AniFlow, AniFlow asks the provider once - through the
configured proxy, like every other request that leaves for the internet - and
keeps the picture. After that the provider never hears from the visitor.

Two rules keep this from becoming something else:

* **Only provider hosts.** The address comes from the caller, so an open fetcher
  would let any logged-in page make this server request anything it can reach,
  its own LAN included. The list below is the whole of what may be fetched, over
  https, by name - never an address, never a redirect to somewhere else.
* **Only pictures.** What comes back is identified from its bytes, the same as an
  upload, and anything else is dropped rather than stored or served.
"""
from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.services.covers import MAX_BYTES, CoverError, detect_suffix

logger = for_component("Cover")

#: A host is allowed when it *is* one of these or ends in ``.`` + one of these.
#: These are the image hosts of the metadata sources AniFlow reads. Adding a
#: provider means adding its host here, on purpose.
ALLOWED_HOSTS: tuple[str, ...] = (
    "anilist.co",
    "kitsu.io",
    "kitsu.app",
    "myanimelist.net",
    "jikan.moe",
    "shikimori.one",
    "shikimori.io",
    "shikimori.me",
    "anidb.net",
)

TIMEOUT_SECONDS = 15.0


class CoverRefused(ValueError):
    """The address is not one AniFlow will fetch."""


class CoverUnavailable(RuntimeError):
    """The provider did not give a usable picture."""


def cache_dir() -> Path:
    """Where fetched provider covers are kept: ``<data_dir>/cover-cache``.

    Apart from ``covers/``, which holds what the user uploaded and is backed up:
    this folder can be deleted at any time and refills itself.
    """
    return Path(get_settings().data_dir) / "cover-cache"


def check_url(url: str) -> str:
    """The address, if it names an image host on the list; otherwise ``CoverRefused``."""
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https":
        raise CoverRefused("only https addresses are fetched")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        raise CoverRefused("not an address")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CoverRefused("an address, not a provider's name")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
        raise CoverRefused("not a metadata provider's image host")
    return parts.geturl()


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cached(url: str) -> Path | None:
    """The stored copy of this address, if there is one."""
    directory = cache_dir()
    if not directory.is_dir():
        return None
    found = sorted(directory.glob(f"{_key(url)}.*"))
    return found[0] if found and not found[0].name.endswith(".part") else None


async def fetch(url: str) -> Path:
    """The picture at this address, from the cache or fetched once and kept.

    Raises ``CoverRefused`` for an address that is not allowed and
    ``CoverUnavailable`` when the provider could not supply an image.
    """
    url = check_url(url)
    existing = cached(url)
    if existing is not None:
        return existing

    from backend.app.core.version import user_agent
    from backend.app.services.outbound import client_kwargs

    try:
        # No redirects: a provider's CDN answers directly, and following one
        # would be a way out of the list above.
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": user_agent()},
            **client_kwargs(url),
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise CoverUnavailable(f"could not be fetched: {str(exc)[:80]}") from exc
    if response.status_code != 200:
        raise CoverUnavailable(f"the provider answered {response.status_code}")
    data = response.content
    if not data or len(data) > MAX_BYTES:
        raise CoverUnavailable("empty or too large")
    try:
        suffix = detect_suffix(data)
    except CoverError as exc:
        raise CoverUnavailable("not an image") from exc

    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{_key(url)}{suffix}"
    # Beside the target and renamed, so a half-written picture is never served.
    staging = directory / f".{target.name}.part"
    staging.write_bytes(data)
    staging.replace(target)
    return target


__all__ = [
    "ALLOWED_HOSTS",
    "CoverRefused",
    "CoverUnavailable",
    "cache_dir",
    "cached",
    "check_url",
    "fetch",
]
