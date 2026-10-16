"""Data models produced by the parser.

The public result object is :class:`ParseResult`.  It is a plain
:mod:`dataclasses` dataclass so the library stays dependency free, while still
being trivially serialisable to ``dict``/JSON.

The model is intentionally *wide*: it is cheaper to add a nullable field than to
lose information.  Anything that was recognised but has no dedicated field ends
up in :attr:`ParseResult.misc`; anything that could not be recognised at all
ends up in both :attr:`ParseResult.misc` and
:attr:`ParseResult.unknown_tokens`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Iterator

__all__ = ["UnknownToken", "ParseResult", "MediaType"]


class MediaType:
    """Namespace of the ``media_type`` values the parser may emit."""

    TV: ClassVar[str] = "tv"
    MOVIE: ClassVar[str] = "movie"
    OVA: ClassVar[str] = "ova"
    ONA: ClassVar[str] = "ona"
    SPECIAL: ClassVar[str] = "special"

    ALL: ClassVar[tuple[str, ...]] = (TV, MOVIE, OVA, ONA, SPECIAL)


@dataclass(frozen=True)
class UnknownToken:
    """A meaningful piece of text the parser could not classify.

    Attributes:
        text: The text as it appears (after separator normalisation).
        location: Where it was found, e.g. ``"text[0] field 2"`` or
            ``"square[3]"``.  Useful when debugging a new release format.
    """

    text: str
    location: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "location": self.location}


@dataclass
class ParseResult:
    """Structured metadata extracted from a single filename.

    Every field is directly accessible as an attribute.  ``None`` means "not
    detected"; the parser never invents a value.  Boolean *flags* default to
    ``False`` because their absence is meaningful ("not a batch release"),
    except :attr:`uncensored` which is tri-state (``None`` = not stated).
    """

    # --- provenance -----------------------------------------------------
    filename: str
    extension: str | None = None

    # --- titles ---------------------------------------------------------
    name: str | None = None
    only_name: str | None = None
    alternative_titles: list[str] = field(default_factory=list)

    subgroup: str | None = None

    # --- structure ------------------------------------------------------
    season: int | None = None
    part: int | None = None
    cour: int | None = None

    episode: int | None = None
    episode_start: int | None = None
    episode_end: int | None = None
    episode_version: int | None = None
    absolute_episode: int | None = None
    episode_name: str | None = None

    year: int | None = None
    media_type: str | None = None

    # --- technical ------------------------------------------------------
    quality: str | None = None
    resolution: str | None = None
    crc32: str | None = None

    video_codec: str | None = None
    encoding: str | None = None
    bit_depth: int | None = None

    audio_codec: str | None = None
    audio_codecs: list[str] = field(default_factory=list)
    audio_channels: str | None = None
    audio_type: str | None = None

    subtitle_language: str | None = None
    subtitle_type: str | None = None
    subtitle_format: str | None = None

    source: str | None = None
    release_type: str | None = None
    service: str | None = None

    # --- release flags --------------------------------------------------
    uncensored: bool | None = None
    batch: bool = False
    repack: bool = False

    # --- release tags ---------------------------------------------------
    #: Every release tag the parser recognised, canonically spelled, in the
    #: order they appear: ``["WEB", "WEB-DL", "Crunchyroll", "1080p", "HEVC",
    #: "x265", "10bit", "AAC", "Multi Sub", "Batch"]``.  It is a *view* over
    #: the technical fields plus the extras that have no field of their own,
    #: for callers that want to show or filter on tags without knowing which
    #: field each one lives in.
    tags: list[str] = field(default_factory=list)

    # --- leftovers ------------------------------------------------------
    misc: list[str] = field(default_factory=list)
    unknown_tokens: list[UnknownToken] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    #: Maps a populated field name to the rule that populated it.  Purely
    #: informational (``--explain`` in the CLI) and excluded from ``to_dict``
    #: unless explicitly requested.
    provenance: dict[str, str] = field(default_factory=dict)

    _EXCLUDED_FROM_DICT: ClassVar[frozenset[str]] = frozenset({"provenance"})

    # ------------------------------------------------------------------
    # introspection helpers
    # ------------------------------------------------------------------
    @classmethod
    def field_names(cls, *, include_internal: bool = False) -> tuple[str, ...]:
        """Return the public field names in declaration order."""
        return tuple(
            f.name
            for f in fields(cls)
            if include_internal or f.name not in cls._EXCLUDED_FROM_DICT
        )

    def to_dict(self, *, include_provenance: bool = False) -> dict[str, Any]:
        """Return a JSON-ready ``dict`` with a stable key order."""
        data = asdict(self)
        data["unknown_tokens"] = [t.to_dict() for t in self.unknown_tokens]
        if not include_provenance:
            for name in self._EXCLUDED_FROM_DICT:
                data.pop(name, None)
        return data

    def to_json(self, *, indent: int | None = 2, include_provenance: bool = False) -> str:
        """Serialise to a JSON string."""
        return json.dumps(
            self.to_dict(include_provenance=include_provenance),
            indent=indent,
            ensure_ascii=False,
        )

    # Mapping-ish access so callers can do ``result["only_name"]`` or work with
    # user supplied field names coming from a CLI/config file.
    def get(self, name: str, default: Any = None) -> Any:
        key = normalise_field_name(name)
        if key not in self.field_names(include_internal=True):
            return default
        return getattr(self, key)

    def __getitem__(self, name: str) -> Any:
        key = normalise_field_name(name)
        if key not in self.field_names(include_internal=True):
            raise KeyError(name)
        return getattr(self, key)

    def __contains__(self, name: str) -> bool:
        return normalise_field_name(name) in self.field_names(include_internal=True)

    def keys(self) -> Iterator[str]:
        return iter(self.field_names())

    # ------------------------------------------------------------------
    @property
    def episodes(self) -> list[int]:
        """Every episode covered by this release (empty when unknown)."""
        if self.episode_start is not None and self.episode_end is not None:
            return list(range(self.episode_start, self.episode_end + 1))
        if self.episode is not None:
            return [self.episode]
        return []


def normalise_field_name(name: str) -> str:
    """Accept ``only-name``, ``Only Name`` and ``only_name`` alike."""
    return name.strip().lower().replace("-", "_").replace(" ", "_")
