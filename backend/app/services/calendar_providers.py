"""Talking to a metadata source, and keeping the poster (build 149).

The half of ``calendar_sources`` that Calendar v2 uses. That module was the
build-83 comparison experiment - five sources stored side by side so the
numbers could decide which to keep - and it answered its question: the answer
is *all of them, merged*, which is what v2 does. What the experiment's code
still held that v2 needed was this: which adapter talks to which source,
whether a URL is a source's "no artwork" stand-in, and where a downloaded
poster goes.

None of that is about the v1 tables, so it outlives them. Everything here was
moved unchanged; the reasoning in the comments is build 83's and 84's and is
kept because it is still the reason.

**Pictures are downloaded rather than linked.** Several of these hosts serve
images a browser on a different page will not load, and a copy on disk is the
only version that survives the source going away - which is the failure the
whole calendar rewrite is about. Kitsu's URLs are pre-signed and expire within
fifteen minutes of being issued, which settles it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Calendar")

#: Between poster downloads. A season is a couple of hundred images from one
#: host; without this it would be a couple of hundred requests at once, which
#: is indistinguishable from an attack.
PICTURE_PAUSE_SECONDS = 0.2

#: A poster larger than this is not a poster. Enough for a 2000px JPEG.
MAX_PICTURE_BYTES = 8 * 1024 * 1024

#: URLs that are known to be a source's "no artwork" stand-in (build 84).
#: Matched before the download, so a placeholder costs nothing at all.
#:
#: Kitsu's is the subtle one: a real poster's path carries the anime's id
#: (``/anime/50613/poster_image/...``) and the generic one does not
#: (``/anime/poster_image/...``). That is the only thing that distinguishes
#: them - same host, same extension, same everything else.
PLACEHOLDER_URLS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/assets/globals/missing_", re.I),      # Shikimori
    re.compile(r"/anime/poster_image/", re.I),          # Kitsu, with no id
    re.compile(r"/(no[_-]?(image|poster)|placeholder)\.", re.I),
)

#: Image types worth keeping, by their own first bytes - the declared content
#: type is a hint from a server we are evaluating precisely because we do not
#: trust it yet.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)

#: The setting each source needs before it can be asked at all.
CREDENTIALS: dict[str, str] = {"mal": "mal_client_id"}


@dataclass(frozen=True)
class SourceInfo:
    """One calendar source, and what it would take to use it."""

    key: str
    label: str
    #: What is missing before this source can be asked. ``None`` means it can
    #: be asked right now. A sentence rather than a flag: "an application id
    #: AniFlow does not have yet" is the answer to the question the reader is
    #: about to ask.
    needs: str | None = None


#: Every source the calendar asks, in the order the merge prefers them.
#:
#: The same order as ``calendar_metadata.ORDER`` and for the same reason: it
#: is a starting judgement about completeness, not a finding.
SOURCES: tuple[SourceInfo, ...] = (
    SourceInfo(key="anilist", label="AniList"),
    SourceInfo(key="mal", label="MyAnimeList", needs="a MyAnimeList client id"),
    SourceInfo(key="jikan", label="Jikan"),
    SourceInfo(key="kitsu", label="Kitsu"),
    SourceInfo(key="shikimori", label="Shikimori"),
)

_BY_KEY = {info.key: info for info in SOURCES}


def _with_credentials(info: SourceInfo) -> SourceInfo:
    """The same source, with ``needs`` cleared once its key is configured.

    Read through ``load_app_settings()`` rather than ``get_settings()``: the
    second is the environment, and everything a user edits lives in
    ``aniflow.yaml``. Getting that wrong was not a subtle failure - the id sat
    in the file and this kept answering "needs a client id", so the source
    could never be tried.
    """
    setting = CREDENTIALS.get(info.key)
    if setting is None:
        return info
    try:
        from backend.app.services.app_settings import load_app_settings

        configured = getattr(load_app_settings(), setting, "")
    except Exception:  # noqa: BLE001 - a missing config file is not a reason
        configured = getattr(get_settings(), setting, "")  # to hide a source
    if not str(configured or "").strip():
        return info
    return SourceInfo(key=info.key, label=info.label, needs=None)


def sources() -> tuple[SourceInfo, ...]:
    """Every source, with what each one currently needs."""
    return tuple(_with_credentials(info) for info in SOURCES)


def source(key: str) -> SourceInfo | None:
    info = _BY_KEY.get(key)
    return None if info is None else _with_credentials(info)


def usable() -> tuple[SourceInfo, ...]:
    """The sources that can be asked right now."""
    return tuple(info for info in sources() if info.needs is None)


def adapter(key: str):
    """The thing that actually knows how to ask this source.

    Imported per call rather than at module scope: each adapter reads settings
    in its constructor, so none should be built until one is wanted.
    """
    if key == "anilist":
        from backend.app.integrations.metadata.anilist import AniListProvider

        return AniListProvider()
    if key == "kitsu":
        from backend.app.integrations.metadata.kitsu import KitsuProvider

        return KitsuProvider()
    if key == "jikan":
        from backend.app.integrations.metadata.jikan import JikanProvider

        return JikanProvider()
    if key == "shikimori":
        from backend.app.integrations.metadata.shikimori import ShikimoriProvider

        return ShikimoriProvider()
    if key == "mal":
        from backend.app.integrations.metadata.mal import MalProvider

        provider = MalProvider()
        return provider if provider.configured else None
    return None


# -- pictures ---------------------------------------------------------------


def art_dir(key: str) -> Path:
    """``<data>/calendar-art/<source>``.

    One folder per source, which is what build 83 needed to compare the
    artwork. v2 keeps its own copy elsewhere (``calendar_art``); this remains
    because the files are still on disk and some watchlist covers still point
    at them.
    """
    return Path(get_settings().data_dir) / "calendar-art" / key


def _suffix(data: bytes) -> str | None:
    for signature, suffix in _SIGNATURES:
        if data.startswith(signature):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def is_placeholder_url(url: str | None) -> bool:
    """Whether this URL is a source's stand-in rather than a poster."""
    if not url:
        return False
    return any(pattern.search(url) for pattern in PLACEHOLDER_URLS)


async def download_picture(
    client: httpx.AsyncClient, key: str, source_id: str, url: str
) -> str | None:
    """Fetch one poster and return its filename, or ``None`` if it failed.

    Never raises. A missing picture is a fact about the source worth
    recording; it is not a reason for an update of two hundred shows to stop.
    """
    directory = art_dir(key)
    directory.mkdir(parents=True, exist_ok=True)
    # Named by the source's own id, so a re-run replaces the file instead of
    # accumulating a copy per run.
    existing = sorted(directory.glob(f"{source_id}.*"))
    try:
        response = await client.get(url)
        if response.is_error:
            return existing[0].name if existing else None
        data = response.content
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("%s: poster for %s could not be fetched: %s", key, source_id, exc)
        return existing[0].name if existing else None

    if not data or len(data) > MAX_PICTURE_BYTES:
        return None
    suffix = _suffix(data)
    if suffix is None:
        logger.debug("%s: poster for %s is not an image AniFlow can show", key, source_id)
        return None
    for stale in existing:
        if stale.suffix != suffix:
            stale.unlink(missing_ok=True)
    target = directory / f"{source_id}{suffix}"
    target.write_bytes(data)
    return target.name


def picture_path(key: str, source_id: str) -> Path | None:
    """The stored poster for one source's id, if there is one on disk."""
    found = sorted(art_dir(key).glob(f"{source_id}.*"))
    return found[0] if found else None


__all__ = [
    "MAX_PICTURE_BYTES",
    "PICTURE_PAUSE_SECONDS",
    "SOURCES",
    "SourceInfo",
    "adapter",
    "art_dir",
    "download_picture",
    "is_placeholder_url",
    "picture_path",
    "source",
    "sources",
    "usable",
]
