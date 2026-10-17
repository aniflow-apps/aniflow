"""Release names, in AniFlow's canonical fields.

The reading is done by the vendored ``anime_filename_parser`` (see
``backend/app/vendor/README.md``); this module is the adapter that maps its
result onto :class:`ParsedRelease` and keeps AniFlow's own decisions - what
counts as a source, when an episode label is withheld, how "censored" is
recorded - in one place.

It never guesses: anything the engine recognised but has no field for, and
anything it could not classify, is preserved in ``unknown_tags`` so downstream
matching stays deterministic and auditable.

AniFlow parsed names itself until 0.0.64. Measured over 95 real release names,
that parser missed 20 episode numbers the engine reads and got 5 wrong -
including reading ``264`` out of ``H.264`` - so a release whose name carried a
codec was filed as episode 264, and one whose name used ``S01E02`` was not
recognised at all.
"""
import re
from dataclasses import dataclass, field, replace

from backend.app.vendor.anime_filename_parser import parse as _engine_parse

# Filesystem extensions that are stripped before parsing. A fixed set keeps
# the decision deterministic instead of stripping any short ".xyz" tail.
_FILE_EXTENSIONS = {
    "mkv", "mp4", "m4v", "avi", "mov", "wmv", "flv", "ogm", "ts", "m2ts", "mpg", "mpeg", "iso",
}

_GROUP_PATTERN = re.compile(r"\[([^\[\]]*)\]|\(([^\(\)]*)\)")

_RESOLUTION_SEARCH = re.compile(r"(4320|2160|1440|1080|720|540|480|360)p")
_VERSION_TAG = re.compile(r"v\s?(\d{1,2})")
# Optional version suffix after an episode number; matches "01 v2", "01v2"
# and "01version3" alike.
_VERSION_SUFFIX_IN_REMAINDER = r"(?:\s*v(?:ersion)?\s*(?P<version>\d{1,2}))?"

# Tail forms, most specific first. Each yields named groups: title, episode,
# optionally version. The lookbehinds stop markers from matching inside
# words ("Movie 1988" must not split at its trailing "e").
_TAIL_PATTERNS = [
    # "Title S01E05(-06)" must run before the plain EP marker, whose bare
    # "e" alternative would otherwise split inside the season token.
    re.compile(
        r"^[\s_\-]*(?P<title>.*?)[\s_\-.]*(?<![\w])s\d{1,2}\s?e(?P<episode>\d{1,3}(?:\s*[-~]\s*\d{1,3})?)"
        + _VERSION_SUFFIX_IN_REMAINDER + r"\s*$",
        re.IGNORECASE,
    ),
    # "Title EP08" / "Title E8" / "Title Episode 08"
    re.compile(
        r"^[\s_\-]*(?P<title>.*?)[\s_\-.]*(?<![\w])(?:eps|episodes|episode|ep|e)\s*\.?\s*"
        r"(?P<episode>\d{1,4}(?:\.\d)?)" + _VERSION_SUFFIX_IN_REMAINDER + r"\s*$",
        re.IGNORECASE,
    ),
    # "Title - 01" / "Title_02" / "Title 01-04" / "Title - 01v2"
    re.compile(
        r"^[\s_\-]*(?P<title>.*?)[\s_\-.]+(?P<episode>\d{1,4}(?:\.\d)?(?:\s*[-~]\s*\d{1,4}(?:\.\d)?)?)"
        + _VERSION_SUFFIX_IN_REMAINDER + r"\s*$",
        re.IGNORECASE,
    ),
]

# Canonical media sources and the synonyms that map onto them.
_SOURCE_SYNONYMS: dict[str, str] = {}
for _canonical, _synonyms in {
    "web_dl": {"web-dl", "webdl", "web dl"},
    "webrip": {"web-rip", "web rip"},
    "web": {"web", "streaming", "stream"},
    "remux": {"remux", "blu-ray remux", "bluray remux", "bd remux"},
    "bluray": {"blu-ray", "bluray", "bd", "bdmv", "bdrip", "bdrmux"},
    "hdtv": {"hdtv", "hd-tv"},
    "dvd": {"dvd", "dvdrip", "dvd-rw"},
    "tv": {"tv"},
    "vhs": {"vhs"},
}.items():
    for _synonym in _synonyms:
        _SOURCE_SYNONYMS[_synonym] = _canonical

_VIDEO_CODECS: dict[str, str] = {}
for _canonical, _synonyms in {
    "h264": {"h264", "x264", "avc", "h.264", "x.264"},
    "h265": {"h265", "x265", "hevc", "h.265", "x.265"},
    "vp9": {"vp9"},
    "av1": {"av1"},
    "mpeg2": {"mpeg2", "mpeg-2"},
    "xvid": {"xvid"},
}.items():
    for _synonym in _synonyms:
        _VIDEO_CODECS[_synonym] = _canonical

_AUDIO_CODECS: dict[str, str] = {}
for _canonical, _synonyms in {
    "aac": {"aac", "aac-lc", "he-aac"},
    "flac": {"flac"},
    "ac3": {"ac3", "ac-3", "dd", "dolby digital"},
    "eac3": {"eac3", "e-ac-3", "e-ac3", "dd+"},
    "dts": {"dts", "dts-hd", "dtshd", "dca"},
    "truehd": {"truehd", "true-hd"},
    "opus": {"opus"},
    "vorbis": {"vorbis"},
    "mp3": {"mp3", "lame"},
    "pcm": {"pcm", "lpcm"},
}.items():
    for _synonym in _synonyms:
        _AUDIO_CODECS[_synonym] = _canonical

# Tags that carry no profile-relevant information and are dropped silently.
_IGNORED_TAGS = {
    "",
    "mkv", "mp4", "avi", "mov", "ts", "m2ts", "iso", "ogm", "wmv", "rmvb", "flv",
    "10bit", "8bit", "hi10", "hi10p", "hi444", "12bit",
    "eng", "jpn", "jap", "ger", "fre", "spa", "multi", "multiple subtitle", "multiple subtitles",
    "sub", "subs", "subbed", "dub", "dubbed", "dual audio", "dualaudio",
    "batch", "complete", "season 1", "s01", "repack", "proper", "internal",
    "nhk", "nhke", "ntv", "tbs", "cx", "mx", "tx", "abc", "bs11", "tvo", "mbs", "at-x", "atx",
}
_CRC32_TAG = re.compile(r"[0-9A-Fa-f]{8}")
_YEAR_TAG = re.compile(r"(?:19|20)\d{2}")
_WXH_RESOLUTION = re.compile(r"1920x1080|3840x2160|2560x1440|1280x720", re.IGNORECASE)
_FOUR_K = re.compile(r"\b4k\b", re.IGNORECASE)

# Compound source spellings that survive whitespace/hyphen removal.
_SQUASHED_SOURCES: dict[str, str] = {}
for _canonical, _synonyms in {
    "remux": {"blu-ray remux", "bluray remux", "bd remux", "blu-rayremux"},
    "web_dl": {"web-dl", "webdl"},
}.items():
    for _synonym in _synonyms:
        _SQUASHED_SOURCES[re.sub(r"[\s\-]", "", _synonym)] = _canonical


@dataclass(frozen=True)
class ParsedRelease:
    """Canonical fields extracted from one release name."""

    raw_name: str
    subgroup: str | None = None
    title: str | None = None
    episode: str | None = None
    #: The range a batch covers, when ``episode`` is one ("01-12") rather than
    #: a single number - the vendored parser's own ``episode_start``/
    #: ``episode_end``, carried through as ints because ``episode`` is a
    #: display label first (see ``_episode_label``) and re-parsing it back out
    #: of "01-12" would be the same information read twice. Both are None for
    #: anything that is not a batch.
    episode_start: int | None = None
    episode_end: int | None = None
    version: int = 1
    resolution: str | None = None
    media_source: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    censored: bool | None = None
    season: int | None = None
    crc32: str | None = None
    unknown_tags: tuple[str, ...] = field(default_factory=tuple)
    #: Everything the name says about itself that no other field accounted
    #: for: "uncensored", "dual-audio", "amzn", and any label nobody has
    #: invented yet. Canonical where AniFlow knows the word, verbatim where it
    #: does not. See services/release_edition.py.
    #:
    #: Called tags rather than edition because an *edition* is the subset that
    #: actually distinguishes one copy from another, and deciding that needs
    #: every file of a group - which is the caller's to hold.
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def edition_key(self) -> str:
        """The tags as one comparable string; empty for a plain release."""
        from backend.app.services.release_edition import edition_key

        return edition_key(self.tags)


def _normalize_token(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", " ").replace("-", " ").strip().lower())


def _strip_extension(name: str) -> str:
    if "." in name:
        stem, _, extension = name.rpartition(".")
        if extension.lower() in _FILE_EXTENSIONS and stem:
            return stem
    return name


def _classify_group(content: str) -> tuple[str, str]:
    """Classify one bracketed or parenthesised tag.

    Returns ``(role, value)`` where role is one of ``ignore``, ``resolution``,
    ``version``, ``source``, ``video``, ``audio``, ``censor`` or ``unknown``.
    """
    normalized = _normalize_token(content)
    crc_match = _CRC32_TAG.fullmatch(content.strip())
    if crc_match:
        return "crc", crc_match.group(0).upper()
    if normalized in _IGNORED_TAGS:
        return "ignore", ""
    if _VERSION_TAG.fullmatch(normalized):
        return "version", _VERSION_TAG.fullmatch(normalized).group(1)
    if normalized.startswith("version ") and normalized[len("version "):].isdigit():
        return "version", normalized[len("version "):]
    if _RESOLUTION_SEARCH.search(normalized):
        return "resolution", f"{_RESOLUTION_SEARCH.search(normalized).group(1)}p"
    if _WXH_RESOLUTION.search(normalized):
        height = _WXH_RESOLUTION.search(normalized).group(0).split("x")[1]
        return "resolution", f"{height}p"
    if _FOUR_K.fullmatch(normalized):
        return "resolution", "2160p"
    if normalized in _SOURCE_SYNONYMS:
        return "source", _SOURCE_SYNONYMS[normalized]
    if normalized in _VIDEO_CODECS:
        return "video", _VIDEO_CODECS[normalized]
    if normalized in _AUDIO_CODECS:
        return "audio", _AUDIO_CODECS[normalized]
    squashed = re.sub(r"[\s\-._]", "", normalized)
    if squashed == "censored":
        return "censor", "censored"
    if squashed == "uncensored":
        return "censor", "uncensored"
    if squashed in _SQUASHED_SOURCES:
        return "source", _SQUASHED_SOURCES[squashed]
    # Secondary attempt for channel-suffixed audio tags such as "[AAC2]" or
    # "(DD5.1)"; only applied when the exact forms above did not match.
    channel_stripped = re.sub(r"\s*\d(?:[.,]\d)?$", "", normalized)
    if channel_stripped != normalized and channel_stripped in _AUDIO_CODECS:
        return "audio", _AUDIO_CODECS[channel_stripped]
    if _YEAR_TAG.fullmatch(normalized):
        return "ignore", ""
    return "unknown", content.strip()


_BARE_EPISODE_REMAINDER = re.compile(
    r"[\s_\-.(]*(?:e(?:ps?|pisode)?)?[\s_\-.]*\d{1,4}(?:\.\d)?(?:\s*[-~]\s*\d{1,4}(?:\.\d)?)?"
    r"(?:\s*v(?:ersion)?\s*\d{1,2})?[\s_\-.)]*$",
    re.IGNORECASE,
)


def _extract_tail(remainder: str) -> tuple[str | None, str | None, int, int]:
    """Extract ``(title, episode, version, title_offset)`` from free text.

    ``title_offset`` is the index into ``remainder`` where the cleaned title
    begins, ignoring leading separators. When nothing but an episode marker
    remains, ``title`` is ``None`` while the episode is still reported.
    """
    bare_episode: str | None = None
    bare_version = 1
    for pattern in _TAIL_PATTERNS:
        match = pattern.match(remainder)
        if not match:
            continue
        raw_title = match.group("title")
        cleaned_title = raw_title.strip().strip("-_. ").rstrip()
        episode = re.sub(r"\s+", "", match.group("episode"))
        version = int(match.group("version")) if match.group("version") else 1
        if len(cleaned_title) < 2:
            if bare_episode is None:
                bare_episode, bare_version = episode, version
            continue
        leading_junk = len(raw_title) - len(raw_title.lstrip(" \t_-.( "))
        return cleaned_title, episode, version, match.start("title") + leading_junk
    if bare_episode is not None and _BARE_EPISODE_REMAINDER.match(remainder):
        return None, bare_episode, bare_version, 0
    return None, None, 1, 0


def _sweep_trailing_tech(working_name: str) -> tuple[str, dict[str, str], int | None, str | None]:
    """Strip trailing technical tokens from a bracket-less (scene-style) name.

    Scene names carry resolution/source/codec tags as dotted tokens after the
    episode number, and the release group often hangs off the last technical
    token as a hyphen suffix (``...x264-GRP``). Only exact canonical
    classifications are removed; anything unclassifiable ends the sweep so
    titles are never eaten. Returns ``(remainder, fields, version, group)``.
    """
    fields: dict[str, str] = {}
    version_found: int | None = None
    group: str | None = None

    def record(role: str, value: str) -> None:
        nonlocal version_found
        if role == "version":
            version_found = int(value) if version_found is None else max(version_found, int(value))
        elif role != "ignore":
            fields.setdefault(role, value)

    tokens = working_name.split()
    while tokens:
        token = tokens[-1]
        # A trailing "<word> -" / "- <word>" pair is a scene-style release
        # group tag; claim the word and continue sweeping past the dash.
        if group is None and len(tokens) >= 2 and token != "-" and not token.isdigit():
            neighbour = tokens[-2]
            if (neighbour == "-" or neighbour.endswith("-")) and _classify_group(token)[0] == "unknown":
                candidate = neighbour[:-1] if neighbour != "-" else ""
                if not candidate or _classify_group(candidate)[0] != "unknown":
                    group = token
                    del tokens[-2:]
                    continue
        role, value = _classify_group(token)
        if role != "unknown":
            record(role, value)
            tokens.pop()
            continue
        if token == "-":
            tokens.pop()
            continue
        if "-" in token.strip("-"):
            left, _, right = token.rpartition("-")
            left_role, left_value = _classify_group(left)
            right_role, right_value = _classify_group(right)
            if left_role == "unknown" and right_role != "unknown":
                record(right_role, right_value)
                if group is None and left and not left.isdigit():
                    group = left.replace("_", " ").strip()
                tokens.pop()
                continue
            if right_role == "unknown" and left_role != "unknown":
                record(left_role, left_value)
                if group is None and right and not right.isdigit():
                    group = right.replace("_", " ").strip()
                tokens.pop()
                continue
        break
    return " ".join(tokens), fields, version_found, group


#: AniFlow's source vocabulary, from what the engine reports. Keyed on the
#: release type first because that is the specific statement ("BDRip" is a
#: Blu-ray rip, "Remux" is not), with the bare medium as the fallback.
_RELEASE_TYPE_SOURCES = {
    "web-dl": "web_dl",
    "webrip": "webrip",
    "remux": "remux",
    "bdrip": "bluray",
    "bdmv": "bluray",
    "dvdrip": "dvd",
    "hdtv": "hdtv",
    "tvrip": "hdtv",
}
_MEDIUM_SOURCES = {
    "web": "web_dl",
    "bd": "bluray",
    "dvd": "dvd",
    "tv": "hdtv",
}

#: A trailing designation that makes ``only_name`` the better title: the engine
#: strips these to give one key across seasons, which is what AniFlow wants.
#: Anything else it strips (a media word like "Movie") is part of the name.
_SEASON_TAIL = re.compile(
    r"^(?:"
    r"\d{1,2}"                                  # "Jujutsu Kaisen 2"
    r"|s\d{1,2}"                                # "Mushoku Tensei S3"
    r"|(?:season|cour|part)\s*\d{1,2}"
    r"|\d{1,2}(?:st|nd|rd|th)\s+season"
    r"|final\s+season"
    r"|ii|iii|iv"
    r")$",
    re.IGNORECASE,
)

#: Scene names separate everything with dots, which the engine reads as one
#: token (and mistakes the tail for a file extension). Recognised by having
#: several dots and no spaces outside the bracketed group.
_DOTTED_SCENE = re.compile(r"^(?:\[[^\]]*\]\s*)?[^\s]*\.[^\s]*\.[^\s]+$")

#: A scene-style group credit at the very end: "… x264 - Group".
_TRAILING_GROUP = re.compile(r"\s-\s([A-Za-z0-9_.]+)$")

#: A group credited after a dash with no space, scene style, possibly with a
#: note and an extension after it: "… H.264 -NanDesuKa (B-Global).mkv".
#:
#: No space after the dash is what makes this safe: " - Something" is handled
#: above and is far more often a subtitle than a credit. The tail must start
#: with a letter and hold no spaces, or "Show - 01.mkv" credits "01.mkv".
_SCENE_GROUP = re.compile(
    r"\s-(?P<group>[A-Za-z][A-Za-z0-9_.\-]*)"
    r"(?:\s*\([^()]*\))?"
    r"(?:\.[A-Za-z0-9]{2,4})?\s*$"
)

#: A season written as a bare number at the end of a title, where the season
#: is already known from an explicit marker: "… Datta Ken 4" beside `S04E21`.
#:
#: Only ever removed when it *matches* the season that was parsed - a title
#: ending in a number is otherwise a title ending in a number ("Gundam 00",
#: "86"), and guessing there files a show under a name nobody uses.
_TRAILING_SEASON_DIGIT = re.compile(r"[\s._-]+(\d{1,2})$")

#: Everything after the first pipe is a note the uploader wrote for readers -
#: the English title again, the absolute episode number - and reading it as
#: part of the name is how twenty episodes became twenty rows.
_NOTE_AFTER_PIPE = re.compile(r"\s*\|.*$", re.DOTALL)

#: Pixel dimensions AniFlow reports as a height.
_PIXEL_DIMENSIONS = re.compile(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", re.IGNORECASE)

#: What the engine calls a codec, in AniFlow's spelling. Stored profiles
#: compare against these values, so the vocabulary is not cosmetic.
_ENGINE_VIDEO_CODECS = {
    "hevc": "h265", "h.265": "h265", "x265": "h265",
    "avc": "h264", "h.264": "h264", "x264": "h264",
    "av1": "av1", "vp9": "vp9", "xvid": "xvid", "divx": "divx", "mpeg-2": "mpeg2",
}


#: A bare resolution, which is a property and never a title.
_RESOLUTION_ONLY = re.compile(r"\d{3,4}[pi]", re.IGNORECASE)

#: A range left in the title because nothing separated it: "Some Anime 12-14".
_TITLE_RANGE = re.compile(r"\s(\d{1,3})-(\d{1,3})\s*$")

#: A decimal episode, which the engine does not read at all: "… - 01.5". These
#: are real - a recap or a split episode is numbered 5.5 - and AniFlow keeps
#: the label as written, so "01.5" stays "01.5".
_TITLE_DECIMAL_EPISODE = re.compile(r"\s-?\s*(\d{1,3}\.\d)\s*$")

#: A bare year at the end of a title, redundant when the name carries one in
#: parentheses as well.
_TRAILING_YEAR = re.compile(r"\s(?:19|20)\d{2}\s*$")


def _title_position_text(raw_name: str, result) -> str | None:
    """Whatever sits where a title should be, for names the engine rejects.

    Only used when the engine read no title at all, and then only if what is
    left is not something it already classified: "1080p" on its own is a
    resolution and no title at all, while "???" is text in the title position
    and has to reach matching as such - a profile that does not check titles
    would otherwise accept a release whose title contradicts every known one,
    because "no title" reads as "nothing to check".
    """
    working = re.sub(r"^(?:\s*[\[(][^\])]*[\])]\s*)+", "", raw_name.strip())
    working = re.split(r"\s-\s|\s+-\s*\d|\s+\d{1,4}\s*$", working)[0]
    working = working.strip(" -_.")
    if not working:
        return None
    if _RESOLUTION_ONLY.fullmatch(working) or _WXH_RESOLUTION.fullmatch(working):
        return None
    classified = {
        (value or "").strip().lower()
        for value in (
            result.quality, result.video_codec, result.audio_codec,
            result.source, result.release_type, result.subgroup, result.crc32,
        )
    }
    if working.lower() in classified:
        return None
    return working


def _prepare(raw_name: str) -> str:
    """The name as the engine reads best.

    Only one transformation, and only for names that are entirely
    dot-separated: the engine treats the final ``.x264`` as a file extension
    and leaves ``S02E07.720p`` sitting in the title. Spacing those out costs
    nothing for names that were already spaced, because they are left alone.
    """
    raw_name = _NOTE_AFTER_PIPE.sub("", raw_name.strip()) or raw_name
    if _DOTTED_SCENE.match(raw_name.strip()):
        group, _, rest = raw_name.strip().partition("]")
        if rest:
            return f"{group}]{rest.replace('.', ' ')}"
        return raw_name.replace(".", " ")
    return raw_name


def _drop_redundant_season(title: str | None, season: int | None) -> str | None:
    """Remove a trailing season number the title repeats from its own marker.

    "[Ironclad] Tensei Shitara Slime Datta Ken 4 - S04E21" reads season 4 from
    the marker and leaves the 4 in the title, so the show is filed under a
    name no other group uses and every episode lands in a row of its own -
    twenty-two of them in the deployment's parse log.

    Deliberately narrow: only when the number *is* the season that was parsed.
    A title ending in a number is otherwise just that, and "Gundam 00" or "86"
    must survive this untouched.
    """
    if not title or season is None:
        return title
    match = _TRAILING_SEASON_DIGIT.search(title)
    if match is None or int(match.group(1)) != season:
        return title
    shortened = title[: match.start()].strip()
    # Never leave nothing behind: a show actually called "4" keeps its name.
    return shortened or title


def _title(result) -> str | None:
    """AniFlow's title: the base name, with a season designation moved aside.

    ``only_name`` is the engine's cross-season key and is usually what AniFlow
    wants - "Mushoku Tensei S3" and "… 3rd Season" both reduce to the name that
    matches every season. But it also strips media words, turning "Anime Movie"
    into "Anime", and a title is not something to shorten on a guess. So it is
    used only when the difference really is a season designation.
    """
    name = (result.name or "").strip()
    only = (result.only_name or "").strip()
    if not only:
        return name or None
    if not name or only == name:
        return only or None
    if name.lower().startswith(only.lower()):
        remainder = name[len(only):].strip(" -_:")
        if _SEASON_TAIL.match(remainder):
            return only
    return name


def _strip_title_noise(title: str | None, result) -> tuple[str | None, str | None]:
    """Take an episode marker and a redundant year off the title.

    Three things the engine leaves in a name that AniFlow reads out of it:

    * a range with no separator, ``Some Anime 12-14`` - the engine reads
      ``- 12-14`` but not this;
    * a decimal episode, ``- 01.5``, which it does not read in any form;
    * a bare year in a name that also carries a parenthesised one.

    All three stay out of the title, because the title is compared against
    provider titles that contain none of them. Returns the cleaned title and
    any episode label recovered from it.
    """
    if not title:
        return title, None

    episode = None
    ranged = _TITLE_RANGE.search(title)
    decimal = _TITLE_DECIMAL_EPISODE.search(title)
    if ranged:
        start, end = int(ranged.group(1)), int(ranged.group(2))
        if start < end:
            episode = f"{start}-{end}"
            title = title[: ranged.start()].strip(" -_")
    elif decimal:
        episode = decimal.group(1)
        title = title[: decimal.start()].strip(" -_")

    if result.year:
        without_year = _TRAILING_YEAR.sub("", title).strip(" -_")
        # Only when something is left: a title that *is* a year keeps it.
        if without_year:
            title = without_year
    return (title or None), episode


def _media_source(result) -> str | None:
    """One of AniFlow's source values, or None when the name does not say.

    Falls back to AniFlow's own synonym table over whatever the engine could
    not classify, so a squashed spelling like ``BluRayRemux`` is still read.
    """
    if result.release_type:
        mapped = _RELEASE_TYPE_SOURCES.get(result.release_type.strip().lower())
        if mapped:
            return mapped
    if result.source:
        mapped = _MEDIUM_SOURCES.get(result.source.strip().lower())
        if mapped:
            return mapped
    for token in _leftovers(result):
        # Both tables are spelling -> canonical.
        squashed = _normalize_token(token).replace(" ", "")
        if squashed in _SQUASHED_SOURCES:
            return _SQUASHED_SOURCES[squashed]
        normalized = _normalize_token(token)
        if normalized in _SOURCE_SYNONYMS:
            return _SOURCE_SYNONYMS[normalized]
    return None


def _resolution(result) -> str | None:
    """A height in ``NNNNp``, however the name spelled it."""
    quality = (result.quality or "").strip()
    if quality:
        if quality.upper() == "4K":
            return "2160p"
        if quality.upper() == "8K":
            return "4320p"
        return quality
    for token in _leftovers(result):
        dimensions = _PIXEL_DIMENSIONS.search(token)
        if dimensions:
            return f"{dimensions.group(2)}p"
    return None


def _version(result) -> int:
    """The release version. A standalone ``[v2]`` tag counts."""
    if result.episode_version:
        return result.episode_version
    for token in _leftovers(result):
        match = _VERSION_TAG.fullmatch(_normalize_token(token).replace(" ", ""))
        if match:
            return int(match.group(1))
    return 1


def _subgroup(result, raw_name: str) -> str | None:
    """The release group. Scene names credit it after a trailing dash.

    Narrow on purpose: the tail has to look like a name rather than an episode
    or a file. Without that, ``Show - 01.mkv`` credits "01.mkv" as the group.
    """
    if result.subgroup:
        return result.subgroup
    trailing = _TRAILING_GROUP.search(raw_name.strip()) or _SCENE_GROUP.search(
        raw_name.strip()
    )
    if not trailing:
        return None
    candidate = trailing.group(1)
    if not re.search(r"[A-Za-z]", candidate):
        return None  # a number is an episode, not a group
    if candidate.rpartition(".")[2].lower() in _FILE_EXTENSIONS:
        return None  # a filename, not a group
    return candidate


def _episode_label(result) -> str | None:
    """The episode as AniFlow stores it, or None when there is no episode.

    A range keeps its label: ``episode_number_from_label`` reads no single
    number from "12-14", so queueing still raises an AMBIGUOUS_RELEASE record
    rather than attaching a multi-episode torrent to one episode - but the
    Downloads list can say what was actually found.
    """
    if result.episode_start is not None and result.episode_end is not None:
        return f"{result.episode_start}-{result.episode_end}"
    if result.episode is None:
        return None
    return f"{result.episode:02d}"


#: "Ep.01-03", "EP 01~12", "E01-E03": an explicit range written after an episode word.
_EPISODE_WORD_RANGE = re.compile(
    r"(?<![A-Za-z0-9])(?:Episodes?|Eps?|E)\.?\s*0*(\d{1,3})\s*[-~–]\s*(?:E|Ep)?\.?0*(\d{1,3})(?![0-9A-Za-z])",
    re.IGNORECASE,
)


def _range_the_engine_missed(raw_name: str, result) -> tuple[int, int] | None:
    """A range the engine read as its first number only (build 246).

    "Akiba Girls Ep.01-03 [UNCEN-ENG SUBS]" came back as episode 1, so a three-episode batch sat in
    the list as episode 1 twice over (two uploaders, one name) and episodes 2 and 3 as "not published".
    The engine reads "- 01-03" and "EP01~03" as ranges but not an episode word followed by a range
    with a dot. Only when the engine found a single episode that *is* the range's first number and
    found no range of its own - never over what it read.
    """
    if result.episode is None or result.episode_start is not None or result.episode_end is not None:
        return None
    match = _EPISODE_WORD_RANGE.search(raw_name)
    if match is None:
        return None
    first, last = int(match.group(1)), int(match.group(2))
    if first != int(result.episode) or last <= first:
        return None
    return first, last


def _leftovers(result) -> list[str]:
    """Everything recognised-but-unmapped and everything unclassified, once.

    ``misc`` and ``unknown_tokens`` overlap; a value appearing in both is one
    finding, not two.
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in [*(result.misc or []), *(token.text for token in result.unknown_tokens or [])]:
        text = (token or "").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def parse_release_name(raw_name: str) -> ParsedRelease:
    """Parse one release name into AniFlow's canonical fields.

    The reading is done by the vendored ``anime_filename_parser``; this maps its
    result onto :class:`ParsedRelease` and applies AniFlow's own conventions.

    Raises ``ValueError`` only for an empty name. A name whose title cannot be
    read yields ``title=None``, which matching reports as ``title_unknown`` -
    surfacing the release for review instead of discarding it.
    """
    if not raw_name or not raw_name.strip():
        raise ValueError("Release name is empty")

    # The note after a pipe is removed once, here, so everything below sees
    # the same name: the engine, the group rule and the tags.
    raw_name = _NOTE_AFTER_PIPE.sub("", raw_name.strip()) or raw_name
    result = _engine_parse(_prepare(raw_name))
    title, title_episode = _strip_title_noise(_title(result), result)
    if title is None:
        # The engine declines to call unreadable text a title. AniFlow needs
        # the text anyway: a profile with the title check switched off would
        # otherwise accept a release whose title contradicts every known one,
        # because "no title" reads as "nothing to check".
        title = _title_position_text(raw_name, result)
    title = _drop_redundant_season(title, result.season)
    consumed_source = _media_source(result)
    consumed_resolution = _resolution(result)
    version = _version(result)

    # Leftovers the adapter itself read are no longer unclassified.
    unknown = [
        token for token in _leftovers(result)
        if not _VERSION_TAG.fullmatch(_normalize_token(token).replace(" ", ""))
        and not _PIXEL_DIMENSIONS.search(token)
        and _normalize_token(token) not in _IGNORED_TAGS
        and _normalize_token(token).replace(" ", "") not in _SQUASHED_SOURCES
    ]

    video = (result.video_codec or "").strip().lower()
    audio = (result.audio_codec or "").strip().lower()

    # Read from the raw name rather than from the engine's fields: these are
    # the words that decide which *copy* of an episode a file is, and the
    # engine has no notion of most of them. See services/release_edition.py.
    from backend.app.services.release_edition import censored_from, markers_in

    named = markers_in(raw_name)

    missed = _range_the_engine_missed(raw_name, result)
    parsed = ParsedRelease(
        raw_name=raw_name,
        subgroup=_subgroup(result, raw_name),
        title=title,
        episode=f"{missed[0]}-{missed[1]}" if missed else (_episode_label(result) or title_episode),
        episode_start=missed[0] if missed else result.episode_start,
        episode_end=missed[1] if missed else result.episode_end,
        version=version,
        resolution=consumed_resolution,
        media_source=consumed_source,
        video_codec=_ENGINE_VIDEO_CODECS.get(video, video or None),
        audio_codec=audio or None,
        # Tri-state. The engine only reports whether a name says
        # "uncensored", so a name saying "CENSORED" used to come through as
        # "does not say" - which is what let the censored copy and the
        # uncensored one share a row. The engine's answer still wins when the
        # markers found neither.
        censored=(
            censored_from(named)
            if censored_from(named) is not None
            else (None if result.uncensored is None else not result.uncensored)
        ),
        season=result.season,
        crc32=result.crc32,
        unknown_tags=tuple(unknown),
    )
    # The full tag set needs the parsed fields to subtract them, so it is
    # computed once the rest of the parse exists. See
    # services/release_edition.tags_in - the named markers are only half of
    # it, and the other half is what makes an unfamiliar label still count.
    from backend.app.services.release_edition import tags_in

    return replace(parsed, tags=tags_in(raw_name, parsed))


#: Letters a title is styled with that a reader takes for another: "PSY\u042fEN" is PSYREN, "$" is S,
#: "\u20ac" is E, "IDOLM@STER" is IDOLMASTER (build 209). Only look-alikes: a spelling that merely sounds
#: alike is not folded here, because two different shows must never become one key.
_LOOKALIKES = str.maketrans(
    {
        "$": "s", "\u20ac": "e", "\u00a3": "l", "\u00a5": "y", "@": "a",
        "\u042f": "r", "\u044f": "r",  # cyrillic ya, a reversed R
        "\u0418": "n", "\u0438": "n",  # cyrillic i, a reversed N
        "\u2200": "a", "\u0245": "a",  # an A without its bar
    }
)

#: Long vowels as romanised both ways: "Kyouka" and "Kyoka", "Kuu" and "Ku". Not "ii" (a roman numeral
#: II would become I and a second season would equal the first) and not "ei" (rei is not re).
_LONG_VOWELS = re.compile(r"oo|ou|uu")


def fold_letters(text: str) -> str:
    """Look-alike letters and accents folded to plain ones, so "\u014ckami" and "Okami" meet."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.translate(_LOOKALIKES))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_title_for_match(title: str) -> str:
    """Aggressive normalization used only for equality comparisons."""
    lowered = fold_letters(title).casefold().replace("&", " and ")
    lowered = lowered.replace("'", "").replace("\u2019", "")
    cleaned = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", lowered)).strip()
    return _LONG_VOWELS.sub(lambda found: found.group(0)[0], cleaned)


_SEASON_SUFFIX = re.compile(
    r"\s*(?:"
    r"\bseason\s*\d+\b|\b\d+(?:st|nd|rd|th)\s+season\b"   # "Season 2", "2nd Season"
    r"|\bs\s?\d{1,2}\b"                                    # "S2", "S 02"
    r"|\b[ivx]{1,4}\b$"                                    # trailing roman numeral
    r")\s*$",
    re.IGNORECASE,
)


# Explicit season markers inside the title: "S2", "Season 2", "2nd Season",
# "Second Season", standalone roman numerals II/III/IV.
_SEASON_PATTERNS = [
    (re.compile(r"\bseason\s+(\d{1,2})\b", re.IGNORECASE), None),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[\s-]+season\b", re.IGNORECASE), None),
    (re.compile(r"\bs\s?(\d{1,2})\b", re.IGNORECASE), None),
    (
        re.compile(r"\b(First|Second|Third|Fourth|Fifth)[\s-]+Season\b", re.IGNORECASE),
        {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5},
    ),
    (re.compile(r"(?<![A-Za-z0-9])(II|III|IV)(?![A-Za-z0-9])"), {"ii": 2, "iii": 3, "iv": 4}),
]
_ROMAN_VALUES = {"ii": 2, "iii": 3, "iv": 4}


def _extract_resolution(remainder: str, fields: dict) -> str:
    """Pull a bare (unbracketed) resolution token out of the free text."""
    match = _RESOLUTION_SEARCH.search(remainder)
    if match:
        fields.setdefault("resolution", f"{match.group(1)}p")
        return remainder[: match.start()] + remainder[match.end():]
    wxh = _WXH_RESOLUTION.search(remainder)
    if wxh:
        height = wxh.group(0).split("x")[1]
        fields.setdefault("resolution", f"{height}p")
        return remainder[: wxh.start()] + remainder[wxh.end():]
    four_k = _FOUR_K.search(remainder)
    if four_k:
        fields.setdefault("resolution", "2160p")
        return remainder[: four_k.start()] + remainder[four_k.end():]
    return remainder


def _extract_season(text: str) -> tuple[str, int | None]:
    """Remove the first explicit season marker; return ``(text, season)``."""
    for pattern, mapping in _SEASON_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1)
        value = mapping[raw.lower()] if mapping else int(raw)
        cleaned = (text[:match.start()] + " " + text[match.end():]).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned, value
    return text, None


def title_base_variants(title: str) -> set[str]:
    """Normalized base forms of a title with season suffixes stripped.

    Releases abbreviate seasons ("Hell Mode S2", "... Odekakechuu II") while
    provider titles spell them out ("... Season 2"). Comparing the base forms
    of both sides stays deterministic while closing the common gap.
    """
    base = normalize_title_for_match(title)
    if not base:
        return set()
    variants = {base}
    stripped = _SEASON_SUFFIX.sub("", base).strip()
    if stripped:
        variants.add(stripped)
    return variants


def search_term_for_title(title: str) -> str:
    """The form of a provider title worth sending to an indexer.

    Two differences from the provider's own wording, both of which decide
    whether a search returns anything at all:

    * the season designation comes off. Release names carry the season as
      ``S2`` or leave it out of the title entirely, so searching for
      "Mushoku Tensei: Jobless Reincarnation Season 2" matches nothing while
      "Mushoku Tensei" matches every episode of it.
    * a colon becomes a space. Release names do not use them, and an indexer
      that matches the term literally finds nothing for "BLEACH: Sennen
      Kessen-hen".

    The result is a starting point the user can still edit, not a promise.
    Returns the title unchanged when there is nothing to take off.
    """
    if not title or not title.strip():
        return ""
    parsed = _engine_parse(title.strip())
    base = (parsed.only_name or parsed.name or title).strip()
    return re.sub(r"\s{2,}", " ", base.replace(":", " ")).strip()
