"""File extension extraction.

This runs *before* normalisation and tokenisation: the extension is the single
most reliable piece of a filename, and removing it first stops ``.mkv`` from
polluting title analysis.  It is a plain function rather than a
:class:`~.base.Rule` because it operates on raw text instead of tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "KNOWN_EXTENSIONS",
    "NEVER_EXTENSIONS",
    "extract_extension",
    "ExtensionResult",
]

#: Extensions recognised without complaint.  Anything else that *looks* like an
#: extension is still extracted, but produces a warning so unknown containers
#: surface instead of silently changing the parse.
KNOWN_EXTENSIONS = frozenset(
    {
        # video -- the three that matter for anime are mkv, mp4 and avi
        "mkv", "mk3d", "mp4", "avi", "webm", "mov", "m4v", "mpv", "wmv",
        "flv", "mpg", "mpeg", "mpe", "m1v", "m2v", "m4p", "ogm", "ogv",
        "ogx", "rmvb", "rm", "ts", "m2ts", "mts", "tp", "vob", "ifo",
        "divx", "3gp", "3g2", "asf", "f4v", "gif", "mxf", "y4m", "amv",
        "swf", "qt", "dat", "avs", "bdmv", "iso", "img",
        # audio
        "mka", "mp3", "flac", "aac", "ac3", "eac3", "dts", "opus", "wav",
        "m4a", "m4b", "ogg", "oga", "wma", "aiff", "ape", "tta", "wv",
        # subtitles / sidecars / metadata
        "ass", "ssa", "srt", "sub", "idx", "sup", "vtt", "smi", "lrc",
        "nfo", "txt", "md5", "sfv", "cue", "chapters", "xml", "json",
        "torrent", "jpg", "jpeg", "png", "webp", "bmp", "tbn",
        # archives
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst",
    }
)

#: Short words that turn up at the end of a title or a release tag and must
#: never be mistaken for an unknown container.  Only consulted for extensions
#: that are *not* in :data:`KNOWN_EXTENSIONS` -- ``.sub`` and ``.ts`` stay
#: real extensions.
NEVER_EXTENSIONS = frozenset(
    {
        "raw", "web", "bd", "br", "hd", "sd", "dvd", "tv", "ova", "oad",
        "ona", "nc", "ncop", "nced", "op", "ed", "pv", "cm", "hen", "san",
        "kun", "chan", "sama", "the", "and", "end", "war", "zero", "one",
        "two", "gate", "note", "live", "next", "dub", "eng", "jpn", "vol",
        "pt", "ep", "cr", "nf", "amzn", "adn", "mx", "hdr", "dv", "avc",
        "hevc", "av1", "vp9", "aac", "ac3", "dd", "ddp", "x264", "x265",
    }
)

#: Wide enough for every known container (``torrent``, ``chapters``).
_EXTENSION_RE = re.compile(r"\.(?P<ext>[A-Za-z][A-Za-z0-9]{0,7})$")
#: An *unknown* trailing segment is only believed if it is extension shaped:
#: two to four characters.  That keeps ``Pokemon.the.Movie`` a title.
_PLAUSIBLE_UNKNOWN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,3}$")
#: ... and, on top of that, it must carry a digit (``mkv2``).  Letters alone are
#: indistinguishable from a title word once a filename is dot-separated and
#: lower case: ``kill.la.kill`` ends in ``kill``, ``air.gear`` in ``gear``, and
#: both used to lose that word to a guessed extension.  A new all-letter
#: container is better added to :data:`KNOWN_EXTENSIONS` than guessed at.
_FILENAME_SHAPED_RE = re.compile(r"^[A-Za-z][A-Za-z]*\d[A-Za-z0-9]*$")


@dataclass(frozen=True)
class ExtensionResult:
    """Outcome of :func:`extract_extension`."""

    stem: str
    extension: str | None
    warning: str | None = None


def extract_extension(filename: str) -> ExtensionResult:
    """Split ``filename`` into a stem and a lower-cased extension.

    The extension is returned *without* the leading dot (``"mkv"``).  When the
    trailing segment is not a known container it is only treated as an
    extension if it is shaped like one, and a warning is emitted.

    >>> extract_extension("Pokemon the Movie.mkv")
    ExtensionResult(stem='Pokemon the Movie', extension='mkv', warning=None)
    >>> extract_extension("[Anime Time] Tekken - Bloodline (Season 1)").extension is None
    True
    """
    trimmed = filename.rstrip()
    match = _EXTENSION_RE.search(trimmed)
    if match is None:
        return ExtensionResult(stem=filename, extension=None)

    candidate = match.group("ext")
    lowered = candidate.lower()
    stem = trimmed[: match.start()]
    if not stem.strip():
        # Something like ".mkv" on its own: keep the filename intact.
        return ExtensionResult(stem=filename, extension=None)

    if lowered in KNOWN_EXTENSIONS:
        return ExtensionResult(stem=stem, extension=lowered)

    if not _PLAUSIBLE_UNKNOWN_RE.match(candidate):
        # A long trailing word such as ``Pokemon.the.Movie``: part of the title.
        return ExtensionResult(stem=filename, extension=None)

    if not _FILENAME_SHAPED_RE.match(candidate) or lowered in NEVER_EXTENSIONS:
        # A short *title* word: ``Fate.Zero``, ``Aria.the.Origination.RAW``.
        return ExtensionResult(stem=filename, extension=None)

    return ExtensionResult(
        stem=stem,
        extension=lowered,
        warning=f"Unrecognised file extension {candidate!r}; treated as an extension.",
    )
