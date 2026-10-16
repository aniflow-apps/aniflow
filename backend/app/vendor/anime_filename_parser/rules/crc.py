"""CRC32 checksum detection.

A CRC32 hash is the most reliable token in an anime filename: exactly eight
hexadecimal characters, alone inside its own bracket.  Extracting it first
removes an eight-character alphanumeric blob that would otherwise confuse
episode and year detection.

To stay conservative the rule requires at least one digit, so ``[Deadbeef]``
(a plausible release group) is left alone rather than guessed at.  An all-digit
checksum such as ``[56277132]`` *is* accepted -- no anime has an episode or a
number that shape -- except when it reads as a broadcast date (``[20240115]``),
which is a real thing some groups stamp on a release.
"""

from __future__ import annotations

import re

from ..context import ParseContext, Roles
from .base import Rule, Stage, register

__all__ = ["Crc32Rule"]

_CRC32_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_HAS_DIGIT = re.compile(r"\d")
#: ``YYYYMMDD`` with a plausible year, month and day: a broadcast date, not a
#: checksum.  Anything else made of eight digits is a checksum.
_DATE_RE = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")


def looks_like_crc32(text: str) -> bool:
    """Is ``text`` confidently a CRC32 checksum?"""
    if not _CRC32_RE.match(text) or not _HAS_DIGIT.search(text):
        return False
    return not _DATE_RE.match(text)


@register
class Crc32Rule(Rule):
    """Claim ``[B4D12414]``-style checksums."""

    name = "crc32"
    order = 100
    stage = Stage.RELIABLE
    description = "Eight hex characters alone in a bracket group."

    def apply(self, ctx: ParseContext) -> None:
        for group in ctx.groups:
            if not group.is_bracketed:
                continue
            tokens = [token for token in group.tokens if token.is_meaningful]
            if len(tokens) != 1:
                continue
            token = tokens[0]
            if not token.available or not looks_like_crc32(token.text):
                continue
            if ctx.set_field("crc32", token.text.upper(), self.name):
                ctx.claim(token, Roles.CRC32, self.name)
