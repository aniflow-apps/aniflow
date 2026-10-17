"""Delimiter-aware tokenizer.

The tokenizer turns a :class:`~.normalization.NormalizedText` into

* a list of :class:`Group` objects -- one per bracket/parenthesis/brace pair
  plus one per free-text run between them -- and
* a flat, ordered list of :class:`Token` objects carrying exact spans.

Different delimiters mean different things in anime filenames, so they are
*not* flattened away: ``[SubsPlease]``, ``(1080p)`` and ``{OP & ED Lyrics}``
stay distinguishable, and each group is further split into *fields* on the
delimiters that separate independent pieces of information (`` - ``, ``|``,
``/`` and -- inside brackets only -- ``,`` and ``;``).

Nothing is ever removed here: rules mark tokens as consumed, and whatever is
left over at the end becomes ``misc``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

__all__ = [
    "Enclosure",
    "TokenKind",
    "Token",
    "Field",
    "Group",
    "tokenize",
    "is_cjk",
    "contains_cjk",
]


class Enclosure(str, Enum):
    """What kind of brackets a group is wrapped in."""

    NONE = "text"
    SQUARE = "square"
    PAREN = "paren"
    CURLY = "curly"
    ANGLE = "angle"


class TokenKind(str, Enum):
    """Coarse shape of a token, used by rules as a cheap pre-filter."""

    WORD = "word"        # contains letters only
    NUMBER = "number"    # digits only
    ALNUM = "alnum"      # mixed (S01E09, 10bit, x265, 01v2)
    DELIMITER = "delim"  # structural separator ( - | / , ; )
    PUNCT = "punct"      # no letters and no digits


_OPENERS = {"[": Enclosure.SQUARE, "(": Enclosure.PAREN, "{": Enclosure.CURLY}
_CLOSERS = {"]": "[", ")": "(", "}": "{"}

#: Characters that always separate tokens.
_HARD_SPACE = " \n\r\t\\"
#: Characters that separate independent fields inside a group.
_FIELD_DELIMS = "|/"
#: Characters that separate fields, but only inside a bracketed group (in free
#: text a comma is ordinary punctuation: ``Kanojo, Okarishimasu``).
_BRACKET_FIELD_DELIMS = ",;"

_TOKEN_EDGE_STRIP = " \t,;:|/\\!?\"'`" + "…"

_DIGITS = re.compile(r"^\d+$")
_LETTERS = re.compile(r"^[^\W\d_]+$", re.UNICODE)
_HAS_ALNUM = re.compile(r"[^\W_]", re.UNICODE)

_CJK_RANGES = (
    (0x3040, 0x30FF),    # hiragana + katakana
    (0x3400, 0x4DBF),    # CJK ext A
    (0x4E00, 0x9FFF),    # CJK unified
    (0xF900, 0xFAFF),    # CJK compatibility
    (0xFF66, 0xFF9F),    # half-width katakana
    (0xAC00, 0xD7AF),    # hangul syllables
    (0x1100, 0x11FF),    # hangul jamo
)


#: One compiled character class instead of a per-character range scan: script
#: detection runs over every token of every filename.
_CJK_RE = re.compile(
    "[" + "".join(f"{chr(low)}-{chr(high)}" for low, high in _CJK_RANGES) + "]"
)


def is_cjk(char: str) -> bool:
    """Is ``char`` a CJK/Japanese/Korean character?"""
    return bool(_CJK_RE.match(char))


def contains_cjk(text: str) -> bool:
    """Does ``text`` contain at least one CJK/Japanese/Korean character?"""
    return bool(_CJK_RE.search(text))


@dataclass
class Token:
    """A single indivisible piece of a filename.

    Attributes:
        text: Normalised text with edge punctuation removed -- what rules match on.
        raw: The normalised slice exactly as it appears.
        start: Inclusive start index into the normalised text / original stem.
        end: Exclusive end index.
        enclosure: Bracket type of the containing group.
        group_index: Index into :attr:`Group.index` of the containing group.
        field_index: Index of the containing :class:`Field` inside that group.
        position: Index of the token inside its group.
        index: Global token order.
        kind: See :class:`TokenKind`.
        delimiter: The delimiter character when ``kind`` is ``DELIMITER``.
        consumed_by: Name of the rule that claimed this token, if any.
        role: What the token was understood to be (``"episode"``, ``"quality"``...).
        keep_in_title: A claimed token that must still appear in ``name``
            (``Part 6``, ``The Movie``, an ambiguous trailing number).
    """

    text: str
    raw: str
    start: int
    end: int
    enclosure: Enclosure
    group_index: int
    field_index: int
    position: int
    index: int
    kind: TokenKind
    delimiter: str | None = None
    consumed_by: str | None = None
    role: str | None = None
    keep_in_title: bool = False

    @property
    def consumed(self) -> bool:
        """Has a rule claimed this token?"""
        return self.consumed_by is not None

    @property
    def available(self) -> bool:
        """Is this token still free for a rule to claim?"""
        return self.consumed_by is None and self.kind is not TokenKind.DELIMITER

    @property
    def is_meaningful(self) -> bool:
        """Does the token carry letters or digits (i.e. is it worth keeping)?"""
        return bool(_HAS_ALNUM.search(self.text))

    @property
    def lower(self) -> str:
        return self.text.lower()

    def as_int(self) -> int | None:
        """Return the token as an ``int`` when it is purely numeric."""
        return int(self.text) if _DIGITS.match(self.text) else None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        state = self.consumed_by or "-"
        return f"Token({self.text!r}, {self.enclosure.value}, {state})"


@dataclass
class Field:
    """A delimiter-separated run of tokens inside a group."""

    index: int
    tokens: list[Token]
    leading_delimiter: str | None = None

    @property
    def start(self) -> int:
        return self.tokens[0].start

    @property
    def end(self) -> int:
        return self.tokens[-1].end

    @property
    def text_tokens(self) -> list[Token]:
        return [t for t in self.tokens if t.kind is not TokenKind.DELIMITER]

    @property
    def is_meaningful(self) -> bool:
        return any(t.is_meaningful for t in self.tokens)


@dataclass
class Group:
    """A bracketed section, or a free-text run between bracketed sections."""

    index: int
    enclosure: Enclosure
    depth: int
    parent: int | None
    start: int
    end: int
    tokens: list[Token] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)

    @property
    def is_bracketed(self) -> bool:
        return self.enclosure is not Enclosure.NONE

    @property
    def meaningful_fields(self) -> list[Field]:
        """Fields that contain at least one letter or digit."""
        return [f for f in self.fields if f.is_meaningful]

    @property
    def available_tokens(self) -> list[Token]:
        return [t for t in self.tokens if t.available]

    @property
    def all_consumed(self) -> bool:
        return all(not t.available for t in self.tokens)

    @property
    def none_consumed(self) -> bool:
        return all(t.consumed_by is None for t in self.tokens if t.kind is not TokenKind.DELIMITER)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Group({self.enclosure.value}[{self.index}], {self.tokens!r})"


# ----------------------------------------------------------------------
# bracket structure
# ----------------------------------------------------------------------
def _find_pairs(text: str) -> dict[int, int]:
    """Map opener index -> closer index for every *matched* bracket pair.

    Unmatched brackets are simply not reported and end up being treated as
    ordinary characters, so a filename like ``[Group Title (1080p].mkv`` still
    parses instead of raising.
    """
    stack: list[tuple[str, int]] = []
    pairs: dict[int, int] = {}
    for index, char in enumerate(text):
        if char in _OPENERS:
            stack.append((char, index))
        elif char in _CLOSERS:
            wanted = _CLOSERS[char]
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == wanted:
                    pairs[stack[depth][1]] = index
                    del stack[depth:]
                    break
    return pairs


def _child_pairs(pairs: dict[int, int], start: int, end: int) -> list[tuple[int, int]]:
    """Top-level bracket pairs fully contained in ``[start, end)``."""
    children: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        closer = pairs.get(cursor)
        if closer is not None and closer < end:
            children.append((cursor, closer))
            cursor = closer + 1
        else:
            cursor += 1
    return children


def _gaps(start: int, end: int, children: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """The character ranges of ``[start, end)`` not covered by ``children``."""
    ranges: list[tuple[int, int]] = []
    cursor = start
    for opener, closer in children:
        if opener > cursor:
            ranges.append((cursor, opener))
        cursor = closer + 1
    if cursor < end:
        ranges.append((cursor, end))
    return ranges


# ----------------------------------------------------------------------
# token splitting
# ----------------------------------------------------------------------
def _is_standalone_dash(text: str, index: int, start: int, end: int) -> bool:
    """A ``-``/``~`` acts as a field delimiter only when isolated by spaces.

    ``Kaitou - Hubuki`` splits; ``WEB-DL``, ``Hananoi-kun`` and ``37-48`` do not.
    """
    if text[index] not in "-~":
        return False
    before_ok = index == start or text[index - 1].isspace()
    after_ok = index + 1 >= end or text[index + 1].isspace()
    return before_ok and after_ok


def _is_break_char(char: str, bracketed: bool) -> bool:
    if char.isspace() or char in _HARD_SPACE:
        return True
    if char in _FIELD_DELIMS:
        return True
    if bracketed and char in _BRACKET_FIELD_DELIMS:
        return True
    return char in _OPENERS or char in _CLOSERS


def _classify(text: str) -> TokenKind:
    if not text:
        return TokenKind.PUNCT
    if _DIGITS.match(text):
        return TokenKind.NUMBER
    if _LETTERS.match(text):
        return TokenKind.WORD
    if _HAS_ALNUM.search(text):
        return TokenKind.ALNUM
    return TokenKind.PUNCT


class _State:
    """Accumulator shared by the recursive group walk."""

    def __init__(self) -> None:
        self.groups: list[Group] = []
        self.tokens: list[Token] = []


def _strip_token_edges(raw: str) -> str:
    text = raw
    previous = None
    while previous != text:
        previous = text
        text = text.strip(_TOKEN_EDGE_STRIP).strip("-.").strip()
    return text


def _add_token(
    state: _State,
    group: Group,
    raw: str,
    start: int,
    end: int,
    *,
    delimiter: str | None = None,
) -> None:
    text = raw.strip() if delimiter else _strip_token_edges(raw)
    token = Token(
        text=text,
        raw=raw,
        start=start,
        end=end,
        enclosure=group.enclosure,
        group_index=group.index,
        field_index=0,
        position=len(group.tokens),
        index=len(state.tokens),
        kind=TokenKind.DELIMITER if delimiter else _classify(text),
        delimiter=delimiter,
    )
    group.tokens.append(token)
    state.tokens.append(token)


def _tokenize_ranges(
    nt, group: Group, ranges: list[tuple[int, int]], state: _State
) -> None:
    """Split the given character ranges of ``nt`` into tokens of ``group``."""
    bracketed = group.is_bracketed
    text = nt.text
    for range_start, range_end in ranges:
        cursor = range_start
        while cursor < range_end:
            char = text[cursor]
            protected = nt.protected_span_at(cursor)
            if protected is not None and protected[0] == cursor:
                stop = min(protected[1], range_end)
                _add_token(state, group, text[cursor:stop], cursor, stop)
                cursor = stop
                continue
            if char.isspace() or char in _HARD_SPACE:
                cursor += 1
                continue
            if char in _FIELD_DELIMS or (bracketed and char in _BRACKET_FIELD_DELIMS):
                _add_token(state, group, char, cursor, cursor + 1, delimiter=char)
                cursor += 1
                continue
            if _is_standalone_dash(text, cursor, range_start, range_end):
                _add_token(state, group, char, cursor, cursor + 1, delimiter="-")
                cursor += 1
                continue
            if char in _OPENERS or char in _CLOSERS:
                # An unmatched bracket: skip the character, the enclosing span
                # still covers it for display purposes.
                cursor += 1
                continue
            stop = cursor
            while stop < range_end:
                span = nt.protected_span_at(stop)
                if span is not None and span[0] <= stop < span[1]:
                    stop = min(span[1], range_end)
                    continue
                if _is_break_char(text[stop], bracketed) or _is_standalone_dash(
                    text, stop, range_start, range_end
                ):
                    break
                stop += 1
            _add_token(state, group, text[cursor:stop], cursor, stop)
            cursor = max(stop, cursor + 1)


def _build_fields(group: Group) -> None:
    """Split a group's tokens into delimiter-separated :class:`Field` objects."""
    built: list[Field] = []
    current: list[Token] = []
    pending: str | None = None
    for token in group.tokens:
        if token.kind is TokenKind.DELIMITER:
            if current:
                built.append(Field(len(built), current, pending))
                current = []
            pending = token.delimiter
            continue
        current.append(token)
    if current:
        built.append(Field(len(built), current, pending))
    for entry in built:
        for token in entry.tokens:
            token.field_index = entry.index
    group.fields = built


def _emit_bracket(
    nt,
    pairs: dict[int, int],
    opener: int,
    closer: int,
    depth: int,
    parent: int | None,
    state: _State,
) -> None:
    group = Group(
        index=len(state.groups),
        enclosure=_OPENERS[nt.text[opener]],
        depth=depth,
        parent=parent,
        start=opener + 1,
        end=closer,
    )
    state.groups.append(group)
    children = _child_pairs(pairs, opener + 1, closer)
    _tokenize_ranges(nt, group, _gaps(opener + 1, closer, children), state)
    _build_fields(group)
    for child_opener, child_closer in children:
        _emit_bracket(nt, pairs, child_opener, child_closer, depth + 1, group.index, state)


def _emit_text_run(nt, start: int, end: int, state: _State) -> None:
    if start >= end or not nt.text[start:end].strip():
        return
    group = Group(
        index=len(state.groups),
        enclosure=Enclosure.NONE,
        depth=0,
        parent=None,
        start=start,
        end=end,
    )
    state.groups.append(group)
    _tokenize_ranges(nt, group, [(start, end)], state)
    _build_fields(group)


def tokenize(nt) -> tuple[list[Group], list[Token]]:
    """Tokenize a :class:`~.normalization.NormalizedText`.

    Args:
        nt: The normalised filename stem.

    Returns:
        ``(groups, tokens)``: groups in document order (a nested group directly
        follows its parent) and the flat token stream in reading order.
    """
    pairs = _find_pairs(nt.text)
    state = _State()
    cursor = 0
    for opener, closer in _child_pairs(pairs, 0, len(nt)):
        _emit_text_run(nt, cursor, opener, state)
        _emit_bracket(nt, pairs, opener, closer, 1, None, state)
        cursor = closer + 1
    _emit_text_run(nt, cursor, len(nt), state)

    ordered = sorted(state.tokens, key=lambda t: t.start)
    for order, token in enumerate(ordered):
        token.index = order
    return state.groups, ordered
