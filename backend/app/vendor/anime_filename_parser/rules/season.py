"""Standalone season, part and cour designations.

Handles ``S2``, ``S01``, ``Season 1``, ``Season 01``, ``2nd Season``,
``Second Season``, ``Final Season``, ``Part 6`` and ``Cour 2``.

Two spellings are treated differently on purpose:

``S2`` / ``S01``
    Pure metadata.  It is removed from the title, so
    ``Jujutsu Kaisen S2 - 2`` yields ``name = "Jujutsu Kaisen"``.

``Season 2`` / ``2nd Season`` / ``Part 6`` / ``Final Season``
    When these appear *inside the title itself* they are part of how the
    release is named, so they stay in ``name`` (``My Hero Academia Final
    Season``, ``JoJo's Bizarre Adventure Part 6``) and are only stripped when
    deriving ``only_name``.  Everywhere else (a bracket, a later field) they
    are plain metadata.

Roman numerals are **not** handled here -- see
:mod:`anime_filename_parser.rules.title_tail`, where enough context is
available to tell a season number from part of a title.
"""

from __future__ import annotations

import re

from ..context import ParseContext, Roles
from ..tokenizer import Token
from ..vocab import (
    COUR_WORDS,
    ORDINAL_SUFFIX_RE,
    ORDINAL_WORDS,
    PART_WORDS,
    SEASON_WORDS,
    is_roman,
    roman_to_int,
)
from .base import Rule, Stage, register

__all__ = ["SeasonRule"]

_COMPACT_SEASON_RE = re.compile(r"^S(?P<number>\d{1,2})$")
#: ``Season2`` written without a separator.
_GLUED_SEASON_RE = re.compile(r"^(?:season|saison)(?P<number>\d{1,2})$", re.IGNORECASE)
_UNNUMBERED_QUALIFIERS = frozenset({"final", "last", "new"})


def _number_from(token: Token | None, anchor: Token | None = None) -> int | None:
    """Interpret a token as a season/part number (digits, ``2nd`` or ``II``).

    When ``anchor`` is given, ``token`` must live in the same field, so a
    number from an unrelated field (``Foo - 12 - Season Finale``) is ignored.
    """
    if token is None or not token.available:
        return None
    if anchor is not None and (
        token.group_index != anchor.group_index or token.field_index != anchor.field_index
    ):
        return None
    value = token.as_int()
    if value is not None:
        return value
    ordinal = ORDINAL_SUFFIX_RE.match(token.text)
    if ordinal:
        return int(ordinal.group("number"))
    if token.lower in ORDINAL_WORDS:
        return ORDINAL_WORDS[token.lower]
    if is_roman(token.text):
        return roman_to_int(token.text)
    return None


@register
class SeasonRule(Rule):
    """Claim explicit season/part/cour designations."""

    name = "season"
    order = 120
    stage = Stage.STRUCTURE
    description = "S2, Season 1, 2nd Season, Final Season, Part 6, Cour 2."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue

            compact = _COMPACT_SEASON_RE.match(token.text) or _GLUED_SEASON_RE.match(token.text)
            if compact is not None:
                # Compact metadata: never part of the title.
                if ctx.set_field("season", int(compact.group("number")), self.name,
                                 source_text=token.text):
                    ctx.claim(token, Roles.SEASON, self.name)
                continue

            lowered = token.lower
            if lowered in SEASON_WORDS:
                self._season_word(ctx, token)
            elif lowered in PART_WORDS:
                self._numbered(ctx, token, "part", Roles.PART)
            elif lowered in COUR_WORDS:
                self._numbered(ctx, token, "cour", Roles.COUR)

    # ------------------------------------------------------------------
    def _season_word(self, ctx: ParseContext, token: Token) -> None:
        """Handle the word ``Season`` in any of its surrounding shapes."""
        keep = ctx.is_in_title_field(token)

        previous = ctx.previous_token(token)
        number = _number_from(previous, token)
        if number is not None:
            # "2nd Season", "Second Season", "6th Season"
            ctx.set_field("season", number, self.name, source_text=ctx.render_tokens([previous, token]))
            ctx.claim([previous, token], Roles.SEASON, self.name, keep_in_title=keep)
            return

        following = ctx.next_token(token)
        number = _number_from(following, token)
        if number is not None:
            # "Season 1", "Season 01", "Season II"
            ctx.set_field("season", number, self.name, source_text=ctx.render_tokens([token, following]))
            ctx.claim([token, following], Roles.SEASON, self.name, keep_in_title=keep)
            return

        if previous is not None and previous.lower in _UNNUMBERED_QUALIFIERS:
            # "Final Season": a real designation, but not a number.  Keep it in
            # the title and let only_name strip it.
            ctx.claim([previous, token], Roles.SEASON_DESIGNATION, self.name, keep_in_title=keep)
            return

        # A lone "Season" with no number: preserve it rather than guess.
        ctx.claim(token, Roles.SEASON_DESIGNATION, self.name, keep_in_title=keep)
        ctx.add_warning(
            "Found the word 'Season' without a usable number; season left unset."
        )

    def _numbered(self, ctx: ParseContext, token: Token, field: str, role: str) -> None:
        """Handle ``Part 6`` / ``Cour 2`` (number may precede or follow)."""
        keep = ctx.is_in_title_field(token)
        for candidate in (ctx.next_token(token), ctx.previous_token(token)):
            number = _number_from(candidate, token)
            if number is None:
                continue
            source = ctx.render_tokens(sorted([token, candidate], key=lambda t: t.start))
            ctx.set_field(field, number, self.name, source_text=source)
            ctx.claim([token, candidate], role, self.name, keep_in_title=keep)
            return
