"""Rule protocol and registry.

A *rule* is a small, independent unit of parsing knowledge.  It receives the
whole :class:`~..context.ParseContext`, claims the tokens it understands and
writes the fields it is responsible for.  Rules never talk to each other
directly; they communicate through the context (claimed tokens, populated
fields), which keeps them individually testable and replaceable.

Ordering is explicit and documented via :attr:`Rule.order` / :attr:`Rule.stage`
so it is always clear *why* one rule runs before another: the more reliable a
signal is, the earlier it is extracted, and the later rules only reason about
what is left.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, TypeVar

from ..context import ParseContext

__all__ = ["Rule", "Stage", "register", "registered_rules", "clear_registry"]


class Stage:
    """Pipeline stages, in execution order (see :mod:`..pipeline`)."""

    RELIABLE = "reliable-metadata"
    STRUCTURE = "season-episode-structure"
    TECHNICAL = "technical-metadata"
    RELEASE = "release-metadata"
    CONTEXTUAL = "contextual-inference"
    NAMING = "naming"
    FINALIZE = "finalize"

    ORDER: ClassVar[tuple[str, ...]] = (
        RELIABLE,
        STRUCTURE,
        TECHNICAL,
        RELEASE,
        CONTEXTUAL,
        NAMING,
        FINALIZE,
    )


class Rule(ABC):
    """Base class for all parsing rules.

    Subclasses set :attr:`name`, :attr:`order` and :attr:`stage`, and implement
    :meth:`apply`.  A rule must be *stateless*: the default parser is shared
    between calls (and threads), so all mutable state belongs on the context.
    """

    #: Stable identifier, also used in ``ParseResult.provenance``.
    name: ClassVar[str] = "rule"
    #: Lower runs first.  Leave gaps so new rules can be slotted in.
    order: ClassVar[int] = 500
    #: Which :class:`Stage` this rule belongs to (documentation only).
    stage: ClassVar[str] = Stage.TECHNICAL
    #: One-line explanation, surfaced by ``python -m anime_filename_parser --rules``.
    description: ClassVar[str] = ""

    @abstractmethod
    def apply(self, ctx: ParseContext) -> None:
        """Inspect ``ctx`` and record whatever this rule understands."""

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} {self.name} order={self.order}>"


_REGISTRY: list[type[Rule]] = []

RuleT = TypeVar("RuleT", bound=type[Rule])


def register(cls: RuleT) -> RuleT:
    """Class decorator adding a :class:`Rule` subclass to the default pipeline.

    Third-party code can use the very same decorator to extend the parser::

        from anime_filename_parser.rules.base import Rule, Stage, register

        @register
        class MyGroupRule(Rule):
            name = "my-group"
            order = 235
            stage = Stage.NAMING

            def apply(self, ctx):
                ...
    """
    if cls not in _REGISTRY:
        _REGISTRY.append(cls)
    return cls


def registered_rules() -> list[Rule]:
    """Instantiate every registered rule, ordered by :attr:`Rule.order`."""
    return [cls() for cls in sorted(_REGISTRY, key=lambda rule: (rule.order, rule.name))]


def clear_registry() -> None:
    """Empty the global registry.

    Rarely needed: to run a different set of rules, pass them to
    :class:`~..pipeline.Parser` instead of mutating global state.
    """
    _REGISTRY.clear()
