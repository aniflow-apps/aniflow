"""The mutable state a parse run threads through the rule pipeline.

:class:`ParseContext` is what every rule receives.  It owns

* the original filename (never modified),
* the normalised, tokenised view of it,
* the :class:`~.models.ParseResult` being filled in, and
* the bookkeeping that makes the parse *explainable*: which rule claimed which
  token, which rule set which field, and why a value was rejected.

Rules never rewrite strings.  They claim tokens (:meth:`ParseContext.claim`)
and set fields (:meth:`ParseContext.set_field`); anything still unclaimed at
the end of the pipeline is turned into ``misc``/``unknown_tokens`` so no
information is silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .models import ParseResult
from .normalization import NormalizedText, clean_display
from .tokenizer import Enclosure, Field, Group, Token, TokenKind

__all__ = ["ParseContext", "Roles"]


class Roles:
    """Canonical ``Token.role`` values, kept in one place for grep-ability."""

    CRC32 = "crc32"
    SEASON = "season"
    SEASON_DESIGNATION = "season_designation"
    PART = "part"
    COUR = "cour"
    EPISODE = "episode"
    EPISODE_KEYWORD = "episode_keyword"
    EPISODE_RANGE = "episode_range"
    EPISODE_VERSION = "episode_version"
    ABSOLUTE_EPISODE = "absolute_episode"
    YEAR = "year"
    QUALITY = "quality"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    SOURCE = "source"
    SERVICE = "service"
    FLAG = "flag"
    MEDIA_TYPE = "media_type"
    SUBGROUP = "subgroup"
    TITLE = "title"
    ALTERNATIVE_TITLE = "alternative_title"
    EPISODE_NAME = "episode_name"
    MISC = "misc"


@dataclass
class ParseContext:
    """Everything a rule needs, and everywhere a rule may write."""

    filename: str
    stem: str
    normalized: NormalizedText
    groups: list[Group]
    tokens: list[Token]
    result: ParseResult

    #: The free-text run that most likely holds the title.  Determined purely
    #: from filename structure (before any rule runs), so rules can rely on it.
    title_run: Group | None = None

    #: Set by the title-tail rule when it interprets a trailing number/Roman
    #: numeral inside the title as a season or episode.  ``only_name`` uses it.
    title_tail_token: Token | None = None

    #: Scene-style tag found glued to a codec (``H.264-VARYG`` -> ``VARYG``).
    #: The subgroup rule decides whether it is a release group or just noise.
    codec_release_tag: str | None = None

    #: The tokens that produced ``result.name``, kept so ``only_name`` can be
    #: derived by trimming spans instead of re-parsing a string.
    name_tokens: list[Token] = field(default_factory=list)

    #: Recognised extras with no field of their own (``Weekly``,
    #: ``{OP & ED Lyrics}``, ``HDR``).  They also go to ``misc``; the tags
    #: rule uses this list to tell them apart from genuinely unknown text.
    recognised_tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # token access
    # ------------------------------------------------------------------
    def available_tokens(self) -> list[Token]:
        """Tokens no rule has claimed yet (delimiters excluded)."""
        return [token for token in self.tokens if token.available]

    def group(self, index: int) -> Group:
        return self.groups[index]

    def groups_of(self, *enclosures: Enclosure) -> list[Group]:
        return [group for group in self.groups if group.enclosure in enclosures]

    def next_token(self, token: Token) -> Token | None:
        """The next non-delimiter token inside the same group."""
        group = self.groups[token.group_index]
        for candidate in group.tokens[token.position + 1 :]:
            if candidate.kind is not TokenKind.DELIMITER:
                return candidate
        return None

    def previous_token(self, token: Token) -> Token | None:
        """The previous non-delimiter token inside the same group."""
        group = self.groups[token.group_index]
        for candidate in reversed(group.tokens[: token.position]):
            if candidate.kind is not TokenKind.DELIMITER:
                return candidate
        return None

    def field_of(self, token: Token) -> Field | None:
        group = self.groups[token.group_index]
        for entry in group.fields:
            if entry.index == token.field_index:
                return entry
        return None

    def is_in_title_field(self, token: Token) -> bool:
        """Is ``token`` inside the first meaningful field of the title run?

        Used to decide whether a designation such as ``Part 6`` belongs to the
        title (and must stay in ``name``) or is release metadata.
        """
        run = self.title_run
        if run is None or token.group_index != run.index:
            return False
        meaningful = run.meaningful_fields
        return bool(meaningful) and token.field_index == meaningful[0].index

    # ------------------------------------------------------------------
    # claiming
    # ------------------------------------------------------------------
    def claim(
        self,
        tokens: Token | Iterable[Token],
        role: str,
        rule: str,
        *,
        keep_in_title: bool = False,
    ) -> None:
        """Mark tokens as understood by ``rule``.

        Args:
            tokens: A single token or an iterable of tokens.
            role: One of :class:`Roles`.
            rule: Name of the claiming rule (recorded for ``--explain``).
            keep_in_title: Keep the token inside ``name`` even though it was
                claimed (``The Movie``, ``Part 6``, an ambiguous trailing
                number).
        """
        if isinstance(tokens, Token):
            tokens = (tokens,)
        for token in tokens:
            if token.consumed_by is None:
                token.consumed_by = rule
                token.role = role
            token.keep_in_title = token.keep_in_title or keep_in_title

    # ------------------------------------------------------------------
    # writing results
    # ------------------------------------------------------------------
    def set_field(
        self,
        name: str,
        value: Any,
        rule: str,
        *,
        source_text: str | None = None,
        overwrite: bool = False,
    ) -> bool:
        """Set ``result.<name>`` unless it is already populated.

        First writer wins.  A second, *different* value is not silently
        dropped: it produces a warning and is preserved in ``misc`` so no
        information is lost.

        Returns:
            ``True`` when the value was written.
        """
        if value is None:
            return False
        current = getattr(self.result, name)
        if current is not None and not overwrite:
            if current == value:
                return False
            self.add_warning(
                f"Conflicting values for {name!r}: kept {current!r} "
                f"(rule {self.result.provenance.get(name, '?')}), "
                f"ignored {value!r} (rule {rule})."
            )
            if source_text:
                self.add_misc(source_text)
            return False
        setattr(self.result, name, value)
        self.result.provenance[name] = rule
        return True

    def set_flag(self, name: str, value: bool, rule: str) -> None:
        """Set a boolean flag field (``batch``, ``repack``, ``uncensored``)."""
        setattr(self.result, name, value)
        self.result.provenance[name] = rule

    def add_misc(self, value: str) -> None:
        """Preserve a recognised-but-unfielded string (deduplicated)."""
        cleaned = clean_display(value)
        if cleaned and cleaned not in self.result.misc:
            self.result.misc.append(cleaned)

    def add_tag(self, value: str) -> None:
        """Record a recognised extra (deduplicated, order preserved)."""
        cleaned = clean_display(value)
        if cleaned and cleaned not in self.recognised_tags:
            self.recognised_tags.append(cleaned)

    def add_alternative_title(self, value: str) -> None:
        cleaned = clean_display(value)
        if not cleaned:
            return
        if cleaned == self.result.name:
            return
        if cleaned not in self.result.alternative_titles:
            self.result.alternative_titles.append(cleaned)

    def add_warning(self, message: str) -> None:
        """Record an explanation of an ambiguous or rejected decision."""
        if message not in self.result.warnings:
            self.result.warnings.append(message)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def render(self, start: int, end: int) -> str:
        """Display-ready text for a span of the normalised filename."""
        return self.normalized.render(start, end)

    def render_tokens(self, tokens: Sequence[Token]) -> str:
        """Display-ready text spanning ``tokens`` (separators preserved)."""
        if not tokens:
            return ""
        return self.render(tokens[0].start, tokens[-1].end)

    def render_group(self, group: Group) -> str:
        return self.render(group.start, group.end)
