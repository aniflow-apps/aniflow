"""The flat list of recognised release tags.

Every technical fact already has its own typed field -- ``source``,
``video_codec``, ``bit_depth`` and a dozen others.  That is the right shape for
code that knows what it is looking for, and the wrong shape for code that just
wants to *show* what a release is, or filter on it, without knowing which of
those fields each tag lives in.

``tags`` is that second view: one ordered, deduplicated list of canonically
spelled tags::

    [SubsPlease] Foo - 01 (1080p) [HEVC 10bit x265][AAC][Multi Sub][Batch]
        -> ["1080p", "HEVC", "x265", "10bit", "AAC", "Multi Sub", "Batch"]

It invents nothing.  Every entry is derived from a field another rule already
populated, or from an extra a rule explicitly recognised (``Weekly``,
``{OP & ED Lyrics}``), so a tag appearing here always means some rule
understood that text.  Spelling is canonical rather than as-written, which is
the point: ``M-SUB``, ``Multi-Subs`` and ``MultiSub`` all become ``Multi Sub``,
so two releases of the same episode compare equal.
"""

from __future__ import annotations

from ..context import ParseContext
from .base import Rule, Stage, register

__all__ = ["TagsRule"]

#: ``audio_type``/``subtitle_type`` hold a short code; these are how they read
#: as a tag.
_AUDIO_TYPE_TAGS = {"dual": "Dual Audio", "multi": "Multi Audio"}
_SUBTITLE_TYPE_TAGS = {
    "multi": "Multi Sub",
    "dual": "Dual Sub",
    "soft": "Soft Sub",
    "hard": "Hard Sub",
}


@register
class TagsRule(Rule):
    """Assemble ``tags`` from the fields every other rule has filled in."""

    name = "tags"
    order = 905
    stage = Stage.FINALIZE
    description = "Flatten the recognised technical fields into one canonical tag list."

    def apply(self, ctx: ParseContext) -> None:
        result = ctx.result
        tags: list[str] = []

        def add(value: object) -> None:
            if value is None or value is False or value == "":
                return
            text = str(value)
            if text not in tags:
                tags.append(text)

        # Ordered the way a release names itself: where it came from, how it
        # looks, how it sounds, how it reads, then what kind of release it is.
        add(result.source)
        add(result.release_type)
        add(result.service)

        add(result.quality)
        add(result.resolution)
        add(result.video_codec)
        add(result.encoding)
        if result.bit_depth is not None:
            add(f"{result.bit_depth}bit")

        for codec in result.audio_codecs or ([result.audio_codec] if result.audio_codec else []):
            add(codec)
        add(result.audio_channels)
        add(_AUDIO_TYPE_TAGS.get(result.audio_type or ""))

        add(_SUBTITLE_TYPE_TAGS.get(result.subtitle_type or ""))
        add(result.subtitle_language)
        add(result.subtitle_format)

        if result.batch:
            add("Batch")
        if result.repack:
            add("REPACK")
        if result.uncensored is True:
            add("Uncensored")
        elif result.uncensored is False:
            add("Censored")

        # Extras that never earned a field of their own, in the order the
        # filename mentioned them.
        for extra in ctx.recognised_tags:
            add(extra)

        result.tags = tags
        if tags:
            result.provenance["tags"] = self.name
