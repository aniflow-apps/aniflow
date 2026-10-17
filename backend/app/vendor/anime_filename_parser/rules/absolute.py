"""Absolute episode numbers.

Long-running shows are sometimes labelled with both a per-season number and a
series-wide one::

    A Will Eternal - Season 4 Episode 9 [174] (4k)

The bracketed ``174`` is the absolute episode number.  It is only claimed when
a per-season episode is already known -- otherwise a lone bracketed number is
too ambiguous (it could be a volume, a batch size or a group's own numbering)
and is preserved as unclassified information instead.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..tokenizer import Enclosure
from .base import Rule, Stage, register

__all__ = ["AbsoluteEpisodeRule", "MAX_ABSOLUTE_EPISODE"]

MAX_ABSOLUTE_EPISODE = 9999


@register
class AbsoluteEpisodeRule(Rule):
    """Claim a bracketed number as the absolute episode number."""

    name = "absolute-episode"
    order = 220
    stage = Stage.CONTEXTUAL
    description = "A bracketed bare number when a season episode is already known."

    def apply(self, ctx: ParseContext) -> None:
        if ctx.result.episode is None:
            return
        for group in ctx.groups:
            if group.enclosure is Enclosure.NONE:
                continue
            for entry in group.meaningful_fields:
                tokens = [token for token in entry.tokens if token.is_meaningful]
                if len(tokens) != 1 or not tokens[0].available:
                    continue
                value = tokens[0].as_int()
                if value is None or not 0 < value <= MAX_ABSOLUTE_EPISODE:
                    continue
                if value == ctx.result.episode:
                    continue
                if ctx.set_field("absolute_episode", value, self.name):
                    ctx.claim(tokens[0], Roles.ABSOLUTE_EPISODE, self.name)
                    return
