"""Which *edition* of an episode a release is (build 97).

Reported against build 96, with three real files for one episode:

    [ToonsHub] … S01E01 1080p AMZN WEB-DL DDP2.0 H.264 (…, English-Sub, CENSORED)
    [ToonsHub] … S01E01 1080p UNCENSORED ADN WEB-DL AAC2.0 H.264 (…, Multi-Subs)
    [ToonsHub] … S01E01 1080p UNCENSORED ADN WEB-DL DUAL AAC2.0 H.264 (…, Dual-Audio, Multi-Subs)

Same group, same quality, same episode, three different files - and the middle
two parsed *identically*, because everything that distinguished them was
thrown away before AniFlow saw it. So one row offered thirty-one files for a
twelve-episode show and the group looked broken.

**Two layers, and the second one is what makes this last.**

A named vocabulary alone (below) would only ever recognise the labels that
exist today. The question asked when this was designed was the right one:
*there could be a future anime with a newer tag, which we do not know yet.*

So the tags of a release are **everything in its name that nothing else
already accounted for** - the title, the episode, the season, the group, the
quality, the codecs, the checksum and the container are all consumed by the
parser, and whatever words are left are what this file says about itself that
its neighbours may not. A label nobody has invented yet lands in there under
its own name, and two files that differ by it are two editions.

The named vocabulary then does one job on top of that: it *canonicalises*.
"DUAL" and "Dual-Audio" are one thing rather than two, "UNCENSORED" is
understood as being about censorship rather than an opaque word, and a stored
rule keeps meaning the same thing when a group changes its spelling. A word
outside the vocabulary is kept verbatim, so it still distinguishes - it simply
reads as itself instead of as a friendly name.

**What is *discriminating* is not decided here.** A tag every file in a group
carries tells you nothing about which copy to take, and a tag on one single
episode ("END", a one-off note) is not an edition either. That judgement needs
the whole set of files for a group, which is the caller's to hold - see
``frontend/src/editions.ts``. This module answers one question about one name.

The canonical forms are what a profile stores, so they must stay stable: a
stored rule saying ``dual-audio`` has to keep meaning that after this file is
edited. Adding a synonym is safe; renaming a canonical form is not.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.app.services.release_parser import ParsedRelease

#: canonical marker -> the patterns that mean it, as whole words in the name.
#:
#: Only the ones worth naming: each is something a person would say "I want
#: that one" about, and each has spellings that need reconciling. Everything
#: else a name carries is kept verbatim by :func:`tags_in`.
#:
#: The negative lookbehind on ``censored`` is doing real work: "UNCENSORED"
#: contains "CENSORED". That was also the original defect - the vendored
#: parser reports only whether a name says "uncensored" and has no notion of
#: one that says the opposite, so the censored copy came through as "does not
#: say" and shared a row with the uncensored one.
_MARKERS: dict[str, tuple[str, ...]] = {
    # What has been done to the picture.
    "uncensored": (r"un\-?censored",),
    "censored": (r"(?<!un)(?<!un\-)censored",),
    # What you can hear. "DUAL" alone is how ToonsHub writes it mid-name;
    # "Dual-Audio" is how they write it in the note at the end.
    "dual-audio": (r"dual[\s\-_.]?audio", r"dual"),
    # What you can read. Kept apart because "everything" and "English only"
    # are a real choice for anyone who watches with a second language.
    "multi-subs": (r"multi[\s\-_.]?subs?", r"multiple[\s\-_.]?subtitles?"),
    "english-sub": (r"eng(?:lish)?[\s\-_.]?subs?",),
    "english-dub": (r"eng(?:lish)?[\s\-_.]?dub(?:bed)?",),
    # Where it came from. Not the media source - "WEB-DL" is all of these -
    # but the platform, which decides the picture and the tracks on offer.
    "amzn": (r"amzn", r"amazon"),
    "adn": (r"adn", r"animation\s?digital\s?network"),
    "crunchyroll": (r"crunchyroll", r"cr(?=[\s\-_.\]])"),
    "netflix": (r"netflix", r"nf(?=[\s\-_.\]])"),
    "disney": (r"dsnp", r"disney\+?"),
    "hidive": (r"hidive",),
    "abema": (r"abema",),
    "bilibili": (r"b\-?global", r"bilibili"),
    "hulu": (r"hulu",),
    # A re-release of the same episode, which is not a new version: v2 means
    # the group fixed the encode and expects you to replace it. Named so it
    # can be *seen*, and deliberately left out of the edition key - a repack
    # is the same edition, re-uploaded.
    "repack": (r"repack", r"proper"),
}

#: Built once. Anchored so "adn" cannot match inside "Kadano" and "dual"
#: cannot match inside "dualism".
_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    marker: tuple(
        re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", re.IGNORECASE)
        for pattern in patterns
    )
    for marker, patterns in _MARKERS.items()
}

#: Markers that are two answers to one question, best evidence first: a name
#: saying both "UNCENSORED" and "CENSORED" describes the uncensored copy of a
#: censored broadcast.
_EXCLUSIVE: tuple[tuple[str, ...], ...] = (("uncensored", "censored"),)

#: Never an edition, however much they vary between files. Nobody chooses
#: between these, and letting them in would split one row into five that
#: differ in nothing anybody cares about.
#:
#: ``end`` and ``final`` are the interesting entries: they appear on the last
#: episode only, so they vary within a group without describing a copy. The
#: caller's "a tag on one episode is not an edition" rule would catch them
#: anyway; naming them here means it does not have to.
_NEVER: frozenset[str] = frozenset(
    {
        # Containers and wrappers.
        "mkv", "mp4", "avi", "mov", "ts", "m2ts", "iso", "ogm", "wmv", "rmvb",
        "flv", "webm", "torrent",
        # Picture and sound detail the parser already reports, or nobody picks.
        "10bit", "8bit", "12bit", "hi10", "hi10p", "hi444", "hdr", "sdr",
        "aac", "ac3", "eac3", "ddp", "dd", "dts", "flac", "opus", "truehd",
        "h264", "h265", "x264", "x265", "hevc", "avc", "av1", "vp9",
        "web", "webdl", "webrip", "bluray", "bdrip", "hdtv", "dvd", "remux",
        # Episode housekeeping.
        "end", "final", "fin", "batch", "complete", "raw", "uncut",
        "v0", "v1", "v2", "v3", "v4",
        # Words that say nothing on their own.
        "the", "of", "and", "a", "an", "no", "wa", "ga", "ni", "de", "to",
        "subs", "sub", "subbed", "dub", "dubbed", "multi", "eng", "jpn",
        "season", "episode", "ep", "part", "cour", "movie", "ova", "special",
    }
)

#: A token has to look like a word to be a tag. A checksum, a date, a file
#: size and a resolution are all "varies between files and means nothing".
_NOISE_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE),      # CRC32
    re.compile(r"^\d+$"),                              # any bare number
    re.compile(r"^\d+(?:[.,]\d+)?(?:gb|mb|gib|mib|kbps|fps|bit|p|k)$", re.IGNORECASE),
    re.compile(r"^s\d{1,2}(?:e\d{1,3})?$", re.IGNORECASE),   # S01, S01E01
    re.compile(r"^e\d{1,3}$", re.IGNORECASE),
    re.compile(r"^v\d{1,2}$", re.IGNORECASE),
    re.compile(r"^\d{3,4}x\d{3,4}$", re.IGNORECASE),   # 1920x1080
    re.compile(r"^(?:19|20)\d{2}$"),                   # a year
)

#: Parsed fields that are also edition axes (build 98).
#:
#: The parser reconciles the spellings already - x265, h265 and HEVC are one
#: value - so these arrive canonical, which is what a stored rule needs. They
#: are *not* residue: something did account for them. They are here because
#: being accounted for is not the same as not being a choice, and one group
#: publishing both a 1.4 GiB AVC WEB-DL and a 458 MiB HEVC WEBRip of the same
#: episode is offering two things.
_FIELD_AXES = ("video_codec", "media_source")

#: How the parser's values read as tags. Anything not named here is used as
#: the parser spelt it.
_FIELD_NAMES = {"web_dl": "web-dl", "h264": "avc", "h265": "hevc"}

#: How a stored edition is written down and read back.
SEPARATOR = "+"

#: Words are split on everything that separates them in a release name.
_TOKEN = re.compile(r"[^0-9A-Za-zÀ-ɏ+]+")


def markers_in(raw_name: str) -> tuple[str, ...]:
    """Every *named* marker this name carries, canonical and sorted.

    Layer one. Sorted rather than in the order they appear: this is a set of
    facts about a file, and two files differing only in word order are the
    same edition.
    """
    if not raw_name:
        return ()
    found = {
        marker
        for marker, patterns in _COMPILED.items()
        if any(pattern.search(raw_name) for pattern in patterns)
    }
    for group in _EXCLUSIVE:
        present = [marker for marker in group if marker in found]
        for loser in present[1:]:
            found.discard(loser)
    return tuple(sorted(found))


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _TOKEN.split(text or "") if word}


def _is_noise(token: str) -> bool:
    if len(token) < 2 or token in _NEVER:
        return True
    # A codec with its channel count glued on: "DDP2.0" and "AAC2.0" split
    # into "ddp2" and "aac2", which are the same non-choice as "ddp" and
    # "aac". The audio codec is a parsed field of its own; it is not an
    # edition.
    if re.sub(r"\d+$", "", token) in _NEVER:
        return True
    return any(shape.match(token) for shape in _NOISE_SHAPES)


def tags_in(raw_name: str, parsed: "ParsedRelease | None" = None) -> tuple[str, ...]:
    """Everything this name says about itself that nothing else accounted for.

    Layer two, and the reason this survives a label nobody has invented yet.
    The named markers come back canonical; every other word that the parser
    did not consume comes back as itself.

    What is subtracted is what something else already knows: the title, the
    group, the episode and season, the quality, the codecs, the source and the
    checksum. What is left is the part of the name that can differ between two
    files of the same episode - which is the definition of an edition.

    Whether a tag actually *discriminates* is not decided here; that needs the
    whole set of files for a group. See the module docstring.
    """
    if not raw_name:
        return ()
    named = markers_in(raw_name)
    # Every word the named markers matched, so they are not also reported raw.
    spoken_for: set[str] = set()
    for marker in named:
        for pattern in _COMPILED[marker]:
            for hit in pattern.finditer(raw_name):
                spoken_for |= _words(hit.group(0))
    if parsed is not None:
        for value in (
            parsed.title,
            parsed.subgroup,
            parsed.episode,
            parsed.resolution,
            parsed.media_source,
            parsed.video_codec,
            parsed.audio_codec,
            parsed.crc32,
            str(parsed.season) if parsed.season is not None else None,
        ):
            spoken_for |= _words(value or "")
        spoken_for |= {tag.casefold() for tag in (parsed.unknown_tags or ())}

    residue = sorted(
        token
        for token in _words(raw_name) - spoken_for
        if not _is_noise(token)
    )
    fields = []
    if parsed is not None:
        for axis in _FIELD_AXES:
            value = (getattr(parsed, axis, None) or "").strip().lower()
            if value:
                fields.append(_FIELD_NAMES.get(value, value))
    return tuple(sorted({*named, *residue, *fields}))


def edition_key(tags: tuple[str, ...] | list[str]) -> str:
    """The tags as one comparable string, ``""`` for a plain release.

    ``repack`` is left out on purpose: a repack is the *same* edition of the
    same episode, re-uploaded, and a key including it would put the fixed file
    in a row of its own and offer it as a different thing to choose.
    """
    return SEPARATOR.join(tag for tag in sorted(tags) if tag != "repack")


def censored_from(tags: tuple[str, ...] | list[str]) -> bool | None:
    """Whether the name says it is censored. ``None`` when it says neither."""
    if "uncensored" in tags:
        return False
    if "censored" in tags:
        return True
    return None


def describe(tags: tuple[str, ...] | list[str]) -> str:
    """The tags as something to put on a row. Empty for a plain release."""
    return " · ".join(tag for tag in sorted(tags) if tag != "repack")


__all__ = [
    "SEPARATOR",
    "censored_from",
    "describe",
    "edition_key",
    "markers_in",
    "tags_in",
]
