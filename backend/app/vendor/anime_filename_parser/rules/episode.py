"""Episode numbers, versions and ranges.

Three independent sub-rules, from most to least certain:

1.  **Keyword forms** -- ``Episode 1``, ``Ep01``, ``EP37``, ``E01``, ``E09v2``.
    An explicit keyword makes the number unambiguous.
2.  **Ranges** -- ``37-48``, ``37~48``.  A range is reported as
    ``episode_start``/``episode_end`` and marks the release as a batch;
    ``episode`` stays ``None`` so ``(37-48)`` is never mistaken for "episode 37".
3.  **Bare numbers in their own field** -- ``Kekkon Yubiwa Monogatari - 01``,
    ``Burst Angel - 012``, ``- 01v2``.  Standing alone behind a separator is a
    strong episodic signal, but the rule refuses to fire if an episode is
    already known, and warns when the number could also be a year.

A trailing number that is *glued to the title* (``Jujutsu Kaisen 2``) is
deliberately **not** handled here -- that needs the whole filename for context
and lives in :mod:`anime_filename_parser.rules.title_tail`.
"""

from __future__ import annotations

import re

from ..context import ParseContext, Roles
from ..tokenizer import Enclosure, Field, Group, Token
from .base import Rule, Stage, register

__all__ = ["EpisodeRule", "MAX_RANGE_LENGTH"]

#: ``Episode 9`` / ``Ep01`` / ``E01`` / ``EP37v2`` as a single token.
_KEYWORD_EPISODE_RE = re.compile(
    r"^(?:episodes?|eps|ep|e)[-_. ]?(?P<episode>\d{1,4})"
    r"(?:[-~](?P<end>\d{1,4}))?(?:v(?P<version>\d{1,2}))?$",
    re.IGNORECASE,
)
#: The bare keyword, with the number in the following token.
_KEYWORD_WORDS = frozenset({"episode", "episodes", "ep", "eps", "e"})
#: ``01``, ``012``, ``0682``, ``01v2``.
_BARE_EPISODE_RE = re.compile(r"^(?P<episode>\d{1,4})(?:v(?P<version>\d{1,2}))?$", re.IGNORECASE)
#: ``37-48``, ``37~48``.
_RANGE_RE = re.compile(r"^(?P<start>\d{1,4})\s*[-~]\s*(?P<end>\d{1,4})$")
#: ``01.5``, ``12.5``: a recap or a split episode.  Normalisation protects the
#: dot so the fraction cannot be split off and read as the episode on its own.
_FRACTIONAL_EPISODE_RE = re.compile(r"^(?P<episode>\d{1,3})\.(?P<fraction>\d)$")

#: Sanity limit so ``1990-2020`` is not read as a 30-episode batch.
MAX_RANGE_LENGTH = 400
#: Field delimiters that introduce an alternative title, never an episode.
_TITLE_DELIMITERS = frozenset({"|", "/"})


def _looks_like_year(value: int) -> bool:
    return 1900 <= value <= 2099


@register
class EpisodeRule(Rule):
    """Claim explicit episode numbers, versions and ranges."""

    name = "episode"
    order = 130
    stage = Stage.STRUCTURE
    description = "Episode keywords, vN re-release versions and episode ranges."

    def apply(self, ctx: ParseContext) -> None:
        self._keyword_forms(ctx)
        self._ranges(ctx)
        self._bare_fields(ctx)
        self._explain_fractions(ctx)

    # ------------------------------------------------------------------
    def _explain_fractions(self, ctx: ParseContext) -> None:
        """Say so when a fractional episode was found but cannot be reported.

        ``episode`` is an integer, so ``- 12.5`` has no representation and is
        deliberately left in the title rather than rounded or split.  Silence
        would leave the caller guessing why an obvious episode marker produced
        nothing, so the reason is stated instead.

        Only a token standing alone in its own free-text field qualifies: a
        bracketed ``5.1`` is an audio layout, and the audio rule has not run
        yet to claim it.
        """
        if ctx.result.episode is not None or ctx.result.episode_start is not None:
            return
        for group in ctx.groups:
            if group.enclosure is not Enclosure.NONE:
                continue
            for entry in group.meaningful_fields:
                tokens = [token for token in entry.tokens if token.is_meaningful]
                if len(tokens) != 1 or not tokens[0].available:
                    continue
                match = _FRACTIONAL_EPISODE_RE.match(tokens[0].text)
                if match is None:
                    continue
                ctx.add_warning(
                    f"Fractional episode {match.group('episode')}."
                    f"{match.group('fraction')} detected; 'episode' left unset "
                    "because it cannot hold a fraction."
                )
                return

    # ------------------------------------------------------------------
    def _keyword_forms(self, ctx: ParseContext) -> None:
        for token in ctx.tokens:
            if not token.available:
                continue

            match = _KEYWORD_EPISODE_RE.match(token.text)
            if match is not None:
                self._record(ctx, token, match.group("episode"), match.group("end"),
                             match.group("version"), [token])
                continue

            if token.lower not in _KEYWORD_WORDS:
                continue
            following = ctx.next_token(token)
            if (
                following is None
                or not following.available
                or following.field_index != token.field_index
            ):
                continue
            bare = _BARE_EPISODE_RE.match(following.text)
            if bare is None:
                continue
            self._record(ctx, following, bare.group("episode"), None,
                         bare.group("version"), [token, following])

    def _record(
        self,
        ctx: ParseContext,
        anchor: Token,
        episode: str,
        end: str | None,
        version: str | None,
        tokens: list[Token],
    ) -> None:
        number = int(episode)
        if end is not None and int(end) > number:
            self._record_range(ctx, number, int(end), tokens)
        else:
            ctx.set_field("episode", number, self.name, source_text=anchor.text)
        if version is not None:
            ctx.set_field("episode_version", int(version), self.name)
        ctx.claim(tokens, Roles.EPISODE, self.name)
        for token in tokens:
            if token.role == Roles.EPISODE and token is not anchor:
                token.role = Roles.EPISODE_KEYWORD

    def _record_range(self, ctx: ParseContext, start: int, end: int, tokens: list[Token]) -> None:
        ctx.set_field("episode_start", start, self.name)
        ctx.set_field("episode_end", end, self.name)
        ctx.set_flag("batch", True, self.name)
        ctx.claim(tokens, Roles.EPISODE_RANGE, self.name)

    # ------------------------------------------------------------------
    def _ranges(self, ctx: ParseContext) -> None:
        """``(37-48)`` -- a range alone in its field."""
        if ctx.result.episode_start is not None:
            return
        for group in ctx.groups:
            for entry in group.meaningful_fields:
                tokens = [token for token in entry.tokens if token.is_meaningful]
                if len(tokens) != 1 or not tokens[0].available:
                    continue
                match = _RANGE_RE.match(tokens[0].text)
                if match is None:
                    continue
                start, end = int(match.group("start")), int(match.group("end"))
                if end <= start or end - start > MAX_RANGE_LENGTH:
                    continue
                if _looks_like_year(start) and _looks_like_year(end):
                    ctx.add_warning(
                        f"Ambiguous range {tokens[0].text!r}: looks like years, "
                        "not classified as an episode range."
                    )
                    continue
                self._record_range(ctx, start, end, [tokens[0]])
                return

    # ------------------------------------------------------------------
    def _bare_fields(self, ctx: ParseContext) -> None:
        """``- 01``, ``- 012``, ``- 01v2``: a lone number behind a separator."""
        if ctx.result.episode is not None or ctx.result.episode_start is not None:
            return
        for group in ctx.groups:
            if group.enclosure is not Enclosure.NONE:
                continue
            for entry in group.meaningful_fields:
                if self._claim_bare_field(ctx, group, entry):
                    return

    def _claim_bare_field(self, ctx: ParseContext, group: Group, entry: Field) -> bool:
        if entry.leading_delimiter in _TITLE_DELIMITERS:
            return False
        if entry.leading_delimiter is None and entry.index == 0:
            # The very first field is the title, not an episode number.
            return False
        tokens = [token for token in entry.tokens if token.is_meaningful]
        if len(tokens) != 1 or not tokens[0].available:
            return False
        token = tokens[0]
        match = _BARE_EPISODE_RE.match(token.text)
        if match is None:
            return False

        number = int(match.group("episode"))
        version = match.group("version")
        if len(match.group("episode")) == 4 and _looks_like_year(number):
            if ctx.result.year is None:
                ctx.add_warning(
                    f"Ambiguous numeric token {token.text!r}: could be a year, "
                    "interpreted as episode because it stands alone in its own field."
                )
            else:
                return False
        ctx.set_field("episode", number, self.name, source_text=token.text)
        if version is not None:
            ctx.set_field("episode_version", int(version), self.name)
        ctx.claim(token, Roles.EPISODE, self.name)
        return True
