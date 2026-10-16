"""Shared vocabularies and small pattern helpers.

Rules deliberately keep their *logic* in :mod:`anime_filename_parser.rules`
and their *knowledge* here.  Teaching the parser a new codec, streaming
service or subtitle spelling is then a one-line change in this module.

Short, ambiguous abbreviations (``CR``, ``NF``, ``DR``, ``IQ``, ``TV``...) are
listed in :data:`CASE_SENSITIVE` and only match when the token is written in
capitals, so a title word such as ``Dr`` is never mistaken for a broadcaster.
"""

from __future__ import annotations

import re

__all__ = [
    "QUALITY_RE",
    "RESOLUTION_RE",
    "normalise_quality",
    "VIDEO_CODECS",
    "BIT_DEPTH_RE",
    "HI10_TOKENS",
    "AUDIO_CODECS",
    "AUDIO_CHANNELS_RE",
    "AUDIO_CODEC_CHANNELS_RE",
    "AUDIO_TYPES",
    "SUBTITLE_TYPES",
    "SUBTITLE_FORMATS",
    "CJK_SUBTITLE_MARKER",
    "CJK_SUBTITLE_PLACEMENT",
    "CJK_SUBTITLE_GROUP_SUFFIXES",
    "CJK_SUBTITLE_SCRIPTS",
    "LANGUAGES",
    "LANGUAGE_SUB_RE",
    "SOURCES",
    "SERVICES",
    "CASE_SENSITIVE",
    "EXACT_VARIANTS",
    "MEDIA_TYPE_WORDS",
    "SEASON_WORDS",
    "PART_WORDS",
    "COUR_WORDS",
    "ORDINAL_WORDS",
    "ORDINAL_SUFFIX_RE",
    "QUANTITY_WORDS",
    "UNCENSORED_TOKENS",
    "CENSORED_TOKENS",
    "BATCH_TOKENS",
    "REPACK_TOKENS",
    "KNOWN_MISC_TOKENS",
    "MISC_TAG_NAMES",
    "TITLE_STOPWORDS",
    "roman_to_int",
    "is_roman",
    "matches_vocab",
]

# ----------------------------------------------------------------------
# quality
# ----------------------------------------------------------------------
#: ``1080p``, ``WEB.1080p`` (after dot normalisation), ``BD720p``, ``4k``.
QUALITY_RE = re.compile(
    r"^(?P<source>WEB|BD|BR|HD|SD|DVD|TV)?(?P<value>\d{3,4}[pi]|[48]k)$",
    re.IGNORECASE,
)

#: ``1440x1080``, ``1920x1080``: a full frame size.  The height is what every
#: other spelling of quality means, so that is what is reported.
RESOLUTION_RE = re.compile(r"^(?P<width>\d{3,4})[xX×](?P<height>\d{3,4})$")

_QUALITY_ALIASES = {"4k": "4K", "8k": "8K"}


def normalise_quality(value: str) -> str:
    """Return the canonical spelling of a quality token (``1080P`` -> ``1080p``)."""
    lowered = value.lower()
    return _QUALITY_ALIASES.get(lowered, lowered)


# ----------------------------------------------------------------------
# video
# ----------------------------------------------------------------------
#: token -> (codec family, explicit encoder string or ``None``)
VIDEO_CODECS: dict[str, tuple[str, str | None]] = {
    "x264": ("AVC", "x264"),
    "x265": ("HEVC", "x265"),
    "h264": ("AVC", "H.264"),
    "h.264": ("AVC", "H.264"),
    "h265": ("HEVC", "H.265"),
    "h.265": ("HEVC", "H.265"),
    "avc": ("AVC", None),
    "hevc": ("HEVC", None),
    "av1": ("AV1", None),
    "vp9": ("VP9", None),
    "xvid": ("XviD", "XviD"),
    "divx": ("DivX", "DivX"),
    "mpeg2": ("MPEG-2", None),
    "mpeg-2": ("MPEG-2", None),
    "prores": ("ProRes", None),
}

BIT_DEPTH_RE = re.compile(r"^(?P<depth>8|10|12)[-\s]?bits?$", re.IGNORECASE)
HI10_TOKENS = {"hi10p": 10, "hi10": 10, "hi444p": 10}

# ----------------------------------------------------------------------
# audio
# ----------------------------------------------------------------------
AUDIO_CODECS: dict[str, str] = {
    "aac": "AAC",
    "ac3": "AC3",
    "ac-3": "AC3",
    "eac3": "EAC3",
    "eac-3": "EAC3",
    "e-ac3": "EAC3",
    "e-ac-3": "EAC3",
    "ddp": "DDP",
    "dd+": "DDP",
    "dd": "DD",
    "dts": "DTS",
    "dts-hd": "DTS-HD",
    "dtshd": "DTS-HD",
    "truehd": "TrueHD",
    "flac": "FLAC",
    "opus": "Opus",
    "mp3": "MP3",
    "pcm": "PCM",
    "lpcm": "LPCM",
    "vorbis": "Vorbis",
}

#: ``AAC2.0``/``DDP2.0`` arrive as a single token; this splits them.
AUDIO_CODEC_CHANNELS_RE = re.compile(
    r"^(?P<codec>[A-Za-z][A-Za-z+-]{1,7})\s?(?P<channels>[1-8]\.[01254])$"
)

AUDIO_CHANNELS_RE = re.compile(r"^(?P<channels>[1-8]\.[01254])$")

#: token -> audio_type
AUDIO_TYPES: dict[str, str] = {
    "dual audio": "dual",
    "dual-audio": "dual",
    "dualaudio": "dual",
    "2audio": "dual",
    "multi audio": "multi",
    "multi-audio": "multi",
    "multiaudio": "multi",
    "multi": "multi",
}

# ----------------------------------------------------------------------
# subtitles
# ----------------------------------------------------------------------
#: token -> subtitle_type
SUBTITLE_TYPES: dict[str, str] = {
    "multi sub": "multi",
    "multi subs": "multi",
    "multi-sub": "multi",
    "multi-subs": "multi",
    "multisub": "multi",
    "multisubs": "multi",
    "m-sub": "multi",
    "m-subs": "multi",
    "msub": "multi",
    "dual sub": "dual",
    "dual-sub": "dual",
    "dual subs": "dual",
    "dual-subs": "dual",
    "softsub": "soft",
    "softsubs": "soft",
    "soft-sub": "soft",
    "hardsub": "hard",
    "hardsubs": "hard",
    "hard-sub": "hard",
}

#: Chinese releases state their subtitles in Chinese.  Matching on the marker
#: characters rather than on whole phrases covers the long tail of spellings
#: (``简繁内封字幕``, ``简体内嵌``, ``简日双语字幕``) without listing each one.
CJK_SUBTITLE_MARKER = "字幕"           # 字幕 -- "subtitles"
CJK_SUBTITLE_PLACEMENT: dict[str, str] = {
    "内嵌": "hard",                    # 内嵌 -- burned in
    "内封": "soft",                    # 内封 -- muxed in
    "外挂": "soft",                    # 外挂 -- separate file
}
#: Chinese fansub groups are *named* after subtitles -- 字幕组 and 字幕社 both
#: mean "subtitle group" -- so the marker above matches their names too.  A
#: name carrying one of these is a release group, never a statement about the
#: subtitles of this file.
CJK_SUBTITLE_GROUP_SUFFIXES: tuple[str, ...] = (
    "字幕组",                      # 字幕组   subtitle group
    "字幕組",                      # 字幕組   ... traditional
    "字幕社",                      # 字幕社   subtitle society
    "字幕团",                      # 字幕团
    "字幕團",                      # 字幕團
    "压制组",                      # 压制组   encoding group
    "工作室",                      # 工作室   studio
)

#: Which Chinese script the subtitles are in.
CJK_SUBTITLE_SCRIPTS: dict[str, str] = {
    "简": "Chinese",                        # 简 -- simplified
    "繁": "Chinese",                        # 繁 -- traditional
}

SUBTITLE_FORMATS = {
    "ass": "ASS",
    "ssa": "SSA",
    "srt": "SRT",
    "pgs": "PGS",
    "vobsub": "VobSub",
}

#: Language spellings the parser understands, mapped to a canonical name.
LANGUAGES: dict[str, str] = {
    "eng": "English",
    "en": "English",
    "english": "English",
    "jpn": "Japanese",
    "jap": "Japanese",
    "japanese": "Japanese",
    "chi": "Chinese",
    "chs": "Chinese",
    "cht": "Chinese",
    "chinese": "Chinese",
    "kor": "Korean",
    "korean": "Korean",
    "spa": "Spanish",
    "spanish": "Spanish",
    "fre": "French",
    "fra": "French",
    "french": "French",
    "ger": "German",
    "deu": "German",
    "german": "German",
    "ita": "Italian",
    "italian": "Italian",
    "por": "Portuguese",
    "portuguese": "Portuguese",
    "rus": "Russian",
    "russian": "Russian",
    "ara": "Arabic",
    "arabic": "Arabic",
}

#: ``Eng Sub``, ``English-Subs``, ``EngSub`` collapsed into one pattern.
LANGUAGE_SUB_RE = re.compile(
    r"^(?P<language>[A-Za-z]{2,12})[-_ ]?(?P<kind>subs?|dub(?:bed)?|audio)$",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# source / service
# ----------------------------------------------------------------------
#: token -> (source, release_type)
SOURCES: dict[str, tuple[str | None, str | None]] = {
    "web-dl": ("WEB", "WEB-DL"),
    "webdl": ("WEB", "WEB-DL"),
    "web-rip": ("WEB", "WEBRip"),
    "webrip": ("WEB", "WEBRip"),
    "web": ("WEB", None),
    "bd": ("BD", None),
    "bdrip": ("BD", "BDRip"),
    "bd-rip": ("BD", "BDRip"),
    "bluray": ("BD", None),
    "blu-ray": ("BD", None),
    "bdmv": ("BD", "BDMV"),
    "brrip": ("BD", "BRRip"),
    "br-rip": ("BD", "BRRip"),
    "dvd": ("DVD", None),
    "dvdrip": ("DVD", "DVDRip"),
    "dvd-rip": ("DVD", "DVDRip"),
    "hdtv": ("TV", "HDTV"),
    "tvrip": ("TV", "TVRip"),
    "tv-rip": ("TV", "TVRip"),
    "vhs": ("VHS", None),
    "vhsrip": ("VHS", "VHSRip"),
    "ld": ("LD", None),
    "remux": (None, "Remux"),
}

#: token -> streaming service / broadcaster
SERVICES: dict[str, str] = {
    "amzn": "Amazon",
    "amazon": "Amazon",
    "cr": "Crunchyroll",
    "crunchyroll": "Crunchyroll",
    "nf": "Netflix",
    "netflix": "Netflix",
    "bili": "Bilibili",
    "bilibili": "Bilibili",
    "b-global": "Bilibili",
    "iqiyi": "iQIYI",
    "iq": "iQIYI",
    "dsnp": "Disney+",
    "disney": "Disney+",
    "hulu": "Hulu",
    "hmax": "HBO Max",
    "max": "HBO Max",
    "adn": "ADN",
    "dr": "DR",
    "vrv": "VRV",
    "abema": "Abema",
    "u-next": "U-NEXT",
    "unext": "U-NEXT",
    "funi": "Funimation",
    "funimation": "Funimation",
    "sentai": "Sentai",
    "atvp": "Apple TV+",
    "mubi": "MUBI",
    "tver": "TVer",
    "youtube": "YouTube",
    "yt": "YouTube",
    "hidive": "HIDIVE",
    "hidi": "HIDIVE",
    "iqiyi": "iQIYI",
    "wetv": "WeTV",
    "youku": "Youku",
    "mgtv": "MGTV",
    "viki": "Viki",
    "rakuten": "Rakuten",
    "wakanim": "Wakanim",
    "aniplus": "Aniplus",
    "laftel": "Laftel",
    "muse": "Muse",
    "ani-one": "Ani-One",
    "baha": "Bahamut",
    "bahamut": "Bahamut",
    "catchplay": "CatchPlay",
    "peacock": "Peacock",
    "paramount": "Paramount+",
    "pmtp": "Paramount+",
    # broadcasters, which appear in exactly the same slot as a streamer
    "at-x": "AT-X",
    "atx": "AT-X",
    "tokyomx": "Tokyo MX",
    "mx": "Tokyo MX",
    "bs11": "BS11",
    "nhk": "NHK",
    "tbs": "TBS",
    "cctv": "CCTV",
}

#: Vocabulary keys that only match when the token is written in capitals.
#: They are short enough to collide with ordinary title words.
CASE_SENSITIVE = frozenset(
    {
        "cr", "nf", "dr", "iq", "max", "yt", "ld", "dd", "tv", "sd",
        "mx", "tbs", "nhk", "atx", "hidi", "baha", "muse",
        "en", "eng", "chi", "chs", "cht", "jpn", "jap", "kor", "spa",
        "fre", "fra", "ger", "deu", "ita", "por", "rus", "ara", "avc",
    }
)

#: Exact spellings that bypass the capitals requirement above.
EXACT_VARIANTS = frozenset({"iQ", "iQIYI", "MULTi", "Multi"})

# ----------------------------------------------------------------------
# structure vocabulary
# ----------------------------------------------------------------------
MEDIA_TYPE_WORDS: dict[str, str] = {
    "movie": "movie",
    "gekijouban": "movie",
    "gekijou-ban": "movie",
    "theatrical": "movie",
    "ova": "ova",
    "oad": "ova",
    "ona": "ona",
    "special": "special",
    "specials": "special",
}

SEASON_WORDS = frozenset({"season", "saison", "temporada"})
PART_WORDS = frozenset({"part"})
COUR_WORDS = frozenset({"cour"})

ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

ORDINAL_SUFFIX_RE = re.compile(r"^(?P<number>\d{1,2})(?:st|nd|rd|th)$", re.IGNORECASE)

#: Words that turn the number after them into part of the *title*, never an
#: episode or season.  ``The Villager of Level 999`` used to be read as
#: episode 999 because the trailing number looked like an episode marker.
#: Deliberately short.  Each entry has to earn its place against a real title
#: that ends in that word plus a number, so the speculative ones (``type``,
#: ``code``, ``case``, ``file``, ``act``, ``rank``, ``class``...) are left out:
#: they guarded nothing and each one was a way to lose a season number.
QUANTITY_WORDS = frozenset(
    {
        "level", "lv", "lvl", "vol", "volume", "chapter", "ch",
        "no", "number", "nr", "ver", "version",
    }
)

UNCENSORED_TOKENS = frozenset({"uncensored", "uncensor", "uncut", "uncen"})
CENSORED_TOKENS = frozenset({"censored", "censor", "cut", "cen"})
BATCH_TOKENS = frozenset({"batch", "batches"})
REPACK_TOKENS = frozenset({"repack", "repacked"})

#: Recognised extras that deserve preserving but not a dedicated field.
KNOWN_MISC_TOKENS = frozenset(
    {
        "weekly", "complete", "proper", "preview", "menu", "extra", "extras",
        "bonus", "raw", "ncop", "nced", "op", "ed", "creditless", "lyrics",
        "hdr", "hdr10", "hdr10+", "dv", "dolbyvision", "sdr", "sample",
        "trailer", "pv", "cm", "nc", "nvenc", "qsv", "vaapi", "amf",
    }
)

#: How each :data:`KNOWN_MISC_TOKENS` entry is spelled in ``tags``.  A tag list
#: is meant to be comparable across release groups, so ``weekly`` and
#: ``Weekly`` must not be two different tags.
MISC_TAG_NAMES: dict[str, str] = {
    "weekly": "Weekly", "complete": "Complete", "proper": "PROPER",
    "preview": "Preview", "menu": "Menu", "extra": "Extra",
    "extras": "Extras", "bonus": "Bonus", "raw": "RAW", "ncop": "NCOP",
    "nced": "NCED", "op": "OP", "ed": "ED", "creditless": "Creditless",
    "lyrics": "Lyrics", "hdr": "HDR", "hdr10": "HDR10", "hdr10+": "HDR10+",
    "dv": "Dolby Vision", "dolbyvision": "Dolby Vision", "sdr": "SDR",
    "sample": "Sample", "trailer": "Trailer", "pv": "PV", "cm": "CM",
    "nc": "NC", "nvenc": "NVENC", "qsv": "QSV", "vaapi": "VAAPI",
    "amf": "AMF",
}

#: Words ignored when comparing two title candidates for word overlap.
TITLE_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "and", "no", "wo", "ni", "ga", "wa", "de",
        "to", "in", "on", "for", "my", "her", "his", "is", "it", "kun",
        "chan", "san", "sama", "movie", "season", "part", "hen",
    }
)

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def roman_to_int(value: str) -> int | None:
    """Convert a Roman numeral to ``int``, or ``None`` if it is not one."""
    text = value.strip().upper()
    if not text or not _ROMAN_RE.match(text):
        return None
    total = 0
    highest = 0
    for char in reversed(text):
        current = _ROMAN_VALUES[char]
        total += current if current >= highest else -current
        highest = max(highest, current)
    return total or None


def is_roman(value: str, *, minimum: int = 2, maximum: int = 20, min_length: int = 2) -> bool:
    """Is ``value`` a Roman numeral usable as a season designation?

    ``min_length`` defaults to 2 on purpose: a lone ``I``, ``V`` or ``X`` at
    the end of a title is far more likely to be part of the title than a
    season number, so those are never claimed.
    """
    if len(value) < min_length:
        return False
    number = roman_to_int(value)
    return number is not None and minimum <= number <= maximum


def matches_vocab(text: str, raw: str, vocabulary) -> bool:
    """Look ``text`` up in ``vocabulary`` honouring :data:`CASE_SENSITIVE`.

    Args:
        text: Lower-cased token text.
        raw: The token exactly as written (used for the capitals check).
        vocabulary: Any container supporting ``in``.
    """
    if text not in vocabulary:
        return False
    if text in CASE_SENSITIVE:
        return raw.isupper() or raw in EXACT_VARIANTS
    return True
