"""Title layout analysis.

Several rules need the same structural question answered: *inside the run of
free text that holds the title, which dash-separated field is what?*

``Ace Attorney - 01 - The First Turnabout``
    title field, episode field, episode-name field.

``We Never Learn - Bokuben - 01 - Genius and X ...``
    two title fields, episode field, episode-name field.

``Beyond Time's Gaze - <CJK title>``
    title field plus an alternative title (detected by script change).

``Jujutsu Kaisen 0 The Movie - Gekijouban Jujutsu Kaisen Zero``
    title field plus an alternative title (detected by word echo).

This module contains no rules and writes nothing: it is pure analysis shared
by :mod:`.rules.title_tail`, :mod:`.rules.title` and :mod:`.rules.misc`.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from .context import ParseContext, Roles
from .tokenizer import Field, Group, Token, contains_cjk
from .vocab import TITLE_STOPWORDS

__all__ = ["TitleLayout", "compute_layout", "significant_words", "is_cjk_text"]

#: Delimiters that always introduce a separate title rather than more title.
ALT_TITLE_DELIMITERS = frozenset({"|", "/"})

#: Roles that mark a field as "this is where the episode number lives".
_EPISODE_ROLES = frozenset({Roles.EPISODE, Roles.EPISODE_KEYWORD, Roles.EPISODE_RANGE})

_MIN_SIGNIFICANT_LENGTH = 3


def significant_words(tokens: list[Token]) -> set[str]:
    """Lower-cased content words of ``tokens`` (stopwords and stubs removed)."""
    words = set()
    for token in tokens:
        word = token.text.lower().strip("'\"")
        if len(word) >= _MIN_SIGNIFICANT_LENGTH and word not in TITLE_STOPWORDS:
            words.add(word)
    return words


def is_cjk_text(tokens: list[Token]) -> bool:
    """Are ``tokens`` written in a CJK script rather than a Latin one?"""
    text = "".join(token.text for token in tokens)
    if not contains_cjk(text):
        return False
    return not any(character.isascii() and character.isalpha() for character in text)


@dataclass
class TitleLayout:
    """How the fields of the title run break down."""

    run: Group | None
    fields: list[Field] = dataclass_field(default_factory=list)
    title_fields: list[Field] = dataclass_field(default_factory=list)
    alternative_fields: list[Field] = dataclass_field(default_factory=list)
    episode_field: Field | None = None
    trailing_fields: list[Field] = dataclass_field(default_factory=list)

    @property
    def has_multiple_title_fields(self) -> bool:
        return len(self.title_fields) > 1

    def title_tokens(self) -> list[Token]:
        """Tokens that make up ``name``.

        Within each title field only the *leading* run of still-available (or
        explicitly kept) tokens counts, so ``Yoroi Shinden Samurai Troopers
        S01E21 Warning`` stops at ``S01E21`` and never swallows the episode
        title that follows it.
        """
        collected: list[Token] = []
        for entry in self.title_fields:
            run: list[Token] = []
            for token in entry.tokens:
                if token.available or token.keep_in_title:
                    run.append(token)
                else:
                    break
            if not run:
                break
            collected.extend(run)
            if len(run) != len(entry.tokens):
                break
        return collected


def _field_has_content(entry: Field) -> bool:
    return any(token.available or token.keep_in_title for token in entry.tokens)


def _append_secondary(layout: "TitleLayout", entry: Field, base_is_cjk: bool) -> None:
    """File a field that is not part of the title.

    ``|``/``/``-delimited or script-switched fields are alternative titles;
    everything else may still turn out to be an episode title.
    """
    is_alternative = entry.leading_delimiter in ALT_TITLE_DELIMITERS or (
        is_cjk_text(entry.tokens) and not base_is_cjk
    )
    if is_alternative:
        layout.alternative_fields.append(entry)
    else:
        layout.trailing_fields.append(entry)


def _episode_field_of(fields: list[Field]) -> Field | None:
    for entry in fields:
        if any(token.role in _EPISODE_ROLES for token in entry.tokens):
            return entry
    return None


def compute_layout(ctx: ParseContext) -> TitleLayout:
    """Classify the fields of ``ctx.title_run``.

    Must be called after the season/episode rules have run, since the episode
    field is located by the roles they assigned.
    """
    run = ctx.title_run
    if run is None:
        return TitleLayout(run=None)

    fields = run.meaningful_fields
    layout = TitleLayout(run=run, fields=fields)
    if not fields:
        return layout

    layout.episode_field = _episode_field_of(fields)
    episode_index = layout.episode_field.index if layout.episode_field is not None else None

    first = fields[0]
    layout.title_fields = [first]
    base_words = significant_words(first.tokens)
    base_is_cjk = is_cjk_text(first.tokens)

    still_title = True
    for entry in fields[1:]:
        if episode_index is not None and entry.index >= episode_index:
            still_title = False
            if entry.index > episode_index:
                _append_secondary(layout, entry, base_is_cjk)
            continue
        if not still_title:
            _append_secondary(layout, entry, base_is_cjk)
            continue
        if entry.leading_delimiter in ALT_TITLE_DELIMITERS:
            layout.alternative_fields.append(entry)
            still_title = False
            continue
        if is_cjk_text(entry.tokens) and not base_is_cjk:
            layout.alternative_fields.append(entry)
            still_title = False
            continue
        if base_words & significant_words(entry.tokens):
            layout.alternative_fields.append(entry)
            still_title = False
            continue
        if not _field_has_content(entry):
            still_title = False
            continue
        layout.title_fields.append(entry)

    return layout
