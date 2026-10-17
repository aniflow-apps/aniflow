"""Subtitle language, type and format.

Normalises the many spellings of the same fact::

    Eng Sub / English-Sub / English Subs -> subtitle_language="English"
    Multi Sub / Multi-Subs / M-SUB       -> subtitle_type="multi"
    Dual ASS                             -> subtitle_type="dual", format="ASS"
    Softsub / Hardsub                    -> subtitle_type="soft"/"hard"

A language name only counts when it is attached to a ``Sub``/``Subs`` marker.
A bare ``[Eng]`` is left unclassified on purpose: it might mean a dub, an
audio track or a title word, and guessing would be worse than preserving it.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..tokenizer import Enclosure, Token
from ..vocab import (
    CJK_SUBTITLE_GROUP_SUFFIXES,
    CJK_SUBTITLE_MARKER,
    CJK_SUBTITLE_PLACEMENT,
    CJK_SUBTITLE_SCRIPTS,
    LANGUAGE_SUB_RE,
    LANGUAGES,
    SUBTITLE_FORMATS,
    SUBTITLE_TYPES,
)
from .base import Rule, Stage, register

__all__ = ["SubtitleRule"]

_SUB_WORDS = frozenset({"sub", "subs", "subtitle", "subtitles", "subbed"})
_TYPE_WORDS = {"multi": "multi", "dual": "dual", "soft": "soft", "hard": "hard"}


@register
class SubtitleRule(Rule):
    """Claim subtitle-related tokens."""

    name = "subtitles"
    order = 170
    stage = Stage.TECHNICAL
    description = "Eng/Multi/Dual subtitle markers, ASS/SRT formats, soft & hard subs."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            lowered = token.lower

            if self._cjk_subtitle_note(ctx, token):
                continue

            if lowered in SUBTITLE_TYPES:
                ctx.set_field("subtitle_type", SUBTITLE_TYPES[lowered], self.name)
                ctx.claim(token, Roles.SUBTITLE, self.name)
                continue

            single = LANGUAGE_SUB_RE.match(token.text)
            if single is not None and single.group("kind").lower().startswith("sub"):
                language = LANGUAGES.get(single.group("language").lower())
                if language is not None:
                    ctx.set_field("subtitle_language", language, self.name)
                    ctx.claim(token, Roles.SUBTITLE, self.name)
                    continue

            if self._two_token_form(ctx, token, lowered):
                continue

            if lowered in SUBTITLE_FORMATS and self._format_is_safe(token):
                ctx.set_field("subtitle_format", SUBTITLE_FORMATS[lowered], self.name)
                ctx.claim(token, Roles.SUBTITLE, self.name)

    # ------------------------------------------------------------------
    def _cjk_subtitle_note(self, ctx: ParseContext, token: Token) -> bool:
        """``简繁内封字幕``, ``简体内嵌``: a Chinese release stating its subtitles."""
        text = token.text
        if any(suffix in text for suffix in CJK_SUBTITLE_GROUP_SUFFIXES):
            # ``悠哈璃羽字幕社`` is who released it, not how it is subtitled.
            return False
        placement = next(
            (kind for marker, kind in CJK_SUBTITLE_PLACEMENT.items() if marker in text),
            None,
        )
        if CJK_SUBTITLE_MARKER not in text and placement is None:
            return False
        for marker, language in CJK_SUBTITLE_SCRIPTS.items():
            if marker in text:
                ctx.set_field("subtitle_language", language, self.name)
                break
        if placement is not None:
            ctx.set_field("subtitle_type", placement, self.name)
        ctx.add_tag(text)
        ctx.claim(token, Roles.SUBTITLE, self.name)
        return True

    # ------------------------------------------------------------------
    def _two_token_form(self, ctx: ParseContext, token: Token, lowered: str) -> bool:
        """``Eng Sub``, ``Multi Sub``, ``Dual ASS`` written as two tokens."""
        following = ctx.next_token(token)
        if (
            following is None
            or not following.available
            or following.field_index != token.field_index
        ):
            return False
        follower = following.lower
        is_sub_word = follower in _SUB_WORDS
        is_format = follower in SUBTITLE_FORMATS

        if not (is_sub_word or is_format):
            return False

        if lowered in _TYPE_WORDS:
            ctx.set_field("subtitle_type", _TYPE_WORDS[lowered], self.name)
        elif is_sub_word and lowered in LANGUAGES:
            ctx.set_field("subtitle_language", LANGUAGES[lowered], self.name)
        else:
            return False

        if is_format:
            ctx.set_field("subtitle_format", SUBTITLE_FORMATS[follower], self.name)
        ctx.claim([token, following], Roles.SUBTITLE, self.name)
        return True

    def _format_is_safe(self, token: Token) -> bool:
        """Only trust a bare ``ASS``/``SRT`` in a metadata-ish position."""
        return token.enclosure is not Enclosure.NONE or token.text.isupper()
