"""Source, release type and streaming service.

The hierarchy is deliberately shallow -- three fields instead of a field per
platform:

``source``
    Where the video came from: ``WEB``, ``BD``, ``DVD``, ``TV``, ``VHS``, ``LD``.
``release_type``
    How it was produced: ``WEB-DL``, ``WEBRip``, ``BDRip``, ``Remux``...
``service``
    Which platform or broadcaster: ``Crunchyroll``, ``Amazon``, ``Netflix``,
    ``Bilibili``, ``iQIYI``, ``DR``...

Short service abbreviations (``CR``, ``NF``, ``DR``, ``IQ``) are only accepted
when written in capitals -- see :data:`..vocab.CASE_SENSITIVE` -- so a title
word is never mistaken for a broadcaster.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..vocab import SERVICES, SOURCES, matches_vocab
from .base import Rule, Stage, register

__all__ = ["ReleaseRule"]


@register
class ReleaseRule(Rule):
    """Claim source, release-type and service tokens."""

    name = "release"
    order = 180
    stage = Stage.RELEASE
    description = "WEB-DL/WEBRip/BD/DVD sources and CR/AMZN/NF/BILI services."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            lowered = token.lower

            if matches_vocab(lowered, token.text, SOURCES):
                source, release_type = SOURCES[lowered]
                ctx.set_field("source", source, self.name)
                ctx.set_field("release_type", release_type, self.name)
                ctx.claim(token, Roles.SOURCE, self.name)
                continue

            if matches_vocab(lowered, token.text, SERVICES):
                ctx.set_field("service", SERVICES[lowered], self.name, source_text=token.text)
                ctx.claim(token, Roles.SERVICE, self.name)
