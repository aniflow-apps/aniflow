"""Year detection.

A four-digit number is only accepted as a year when it stands **alone inside a
bracket or parenthesis** -- ``(2022)``, ``(2005)``, ``(2026)``.  That is the
one position where release groups consistently put a year, and it is the only
position where the number cannot plausibly be an episode.

Everything else is left alone.  ``Case Closed - 0682`` is an episode, and a
loose ``2022`` in the middle of a filename is genuinely ambiguous, so it stays
unclassified (and therefore preserved in ``misc``) with a warning rather than
being guessed at.
"""

from __future__ import annotations

import re
from datetime import date

from ..context import ParseContext, Roles
from ..tokenizer import Enclosure
from .base import Rule, Stage, register

__all__ = ["YearRule", "MIN_YEAR", "max_year"]

MIN_YEAR = 1900
#: Releases are occasionally labelled a year or two ahead of the current one.
_FUTURE_SLACK = 2

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def max_year() -> int:
    """The newest year the parser will accept."""
    return date.today().year + _FUTURE_SLACK


def looks_like_year(text: str) -> bool:
    """Is ``text`` a plausible release year?"""
    return bool(_YEAR_RE.match(text)) and MIN_YEAR <= int(text) <= max_year()


@register
class YearRule(Rule):
    """Claim ``(2022)``-style years."""

    name = "year"
    order = 200
    stage = Stage.RELEASE
    description = "A four-digit year alone inside brackets."

    def apply(self, ctx: ParseContext) -> None:
        for group in ctx.groups:
            if group.enclosure is Enclosure.NONE:
                continue
            for entry in group.meaningful_fields:
                tokens = [token for token in entry.tokens if token.is_meaningful]
                if len(tokens) != 1:
                    continue
                token = tokens[0]
                if not token.available or not looks_like_year(token.text):
                    continue
                ctx.set_field("year", int(token.text), self.name, source_text=token.text)
                ctx.claim(token, Roles.YEAR, self.name)

        self._warn_about_loose_years(ctx)

    def _warn_about_loose_years(self, ctx: ParseContext) -> None:
        for token in ctx.available_tokens():
            if looks_like_year(token.text) and not ctx.is_in_title_field(token):
                ctx.add_warning(
                    f"Ambiguous numeric token {token.text!r}: looks like a year but is "
                    "not bracketed; left unclassified and preserved in 'misc'."
                )
