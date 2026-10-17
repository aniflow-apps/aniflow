"""Final consistency pass.

Runs last.  It never invents values: it only tidies collections, resolves
relationships between fields that are now all known, and adds the last few
explanatory warnings.
"""

from __future__ import annotations

from ..context import ParseContext
from .base import Rule, Stage, register

__all__ = ["FinalizeRule"]


@register
class FinalizeRule(Rule):
    """Tidy up collections and cross-check related fields."""

    name = "finalize"
    order = 910
    stage = Stage.FINALIZE
    description = "Deduplicate collections, cross-check episode/season/batch consistency."

    def apply(self, ctx: ParseContext) -> None:
        result = ctx.result

        # A range and a single episode are contradictory statements about the
        # same release; keep both but say so.
        if result.episode is not None and result.episode_start is not None:
            ctx.add_warning(
                f"Filename states both a single episode ({result.episode}) and a range "
                f"({result.episode_start}-{result.episode_end}); both were preserved."
            )

        # A range always covers more than one episode.
        if result.episode_start is not None and not result.batch:
            ctx.set_flag("batch", True, self.name)

        # An alternative title identical to the name carries no information.
        result.alternative_titles = [
            title for title in result.alternative_titles if title != result.name
        ]

        # Anything promoted to a real field should not linger in misc as well.
        claimed_values = {
            value
            for value in (result.subgroup, result.name, result.episode_name)
            if value
        }
        result.misc = [item for item in result.misc if item not in claimed_values]

        if result.name is None and not result.misc:
            ctx.add_warning("No recognisable metadata found in this filename.")
