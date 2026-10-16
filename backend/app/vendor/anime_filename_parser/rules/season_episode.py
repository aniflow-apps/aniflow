"""Combined season/episode markers (``S01E09``, ``S1E9``, ``ONA1E01``).

This is the single most reliable structural signal in a filename, so it runs
before the standalone season and episode rules: once ``S01E09`` is understood,
no other rule has to wonder whether ``9`` might be a year or a part number.

A prefix (``S``, ``ONA``, ``OVA``, ``SP``, ``TV``) is *required*.  Without it a
bare ``1E9`` would happily match fragments of CRC32 hashes and codec strings.
"""

from __future__ import annotations

import re

from ..context import ParseContext, Roles
from ..models import MediaType
from .base import Rule, Stage, register

__all__ = ["SeasonEpisodeRule"]

_SXXEXX_RE = re.compile(
    r"""
    ^(?P<kind>S|ONA|OVA|OAD|SP|TV)      # required prefix
    (?P<season>\d{1,2})
    E(?P<episode>\d{1,4})
    (?:\.(?P<fraction>\d))?            # S01E12.5 recaps and split episodes
    (?:[-~]?E?(?P<end>\d{1,4}))?        # S01E01-E12 ranges
    (?:V(?P<version>\d{1,2}))?          # S01E01v2
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Prefix -> ``media_type``.  ``S`` implies a regular TV season.
_KIND_MEDIA_TYPE = {
    "s": MediaType.TV,
    "tv": MediaType.TV,
    "ona": MediaType.ONA,
    "ova": MediaType.OVA,
    "oad": MediaType.OVA,
    "sp": MediaType.SPECIAL,
}


@register
class SeasonEpisodeRule(Rule):
    """Claim ``SxxExx``-style tokens and everything they imply."""

    name = "season-episode"
    order = 110
    stage = Stage.STRUCTURE
    description = "Explicit combined markers such as S01E09, S1E9 or ONA1E01."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            match = _SXXEXX_RE.match(token.text)
            if match is None:
                continue

            season = int(match.group("season"))
            episode = int(match.group("episode"))
            end = match.group("end")
            version = match.group("version")
            fraction = match.group("fraction")

            ctx.set_field("season", season, self.name, source_text=token.text)
            if fraction is not None:
                # ``S01E12.5``: a recap or a split episode.  ``episode`` is an
                # integer field, so the honest answer is to leave it unset --
                # reporting 12 would file the recap over the real episode 12.
                ctx.add_warning(
                    f"Fractional episode {episode}.{fraction} detected; 'episode' "
                    "left unset because it cannot hold a fraction."
                )
            elif end is not None and int(end) > episode:
                ctx.set_field("episode_start", episode, self.name)
                ctx.set_field("episode_end", int(end), self.name)
                ctx.set_flag("batch", True, self.name)
                ctx.add_warning(
                    f"Episode range {episode}-{int(end)} detected; "
                    "'episode' left unset and the range reported instead."
                )
            else:
                ctx.set_field("episode", episode, self.name, source_text=token.text)
            if version is not None:
                ctx.set_field("episode_version", int(version), self.name)

            media_type = _KIND_MEDIA_TYPE.get(match.group("kind").lower())
            if media_type is not None:
                ctx.set_field("media_type", media_type, self.name)

            ctx.claim(token, Roles.EPISODE, self.name)
