"""Release flags, media type and recognised extras.

Covers the small boolean facts (``Uncensored``, ``Batch``, ``REPACK``), the
media-type words (``Movie``, ``Gekijouban``, ``OVA``, ``ONA``, ``Special``),
and the long tail of tokens that are understood but do not deserve their own
field (``Weekly``, ``Complete``, ``HDR``, ``NCOP``...), which are preserved in
``misc``.

``uncensored`` is tri-state: ``True`` for ``Uncensored``/``Uncut``, ``False``
for ``Censored``/``Cut``, and ``None`` when the filename says nothing.

Media-type words that sit inside the title stay in ``name`` -- dropping the
word from ``Pokemon the Movie`` would corrupt the title -- while the same word
inside a bracket is pure metadata.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..tokenizer import Enclosure
from ..vocab import (
    BATCH_TOKENS,
    CENSORED_TOKENS,
    KNOWN_MISC_TOKENS,
    MEDIA_TYPE_WORDS,
    MISC_TAG_NAMES,
    REPACK_TOKENS,
    UNCENSORED_TOKENS,
)
from .base import Rule, Stage, register

__all__ = ["FlagRule", "CurlyAnnotationRule"]

#: ``Cut``/``Censored`` are ordinary English words, so only trust them inside
#: brackets where they can only be release metadata.
_BRACKET_ONLY = frozenset({"cut", "censor", "cen", "censored"})


@register
class FlagRule(Rule):
    """Claim boolean flags, media-type words and recognised extras."""

    name = "flags"
    order = 190
    stage = Stage.RELEASE
    description = "Uncensored/Batch/REPACK flags, Movie/OVA/ONA media types, known extras."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            lowered = token.lower

            if lowered in UNCENSORED_TOKENS:
                ctx.set_flag("uncensored", True, self.name)
                ctx.claim(token, Roles.FLAG, self.name)
                continue

            if lowered in CENSORED_TOKENS:
                if lowered in _BRACKET_ONLY and token.enclosure is Enclosure.NONE:
                    continue
                ctx.set_flag("uncensored", False, self.name)
                ctx.claim(token, Roles.FLAG, self.name)
                continue

            if lowered in BATCH_TOKENS:
                ctx.set_flag("batch", True, self.name)
                ctx.claim(token, Roles.FLAG, self.name)
                continue

            if lowered in REPACK_TOKENS:
                ctx.set_flag("repack", True, self.name)
                ctx.claim(token, Roles.FLAG, self.name)
                continue

            if lowered in MEDIA_TYPE_WORDS:
                keep = ctx.is_in_title_field(token)
                ctx.set_field("media_type", MEDIA_TYPE_WORDS[lowered], self.name)
                ctx.claim(token, Roles.MEDIA_TYPE, self.name, keep_in_title=keep)
                continue

            if lowered in KNOWN_MISC_TOKENS and self._extra_is_metadata(ctx, token):
                text = ctx.render(token.start, token.end)
                ctx.add_misc(text)
                ctx.add_tag(MISC_TAG_NAMES.get(lowered, text))
                ctx.claim(token, Roles.MISC, self.name)


    # ------------------------------------------------------------------
    @staticmethod
    def _extra_is_metadata(ctx: ParseContext, token) -> bool:
        """Is this recognised extra release metadata rather than a title word?

        Inside a bracket it can only be metadata.  Outside one, capitals alone
        are not enough: plenty of shows are titled in capitals, and ``RAW HERO``
        or ``BONUS TRACK`` would lose their first word.  Two further conditions
        put the token unambiguously in the *metadata tail*:

        * something earlier in the same run is already claimed, so the title is
          over -- ``...S01E01.RAW`` qualifies, a leading ``RAW HERO...`` does not;
        * nothing unclaimed follows it in its field, so it is not the first word
          of an episode title -- ``- 01 - RAW EMOTION`` keeps its name.
        """
        if token.enclosure is not Enclosure.NONE:
            return True
        if len(token.text) < 3 or not token.text.isupper():
            return False

        group = ctx.groups[token.group_index]
        earlier_claimed = any(
            other.is_meaningful and other.consumed_by is not None
            for other in group.tokens[: token.position]
        )
        later_free = any(
            other.is_meaningful and other.available
            and other.field_index == token.field_index
            for other in group.tokens[token.position + 1 :]
        )
        return earlier_claimed and not later_free


@register
class CurlyAnnotationRule(Rule):
    """Preserve ``{...}`` annotations such as ``{OP & ED Lyrics}`` verbatim.

    Braces are used by a few groups for free-form notes.  There is nothing to
    decompose, but the content must not be lost, so the whole group is kept as
    a single ``misc`` entry.
    """

    name = "curly-annotation"
    order = 185
    stage = Stage.RELEASE
    description = "Keep brace-delimited annotations as one misc entry."

    def apply(self, ctx: ParseContext) -> None:
        for group in ctx.groups:
            if group.enclosure is not Enclosure.CURLY or group.none_consumed is False:
                continue
            text = ctx.render_group(group)
            if not text:
                continue
            ctx.add_misc(text)
            ctx.add_tag(text)
            ctx.claim(group.tokens, Roles.MISC, self.name)
