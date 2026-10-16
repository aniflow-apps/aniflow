"""Release group (subgroup) detection.

A leading ``[...]`` is *usually* the release group -- but only usually.  These
are not groups::

    [1080p] [Batch] [Uncensored] [BD] [WEB 1080p] [HEVC 10bit x265] [Multi Sub]

Rather than maintaining a blocklist, this rule runs **after** every technical
rule and only considers brackets whose contents nobody else claimed.  That is
what makes it extensible: teaching the parser a new technical token
automatically stops it from being mistaken for a release group.

Three positions are recognised, in order of confidence:

1.  A fully unclaimed bracket group *before* the title -- the normal case.
2.  A fully unclaimed bracket group *after* the title, next to a CRC32 or at
    the very end -- ``Ace Attorney - 01 - The First Turnabout [Judas] [1D05CD09]``.
3.  A scene-style tag glued to the codec -- ``H.264-VARYG``.

Anything else that looks like a second candidate is preserved in ``misc`` with
a warning instead of overwriting the first choice.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..tokenizer import Enclosure, Group, contains_cjk
from ..vocab import CJK_SUBTITLE_GROUP_SUFFIXES
from .base import Rule, Stage, register

__all__ = ["SubgroupRule", "MAX_SUBGROUP_TOKENS"]

#: ``[Kaitou - Hubuki]`` is fine; a whole sentence in brackets is not a group.
MAX_SUBGROUP_TOKENS = 4


@register
class SubgroupRule(Rule):
    """Claim the release group, or explain why it could not be identified."""

    name = "subgroup"
    order = 230
    stage = Stage.NAMING
    description = "Unclassified bracket before the title, next to a CRC32, or a codec tag."

    def apply(self, ctx: ParseContext) -> None:
        candidates = [group for group in ctx.groups if self._is_candidate(ctx, group)]
        title_index = ctx.title_run.index if ctx.title_run is not None else None

        leading = [
            group
            for group in candidates
            if title_index is None or group.index < title_index
        ]
        # A CJK bracket is only a release group when it signs the front of the
        # name (``[百冬练习组&LoliHouse] ...``).  Anywhere else CJK text in a
        # bracket is an alternative title, so it stays a candidate for those.
        leading = [group for group in leading if self._may_be_cjk_group(ctx, group, title_index)]
        if leading:
            self._claim(ctx, leading[0])
            for extra in leading[1:]:
                ctx.add_misc(ctx.render_group(extra))
                ctx.claim(extra.tokens, Roles.MISC, self.name)
                ctx.add_warning(
                    f"Multiple unclassified leading brackets; {ctx.render_group(extra)!r} "
                    "was not treated as the release group."
                )
            return

        trailing = [
            group
            for group in candidates
            if title_index is not None
            and group.index > title_index
            and not contains_cjk(ctx.render_group(group))
            and self._is_trailing_position(ctx, group)
        ]
        if trailing:
            self._claim(ctx, trailing[-1])
            return

        if ctx.codec_release_tag:
            ctx.set_field("subgroup", ctx.codec_release_tag, self.name)
            ctx.codec_release_tag = None

    # ------------------------------------------------------------------
    def _is_candidate(self, ctx: ParseContext, group: Group) -> bool:
        if group.enclosure not in (Enclosure.SQUARE, Enclosure.PAREN):
            return False
        tokens = [token for token in group.tokens if token.is_meaningful]
        if not tokens or len(tokens) > MAX_SUBGROUP_TOKENS:
            return False
        if any(not token.available for token in tokens):
            return False
        text = ctx.render_group(group)
        if not text:
            return False
        if all(token.as_int() is not None for token in tokens):
            # ``[50]``, ``[174]``: an episode number, never a group name.
            return False
        return True

    def _may_be_cjk_group(
        self, ctx: ParseContext, group: Group, title_index: int | None
    ) -> bool:
        text = ctx.render_group(group)
        if not contains_cjk(text):
            return True
        if title_index is not None:
            return group.index < title_index
        # No free text anywhere: every bracket is a candidate, and the first
        # one is as likely to be the title as the group.  Only a name that says
        # it is a fansub group (``字幕社``, ``字幕组``) settles it.
        return any(suffix in text for suffix in CJK_SUBTITLE_GROUP_SUFFIXES)

    def _is_trailing_position(self, ctx: ParseContext, group: Group) -> bool:
        """Only trust a trailing bracket next to a CRC32 or at the very end."""
        neighbours = (group.index - 1, group.index + 1)
        for index in neighbours:
            if 0 <= index < len(ctx.groups):
                sibling = ctx.groups[index]
                if any(token.role == Roles.CRC32 for token in sibling.tokens):
                    return True
        if group.enclosure is Enclosure.SQUARE and group.index == len(ctx.groups) - 1:
            return True
        return False

    def _claim(self, ctx: ParseContext, group: Group) -> None:
        text = ctx.render_group(group)
        if ctx.set_field("subgroup", text, self.name):
            ctx.claim(group.tokens, Roles.SUBGROUP, self.name)
            if ctx.codec_release_tag:
                ctx.add_misc(ctx.codec_release_tag)
                ctx.codec_release_tag = None
