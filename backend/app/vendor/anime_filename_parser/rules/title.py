"""Title, episode title and alternative titles.

Runs near the end of the pipeline on purpose: by now every reliable signal has
claimed its tokens, so the title is simply *the leading run of text nobody
else wanted*.  That is far more robust than trying to describe titles with
patterns.

Three rules live here:

:class:`TitleRule`
    ``name`` (the release title as written, season designation included),
    ``episode_name``, and alternative titles found inside the title run.
:class:`AlternativeTitleRule`
    Alternative titles found elsewhere -- parenthesised translations,
    ``|``/``/``-delimited titles, and CJK titles appended at the end.
:class:`OnlyNameRule`
    ``only_name``: the normalised base title used for matching the same show
    across release groups.
"""

from __future__ import annotations

import re

from ..context import ParseContext, Roles
from ..layout import ALT_TITLE_DELIMITERS, compute_layout, is_cjk_text
from ..tokenizer import Enclosure, Field, Token, TokenKind
from .base import Rule, Stage, register

__all__ = ["TitleRule", "AlternativeTitleRule", "OnlyNameRule"]

_EPISODE_ROLES = frozenset({Roles.EPISODE, Roles.EPISODE_KEYWORD, Roles.EPISODE_RANGE})
#: An alternative title needs at least this many words to be distinguishable
#: from a release group or a stray flag.
_MIN_ALT_TOKENS = 2
#: ... unless the single token is long enough to be a title on its own.
_MIN_TRAILING_CHARS = 8
#: Roles carried by words that are metadata *and* legitimately part of a title
#: (``Gekijouban``, ``The Movie``, ``Final Season``).  They do not interrupt a
#: title run the way ``1080p`` or ``WEB-DL`` does.
_TITLE_SAFE_ROLES = frozenset({Roles.MEDIA_TYPE, Roles.SEASON_DESIGNATION})


def _leading_available_run(tokens: list[Token]) -> list[Token]:
    """The leading run of tokens that are still free (or kept in the title).

    It stops at the *first* claimed token rather than skipping over it, which is
    what keeps technical metadata from bridging into a title.  In
    ``S01E09 1080p WEB-DL StrangeUnknownThing`` the run after the episode
    marker is empty, so ``StrangeUnknownThing`` becomes ``misc`` instead of
    being mistaken for an episode title.
    """
    run: list[Token] = []
    for token in tokens:
        if token.kind is TokenKind.DELIMITER:
            continue
        if token.available or token.keep_in_title or token.role in _TITLE_SAFE_ROLES:
            run.append(token)
        else:
            break
    return run


def _has_word(tokens: list[Token]) -> bool:
    return any(token.kind in (TokenKind.WORD, TokenKind.ALNUM) for token in tokens)


@register
class TitleRule(Rule):
    """Derive ``name`` and ``episode_name`` from what is left of the title run."""

    name = "title"
    order = 240
    stage = Stage.NAMING
    description = "Leading unclaimed text becomes the title; the rest may be an episode title."

    def apply(self, ctx: ParseContext) -> None:
        layout = compute_layout(ctx)
        tokens = layout.title_tokens()

        if tokens:
            ctx.name_tokens = tokens
            ctx.set_field("name", ctx.render_tokens(tokens), self.name)
            ctx.claim(tokens, Roles.TITLE, self.name)
        else:
            ctx.add_warning(
                "Could not confidently determine the title boundary; 'name' left unset."
            )

        if layout.has_multiple_title_fields and layout.episode_field is None:
            ctx.add_warning(
                "Several separator-delimited fields with no episode marker; all of them "
                "were kept in 'name' rather than guessing which part is the title."
            )

        for entry in layout.alternative_fields:
            self._add_alternative(ctx, entry)

        self._episode_name(ctx, layout)

    # ------------------------------------------------------------------
    def _add_alternative(self, ctx: ParseContext, entry: Field) -> None:
        run = _leading_available_run(entry.tokens)
        if not run:
            return
        ctx.add_alternative_title(ctx.render_tokens(run))
        ctx.claim(run, Roles.ALTERNATIVE_TITLE, self.name)

    # ------------------------------------------------------------------
    def _episode_name(self, ctx: ParseContext, layout) -> None:
        """Find an episode title, conservatively.

        Two shapes are accepted: text following the episode number inside the
        same field (``S01E21 Warning``, ``Episode 10 Saintly Trials``), and
        whole fields after the episode field (``- 01 - The First Turnabout``).
        Technical metadata can never leak in, because it has already been
        claimed by an earlier rule and only unclaimed tokens are considered.
        """
        if ctx.result.episode_name is not None:
            return
        if self._after_episode_token(ctx):
            return
        self._trailing_fields(ctx, layout)

    def _after_episode_token(self, ctx: ParseContext) -> bool:
        anchor = None
        for token in ctx.tokens:
            if token.role in _EPISODE_ROLES:
                anchor = token
        if anchor is None or anchor.keep_in_title:
            return False

        group = ctx.groups[anchor.group_index]
        following = [
            token
            for token in group.tokens
            if token.position > anchor.position and token.field_index == anchor.field_index
        ]
        run = _leading_available_run(following)
        if not run or not _has_word(run):
            return False
        if is_cjk_text(run):
            return False
        text = ctx.render_tokens(run)
        if not text:
            return False
        ctx.set_field("episode_name", text, self.name)
        ctx.claim(run, Roles.EPISODE_NAME, self.name)
        return True

    def _trailing_fields(self, ctx: ParseContext, layout) -> bool:
        candidates: list[Token] = []
        for entry in layout.trailing_fields:
            if entry.leading_delimiter in ALT_TITLE_DELIMITERS:
                break
            run = _leading_available_run(entry.tokens)
            if not run or not _has_word(run) or is_cjk_text(run):
                break
            candidates.extend(run)
        if not candidates:
            return False
        ctx.set_field("episode_name", ctx.render_tokens(candidates), self.name)
        ctx.claim(candidates, Roles.EPISODE_NAME, self.name)
        return True


@register
class AlternativeTitleRule(Rule):
    """Collect alternative titles from outside the title run."""

    name = "alternative-titles"
    order = 245
    stage = Stage.NAMING
    description = "Parenthesised translations, |/-delimited titles and appended CJK titles."

    def apply(self, ctx: ParseContext) -> None:
        title_index = ctx.title_run.index if ctx.title_run is not None else -1
        for group in ctx.groups:
            if group.enclosure is Enclosure.PAREN:
                self._from_paren(ctx, group)
            elif group.enclosure is Enclosure.NONE and group.index != title_index:
                self._from_text_run(ctx, group)

    # ------------------------------------------------------------------
    def _from_paren(self, ctx: ParseContext, group) -> None:
        for entry in group.meaningful_fields:
            run = _leading_available_run(entry.tokens)
            if not run:
                continue
            if not (len(run) >= _MIN_ALT_TOKENS or is_cjk_text(run)):
                continue
            if not _has_word(run):
                continue
            ctx.add_alternative_title(ctx.render_tokens(run))
            ctx.claim(run, Roles.ALTERNATIVE_TITLE, self.name)

    def _from_text_run(self, ctx: ParseContext, group) -> None:
        for entry in group.meaningful_fields:
            run = _leading_available_run(entry.tokens)
            if not run or not _has_word(run):
                continue
            delimited = entry.leading_delimiter in ALT_TITLE_DELIMITERS
            if delimited or is_cjk_text(run):
                ctx.add_alternative_title(ctx.render_tokens(run))
                ctx.claim(run, Roles.ALTERNATIVE_TITLE, self.name)
                continue
            if self._is_trailing_english_title(ctx, group, run):
                # ``[Anime Time] <romaji> (Season 01) [tags...] <English title>``
                # -- several groups park the translated title at the very end
                # with no delimiter at all.  Only claimed when it is the last
                # thing in the filename and the primary title is already known,
                # and always with a warning, because a trailing run could also
                # be a tagline.
                ctx.add_alternative_title(ctx.render_tokens(run))
                ctx.claim(run, Roles.ALTERNATIVE_TITLE, self.name)
                ctx.add_warning(
                    f"Trailing text {ctx.render_tokens(run)!r} after all metadata was "
                    "read as an alternative title; it carried no delimiter."
                )
            # Otherwise it is preserved by the misc rule instead: it is just as
            # likely to be a note as a title.

    # ------------------------------------------------------------------
    def _is_trailing_english_title(self, ctx: ParseContext, group, run) -> bool:
        """Is this the translated title parked at the end of the filename?"""
        if ctx.result.name is None or ctx.title_run is None:
            return False
        if group.index <= ctx.title_run.index:
            return False
        if group.index != ctx.groups[-1].index:
            return False
        # It must be *all* that is left of the run, and long enough to be a
        # title rather than a stray uploader tag.  One token is enough when it
        # is a hyphenated compound (``Rent-A-Girlfriend``).
        if len(run) < _MIN_ALT_TOKENS and len(ctx.render_tokens(run)) < _MIN_TRAILING_CHARS:
            return False
        return len(group.meaningful_fields) == 1


# ----------------------------------------------------------------------
# only_name
# ----------------------------------------------------------------------
_ROMAN = "IVXL"
#: Trailing designations that are part of how a release is *written* but not
#: part of the show's base title.  Applied repeatedly until stable.
_ONLY_NAME_STRIPS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\s\-:,]+$"),
    re.compile(r"\s+S\d{1,2}$"),
    re.compile(r"\s+(?:Season|Saison)\s*(?:\d{1,2}|[" + _ROMAN + r"]+)$", re.IGNORECASE),
    re.compile(r"\s+\d{1,2}(?:st|nd|rd|th)\s+(?:Season|Saison)$", re.IGNORECASE),
    re.compile(
        r"\s+(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth"
        r"|Final|Last|New)\s+(?:Season|Saison)$",
        re.IGNORECASE,
    ),
    re.compile(r"\s+(?:Season|Saison)$", re.IGNORECASE),
    re.compile(r"\s+(?:Part|Cour)\s*(?:\d{1,2}|[" + _ROMAN + r"]+)$", re.IGNORECASE),
    re.compile(r"\s+(?:The\s+)?Movie$", re.IGNORECASE),
    re.compile(r"\s+Gekijou-?ban$", re.IGNORECASE),
    re.compile(r"^Gekijou-?ban\s+", re.IGNORECASE),
)


def derive_only_name(name: str) -> str:
    """Strip season/part/movie designations from a title.

    >>> derive_only_name("My Hero Academia Final Season")
    'My Hero Academia'
    >>> derive_only_name("Pokemon the Movie")
    'Pokemon'
    """
    text = name.strip()
    previous = None
    while previous != text:
        previous = text
        for pattern in _ONLY_NAME_STRIPS:
            stripped = pattern.sub("", text).strip()
            if stripped and stripped != text:
                text = stripped
    return text


@register
class OnlyNameRule(Rule):
    """Derive ``only_name`` from ``name``.

    ``only_name`` is what makes results comparable between release groups:
    ``Jujutsu Kaisen 2``, ``Jujutsu Kaisen S2`` and ``Jujutsu Kaisen II`` all
    collapse to ``Jujutsu Kaisen``.  The season/episode number itself is
    removed by span (it is a token this parse already identified), and only the
    remaining word-shaped designations need patterns.
    """

    name = "only-name"
    order = 250
    stage = Stage.NAMING
    description = "Normalise 'name' into a base title for cross-group matching."

    def apply(self, ctx: ParseContext) -> None:
        if ctx.result.name is None:
            return

        base = ctx.result.name
        tail = ctx.title_tail_token
        if tail is not None and ctx.name_tokens and ctx.name_tokens[-1] is tail:
            base = ctx.render(ctx.name_tokens[0].start, tail.start)

        only = derive_only_name(base)
        ctx.set_field("only_name", only or ctx.result.name, self.name)
