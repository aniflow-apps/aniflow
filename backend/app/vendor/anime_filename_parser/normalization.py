"""Separator normalisation.

Anime filenames use ``_``, ``.``, ``-`` and full-width punctuation as
separators, but the very same characters also appear *inside* meaningful
tokens (``H.264``, ``DDP2.0``, ``WEB-DL``, ``Hananoi-kun``).  Blindly replacing
punctuation destroys information, so normalisation here obeys two rules:

1.  It is **character preserving**: the normalised string always has exactly
    the same length as the input, so every token keeps an exact span into the
    original filename.  Nothing is ever deleted, only substituted 1:1.
2.  It is **conservative**: regions matching one of the *protected patterns*
    (see :data:`DEFAULT_PROTECTED_PATTERNS`) are never touched, and a ``.`` is
    only treated as a separator when it actually sits between two word
    characters.

The original filename is kept verbatim on the result object; everything the
parser reasons about is a view over :class:`NormalizedText`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

__all__ = [
    "NormalizationConfig",
    "NormalizedText",
    "normalize",
    "clean_display",
    "DEFAULT_PROTECTED_PATTERNS",
]

#: Regions that must survive normalisation untouched.  Order is irrelevant;
#: all matches are unioned into a protection mask.
DEFAULT_PROTECTED_PATTERNS: tuple[str, ...] = (
    r"[Hh]\.?26[45](?:-[A-Za-z0-9]{2,})?",          # H.264, H.265-VARYG
    # An audio channel layout only: exactly one digit either side, with no
    # further digit or dot touching it.  A wider pattern used to swallow the
    # separator in ``S22E31.1080p`` (matching "31.1080") and lose both the
    # episode and the quality of every dotted scene release.
    r"(?<![\d.])[1-8]\.[0-2456](?!\d)",            # 2.0, 5.1, AAC2.0
    # A dotted acronym, and only when it starts on a word boundary.  Without
    # the lookbehind this matched *across* words -- ``Raids.A.Tank`` contains
    # "s.A.T" -- and protected separators that were doing separator work.
    r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}[A-Za-z]?",  # A.I.C.O, E.A.C
    r"(?:No|Vol|Pt|Ep)\.\s?\d+",                    # Vol.1, Ep.3
    # A decimal episode number standing on its own: ``- 01.5``, ``12.5``.
    # Recaps and split episodes are numbered this way, so the dot between the
    # halves is not doing separator work and must survive.
    #
    # Restored in 0.2.1. The audio-layout pattern above replaced a blanket
    # ``\d+\.\d+`` - rightly, since that one matched "31.1080" in
    # ``S22E31.1080p`` - and took this case with it: unprotected, ``01.5``
    # splits and the *fraction* is then read as the episode number. Reporting
    # episode 5 for episode 1.5 is worse than reporting none, which is what
    # happened before either pattern existed.
    #
    # Anchored the same way, so it cannot reach into a dotted name: a digit or
    # a dot on either side refuses the match, which rules out "31.1080"
    # (preceded by ``E``, followed by a digit) and "2.0" in ``AAC2.0``
    # (preceded by ``C`` - the audio pattern claims that one).
    r"(?<![\w.])\d{1,3}\.\d(?![\d.])",              # 01.5, 12.5
    # Added in 0.2.2.  The same decimal written inside a combined marker:
    # ``S01E12.5``.  The pattern above cannot reach it -- its ``E`` is a word
    # character, so the lookbehind refuses -- which left this spelling
    # splitting into "S01E12" and "5", reporting the recap as episode 12 and
    # overwriting a real one.
    # Anchored identically: the trailing digit of ``S22E31.1080p`` refuses it.
    r"[Ee]\d{1,3}\.\d(?![\d.])",                    # S01E12.5, E09.5
)

_NBSP = " "
_IDEOGRAPHIC_SPACE = "　"
_BOM = "﻿"

#: 1:1 character substitutions that are always safe.
_ALWAYS_MAP = {
    _NBSP: " ",
    _IDEOGRAPHIC_SPACE: " ",
    "\t": " ",
    _BOM: " ",
    "（": "(",
    "）": ")",
    # Lenticular brackets: what Chinese release groups sign their names with,
    # and structurally a square bracket in every filename that uses them.
    "【": "[",
    "】": "]",
    "［": "[",
    "］": "]",
    "｛": "{",
    "｝": "}",
}

_DASHES = "‐‑‒–—―−"

_WORD_CHAR = re.compile(r"[^\W_]", re.UNICODE)


@dataclass(frozen=True)
class NormalizationConfig:
    """Knobs for :func:`normalize`.

    Attributes:
        underscore_to_space: Treat ``_`` as a separator (``Burst_Angel``).
        dot_to_space: Treat ``.`` as a separator when it sits between two word
            characters (``Pokemon.the.Movie``, ``WEB.1080p.AV1``).
        unify_dashes: Fold unicode dashes onto ASCII ``-`` so range and
            delimiter handling only has to deal with one character.
        fold_punctuation: Apply the always-safe substitutions (full-width
            brackets, exotic spaces).
        protected_patterns: Regions that must not be rewritten.
    """

    underscore_to_space: bool = True
    dot_to_space: bool = True
    unify_dashes: bool = True
    fold_punctuation: bool = True
    protected_patterns: tuple[str, ...] = DEFAULT_PROTECTED_PATTERNS


DEFAULT_CONFIG = NormalizationConfig()


@dataclass
class NormalizedText:
    """A character-aligned, separator-normalised view of a filename stem."""

    original: str
    text: str
    protected: tuple[tuple[int, int], ...] = ()
    config: NormalizationConfig = field(default=DEFAULT_CONFIG)

    def __post_init__(self) -> None:
        if len(self.original) != len(self.text):
            raise ValueError("normalisation must be character preserving")

    def __len__(self) -> int:
        return len(self.text)

    def is_protected(self, index: int) -> bool:
        """Is the character at ``index`` inside a protected region?"""
        return any(start <= index < end for start, end in self.protected)

    def protected_span_at(self, index: int) -> tuple[int, int] | None:
        """Return the protected span covering ``index``, if any."""
        for span in self.protected:
            if span[0] <= index < span[1]:
                return span
        return None

    def raw_slice(self, start: int, end: int) -> str:
        """The normalised characters in ``[start, end)`` without clean-up."""
        return self.text[start:end]

    def render(self, start: int, end: int) -> str:
        """Return a display-ready string for the span.

        Collapses whitespace runs and strips separator punctuation from the
        edges, but keeps everything inside intact.
        """
        return clean_display(self.text[start:end])


_WS_RUN = re.compile(r"\s+")
#: Separator punctuation trimmed from the edges of rendered spans.  The
#: ellipsis is deliberately absent: a truncated episode title keeps its "...".
_EDGE_STRIP = " \t\\-~_,;:|/." + "　"


def clean_display(value: str) -> str:
    """Collapse whitespace and strip separator punctuation from both ends."""
    collapsed = _WS_RUN.sub(" ", value).strip()
    previous = None
    while previous != collapsed:
        previous = collapsed
        collapsed = collapsed.strip(_EDGE_STRIP).strip()
        for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
            if collapsed.startswith(opener) and closer not in collapsed:
                collapsed = collapsed[1:].strip()
            if collapsed.endswith(closer) and opener not in collapsed:
                collapsed = collapsed[:-1].strip()
    return collapsed


def _protected_spans(text: str, patterns: Iterable[str]) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if match.end() > match.start():
                spans.append((match.start(), match.end()))
    return tuple(_merge_spans(spans))


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _is_word_char(char: str) -> bool:
    return bool(_WORD_CHAR.match(char))


def _is_separator_neighbour(char: str) -> bool:
    """May a ``.`` next to ``char`` be acting as a separator?

    True for word characters and for a dash separator, so ``Burst.Angel.-.012``
    normalises cleanly.  False for whitespace, another dot and the string edge,
    which keeps ``Dr. Stone`` and a truncated ``Broadcast...`` intact.
    """
    return bool(char) and (_is_word_char(char) or char in "-~")


def normalize(stem: str, config: NormalizationConfig | None = None) -> NormalizedText:
    """Normalise separators in ``stem`` without changing its length.

    Args:
        stem: The filename with the extension already removed.
        config: Optional :class:`NormalizationConfig`; defaults are conservative.

    Returns:
        A :class:`NormalizedText` whose ``text`` is index-aligned with ``stem``.
    """
    cfg = config or DEFAULT_CONFIG
    protected = (
        _protected_spans(stem, cfg.protected_patterns) if cfg.protected_patterns else ()
    )
    chars = list(stem)
    length = len(chars)

    for index, char in enumerate(chars):
        if cfg.fold_punctuation and char in _ALWAYS_MAP:
            chars[index] = _ALWAYS_MAP[char]
            continue
        if any(start <= index < end for start, end in protected):
            continue
        if char == "_" and cfg.underscore_to_space:
            chars[index] = " "
        elif cfg.unify_dashes and char in _DASHES:
            chars[index] = "-"
        elif char == "." and cfg.dot_to_space:
            previous = stem[index - 1] if index > 0 else ""
            following = stem[index + 1] if index + 1 < length else ""
            if _is_separator_neighbour(previous) and _is_separator_neighbour(following):
                chars[index] = " "

    return NormalizedText(
        original=stem, text="".join(chars), protected=protected, config=cfg
    )
