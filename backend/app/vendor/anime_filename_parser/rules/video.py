"""Video codec, encoder and bit depth.

The model separates two things release groups often mention together:

``video_codec``
    The codec *family* -- ``AVC``, ``HEVC``, ``AV1``...
``encoding``
    The concrete encoder string as written -- ``x265``, ``H.264``.

So ``[HEVC 10bit x265]`` yields ``video_codec="HEVC"``, ``encoding="x265"``,
``bit_depth=10``, and a lone ``H.264`` yields ``video_codec="AVC"``,
``encoding="H.264"``.

Scene-style group suffixes (``H.264-VARYG``) are split here: the codec is
understood and the trailing tag is offered to the subgroup rule.
"""

from __future__ import annotations

import re

from ..context import ParseContext, Roles
from ..vocab import BIT_DEPTH_RE, HI10_TOKENS, VIDEO_CODECS
from .base import Rule, Stage, register

__all__ = ["VideoRule"]

#: ``H.264-VARYG``, ``x265-GROUP``: codec plus a release-group tag.
_CODEC_GROUP_RE = re.compile(
    r"^(?P<codec>[Hh]\.?26[45]|[xX]26[45]|AVC|HEVC|AV1|VP9)-(?P<group>[A-Za-z0-9]{2,})$"
)
#: ``264-VARYG`` -- the codec's ``H``/``x`` prefix sits in the previous token
#: because the release wrote ``H 264`` with a space instead of ``H.264``.
_SPLIT_CODEC_RE = re.compile(r"^(?P<codec>26[45])(?:-(?P<group>[A-Za-z0-9]{2,}))?$")

#: ``10bit`` split across two tokens: ``10`` ``bit``.
_BIT_WORDS = frozenset({"bit", "bits"})

#: Suffixes after a codec that are technical statements, never release groups.
_NEVER_RELEASE_TAGS = frozenset(
    {"10bit", "8bit", "12bit", "10bits", "8bits", "hi10p", "hi10", "bit", "bits"}
)


@register
class VideoRule(Rule):
    """Claim codec, encoder and bit-depth tokens."""

    name = "video"
    order = 150
    stage = Stage.TECHNICAL
    description = "x264/x265/H.264/HEVC/AV1 codecs, encoders and 8/10/12 bit depth."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            lowered = token.lower

            group_match = _CODEC_GROUP_RE.match(token.text)
            if group_match is not None:
                self._set_codec(ctx, group_match.group("codec"))
                suffix = group_match.group("group")
                depth = BIT_DEPTH_RE.match(suffix)
                if depth is not None:
                    # ``HEVC-10bit`` is one codec statement, not a codec plus a
                    # release group.  Without this the bit depth was offered to
                    # the subgroup rule and released as ``subgroup='10bit'``.
                    ctx.set_field("bit_depth", int(depth.group("depth")), self.name)
                elif suffix.lower() not in _NEVER_RELEASE_TAGS:
                    # Handed to the subgroup rule, which decides whether this is
                    # really the release group or just an unclassified tag.
                    ctx.codec_release_tag = suffix
                ctx.claim(token, Roles.VIDEO, self.name)
                continue

            split_match = _SPLIT_CODEC_RE.match(token.text)
            if split_match is not None:
                # ``H 264-VARYG``: some groups write the codec with a space
                # where the dot belongs, which splits it across two tokens.
                previous = ctx.previous_token(token)
                if (
                    previous is not None
                    and previous.available
                    and previous.lower in ("h", "x")
                    and previous.field_index == token.field_index
                ):
                    self._set_codec(ctx, f"{previous.text}.{split_match.group('codec')}")
                    suffix = split_match.group("group")
                    if suffix and suffix.lower() not in _NEVER_RELEASE_TAGS:
                        ctx.codec_release_tag = suffix
                    ctx.claim([previous, token], Roles.VIDEO, self.name)
                    continue

            if lowered in VIDEO_CODECS:
                self._set_codec(ctx, token.text)
                ctx.claim(token, Roles.VIDEO, self.name)
                continue

            depth_match = BIT_DEPTH_RE.match(token.text)
            if depth_match is not None:
                ctx.set_field("bit_depth", int(depth_match.group("depth")), self.name)
                ctx.claim(token, Roles.VIDEO, self.name)
                continue

            if lowered in HI10_TOKENS:
                ctx.set_field("bit_depth", HI10_TOKENS[lowered], self.name)
                ctx.claim(token, Roles.VIDEO, self.name)
                continue

            if lowered in _BIT_WORDS:
                previous = ctx.previous_token(token)
                if (
                    previous is not None
                    and previous.available
                    and previous.field_index == token.field_index
                    and previous.as_int() in (8, 10, 12)
                ):
                    ctx.set_field("bit_depth", previous.as_int(), self.name)
                    ctx.claim([previous, token], Roles.VIDEO, self.name)

    # ------------------------------------------------------------------
    def _set_codec(self, ctx: ParseContext, text: str) -> None:
        family, encoding = VIDEO_CODECS[text.lower()]
        ctx.set_field("video_codec", family, self.name)
        if encoding is not None:
            ctx.set_field("encoding", encoding, self.name)
