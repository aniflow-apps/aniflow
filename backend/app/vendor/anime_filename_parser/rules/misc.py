"""Leftover collection: never throw information away.

Whatever no rule claimed is gathered here into contiguous runs and preserved
twice:

* in ``misc`` -- a flat list of strings, easy for callers to display or index;
* in ``unknown_tokens`` -- the same text plus *where* it was found, which is
  what you want when teaching the parser a new release format.

A single warning names the unclassified runs, so an unusual filename explains
itself without drowning the caller in one warning per token.
"""

from __future__ import annotations

from ..context import ParseContext, Roles
from ..models import UnknownToken
from ..tokenizer import Enclosure, Group, Token
from .base import Rule, Stage, register

__all__ = ["MiscRule", "MAX_REPORTED_UNKNOWNS"]

#: Only this many unclassified runs are named in the warning text.
MAX_REPORTED_UNKNOWNS = 4

_ENCLOSURE_LABEL = {
    Enclosure.NONE: "text",
    Enclosure.SQUARE: "square",
    Enclosure.PAREN: "paren",
    Enclosure.CURLY: "curly",
    Enclosure.ANGLE: "angle",
}


def _location(group: Group, token: Token) -> str:
    label = _ENCLOSURE_LABEL.get(group.enclosure, "text")
    return f"{label}[{group.index}] field {token.field_index}"


@register
class MiscRule(Rule):
    """Preserve every unclaimed token run."""

    name = "misc"
    order = 900
    stage = Stage.FINALIZE
    description = "Collect unclaimed tokens into misc/unknown_tokens with their location."

    def apply(self, ctx: ParseContext) -> None:
        unknown: list[str] = []
        for group in ctx.groups:
            for run in self._runs(group):
                text = ctx.render_tokens(run)
                if not text:
                    continue
                ctx.add_misc(text)
                location = _location(group, run[0])
                ctx.result.unknown_tokens.append(UnknownToken(text=text, location=location))
                ctx.claim(run, Roles.MISC, self.name)
                unknown.append(text)

        if unknown:
            shown = ", ".join(repr(item) for item in unknown[:MAX_REPORTED_UNKNOWNS])
            extra = len(unknown) - MAX_REPORTED_UNKNOWNS
            if extra > 0:
                shown += f" (+{extra} more)"
            ctx.add_warning(f"Unclassified metadata preserved in 'misc': {shown}.")

    # ------------------------------------------------------------------
    def _runs(self, group: Group) -> list[list[Token]]:
        """Group the remaining tokens of ``group`` into contiguous runs."""
        runs: list[list[Token]] = []
        current: list[Token] = []
        current_field: int | None = None
        for token in group.tokens:
            if token.available and token.is_meaningful:
                if current and token.field_index != current_field:
                    runs.append(current)
                    current = []
                current.append(token)
                current_field = token.field_index
            elif current:
                runs.append(current)
                current = []
                current_field = None
        if current:
            runs.append(current)
        return runs
