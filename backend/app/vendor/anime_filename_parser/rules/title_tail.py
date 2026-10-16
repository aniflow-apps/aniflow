"""The ambiguous number (or Roman numeral) at the end of a title.

``Jujutsu Kaisen 2`` is genuinely ambiguous: the ``2`` could be an episode, a
season, a part or even part of the title.  This rule resolves it *from
context* -- never from a list of known series -- and always says what it did.

The contextual rule, in one sentence: **a trailing number is a season when an
episode has already been found somewhere else, and an episode otherwise.**

    Jujutsu Kaisen 2        -> name="Jujutsu Kaisen 2", episode=2, season=None
    Jujutsu Kaisen 2 - 2    -> name="Jujutsu Kaisen 2", episode=2, season=2
    Jujutsu Kaisen S2 - 2   -> name="Jujutsu Kaisen",   episode=2, season=2
    Jujutsu Kaisen II - 2   -> name="Jujutsu Kaisen II", episode=2, season=2

A Roman numeral is never read as an episode number -- no release group counts
episodes that way -- so it only ever becomes a season.  Single-letter numerals
(``I``, ``V``, ``X``) are ignored entirely, being far more likely to be part
of the title.

In every case the token *stays in* ``name`` (that is how the release is
actually written) and is stripped from ``only_name``, which is what makes
``only_name`` comparable across release groups.  Because it runs after all
technical rules, the trailing token examined here is guaranteed not to be
metadata.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..layout import compute_layout
from ..models import MediaType
from ..vocab import QUANTITY_WORDS, is_roman, roman_to_int
from .base import Rule, Stage, register
from .year import looks_like_year

__all__ = ["TitleTailRule", "MAX_SEASON", "MAX_EPISODE"]

#: A trailing number above this is not plausibly a season.
MAX_SEASON = 40
#: ... nor an episode.
MAX_EPISODE = 999


@register
class TitleTailRule(Rule):
    """Interpret a number/Roman numeral glued to the end of the title."""

    name = "title-tail"
    order = 210
    stage = Stage.CONTEXTUAL
    description = "Resolve a trailing number or Roman numeral into season or episode."

    def apply(self, ctx: ParseContext) -> None:
        layout = compute_layout(ctx)
        tokens = layout.title_tokens()
        if len(tokens) < 2:
            # Nothing to fall back on: claiming the only token would leave the
            # release without a title at all.
            return

        last = tokens[-1]
        if not last.available:
            return

        previous = tokens[-2]
        if previous.lower.rstrip(".") in QUANTITY_WORDS:
            # ``Level 999``, ``Vol 3``: the word before the number claims it,
            # so the number is title material and nothing else.
            return

        if is_roman(last.text):
            self._as_season(ctx, last, roman_to_int(last.text), roman=True)
            return

        value = last.as_int()
        if value is None or looks_like_year(last.text):
            return

        result = ctx.result
        if result.episode is not None or result.episode_start is not None:
            self._as_season(ctx, last, value, roman=False)
        elif result.media_type == MediaType.MOVIE:
            # "Jujutsu Kaisen 0 The Movie": a movie has no episode number, and
            # the digit is part of how the film is titled.
            return
        else:
            self._as_episode(ctx, last, value)

    # ------------------------------------------------------------------
    def _as_season(self, ctx: ParseContext, token, value: int | None, *, roman: bool) -> None:
        if value is None or not 2 <= value <= MAX_SEASON:
            return
        if ctx.result.season is not None:
            # Already known from an explicit marker; leave the token in the title.
            return
        ctx.set_field("season", value, self.name, source_text=token.text)
        ctx.claim(token, Roles.SEASON, self.name, keep_in_title=True)
        ctx.title_tail_token = token
        if roman:
            ctx.add_warning(
                f"Roman numeral {token.text!r} at the end of the title interpreted "
                f"as season {value}; kept in 'name' and stripped from 'only_name'."
            )
        else:
            ctx.add_warning(
                f"Ambiguous numeric token {token.text!r}: interpreted as season "
                "because an explicit episode was found elsewhere in the filename."
            )

    def _as_episode(self, ctx: ParseContext, token, value: int) -> None:
        if value > MAX_EPISODE:
            return
        ctx.set_field("episode", value, self.name, source_text=token.text)
        ctx.claim(token, Roles.EPISODE, self.name, keep_in_title=True)
        ctx.title_tail_token = token
        ctx.add_warning(
            f"Ambiguous numeric token {token.text!r}: interpreted as episode based on "
            "filename structure (no explicit season or episode marker present)."
        )
