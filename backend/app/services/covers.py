"""Covers the user uploads for an anime.

The provider's artwork is a URL the browser fetches directly; an uploaded cover
is a file AniFlow stores and serves. It wins over the provider's image while it
exists, can be removed to fall back, and is deleted with the anime - artwork
for a show that is no longer on the watchlist is nothing but a leak.

Uploads arrive as a raw request body rather than a multipart form: form parsing
would pull in another dependency for a single-file upload, and the browser can
POST a File object directly.
"""
from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component

logger = for_component("Cover")

#: Formats a browser can display and that we can identify from the bytes.
#: The declared content type is a hint from the client; the signature is
#: the evidence, so an executable renamed to .png never reaches the disk.
SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)

#: 8 MB. A 2000px poster is well under one; anything larger is not a cover.
MAX_BYTES = 8 * 1024 * 1024


class CoverError(ValueError):
    """The upload was refused, with a reason meant for the user."""


def covers_dir() -> Path:
    """Where uploaded covers live: ``<data_dir>/covers``."""
    return Path(get_settings().data_dir) / "covers"


def detect_suffix(data: bytes) -> str:
    """The file extension implied by the bytes themselves.

    WebP needs both ends of its header checked - "RIFF" alone is also AVI and
    WAV, which a browser will not render as a cover.
    """
    for signature, suffix in SIGNATURES:
        if data.startswith(signature):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise CoverError("That file is not a PNG, JPEG, GIF or WebP image")


def store_cover(anime_id: str, data: bytes) -> str:
    """Write one cover and return its filename.

    Any previous cover for this anime is replaced, including when the format
    changes, so an upload can never leave two files behind.
    """
    if not data:
        raise CoverError("The upload was empty")
    if len(data) > MAX_BYTES:
        raise CoverError(f"Covers are limited to {MAX_BYTES // (1024 * 1024)} MB")
    suffix = detect_suffix(data)

    directory = covers_dir()
    directory.mkdir(parents=True, exist_ok=True)
    remove_cover_files(anime_id)
    filename = f"{anime_id}{suffix}"
    target = directory / filename
    # Written beside the target and renamed, so a half-written cover is never
    # served: os.replace is atomic within a filesystem.
    staging = directory / f".{filename}.part"
    staging.write_bytes(data)
    staging.replace(target)
    logger.info("stored a %s KB cover for %s", len(data) // 1024, anime_id)
    return filename


def cover_path(anime_id: str, filename: str | None) -> Path | None:
    """The stored cover's path, or None when there is nothing to serve."""
    if not filename:
        return None
    # The filename is ours (``<id><suffix>``), never the client's, but it still
    # gets resolved against the covers folder rather than trusted.
    directory = covers_dir().resolve()
    candidate = (directory / filename).resolve()
    if candidate.parent != directory or not candidate.is_file():
        return None
    return candidate


def remove_cover_files(anime_id: str) -> int:
    """Delete every stored cover for one anime. Returns how many were removed."""
    directory = covers_dir()
    if not directory.is_dir():
        return 0
    removed = 0
    for suffix in {suffix for _, suffix in SIGNATURES} | {".webp"}:
        target = directory / f"{anime_id}{suffix}"
        if target.is_file():
            target.unlink()
            removed += 1
    return removed


__all__ = [
    "MAX_BYTES",
    "CoverError",
    "cover_path",
    "covers_dir",
    "detect_suffix",
    "remove_cover_files",
    "store_cover",
]
