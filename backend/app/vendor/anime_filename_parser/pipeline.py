"""The staged parsing pipeline.

::

    raw filename
        |
        v  extract extension            (rules/extension.py)
        v  normalise separators         (normalization.py)
        v  tokenize & classify brackets (tokenizer.py)
        v  locate the title text run    (this module)
        v  ordered rules                (rules/__init__.py)
        |     reliable metadata -> structure -> technical -> release
        |     -> contextual inference -> naming -> finalize
        v
    ParseResult

The pipeline itself is deliberately dumb: all knowledge lives in the rules, so
this module rarely needs to change.  It guarantees two things the rules can
rely on -- the original filename is never modified, and a failing rule can
never abort a parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .context import ParseContext
from .models import ParseResult
from .normalization import NormalizationConfig, normalize
from .rules import Rule, default_rules, extract_extension
from .tokenizer import Enclosure, Group, Token, tokenize
from .vocab import (
    AUDIO_CODEC_CHANNELS_RE,
    AUDIO_CODECS,
    BIT_DEPTH_RE,
    KNOWN_MISC_TOKENS,
    QUALITY_RE,
    SERVICES,
    SOURCES,
    SUBTITLE_TYPES,
    VIDEO_CODECS,
)

__all__ = ["Parser", "parse", "parse_many", "default_parser"]

#: Words that are technical metadata rather than title material.  Used only to
#: avoid mistaking a trailing metadata run for the title run.
_TECHNICAL_WORDS = (
    set(SOURCES)
    | set(SERVICES)
    | set(AUDIO_CODECS)
    | set(VIDEO_CODECS)
    | set(SUBTITLE_TYPES)
    | set(KNOWN_MISC_TOKENS)
)


_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

#: Token shapes that are metadata no matter where they appear.
_METADATA_SHAPES = (
    QUALITY_RE,
    BIT_DEPTH_RE,
    re.compile(r"^S\d{1,2}(?:E\d{1,4})?$", re.IGNORECASE),
    re.compile(r"^[0-9A-Fa-f]{8}$"),
    AUDIO_CODEC_CHANNELS_RE,
)


def _is_title_material(token: Token) -> bool:
    """Could this token be part of a title rather than metadata?"""
    if not token.text or not _HAS_LETTER.search(token.text):
        return False
    if token.lower in _TECHNICAL_WORDS:
        return False
    return not any(shape.match(token.text) for shape in _METADATA_SHAPES)


def _looks_like_title_run(group: Group) -> bool:
    """Does this free-text run contain something that could be a title?"""
    return any(_is_title_material(token) for token in group.tokens)


def _find_title_run(groups: Sequence[Group]) -> Group | None:
    """The first free-text run that plausibly holds the title.

    Determined from structure alone, before any rule runs, so every rule can
    ask "is this token inside the title?" and get a stable answer.  A title
    made only of digits (``86``) still gets a run via the fallback, so a
    numeric title is never lost.
    """
    text_runs = [group for group in groups if group.enclosure is Enclosure.NONE]
    for group in text_runs:
        if _looks_like_title_run(group):
            return group
    for group in text_runs:
        if any(token.is_meaningful for token in group.tokens):
            return group
    return None


@dataclass
class Parser:
    """A configured parsing engine.

    Args:
        rules: Ordered rules to run.  Defaults to the registered pipeline.
        normalization: Separator-normalisation configuration.

    A :class:`Parser` is immutable in practice and safe to share: all mutable
    state of a parse lives on its :class:`~.context.ParseContext`.

    >>> Parser().parse("[SubsPlease] Jujutsu Kaisen 2 (1080p) [B4D12414].mkv").only_name
    'Jujutsu Kaisen'
    """

    rules: list[Rule] = field(default_factory=default_rules)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)

    # ------------------------------------------------------------------
    def parse(self, filename: str, *, basename: bool = False) -> ParseResult:
        """Parse a single filename.

        Args:
            filename: The filename to parse.  Stored verbatim on the result.
            basename: Strip a leading directory path first.  Off by default
                because ``/`` is a legitimate alternative-title separator
                (``A Will Eternal / Yi Nian Yong Heng``).

        Returns:
            A :class:`~.models.ParseResult`.  Never raises: a failure inside a
            rule is reported as a warning and the rest of the parse continues.
        """
        return self.parse_with_context(filename, basename=basename)[0]

    def parse_many(
        self, filenames: Iterable[str], *, basename: bool = False
    ) -> list[ParseResult]:
        """Parse many filenames.  One bad input never affects the others."""
        return [self.parse(name, basename=basename) for name in filenames]

    def iter_parse(
        self, filenames: Iterable[str], *, basename: bool = False
    ) -> Iterator[ParseResult]:
        """Lazy version of :meth:`parse_many` for large batches."""
        for name in filenames:
            yield self.parse(name, basename=basename)

    # ------------------------------------------------------------------
    def parse_with_context(
        self, filename: str, *, basename: bool = False
    ) -> tuple[ParseResult, ParseContext]:
        """Parse and also return the internal context.

        Useful for debugging a rule or for asserting in tests that no token was
        silently dropped.  The context is not part of the stable API surface.
        """
        source = filename
        if basename:
            source = source.replace("\\", "/").rsplit("/", 1)[-1]

        result = ParseResult(filename=filename)
        ctx = self._build_context(source, result)

        for rule in self.rules:
            try:
                rule.apply(ctx)
            except Exception as exc:  # pragma: no cover - defensive
                ctx.add_warning(
                    f"Rule {rule.name!r} failed ({type(exc).__name__}: {exc}); "
                    "parsing continued without it."
                )
        return result, ctx

    # ------------------------------------------------------------------
    def _build_context(self, source: str, result: ParseResult) -> ParseContext:
        extension = extract_extension(source)
        result.extension = extension.extension
        if extension.extension is not None:
            result.provenance["extension"] = "extension"
        if extension.warning:
            result.warnings.append(extension.warning)

        normalized = normalize(extension.stem, self.normalization)
        groups, tokens = tokenize(normalized)
        ctx = ParseContext(
            filename=result.filename,
            stem=extension.stem,
            normalized=normalized,
            groups=groups,
            tokens=tokens,
            result=result,
            title_run=_find_title_run(groups),
        )
        return ctx

    # ------------------------------------------------------------------
    def describe_rules(self) -> list[tuple[int, str, str, str]]:
        """``(order, name, stage, description)`` for every rule, in order."""
        return [
            (rule.order, rule.name, rule.stage, rule.description) for rule in self.rules
        ]


#: Shared default parser.  Building the rule list is cheap but not free, and
#: rules are stateless, so one instance is reused by the module-level helpers.
_DEFAULT_PARSER: Parser | None = None


def default_parser() -> Parser:
    """Return (and lazily build) the shared default :class:`Parser`."""
    global _DEFAULT_PARSER
    if _DEFAULT_PARSER is None:
        _DEFAULT_PARSER = Parser()
    return _DEFAULT_PARSER


def parse(filename: str, *, basename: bool = False) -> ParseResult:
    """Parse ``filename`` with the default parser.

    >>> result = parse("[SubsPlease] Jujutsu Kaisen 2 (1080p) [B4D12414].mkv")
    >>> result.name, result.episode, result.quality, result.crc32
    ('Jujutsu Kaisen 2', 2, '1080p', 'B4D12414')
    """
    return default_parser().parse(filename, basename=basename)


def parse_many(filenames: Iterable[str], *, basename: bool = False) -> list[ParseResult]:
    """Parse many filenames with the default parser.

    >>> [r.episode for r in parse_many(["Foo - 01.mkv", "Foo - 02.mkv"])]
    [1, 2]
    """
    return default_parser().parse_many(filenames, basename=basename)
