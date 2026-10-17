"""Audio codecs, channel layouts and audio track types.

Decomposes the spellings that appear in the wild::

    AAC2.0      -> audio_codec="AAC",  audio_channels="2.0"
    DDP 2.0     -> audio_codec="DDP",  audio_channels="2.0"
    EAC-3       -> audio_codec="EAC3"
    Dual Audio  -> audio_type="dual"
    MULTi       -> audio_type="multi"

A release can carry several tracks (``Dual EAC3 AAC``).  The first codec seen
becomes ``audio_codec`` and every codec is listed in ``audio_codecs`` so
nothing is lost.

``Dual`` is context sensitive: ``Dual ASS`` is about subtitles, not audio, so
the word only sets ``audio_type`` when it is not followed by a subtitle token.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..tokenizer import Token
from ..vocab import (
    AUDIO_CHANNELS_RE,
    AUDIO_CODEC_CHANNELS_RE,
    AUDIO_CODECS,
    AUDIO_TYPES,
    SUBTITLE_FORMATS,
    matches_vocab,
)
from .base import Rule, Stage, register

__all__ = ["AudioRule"]

_DUAL_WORDS = frozenset({"dual", "dual-audio", "dualaudio"})
_MULTI_WORDS = frozenset({"multi", "multi-audio", "multiaudio"})
_AUDIO_WORDS = frozenset({"audio", "audios"})
#: Tokens that turn a preceding ``Dual``/``Multi`` into a subtitle statement.
_SUBTITLE_FOLLOWERS = frozenset(set(SUBTITLE_FORMATS) | {"sub", "subs", "subtitle", "subtitles"})


@register
class AudioRule(Rule):
    """Claim audio codec, channel and track-type tokens."""

    name = "audio"
    order = 160
    stage = Stage.TECHNICAL
    description = "AAC/DDP/EAC3/FLAC codecs, 2.0-style channels, dual & multi audio."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            lowered = token.lower

            if matches_vocab(lowered, token.text, AUDIO_CODECS):
                self._add_codec(ctx, AUDIO_CODECS[lowered])
                ctx.claim(token, Roles.AUDIO, self.name)
                self._absorb_following_channels(ctx, token)
                continue

            combined = AUDIO_CODEC_CHANNELS_RE.match(token.text)
            if combined is not None and combined.group("codec").lower() in AUDIO_CODECS:
                self._add_codec(ctx, AUDIO_CODECS[combined.group("codec").lower()])
                ctx.set_field("audio_channels", combined.group("channels"), self.name)
                ctx.claim(token, Roles.AUDIO, self.name)
                continue

            if AUDIO_CHANNELS_RE.match(token.text):
                ctx.set_field("audio_channels", token.text, self.name)
                ctx.claim(token, Roles.AUDIO, self.name)
                continue

            if lowered in AUDIO_TYPES and self._is_audio_context(ctx, token):
                self._claim_type(ctx, token, AUDIO_TYPES[lowered])
                continue

            if lowered in _DUAL_WORDS or lowered in _MULTI_WORDS:
                if not self._is_audio_context(ctx, token):
                    continue
                self._claim_type(ctx, token, "dual" if lowered in _DUAL_WORDS else "multi")

    # ------------------------------------------------------------------
    def _claim_type(self, ctx: ParseContext, token: Token, value: str) -> None:
        """Claim ``Dual``/``Multi`` plus a following ``Audio`` word, if present."""
        tokens = [token]
        following = ctx.next_token(token)
        if (
            following is not None
            and following.available
            and following.field_index == token.field_index
            and following.lower in _AUDIO_WORDS
        ):
            tokens.append(following)
        ctx.set_field("audio_type", value, self.name)
        ctx.claim(tokens, Roles.AUDIO, self.name)

    def _is_audio_context(self, ctx: ParseContext, token: Token) -> bool:
        """Reject ``Dual``/``Multi`` when the next token is about subtitles."""
        following = ctx.next_token(token)
        if following is None or following.field_index != token.field_index:
            return True
        return following.lower not in _SUBTITLE_FOLLOWERS

    def _absorb_following_channels(self, ctx: ParseContext, token: Token) -> None:
        """``DDP 2.0``: pick up a channel layout in the next token."""
        following = ctx.next_token(token)
        if (
            following is not None
            and following.available
            and following.field_index == token.field_index
            and AUDIO_CHANNELS_RE.match(following.text)
        ):
            ctx.set_field("audio_channels", following.text, self.name)
            ctx.claim(following, Roles.AUDIO, self.name)

    def _add_codec(self, ctx: ParseContext, codec: str) -> None:
        """Record a codec; several tracks are normal, not a conflict."""
        if codec not in ctx.result.audio_codecs:
            ctx.result.audio_codecs.append(codec)
        if ctx.result.audio_codec is None:
            ctx.set_field("audio_codec", codec, self.name)
