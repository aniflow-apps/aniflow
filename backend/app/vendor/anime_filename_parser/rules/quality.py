"""Resolution / quality detection.

Recognises ``480p``, ``720p``, ``1080p``, ``1440p``, ``2160p``, ``4K`` and
``8K``, including the compound spellings release groups like to use:
``BD720p`` and ``WEB1080p`` (the latter reached after ``WEB.1080p`` is
normalised).  When a compound token carries a source prefix, the source is
handed to the release rule's fields directly instead of being thrown away.

Quality is extracted early because a bare ``1080p``-shaped token is never
anything else, and removing it makes the surrounding title boundary obvious.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..vocab import QUALITY_RE, RESOLUTION_RE, SOURCES, normalise_quality
from .base import Rule, Stage, register

__all__ = ["QualityRule"]


@register
class QualityRule(Rule):
    """Claim resolution tokens and any source prefix glued to them."""

    name = "quality"
    order = 140
    stage = Stage.TECHNICAL
    description = "480p/720p/1080p/2160p/4K, including BD720p, WEB 1080p and 1440x1080."

    def apply(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue
            match = QUALITY_RE.match(token.text)
            if match is None:
                frame = RESOLUTION_RE.match(token.text)
                if frame is None:
                    continue
                # ``1440x1080`` states the same fact as ``1080p``.
                ctx.set_field(
                    "quality", f"{int(frame.group('height'))}p", self.name,
                    source_text=token.text,
                )
                ctx.set_field("resolution", token.text.lower().replace("×", "x"), self.name)
                ctx.claim(token, Roles.QUALITY, self.name)
                continue

            quality = normalise_quality(match.group("value"))
            ctx.set_field("quality", quality, self.name, source_text=token.text)

            prefix = match.group("source")
            if prefix:
                source, release_type = SOURCES.get(prefix.lower(), (None, None))
                ctx.set_field("source", source, self.name)
                ctx.set_field("release_type", release_type, self.name)

            ctx.claim(token, Roles.QUALITY, self.name)
